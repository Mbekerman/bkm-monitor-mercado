"""im_fechar_coleta pela API: resposta perdida no gateway não vira falha da coleta."""
from __future__ import annotations

import httpx
import pytest

from coletar import banco as banco_mod
from coletar.banco import BancoSupabase
from coletar.cli import main
from coletar.erros import ColetaErro

from test_rodada import FonteFixa

URL = "https://exemplo.supabase.co"


class Relogio:
    def __init__(self):
        self.t = 0.0
        self.dormidas = []

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.dormidas.append(s)
        self.t += s


def _banco(handler, relogio=None):
    relogio = relogio or Relogio()
    return BancoSupabase(URL, "chave-de-teste", transporte=httpx.MockTransport(handler),
                         sleep=relogio.dormir, relogio=relogio), relogio


def _servidor(fechar, situacoes, vistos):
    """fechar: Response ou exceção para o RPC; situacoes: o que a leitura de im_coletas devolve, em ordem."""
    fila = list(situacoes)

    def handler(request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        vistos.append((request.method, caminho, request.extensions.get("timeout")))
        if caminho.endswith("/rpc/im_fechar_coleta"):
            if isinstance(fechar, Exception):
                raise fechar
            return fechar
        if caminho.endswith("/rpc/im_falhar_coleta"):
            return httpx.Response(204)
        if caminho.endswith("/rpc/im_abrir_coleta"):
            return httpx.Response(200, json="c1")
        if caminho.endswith("/rpc/im_importar"):
            return httpx.Response(200, json={"coletados": None, "unidades": None, "ignorados": 0, "erros": 0})
        if caminho.startswith("/storage/"):
            return httpx.Response(200, json={"Key": "ok"})
        if caminho.endswith("/im_coletas"):
            sit = fila.pop(0) if len(fila) > 1 else fila[0]
            return httpx.Response(200, json=[{"situacao": sit, "coletados": 2, "unidades": 7, "esperados": 2}])
        if caminho.endswith("/im_vendas"):
            assert request.headers["prefer"] == "count=exact"
            return httpx.Response(206, json=[{"id": "x"}], headers={"content-range": "0-0/5"})
        if caminho.endswith("/im_unidades"):
            return httpx.Response(200, json=[], headers={"content-range": "*/0"})
        if caminho.endswith("/im_ids_externos"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return handler


def test_fechar_espera_mais_que_o_timeout_do_banco():
    vistos = []
    b, _ = _banco(_servidor(httpx.Response(200, json={"situacao": "completa", "vendas": 1}), ["completa"], vistos))
    assert b.fechar_coleta("c1", 2)["situacao"] == "completa"
    timeout = [t for m, c, t in vistos if c.endswith("/im_fechar_coleta")][0]
    assert timeout["read"] >= 600  # o banco dá 10 min para im_fechar_coleta
    assert banco_mod.FECHAR_ESPERA_S > 600


@pytest.mark.parametrize("perda", [
    httpx.Response(504, text="upstream request timeout"),
    httpx.Response(502, text="bad gateway"),
    httpx.ReadTimeout("leitura"),
    httpx.RemoteProtocolError("conexão caiu"),
])
def test_resposta_perdida_espera_a_coleta_fechar(perda):
    vistos = []
    b, rel = _banco(_servidor(perda, ["rodando", "rodando", "completa"], vistos))
    r = b.fechar_coleta("c1", 2)
    assert r == {"situacao": "completa", "coletados": 2, "unidades": 7, "esperados": 2,
                 "vendas": 5, "removidas": 0, "aguardado": True}
    assert rel.dormidas == [banco_mod.FECHAR_INTERVALO_S] * 2
    assert not any(c.endswith("/im_falhar_coleta") for _, c, _ in vistos)
    assert sum(c.endswith("/im_fechar_coleta") for _, c, _ in vistos) == 1  # não fecha de novo


def test_leitura_que_falha_no_meio_continua_esperando():
    vistos, n = [], {"i": 0}
    base = _servidor(httpx.Response(504), ["completa"], vistos)

    def handler(request):
        if request.url.path.endswith("/im_coletas"):
            n["i"] += 1
            if n["i"] == 1:
                raise httpx.ConnectError("caiu")
            if n["i"] == 2:
                return httpx.Response(503)
        return base(request)

    b, rel = _banco(handler)
    assert b.fechar_coleta("c1", 2)["situacao"] == "completa"
    assert len(rel.dormidas) == 2


def test_coleta_que_nao_sai_de_rodando_vira_erro_sem_causa_de_rede():
    vistos = []
    b, rel = _banco(_servidor(httpx.Response(504), ["rodando"], vistos))
    with pytest.raises(ColetaErro) as exc:
        b.fechar_coleta("c1", 2)
    assert "continua 'rodando'" in str(exc.value)
    assert exc.value.__cause__ is None  # a CLI não chama im_falhar_coleta com o fechamento vivo
    assert rel.t >= banco_mod.FECHAR_ESPERA_S


def test_erro_do_banco_nao_espera():
    vistos = []
    resp = httpx.Response(500, json={"code": "57014", "message": "canceling statement due to statement timeout"})
    b, rel = _banco(_servidor(resp, ["rodando"], vistos))
    with pytest.raises(ColetaErro, match="57014"):
        b.fechar_coleta("c1", 2)
    assert rel.dormidas == []
    assert not any(c.endswith("/im_coletas") for _, c, _ in vistos)


def test_cli_com_504_no_fechamento_sai_completa():
    vistos = []
    b, _ = _banco(_servidor(httpx.Response(504, text="upstream request timeout"), ["rodando", "completa"], vistos))
    codigo = main(["--tipo", "mensal", "--data", "2026-10-24"],
                  fonte=FonteFixa([{"id_externo": "1"}, {"id_externo": "2"}]), banco=b)
    assert codigo == 0
    assert not any(c.endswith("/im_falhar_coleta") for _, c, _ in vistos)
