"""Resolução de --municipio: as duas cidades de sempre e as 17 vizinhas."""
from __future__ import annotations

from pathlib import Path

import pytest

from coletar.cli import main, municipios_pedidos
from coletar.descoberta import SJC_BBOX, SP_BBOX, params_cidade
from coletar.erros import ColetaErro
from coletar.fonte import ResultadoFonte
from coletar.municipios import (
    MUNICIPIOS_MONITORADOS,
    MUNICIPIOS_PADRAO,
    MUNICIPIO_TABELAS,
    municipios_para_tabelas,
)

RAIZ = Path(__file__).resolve().parents[3]

ESPERADOS = (
    ("São Paulo", "3550308", SP_BBOX),
    ("São José dos Campos", "3549904", SJC_BBOX),
    ("Guarulhos", "3518800", (-23.51, -46.58, -23.27, -46.33)),
    ("Santo André", "3547809", (-23.82, -46.57, -23.60, -46.27)),
    ("Osasco", "3534401", (-23.61, -46.83, -23.46, -46.74)),
    ("São Caetano do Sul", "3548807", (-23.66, -46.59, -23.60, -46.54)),
    ("São Bernardo do Campo", "3548708", (-23.97, -46.66, -23.64, -46.41)),
    ("Taboão da Serra", "3552809", (-23.65, -46.82, -23.59, -46.75)),
    ("Diadema", "3513801", (-23.74, -46.66, -23.65, -46.57)),
    ("Barueri", "3505708", (-23.56, -46.97, -23.47, -46.79)),
    ("Poá", "3539806", (-23.58, -46.37, -23.50, -46.32)),
    ("Santana de Parnaíba", "3547304", (-23.51, -47.04, -23.38, -46.80)),
    ("Cotia", "3513009", (-23.81, -47.09, -23.56, -46.80)),
    ("Mauá", "3529401", (-23.72, -46.50, -23.61, -46.38)),
    ("Ferraz de Vasconcelos", "3515707", (-23.62, -46.40, -23.51, -46.35)),
    ("Itaquaquecetuba", "3523107", (-23.52, -46.40, -23.42, -46.27)),
    ("Itapecerica da Serra", "3522208", (-23.81, -46.94, -23.66, -46.76)),
    ("Cajamar", "3509205", (-23.43, -46.95, -23.27, -46.81)),
    ("Embu das Artes", "3515004", (-23.69, -46.92, -23.61, -46.79)),
)


class _Banco:
    def abrir(self, *a):
        return "c1"

    def gravar_bruto(self, *a):
        pass

    def importar(self, *a):
        return {}

    def fechar_coleta(self, *a, **k):
        return {"situacao": "completa"}

    def agrupar(self):
        return {}

    def anotar_falha(self, *a):
        pass

    def conhecidos(self, fonte):
        return {}

    def fechar(self):
        pass


class _Fonte:
    nome = "orulo_login"

    def coletar(self, ctx):
        self.ctx = ctx
        return ResultadoFonte(empreendimentos=[], bruto={"itens": []}, esperados=0)


def test_catalogo_tem_as_dezenove_cidades_com_ibge_e_retangulo():
    assert [(m.nome, m.codigo_ibge, m.bbox) for m in MUNICIPIOS_MONITORADOS] == list(ESPERADOS)
    assert all(m.uf == "SP" for m in MUNICIPIOS_MONITORADOS)
    codigos = [m.codigo_ibge for m in MUNICIPIOS_MONITORADOS]
    assert len(set(codigos)) == 19
    assert all(c and len(c) == 7 and c.isdigit() for c in codigos)
    for m in MUNICIPIOS_MONITORADOS:
        sw_lat, sw_lng, ne_lat, ne_lng = m.bbox
        assert sw_lat < ne_lat and sw_lng < ne_lng


def test_retangulos_de_sao_paulo_e_sjc_sao_os_de_sempre():
    assert SP_BBOX == (-23.80, -46.83, -23.35, -46.36)
    assert SJC_BBOX == (-23.30, -46.20, -22.85, -45.70)
    assert [m.nome for m in MUNICIPIOS_PADRAO] == ["São Paulo", "São José dos Campos"]
    assert MUNICIPIOS_PADRAO[0].bbox is SP_BBOX
    assert MUNICIPIOS_PADRAO[1].bbox is SJC_BBOX


def test_sem_flag_continua_so_as_duas():
    pedidos = municipios_pedidos(None)
    assert [(m.nome, m.bbox) for m in pedidos] == [
        ("São Paulo", SP_BBOX),
        ("São José dos Campos", SJC_BBOX),
    ]


def test_nome_sem_acento_e_dezenove_na_mesma_rodada():
    assert municipios_pedidos(["sao paulo"])[0].nome == "São Paulo"
    assert municipios_pedidos(["  SAO JOSE DOS CAMPOS "])[0].nome == "São José dos Campos"
    assert municipios_pedidos(["poa"])[0].codigo_ibge == "3539806"
    assert municipios_pedidos(["sao caetano do sul"])[0].nome == "São Caetano do Sul"
    assert municipios_pedidos(["embu das artes"])[0].nome == "Embu das Artes"
    nomes = [m.nome for m in MUNICIPIOS_MONITORADOS]
    assert [m.nome for m in municipios_pedidos(nomes)] == nomes
    with pytest.raises(ColetaErro, match="fora da lista"):
        municipios_pedidos(["Campinas"])


def test_descoberta_usa_o_nome_oficial():
    guarulhos = municipios_pedidos(["Guarulhos"])[0]
    assert params_cidade(guarulhos.uf, guarulhos.nome)[:2] == [("state", "SP"), ("city", "Guarulhos")]


def test_cli_repassa_as_dezenove_e_o_padrao():
    fonte = _Fonte()
    args = ["--tipo", "lancamentos", "--data", "2026-10-06"]
    for m in MUNICIPIOS_MONITORADOS:
        args += ["--municipio", m.nome]
    assert main(args, fonte=fonte, banco=_Banco()) == 0
    assert [m.nome for m in fonte.ctx.municipios] == [m.nome for m in MUNICIPIOS_MONITORADOS]

    fonte_padrao = _Fonte()
    assert main(["--tipo", "lancamentos", "--data", "2026-10-06"], fonte=fonte_padrao, banco=_Banco()) == 0
    assert [m.nome for m in fonte_padrao.ctx.municipios] == ["São Paulo", "São José dos Campos"]
    assert main(["--tipo", "mensal", "--data", "2026-10-24", "--municipio", "Campinas"]) == 1


def test_tabelas_so_sao_paulo():
    assert municipios_para_tabelas(None) == [MUNICIPIO_TABELAS]
    assert municipios_para_tabelas(["São Paulo", "Guarulhos", "São José dos Campos"]) == ["São Paulo"]
    assert municipios_para_tabelas(["sao paulo"]) == ["São Paulo"]
    assert municipios_para_tabelas(["Poá", "Cotia"]) == []


def test_docs_listam_as_dezenove_cidades():
    coletar = (RAIZ / "docs" / "coletar.md").read_text(encoding="utf-8")
    cloud = (RAIZ / "docs" / "cloud-run.md").read_text(encoding="utf-8")
    for nome, codigo, _bbox in ESPERADOS:
        assert nome in coletar
        assert codigo in coletar
        assert nome in cloud
