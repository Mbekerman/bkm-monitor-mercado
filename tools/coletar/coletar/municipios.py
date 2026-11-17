"""Municípios aceitos em --municipio.

O nome é o oficial do IBGE (API de localidades, estado 35, lida em 17/nov/2026)
e o mesmo texto de address.city no Órulo. Conferido no /api/elastic público,
sem login: o address.city de cada um dos 19 nomes é o próprio nome. Com o
filtro da coleta (pronto ou em obra, residencial), Mauá, Itapecerica da Serra
e Embu das Artes voltaram vazios nessa data — o que havia era usado, comercial
ou horizontal. Não há id numérico de cidade no cliente; a descoberta manda
state, city e o retângulo.

O retângulo das 17 cidades vizinhas é a extrema da malha municipal do IBGE
(qualidade intermediária), arredondada para fora em duas casas decimais
(~1,1 km). Vizinho que cair dentro sai no filtro de address.city. São Paulo
e São José dos Campos continuam com os retângulos de sempre (descoberta.py):
a malha de São Paulo desce até cerca de -24,01, e o retângulo em uso para em -23,80.

Sem --municipio, a rodada continua só com São Paulo e São José dos Campos.
O código IBGE não é enviado à fonte. O banco é que o guarda em im_municipios;
enquanto a cidade não estiver lá e em im_config.municipios, a importação marca
fora_do_filtro e não grava o empreendimento.
"""
from __future__ import annotations

from coletar.descoberta import SJC_BBOX, SP_BBOX
from coletar.fonte import Municipio
from coletar.mapeamento import norm

# Categoria social (tabela de preços) só de São Paulo.
MUNICIPIO_TABELAS = "São Paulo"

# (nome oficial, código IBGE, sw_lat, sw_lng, ne_lat, ne_lng)
_VIZINHOS = (
    ("Guarulhos", "3518800", -23.51, -46.58, -23.27, -46.33),
    ("Santo André", "3547809", -23.82, -46.57, -23.60, -46.27),
    ("Osasco", "3534401", -23.61, -46.83, -23.46, -46.74),
    ("São Caetano do Sul", "3548807", -23.66, -46.59, -23.60, -46.54),
    ("São Bernardo do Campo", "3548708", -23.97, -46.66, -23.64, -46.41),
    ("Taboão da Serra", "3552809", -23.65, -46.82, -23.59, -46.75),
    ("Diadema", "3513801", -23.74, -46.66, -23.65, -46.57),
    ("Barueri", "3505708", -23.56, -46.97, -23.47, -46.79),
    ("Poá", "3539806", -23.58, -46.37, -23.50, -46.32),
    ("Santana de Parnaíba", "3547304", -23.51, -47.04, -23.38, -46.80),
    ("Cotia", "3513009", -23.81, -47.09, -23.56, -46.80),
    ("Mauá", "3529401", -23.72, -46.50, -23.61, -46.38),
    ("Ferraz de Vasconcelos", "3515707", -23.62, -46.40, -23.51, -46.35),
    ("Itaquaquecetuba", "3523107", -23.52, -46.40, -23.42, -46.27),
    ("Itapecerica da Serra", "3522208", -23.81, -46.94, -23.66, -46.76),
    ("Cajamar", "3509205", -23.43, -46.95, -23.27, -46.81),
    ("Embu das Artes", "3515004", -23.69, -46.92, -23.61, -46.79),
)

MUNICIPIOS_PADRAO = (
    Municipio("São Paulo", "SP", SP_BBOX, "3550308"),
    Municipio("São José dos Campos", "SP", SJC_BBOX, "3549904"),
)

MUNICIPIOS_MONITORADOS = MUNICIPIOS_PADRAO + tuple(
    Municipio(nome, "SP", (sw_lat, sw_lng, ne_lat, ne_lng), codigo)
    for nome, codigo, sw_lat, sw_lng, ne_lat, ne_lng in _VIZINHOS
)


def municipios_para_tabelas(nomes: list[str] | None) -> list[str]:
    """Categoria social só de São Paulo.

    None (flag ausente) vira São Paulo. Outra cidade sai da lista. Se só
    houver outras cidades, a lista volta vazia e a etapa não roda.
    """
    if nomes is None:
        return [MUNICIPIO_TABELAS]
    if any(norm(nome) == norm(MUNICIPIO_TABELAS) for nome in nomes):
        return [MUNICIPIO_TABELAS]
    return []
