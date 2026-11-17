"""Etapa dos alvarás (processos de licenciamento de SP) com prefeitura, GeoSampa e banco falsos."""
import io
import json

import httpx
import pytest

import coletar.licenciamento as lic

CSV = (
    "processo;protocolo;data_autuacao;sql;assunto;subcategoria_concatenado;unidades_residenciais;area_terreno;blocos;pavimentos\n"
    "P1;A1;10/01/2021;008.020.0127-4;Alvará de Aprovação de Edificação Nova;R2h | HIS 1;100;1.234,5;2;12 pav\n"
    "P1;A1;10/01/2021;008.020.0128-2;Alvará de Aprovação de Edificação Nova;R2h | HIS 1;100;1.234,5;2;12\n"
    "P2;A2;2022-03-10;08602000073;Alvará de Execução de Edificação Nova;HMP;não informado;;;\n"
    "P3;A3;2022-03-10;0010010001;Alvará de Reforma;HIS;10;;;\n"
).encode("latin1")


def _xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Processo", "Data_Autuação", "SQL", "Assunto", "Subcategoria", "Unidades_Residenciais"])
    ws.append(["P9", "2026-05-02", "007.007.0001-1", "Edificação Nova (HIS)", "R2v", 80])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_normaliza_csv_latin1_e_junta_lotes():
    procs = {p["processo"]: p for p in lic.normalizar(lic.ler("x.csv", CSV), "Extrato X")}
    assert set(procs) == {"P1", "P2"}, "só edificação nova"
    p1 = procs["P1"]
    assert p1["lotes"] == ["0080200127", "0080200128"] and p1["his"] and not p1["hmp"]
    assert p1["data_autuacao"] == "2021-01-10" and p1["unidades"] == 100 and p1["area_terreno"] == 1234.5
    assert p1["pavimentos"] == 12 and p1["origem"] == "Extrato X"
    p2 = procs["P2"]
    assert p2["hmp"] and p2["unidades"] is None and p2["lotes"] == ["0860200007"] and p2["data_autuacao"] == "2022-03-10"


def test_normaliza_xlsx():
    (p,) = lic.normalizar(lic.ler("x.xlsx", _xlsx()), "Extrato Y")
    assert p["processo"] == "P9" and p["unidades"] == 80 and p["r2v"] and p["social_no_assunto"]
    assert p["data_autuacao"] == "2026-05-02" and p["lotes"] == ["0070070001"]


class Banco:
    def __init__(self, carregados=(), pendentes=()):
        self.carregados = list(carregados)
        self.pendentes = list(pendentes)
        self.processos, self.extratos, self.lotes, self.chamadas = [], [], [], []

    def extratos_carregados(self):
        return self.carregados

    def registrar_processos(self, procs):
        self.processos += procs
        return len(procs)

    def registrar_extrato(self, d):
        self.extratos.append(d)

    def lotes_pendentes(self):
        return self.pendentes

    def registrar_lotes(self, lotes):
        self.lotes += lotes
        return len(lotes)

    def conferir_unidades(self):
        self.chamadas.append("conferir")
        return {"conferido": 1}

    def marcar_alvara(self):
        self.chamadas.append("marcar")
        return {"marcados": 1}


CKAN = {"result": {"resources": [
    {"id": "velho", "name": "Extrato - Aprova Digital - 1º sem./2026", "url": "https://p/velho.csv",
     "last_modified": "2026-08-20T16:41:22.254071"},
    {"id": "novo", "name": "Extrato - Aprova Digital - 2º sem./2026", "url": "https://p/novo.csv",
     "last_modified": "2027-02-20T10:00:00"},
    {"id": "dic", "name": "Dicionario_AD_v1.2.xlsx", "url": "https://p/dic.xlsx"},
]}}

LOTE = {"type": "Feature", "properties": {"cd_setor_fiscal": "007", "cd_quadra_fiscal": "007", "cd_lote": "0001"},
        "geometry": {"type": "Polygon", "coordinates": [[[-46.6, -23.5], [-46.6, -23.51], [-46.61, -23.51], [-46.6, -23.5]]]}}


def _http(pedidos, geosampa_status=200):
    def h(req: httpx.Request):
        pedidos.append(str(req.url))
        if "package_show" in str(req.url):
            return httpx.Response(200, json=CKAN)
        if req.url.path.endswith("novo.csv"):
            return httpx.Response(200, content=CSV)
        if "geosampa" in str(req.url):
            if geosampa_status != 200:
                return httpx.Response(geosampa_status)
            return httpx.Response(200, json={"features": [LOTE]})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(h))


OWS = "https://terrenos.example/geosampa/geoserver/geoportal/ows"


def test_so_baixa_extrato_novo_e_marca():
    pedidos = []
    banco = Banco(carregados=[{"recurso_id": "velho", "modificado_em": "2026-08-20 16:41:22.254071+00"}],
                  pendentes=[{"orulo_id": 7, "lat": -23.505, "lon": -46.605}])
    r = lic.rodar_alvaras(banco, http=_http(pedidos), ows=OWS)
    assert not any("velho.csv" in p or "dic.xlsx" in p for p in pedidos)
    assert r.extratos_novos == ["Extrato - Aprova Digital - 2º sem./2026"] and r.processos == 2
    assert banco.extratos[0]["recurso_id"] == "novo" and banco.extratos[0]["processos"] == 2
    assert banco.lotes == [{"orulo_id": 7, "lote": "0070070001", "distancia_m": 0}]
    assert banco.chamadas == ["conferir", "marcar"] and not r.avisos


def test_sem_extrato_novo_ainda_reconfere():
    pedidos = []
    banco = Banco(carregados=[{"recurso_id": "velho", "modificado_em": "2026-08-20T16:41:22"},
                              {"recurso_id": "novo", "modificado_em": "2027-02-20T10:00:00+00:00"}])
    r = lic.rodar_alvaras(banco, http=_http(pedidos), ows=OWS)
    assert r.extratos_novos == [] and banco.processos == []
    assert banco.chamadas == ["conferir", "marcar"]


@pytest.mark.parametrize("status", [403, 429])
def test_bloqueio_do_geosampa_para_os_lotes(status):
    pedidos = []
    banco = Banco(carregados=[{"recurso_id": "velho", "modificado_em": "2026-08-20T16:41:22"},
                              {"recurso_id": "novo", "modificado_em": "2027-02-20T10:00:00"}],
                  pendentes=[{"orulo_id": i, "lat": -23.5, "lon": -46.6} for i in range(20)])
    r = lic.rodar_alvaras(banco, http=_http(pedidos, status), ows=OWS)
    assert any(str(status) in a for a in r.avisos)
    assert banco.lotes == []
    assert sum("geosampa" in p for p in pedidos) < 20, "para logo, sem repetir em todos"
    assert banco.chamadas == ["conferir", "marcar"], "a conferência segue com o que já tem"


def test_lotes_parse():
    assert lic.lotes("008.020.0127-4 | 08602000073") == ["0080200127", "0860200007"]


def test_etapa_alvaras_desligada_e_so_sp(monkeypatch):
    import coletar.cli as cli
    vistos = []
    monkeypatch.setattr(lic, "rodar_alvaras", lambda banco, **k: vistos.append(banco))
    monkeypatch.setenv("COLETAR_ALVARAS", "0")
    cli.etapa_alvaras("b0", ["São Paulo"])
    monkeypatch.setenv("COLETAR_ALVARAS", "1")
    cli.etapa_alvaras("b1", ["Guarulhos", "Poá"])
    cli.etapa_alvaras("b2", ["São José dos Campos", "São Paulo"])
    assert vistos == ["b2"]


def test_falha_da_etapa_nao_muda_a_coleta(monkeypatch):
    import coletar.cli as cli
    def explode(banco, **k):
        raise RuntimeError("caiu")
    monkeypatch.setattr(lic, "rodar_alvaras", explode)
    monkeypatch.setenv("COLETAR_ALVARAS", "1")
    cli.etapa_alvaras(object(), ["São Paulo"])  # não levanta


def test_mensal_chama_alvaras_depois_das_tabelas(monkeypatch):
    import coletar.cli as cli
    from coletar.fonte import ResultadoFonte
    ordem = []
    monkeypatch.setattr(cli, "etapa_tabelas", lambda banco, m: ordem.append(("tabelas", tuple(m))))
    monkeypatch.setattr(cli, "etapa_alvaras", lambda banco, m: ordem.append(("alvaras", tuple(m))))

    class Fonte:
        nome = "orulo_login"

        def coletar(self, ctx):
            return ResultadoFonte(empreendimentos=[], bruto={"itens": []}, esperados=0, falhas=[],
                                  forcar_incompleta=False, falhou=False, motivo_falha=None)

    class BancoMin:
        coleta_aberta = None
        def abrir(self, *a): return "c1"
        def gravar_bruto(self, *a): pass
        def fechar_coleta(self, *a, **k): return {"situacao": "completa"}
        def agrupar(self): return {}
        def anotar_falha(self, *a): pass
        def conhecidos(self, fonte): return {}
        def fechar(self): pass

    monkeypatch.setattr(cli, "criar_fonte", lambda *a, **k: Fonte())
    assert cli.main(["--tipo", "mensal", "--data", "2026-12-01"], banco=BancoMin()) == 0
    assert [o[0] for o in ordem] == ["tabelas", "alvaras"]
    ordem.clear()
    assert cli.main(["--tipo", "lancamentos", "--data", "2026-12-07"], banco=BancoMin()) == 0
    assert ordem == [], "semanal não roda tabelas nem alvarás"
