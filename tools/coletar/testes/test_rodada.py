from __future__ import annotations

import gzip
import json
from datetime import date

import httpx
import pytest

from coletar.cli import main
from coletar.erros import OruloAuthError
from coletar.fonte import ContextoColeta, Municipio, ResultadoFonte
from coletar.orulo_planilha import AVISO_PLANILHA, OruloPlanilha
from coletar.rodada import executar


class BancoMem:
    def __init__(self):
        self.chamadas = []
        self.bruto = b""
        self.lotes = []
        self.conhecidos_map = {}

    def abrir(self, fonte, tipo, referencia, caminho):
        self.chamadas.append(("abrir", fonte, tipo, referencia.isoformat(), caminho))
        return "c1"

    def importar(self, coleta, emps):
        self.lotes.append(len(emps))
        return {"coletados": sum(self.lotes), "unidades": 0, "ignorados": 0, "erros": 0}

    def fechar_coleta(self, coleta, esperados, *, forcar_incompleta=False):
        self.chamadas.append(("fechar", esperados, forcar_incompleta))
        return {"situacao": "incompleta" if forcar_incompleta else "completa", "vendas": 0, "removidas": 0}

    def anotar_falha(self, coleta, id_externo, motivo, detalhe):
        self.chamadas.append(("anotar", id_externo, motivo))

    def falhar(self, coleta, motivo):
        self.chamadas.append(("falhar", motivo))

    def conhecidos(self, fonte):
        return dict(self.conhecidos_map)

    def gravar_bruto(self, caminho, conteudo):
        self.bruto = conteudo
        self.chamadas.append(("bruto", caminho))

    def fechar(self):
        pass


class FonteFixa:
    nome = "orulo_login"

    def __init__(self, emps, **kw):
        self.emps = emps
        self.kw = kw

    def coletar(self, ctx):
        if self.kw.get("estoura_auth"):
            raise OruloAuthError("401 mesmo após relogin")
        return ResultadoFonte(
            empreendimentos=self.emps,
            bruto={"itens": [e.get("id_externo") for e in self.emps]},
            esperados=self.kw.get("esperados", len(self.emps)),
            falhas=self.kw.get("falhas", []),
            forcar_incompleta=self.kw.get("forcar", False),
            falhou=self.kw.get("falhou", False),
            motivo_falha=self.kw.get("motivo"),
            avisos=self.kw.get("avisos", []),
        )


def _ctx():
    from coletar.descoberta import SP_BBOX
    return ContextoColeta(tipo="completa_mensal", referencia=date(2026, 10, 1),
                          municipios=[Municipio("São Paulo", "SP", SP_BBOX)])


def test_lotes_de_50_e_bruto_gzip():
    banco = BancoMem()
    emps = [{"id_externo": str(i)} for i in range(120)]
    resultado = executar(banco, FonteFixa(emps), _ctx())
    assert banco.lotes == [50, 50, 20]
    assert resultado.situacao == "completa"
    assert resultado.caminho == "mercado/2026-10-01-completa_mensal.json.gz"
    assert json.loads(gzip.decompress(banco.bruto))["itens"][0] == "0"
    assert [c[0] for c in banco.chamadas] == ["abrir", "bruto", "fechar"]
    assert banco.chamadas[0][2] == "completa_mensal"


def test_parada_marca_incompleta(caplog):
    banco = BancoMem()
    with caplog.at_level("WARNING"):
        resultado = executar(banco, FonteFixa([{"id_externo": "1"}], forcar=True, esperados=10,
                                              falhas=[{"id_externo": "", "motivo": "bloqueado", "detalhe": "429"}]),
                             _ctx())
    assert resultado.situacao == "incompleta"
    assert banco.chamadas[-1] == ("fechar", 10, True)
    assert "coleta incompleta" in caplog.text
    assert ("anotar", "", "bloqueado") in banco.chamadas


def test_401_encerra_como_falhou():
    banco = BancoMem()
    resultado = executar(banco, FonteFixa([], estoura_auth=True), _ctx())
    assert resultado.situacao == "falhou"
    assert banco.chamadas[-1][0] == "falhar"
    assert "401" in banco.chamadas[-1][1]


def test_planilha_avisa_no_log_da_rodada(tmp_path, caplog):
    arq = tmp_path / "t.csv"
    arq.write_text(
        "id,nome,cidade,uf,latitude,longitude,situacao,tipo,finalidade\n"
        "1,Ares,São Paulo,SP,-23.5,-46.6,Lançamento,Vertical,Residencial\n",
        encoding="utf-8",
    )
    banco = BancoMem()
    with caplog.at_level("WARNING"):
        resultado = executar(banco, OruloPlanilha(str(arq)), _ctx())
    assert AVISO_PLANILHA in resultado.avisos
    assert "planilha real" in caplog.text
    assert resultado.situacao == "completa"


def test_cli_mensal_e_ajuda():
    banco = BancoMem()
    codigo = main(
        ["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "São Paulo"],
        fonte=FonteFixa([{"id_externo": "1"}]), banco=banco,
    )
    assert codigo == 0
    assert banco.chamadas[0][2] == "completa_mensal"
    assert banco.chamadas[0][3] == "2026-10-24"
    with pytest.raises(SystemExit) as saida:
        main(["--help"])
    assert saida.value.code == 0
    assert main(["--tipo", "lancamentos", "--data", "2026-13-01"]) == 1
    assert main(["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "Campinas"]) == 1


def test_banco_supabase_monta_rpc_e_storage():
    from coletar.banco import BancoSupabase

    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append((
            request.method, request.url.path, request.headers.get("authorization"),
            request.content, request.headers.get("content-profile"),
        ))
        if request.url.path.endswith("/im_abrir_coleta"):
            return httpx.Response(200, json="coleta-uuid")
        if request.url.path.endswith("/im_importar"):
            return httpx.Response(200, json={"coletados": 1, "unidades": 2, "ignorados": 0, "erros": 0})
        if request.url.path.endswith("/im_fechar_coleta"):
            return httpx.Response(200, json={"situacao": "completa", "vendas": 0, "removidas": 0})
        if request.url.path.startswith("/storage/"):
            return httpx.Response(200, json={"Key": "ok"})
        if "im_ids_externos" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    banco = BancoSupabase("https://exemplo.supabase.co", "chave-de-teste", transporte=httpx.MockTransport(handler))
    assert banco.abrir("orulo_login", "completa_mensal", date(2026, 10, 1), "mercado/2026-10-01-completa_mensal.json.gz") == "coleta-uuid"
    banco.importar("coleta-uuid", [{"id_externo": "1"}])
    banco.fechar_coleta("coleta-uuid", 1, forcar_incompleta=False)
    banco.gravar_bruto("mercado/2026-10-01-completa_mensal.json.gz", b"abc")
    banco.conhecidos("orulo_login")
    assert vistos[0][2] == "Bearer chave-de-teste"
    assert vistos[0][4] is None
    corpo = json.loads(vistos[0][3])
    assert "p_empresa" not in corpo
    assert corpo["p_bruto_caminho"].endswith("completa_mensal.json.gz")
    assert corpo["p_fonte"] == "orulo_login"
    assert "/storage/v1/object/im-bruto/mercado/2026-10-01-completa_mensal.json.gz" in vistos[3][1]
    assert b"chave-de-teste" not in vistos[3][3]
    assert "empresa_id" not in vistos[4][1]
