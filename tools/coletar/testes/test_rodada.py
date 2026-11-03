from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from coletar.cli import main
from coletar.erros import OruloAuthError
from coletar.fonte import ContextoColeta, Municipio, ResultadoFonte
from coletar.orulo_login import OruloLogin
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
        self.ctx = ctx
        if self.kw.get("estoura_auth"):
            raise OruloAuthError("401 mesmo após relogin")
        if self.kw.get("estoura_rede"):
            raise httpx.ConnectError("conexao recusada")
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


def _predio(pid):
    return {
        "id": pid, "name": f"Predio {pid}", "status": "Em construção", "stage": "Lançamento",
        "type": "Vertical", "finality": "Residencial",
        "address": {"city": "São Paulo", "state": "SP", "street": "Rua A", "number": 1,
                    "latitude": -23.55, "longitude": -46.63, "area": "Centro"},
        "developer": {"id": "9", "name": "Inc"},
        "total_units": 4, "stock": 4,
    }


def test_cli_id_passa_pela_rodada_sem_descoberta():
    pedidos = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidos.append(request.url.path)
        if request.url.host == "auth.orulo.com.br":
            return httpx.Response(200, json={"access_token": "tok"})
        if request.url.path == "/api/elastic":
            raise AssertionError("id explícito não varre a listagem")
        if request.url.path in ("/api/portal/buildings/83029", "/api/portal/buildings/12"):
            return httpx.Response(200, json=_predio(request.url.path.rsplit("/", 1)[-1]))
        if request.url.path.endswith("/units"):
            return httpx.Response(200, json={"units": [{
                "reference": "1", "section": "A", "status": "Disponível", "type": "Apartamento",
                "price": 10, "private_area": 40,
            }]})
        return httpx.Response(404)

    fonte = OruloLogin(
        email="pessoa@bkm.test", senha="segredo", atraso=(0, 0), sleep=lambda _s: None,
        transporte=httpx.MockTransport(handler),
    )
    banco = BancoMem()
    banco.conhecidos_map = {"83029": "lancamento"}
    codigo = main(
        ["--tipo", "lancamentos", "--data", "2026-10-24", "--id", "83029", "--id", " 12 ", "--id", "83029"],
        fonte=fonte, banco=banco,
    )
    assert codigo == 0
    assert pedidos.count("/api/portal/buildings/83029") == 1
    assert "/api/elastic" not in pedidos
    assert "/api/portal/buildings/83029/units" in pedidos
    assert "/api/portal/buildings/12" in pedidos
    assert "/api/portal/buildings/12/units" in pedidos
    assert banco.chamadas[0] == ("abrir", "orulo_login", "lancamentos", "2026-10-24", "mercado/2026-10-24-lancamentos.json.gz")
    assert banco.lotes == [2]
    assert ("fechar", 2, False) in banco.chamadas


def test_cli_repassa_os_ids_normalizados():
    fonte = FonteFixa([{"id_externo": "83029"}])
    banco = BancoMem()
    codigo = main(
        ["--tipo", "mensal", "--data", "2026-10-24", "--id", "83029", "--id", " 12 ", "--id", "83029"],
        fonte=fonte, banco=banco,
    )
    assert codigo == 0
    assert fonte.ctx.ids == ("83029", "12")
    assert banco.chamadas[0][0] == "abrir"


def test_cli_id_na_planilha_nao_abre_coleta(capsys):
    banco = BancoMem()
    codigo = main(
        ["--tipo", "mensal", "--data", "2026-10-24", "--fonte", "orulo_planilha", "--id", "1"],
        banco=banco,
    )
    assert codigo == 1
    assert "não coleta por id" in capsys.readouterr().err
    assert banco.chamadas == []


def test_cli_id_vazio(capsys):
    assert main(["--tipo", "mensal", "--data", "2026-10-24", "--id", " "]) == 1
    assert "id vazio" in capsys.readouterr().err


def test_erro_de_rede_marca_a_coleta_e_sai_1(caplog, capsys):
    banco = BancoMem()
    with caplog.at_level("ERROR", logger="coletar"):
        codigo = main(
            ["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "São Paulo"],
            fonte=FonteFixa([], estoura_rede=True), banco=banco,
        )
    err = capsys.readouterr().err
    assert codigo == 1
    assert "Traceback" not in err
    assert "erro de rede: ConnectError" in caplog.text
    assert banco.chamadas[0][0] == "abrir"
    assert ("falhar", "erro de rede: ConnectError") in banco.chamadas


def test_erro_de_rede_antes_de_abrir_so_loga(caplog, capsys):
    class BancoCai(BancoMem):
        def conhecidos(self, fonte):
            raise httpx.ConnectError("sem dns")

    banco = BancoCai()
    with caplog.at_level("ERROR", logger="coletar"):
        codigo = main(
            ["--tipo", "lancamentos", "--data", "2026-10-24"],
            fonte=FonteFixa([]), banco=banco,
        )
    assert codigo == 1
    assert "Traceback" not in capsys.readouterr().err
    assert "erro de rede: ConnectError" in caplog.text
    assert not any(c[0] == "falhar" for c in banco.chamadas)
    assert not any(c[0] == "abrir" for c in banco.chamadas)


def test_connecterror_no_detalhe_falha_a_coleta(caplog, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.orulo.com.br":
            return httpx.Response(200, json={"access_token": "tok"})
        if request.url.path == "/api/elastic":
            return httpx.Response(200, json={"buildings": [_predio("9")], "quantity_found": 1})
        raise httpx.ConnectError("caiu")

    fonte = OruloLogin(
        email="pessoa@bkm.test", senha="segredo", atraso=(0, 0), sleep=lambda _s: None,
        transporte=httpx.MockTransport(handler),
    )
    banco = BancoMem()
    with caplog.at_level("ERROR", logger="coletar"):
        codigo = main(
            ["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "São Paulo"],
            fonte=fonte, banco=banco,
        )
    assert codigo == 1
    assert "Traceback" not in capsys.readouterr().err
    assert ("falhar", "erro de rede: ConnectError") in banco.chamadas
    assert "erro de rede: ConnectError" in caplog.text


def test_erro_de_rede_na_descoberta_anota_e_fecha_incompleta(caplog, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.orulo.com.br":
            return httpx.Response(200, json={"access_token": "tok"})
        raise httpx.ConnectError("caiu")

    fonte = OruloLogin(
        email="pessoa@bkm.test", senha="segredo", atraso=(0, 0), sleep=lambda _s: None,
        transporte=httpx.MockTransport(handler),
    )
    banco = BancoMem()
    with caplog.at_level("ERROR", logger="coletar"):
        codigo = main(
            ["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "São Paulo"],
            fonte=fonte, banco=banco,
        )
    err = capsys.readouterr().err
    assert codigo == 2
    assert "Traceback" not in err
    assert ("anotar", "", "descoberta") in banco.chamadas
    assert banco.chamadas[-1][0] == "fechar" and banco.chamadas[-1][2] is True
    assert not any(c[0] == "falhar" for c in banco.chamadas)


def test_erro_de_rede_na_importacao_falha_uma_vez(caplog, capsys):
    class BancoImporta(BancoMem):
        def importar(self, coleta, emps):
            raise httpx.ConnectError("caiu")

    banco = BancoImporta()
    with caplog.at_level("ERROR", logger="coletar"):
        codigo = main(
            ["--tipo", "mensal", "--data", "2026-10-24"],
            fonte=FonteFixa([{"id_externo": "1"}]), banco=banco,
        )
    assert codigo == 1
    assert "Traceback" not in capsys.readouterr().err
    assert [c for c in banco.chamadas if c[0] == "falhar"] == [("falhar", "erro de rede: ConnectError")]
    assert "erro de rede: ConnectError" in caplog.text


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


def _rodar_processo(argv, **env_extra):
    env = os.environ.copy()
    env.pop("COLETAR_TIPO", None)
    env.pop("COLETAR_PG_DSN", None)
    env.update(env_extra)
    raiz = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = raiz
    return subprocess.run(
        [sys.executable, "-m", "coletar", *argv],
        cwd=raiz, env=env, capture_output=True, text=True,
    )


def test_sem_args_usa_coletar_tipo_e_hoje_em_sao_paulo(monkeypatch, caplog):
    # 03:10 UTC do dia 1 é 00:10 em São Paulo: a mensal cai no dia 1, não no mês anterior.
    banco = BancoMem()
    fonte = FonteFixa([{"id_externo": "1"}])
    monkeypatch.setenv("COLETAR_TIPO", " mensal ")
    momento = datetime(2026, 11, 1, 3, 10, tzinfo=timezone.utc)
    with caplog.at_level("INFO", logger="coletar"):
        codigo = main([], fonte=fonte, banco=banco, agora=momento)
    assert codigo == 0
    assert banco.chamadas[0][2] == "completa_mensal"
    assert banco.chamadas[0][3] == "2026-11-01"
    assert "referência 2026-11-01, tipo mensal" in caplog.text


def test_meia_noite_utc_ainda_e_o_dia_anterior_em_sao_paulo(monkeypatch):
    # 02:30 UTC é 23:30 do dia anterior em São Paulo (UTC−3, sem horário de verão).
    banco = BancoMem()
    fonte = FonteFixa([{"id_externo": "1"}])
    monkeypatch.setenv("COLETAR_TIPO", "lancamentos")
    momento = datetime(2026, 11, 3, 2, 30, tzinfo=timezone.utc)
    assert main([], fonte=fonte, banco=banco, agora=momento) == 0
    assert fonte.ctx.tipo == "lancamentos"
    assert fonte.ctx.referencia == date(2026, 11, 2)


def test_segunda_de_lancamentos_cai_no_mesmo_dia(monkeypatch):
    # Agenda: segunda 06:00 em São Paulo = 09:00 UTC. O dia não muda.
    banco = BancoMem()
    fonte = FonteFixa([{"id_externo": "83029"}])
    monkeypatch.setenv("COLETAR_TIPO", "lancamentos")
    momento = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    assert main(["--id", "83029"], fonte=fonte, banco=banco, agora=momento) == 0
    assert fonte.ctx.ids == ("83029",)
    assert fonte.ctx.referencia == date(2026, 11, 2)
    assert banco.chamadas[0][2] == "lancamentos"


def test_flag_explicita_ganha_do_ambiente_e_do_relogio(monkeypatch):
    banco = BancoMem()
    fonte = FonteFixa([{"id_externo": "1"}])
    monkeypatch.setenv("COLETAR_TIPO", "lancamentos")
    momento = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    codigo = main(
        ["--tipo", "mensal", "--data", "2026-10-01"],
        fonte=fonte, banco=banco, agora=momento,
    )
    assert codigo == 0
    assert banco.chamadas[0][2] == "completa_mensal"
    assert banco.chamadas[0][3] == "2026-10-01"


def test_tipo_ausente_ou_invalido_sai_1_sem_abrir(monkeypatch, capsys):
    banco = BancoMem()
    fonte = FonteFixa([{"id_externo": "1"}])
    monkeypatch.delenv("COLETAR_TIPO", raising=False)
    assert main([], fonte=fonte, banco=banco) == 1
    assert "COLETAR_TIPO" in capsys.readouterr().err
    assert banco.chamadas == []
    monkeypatch.setenv("COLETAR_TIPO", "semanal")
    assert main([], fonte=fonte, banco=banco) == 1
    assert banco.chamadas == []


def test_data_invalida_sem_tipo_na_linha_sai_1(monkeypatch, capsys):
    monkeypatch.setenv("COLETAR_TIPO", "mensal")
    banco = BancoMem()
    assert main(["--data", "2026-13-01"], fonte=FonteFixa([]), banco=banco) == 1
    assert "AAAA-MM-DD" in capsys.readouterr().err
    assert banco.chamadas == []


def test_fuso_sem_base_iana_usa_utc_menos_3(monkeypatch):
    from zoneinfo import ZoneInfoNotFoundError

    from coletar import cli

    def sem_fuso(_nome):
        raise ZoneInfoNotFoundError("sem tzdata")

    monkeypatch.setattr(cli, "ZoneInfo", sem_fuso)
    momento = datetime(2026, 11, 3, 2, 30, tzinfo=timezone.utc)
    assert cli.data_de_hoje(momento) == date(2026, 11, 2)


def test_tipo_desconhecido_na_flag_sai_2():
    with pytest.raises(SystemExit) as saida:
        main(["--tipo", "semanal", "--data", "2026-10-24"])
    assert saida.value.code not in (0, None)
    assert saida.value.code == 2


def test_processo_sem_config_nao_sai_zero():
    sem = _rodar_processo([])
    assert sem.returncode == 1
    assert "usage:" not in sem.stderr
    assert "COLETAR_TIPO" in sem.stderr
    invalido = _rodar_processo([], COLETAR_TIPO="semanal")
    assert invalido.returncode == 1
    assert "usage:" not in invalido.stderr
    ajuda = _rodar_processo(["--help"])
    assert ajuda.returncode == 0
    assert ajuda.stdout.startswith("usage: coletar")
    desconhecido = _rodar_processo(["--nao-existe"])
    assert desconhecido.returncode == 2
    assert "usage: coletar" in desconhecido.stderr
