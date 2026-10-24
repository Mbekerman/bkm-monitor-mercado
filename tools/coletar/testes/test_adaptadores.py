from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from coletar.descoberta import SP_BBOX, descobrir
from coletar.erros import OruloAuthError, OruloBlocked
from coletar.fonte import ContextoColeta, Municipio
from coletar.mapeamento import (
    data_iso, mapear_empreendimento, mapear_unidade, numero, preco,
    situacao_empreendimento, situacao_unidade,
)
from coletar.orulo_api import OruloApi
from coletar.orulo_login import CLIENT_ID, OruloLogin
from coletar.orulo_planilha import AVISO_PLANILHA, OruloPlanilha

FIX = Path(__file__).parent / "fixtures"
ATRASO = (0, 0)


def _ctx(tipo="completa_mensal", conhecidos=None):
    return ContextoColeta(
        tipo=tipo,
        referencia=date(2026, 10, 24),
        municipios=[Municipio("São Paulo", "SP", SP_BBOX)],
        conhecidos=conhecidos or {},
    )


def _noop(_segundos):
    return None


def _predio(pid, stage, status, cidade="São Paulo"):
    return {
        "id": pid, "name": f"Predio {pid}", "status": status, "stage": stage,
        "type": "Vertical", "finality": "Residencial",
        "address": {"city": cidade, "state": "SP", "street": "Rua A", "number": 1,
                    "latitude": -23.55, "longitude": -46.63, "area": "Centro"},
        "developer": {"id": "9", "name": "Inc"},
    }


class TestMapeamento:
    def test_fixture_no_formato_da_spec(self):
        detalhe = json.loads((FIX / "detail_83029.json").read_text())
        unidades = json.loads((FIX / "units_83029.json").read_text())["units"]
        extra = dict(unidades[0])
        extra.update(reference="SENT", price=0.1, original_price="0,1", status="Disponível", type="Studio - NR")
        loja = dict(unidades[0])
        loja.update(reference="LJ", section="TORRE A", price=1000, status="Vendida", type="Loja")
        emp = mapear_empreendimento(None, detalhe, [extra, loja, *unidades[:2]], incluir_unidades=True)
        esperadas = {
            "id_externo", "nome", "incorporadora", "endereco", "bairro", "municipio", "uf",
            "latitude", "longitude", "situacao", "tipo", "finalidade", "data_lancamento",
            "previsao_entrega", "torres", "programa_social", "unidades_informadas",
            "estoque_informado", "tabela_atualizada_em", "link_fonte", "unidades",
        }
        assert esperadas <= set(emp)
        assert emp["id_externo"] == "83029"
        assert emp["nome"] == "Ares do Horto Pau Brasil"
        assert emp["situacao"] == "lancamento"
        assert emp["tipo"] == "vertical"
        assert emp["finalidade"] == "residencial"
        assert emp["data_lancamento"] == "2026-07-01"
        assert emp["previsao_entrega"] == "2029-07-31"
        assert emp["tabela_atualizada_em"] == "2026-10-02"
        assert emp["torres"] == 2
        assert emp["programa_social"] == "nao"
        assert emp["unidades_informadas"] == 574
        assert emp["estoque_informado"] == 455
        assert emp["municipio"] == "São Paulo"
        assert emp["incorporadora"]["id_externo"] == "674"
        assert emp["endereco"] == "Estrada do Campo Limpo, 1501"
        sent = emp["unidades"][0]
        assert sent["preco"] is None and sent["preco_original"] is None
        assert sent["tipo_unidade"] == "Studio - NR"
        assert sent["id_externo"] is None
        assert emp["unidades"][1]["tipo_unidade"] == "Loja"
        assert emp["unidades"][1]["situacao"] == "vendida"
        unidade_chaves = {
            "id_externo", "torre", "numero", "andar", "area_privativa_m2", "dormitorios",
            "suites", "vagas", "tipologia_fonte", "tipo_unidade", "situacao", "preco", "preco_original",
        }
        assert unidade_chaves <= set(emp["unidades"][2])

    def test_tipo_da_tipologia_quando_a_unidade_nao_traz(self):
        tips = {"130142": {"id": "130142", "type": "Apartamento"}}
        u = mapear_unidade({"reference": "10", "section": "TORRE B", "typology_id": 130142,
                            "status": "Disponível", "price": 10, "private_area": 32}, tips)
        assert u["tipo_unidade"] == "Apartamento"
        assert u["tipologia_fonte"] == "130142"
        assert u["torre"] == "TORRE B"

    @pytest.mark.parametrize("rotulo,esperado", [
        ("Disponível", "disponivel"),
        ("Reservada", "reservada"),
        ("Em negociação", "reservada"),
        ("Vendida", "vendida"),
        ("Venda aprovada", "vendida"),
        ("Venda suspensa", "indisponivel"),
        ("Fora de venda", "indisponivel"),
        ("Reserva técnica", "indisponivel"),
        ("Permutada", "indisponivel"),
        ("Alugada", "indisponivel"),
        ("available", "disponivel"),
        ("under_negotiation", "reservada"),
        ("sold", "vendida"),
        ("approved_sale", "vendida"),
        ("Algo novo", "nao_informado"),
    ])
    def test_status_da_unidade(self, rotulo, esperado):
        assert situacao_unidade(rotulo) == esperado

    def test_estagio_de_lancamento_ganha_da_obra(self):
        assert situacao_empreendimento("Em construção", "Lançamento") == "lancamento"
        assert situacao_empreendimento("Em construção", "Breve lançamento") == "breve_lancamento"
        assert situacao_empreendimento("Em construção", "Em construção") == "em_obras"
        assert situacao_empreendimento("Pronto novo", "Pronto novo") == "pronto"
        assert situacao_empreendimento("Usado", None) == "usado"

    def test_amostra_elastic_vira_lista_sem_unidades(self):
        amostra = json.loads((FIX / "elastic_sample.json").read_text())
        assert amostra["quantity_found"] == 4211
        emp = mapear_empreendimento(amostra["buildings"][0], None, None, incluir_unidades=False)
        assert emp["nome"] == "Vista Laura"
        assert emp["situacao"] == "pronto"
        assert emp["tipo"] == "vertical"
        assert emp["finalidade"] == "residencial"
        assert emp["municipio"] == "São Paulo"
        assert "unidades" not in emp

    def test_sentinela_data_e_numero(self):
        assert preco(0.1) is None
        assert preco("0,1") is None
        assert preco("") is None
        assert preco(None) is None
        assert preco(234591.84) == 234591.84
        assert numero("1.234,56") == 1234.56
        assert numero("32,14") == 32.14
        assert data_iso("02/10/2026 15:09:34") == "2026-10-02"
        assert data_iso("01/07/2026") == "2026-07-01"
        assert data_iso("2026-07-01") == "2026-07-01"


def _cliente(handler):
    return OruloLogin(
        email="pessoa@bkm.test", senha="segredo", atraso=ATRASO, sleep=_noop,
        transporte=httpx.MockTransport(handler),
    )


class TestLogin:
    def test_login_grava_o_cookie_e_nao_loga_a_senha(self, caplog):
        visto = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "auth.orulo.com.br":
                corpo = json.loads(request.content)
                visto["corpo"] = corpo
                return httpx.Response(200, json={"access_token": "tok-1"})
            if request.url.path == "/api/elastic":
                return httpx.Response(200, json={"buildings": [], "quantity_found": 0})
            return httpx.Response(404)

        with caplog.at_level("INFO"):
            resultado = _cliente(handler).coletar(_ctx())
        assert visto["corpo"]["grant_type"] == "password"
        assert visto["corpo"]["email"] == "pessoa@bkm.test"
        assert visto["corpo"]["password"] == "segredo"
        assert visto["corpo"]["clientId"] == CLIENT_ID
        assert "segredo" not in caplog.text
        assert resultado.esperados == 0

    def test_relogin_em_401(self):
        logins = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "auth.orulo.com.br":
                logins.append(1)
                return httpx.Response(200, json={"access_token": f"tok-{len(logins)}"})
            if request.url.path == "/api/elastic":
                params = request.url.params
                assert "used" not in params.get_list("status[]")
                assert params.get_list("status[]") == ["ready", "under_construction"]
                assert params.get_list("finality[]") == ["residential"]
                return httpx.Response(200, json={
                    "buildings": [_predio(1, "Lançamento", "Em construção")],
                    "quantity_found": 1,
                })
            if request.url.path == "/api/portal/buildings/1":
                return httpx.Response(200, json=_predio(1, "Lançamento", "Em construção"))
            if request.url.path == "/api/portal/buildings/1/units":
                cookie = request.headers.get("cookie")
                if cookie == "user_access_token=tok-1":
                    return httpx.Response(401, json={"message": "unauthorized"})
                assert cookie == "user_access_token=tok-2"
                assert request.url.params.get_list("status[]") == ["available", "reserved", "sold"]
                assert request.url.params.get_list("include[]") == ["not_available"]
                return httpx.Response(200, json={"units": [{
                    "reference": "101", "section": "A", "status": "Disponível", "type": "Apartamento",
                    "price": 200000, "private_area": 40, "floor": 1,
                }]})
            return httpx.Response(404)

        resultado = _cliente(handler).coletar(_ctx())
        assert len(logins) == 2
        assert resultado.falhou is False
        assert resultado.empreendimentos[0]["unidades"][0]["situacao"] == "disponivel"
        assert resultado.empreendimentos[0]["situacao"] == "lancamento"

    def test_para_em_429(self):
        urls = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(request.url.path)
            if request.url.host == "auth.orulo.com.br":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/api/elastic":
                return httpx.Response(429, json={"message": "slow down"})
            return httpx.Response(500)

        resultado = _cliente(handler).coletar(_ctx())
        assert resultado.forcar_incompleta is True
        assert resultado.falhas[0]["motivo"] == "bloqueado"
        assert all("/buildings/" not in u for u in urls)

    def test_http_500_tenta_de_novo(self):
        elastic = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "auth.orulo.com.br":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/api/elastic":
                elastic["n"] += 1
                if elastic["n"] < 3:
                    return httpx.Response(500, json={"message": "erro"})
                return httpx.Response(200, json={"buildings": [], "quantity_found": 0})
            return httpx.Response(404)

        resultado = _cliente(handler).coletar(_ctx())
        assert elastic["n"] == 3
        assert resultado.forcar_incompleta is False
        assert resultado.esperados == 0

    def test_segundo_401_falha_a_coleta(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "auth.orulo.com.br":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/api/elastic":
                return httpx.Response(200, json={
                    "buildings": [_predio(1, "Lançamento", "Em construção")], "quantity_found": 1,
                })
            if request.url.path == "/api/portal/buildings/1":
                return httpx.Response(200, json=_predio(1, "Lançamento", "Em construção"))
            if request.url.path.endswith("/units"):
                return httpx.Response(401)
            return httpx.Response(404)

        resultado = _cliente(handler).coletar(_ctx())
        assert resultado.falhou is True
        assert "401" in (resultado.motivo_falha or "")

    def test_lancamentos_so_busca_unidade_de_novo_e_de_quem_saiu_de_breve(self):
        pedidos = []

        def handler(request: httpx.Request) -> httpx.Response:
            pedidos.append(request.url.path)
            if request.url.host == "auth.orulo.com.br":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/api/elastic":
                return httpx.Response(200, json={"buildings": [
                    _predio(1, "Pronto novo", "Pronto novo"),
                    _predio(2, "Em construção", "Em construção"),
                    _predio(3, "Lançamento", "Em construção"),
                ], "quantity_found": 3})
            if request.url.path.endswith("/units"):
                return httpx.Response(200, json={"units": [{
                    "reference": "1", "section": "A", "status": "Disponível", "type": "Apartamento",
                    "price": 10, "private_area": 40,
                }]})
            return httpx.Response(404)

        resultado = _cliente(handler).coletar(_ctx("lancamentos", {"1": "pronto", "3": "breve_lancamento"}))
        assert "/api/portal/buildings/1/units" not in pedidos
        assert "/api/portal/buildings/2/units" in pedidos
        assert "/api/portal/buildings/3/units" in pedidos
        assert "/api/portal/buildings/2" not in pedidos
        por_id = {e["id_externo"]: e for e in resultado.empreendimentos}
        assert "unidades" not in por_id["1"]
        assert "unidades" in por_id["2"] and "unidades" in por_id["3"]
        assert por_id["3"]["situacao"] == "lancamento"


class TestDescoberta:
    def test_fatia_que_volta_500_e_dividida(self):
        chamadas = []

        def buscar(params):
            p = dict(params)
            sw, ne = float(p["map_sw_lat"]), float(p["map_ne_lat"])
            chamadas.append(ne - sw)
            if ne - sw > 0.4:
                return {
                    "buildings": [{"id": i, "address": {"city": "São Paulo"}} for i in range(500)],
                    "quantity_found": 500,
                }
            ident = int(abs(sw) * 1_000_000) + int(abs(float(p["map_sw_lng"])) * 1000)
            return {"buildings": [{"id": ident, "address": {"city": "São Paulo"}}], "quantity_found": 1}

        out, stats = descobrir(
            buscar, params_base=[("state", "SP"), ("city", "São Paulo")],
            bbox=SP_BBOX, cidade="São Paulo",
        )
        assert stats["chamadas"] == 5
        assert len(out) == 4
        assert 0 not in out

    def test_quantity_found_maior_que_o_retorno_tambem_divide(self):
        def buscar(params):
            p = dict(params)
            sw, ne = float(p["map_sw_lat"]), float(p["map_ne_lat"])
            if ne - sw > 0.4:
                return {"buildings": [{"id": 1, "address": {"city": "São Paulo"}}], "quantity_found": 800}
            ident = int(abs(sw) * 1_000_000) + int(abs(float(p["map_sw_lng"])) * 1000)
            return {"buildings": [{"id": ident, "address": {"city": "São Paulo"}}], "quantity_found": 1}

        out, stats = descobrir(
            buscar, params_base=[("state", "SP"), ("city", "São Paulo")],
            bbox=SP_BBOX, cidade="São Paulo",
        )
        assert stats["chamadas"] == 5
        assert len(out) == 4


class TestPlanilha:
    def test_csv_mapeia_sentinela_e_avisa(self, tmp_path):
        arq = tmp_path / "mercado.csv"
        arq.write_text(
            "id;nome;cidade;uf;latitude;longitude;situacao;tipo;finalidade;torre;numero;area;preco;tipo_unidade;status_unidade\n"
            "83029;Ares;São Paulo;SP;-23.64;-46.76;Lançamento;Vertical;Residencial;A;101;32,14;0,1;Apartamento;Disponível\n"
            "83029;Ares;São Paulo;SP;-23.64;-46.76;Lançamento;Vertical;Residencial;A;102;40;250000;Studio - NR;Vendida\n"
            "9;Fora;Rio de Janeiro;RJ;-22.9;-43.2;Lançamento;Vertical;Residencial;A;1;40;10;Apartamento;Disponível\n",
            encoding="utf-8",
        )
        resultado = OruloPlanilha(str(arq)).coletar(_ctx())
        assert resultado.avisos == [AVISO_PLANILHA]
        assert len(resultado.empreendimentos) == 1
        emp = resultado.empreendimentos[0]
        assert emp["id_externo"] == "83029"
        assert emp["situacao"] == "lancamento"
        assert emp["municipio"] == "São Paulo"
        assert [u["numero"] for u in emp["unidades"]] == ["101", "102"]
        assert emp["unidades"][0]["preco"] is None
        assert emp["unidades"][0]["area_privativa_m2"] == 32.14
        assert emp["unidades"][1]["tipo_unidade"] == "Studio - NR"
        assert emp["unidades"][1]["situacao"] == "vendida"

    def test_mapa_de_colunas_e_xlsx(self, tmp_path):
        import openpyxl
        arq = tmp_path / "mercado.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Empreendimento", "Cidade", "UF", "Lat", "Lng", "Status", "Tipo imovel", "Finalidade"])
        ws.append(["Vista", "São Paulo", "SP", -23.5, -46.6, "Em construção", "Vertical", "Residencial"])
        wb.save(arq)
        mapa = tmp_path / "mapa.json"
        mapa.write_text(json.dumps({
            "nome": "Empreendimento", "municipio": "Cidade", "uf": "UF",
            "latitude": "Lat", "longitude": "Lng", "situacao": "Status", "tipo": "Tipo imovel",
        }), encoding="utf-8")
        resultado = OruloPlanilha(str(arq), json.loads(mapa.read_text())).coletar(_ctx())
        emp = resultado.empreendimentos[0]
        assert emp["nome"] == "Vista"
        assert emp["situacao"] == "em_obras"
        assert emp["tipo"] == "vertical"
        assert "unidades" not in emp
        assert AVISO_PLANILHA in resultado.avisos


class TestApi:
    def test_sem_credencial_nao_chama_a_rede(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"não deveria chamar a rede: {request.url}")

        api = OruloApi(client_id="", client_secret="", atraso=ATRASO, sleep=_noop,
                       transporte=httpx.MockTransport(handler))
        with pytest.raises(OruloAuthError):
            api.coletar(_ctx())

    def test_esqueleto_com_token_mapeia_um_empreendimento(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                corpo = request.content.decode()
                assert "grant_type=client_credentials" in corpo
                assert "client_id=cid" in corpo
                assert "client_secret=segredo" in corpo
                return httpx.Response(200, json={"access_token": "api-tok"})
            assert request.headers["authorization"] == "Bearer api-tok"
            if request.url.path == "/api/v2/buildings":
                assert request.url.params.get_list("finality[]") == ["residential"]
                return httpx.Response(200, json={
                    "buildings": [_predio(7, "Lançamento", "Em construção")],
                    "page": 1, "total_pages": 1,
                })
            if request.url.path == "/api/v2/buildings/7":
                return httpx.Response(200, json=_predio(7, "Lançamento", "Em construção"))
            if request.url.path == "/api/v2/buildings/7/units":
                assert "not_available" in request.url.params.get_list("include[]")
                return httpx.Response(200, json={"units": [{
                    "reference": "1", "section": "A", "status": "available", "type": "Apartamento",
                    "price": 10, "private_area": 40,
                }]})
            return httpx.Response(404)

        api = OruloApi(client_id="cid", client_secret="segredo", atraso=ATRASO, sleep=_noop,
                       transporte=httpx.MockTransport(handler))
        resultado = api.coletar(_ctx())
        assert resultado.empreendimentos[0]["id_externo"] == "7"
        assert resultado.empreendimentos[0]["unidades"][0]["situacao"] == "disponivel"
        assert any("OpenAPI" in a for a in resultado.avisos)
