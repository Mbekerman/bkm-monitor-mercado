"""Descoberta no /api/elastic.

A busca devolve no máximo 500 prédios e ignora paginação. O retângulo é
dividido em 4 quando a fatia volta 500 itens ou `quantity_found` passa do
que veio. Os ids se juntam. Vizinho de outra cidade fica de fora.
"""
from __future__ import annotations

import logging

log = logging.getLogger("coletar")

MAX_POR_CHAMADA = 500
PROFUNDIDADE_MAX = 9

# Os mesmos limites aproximados do município que o código de referência usou.
SP_BBOX = (-23.80, -46.83, -23.35, -46.36)
# Retângulo aproximado de São José dos Campos, no mesmo papel do de São Paulo.
SJC_BBOX = (-23.30, -46.20, -22.85, -45.70)


def params_cidade(uf: str, cidade: str) -> list[tuple[str, str]]:
    # Na fonte, o que der: obra e pronto novo, finalidade residencial. Usado (revenda) fica de fora.
    return [
        ("state", uf),
        ("city", cidade),
        ("status[]", "ready"),
        ("status[]", "under_construction"),
        ("finality[]", "residential"),
    ]


def descobrir(buscar, *, params_base: list[tuple[str, str]], bbox: tuple[float, float, float, float], cidade: str):
    found: dict[int, dict] = {}
    stats = {"chamadas": 0, "folhas_truncadas": 0}

    def fatia(sw_lat, sw_lng, ne_lat, ne_lng, depth):
        params = list(params_base) + [
            ("map_sw_lat", f"{sw_lat:.6f}"),
            ("map_sw_lng", f"{sw_lng:.6f}"),
            ("map_ne_lat", f"{ne_lat:.6f}"),
            ("map_ne_lng", f"{ne_lng:.6f}"),
        ]
        data = buscar(params)
        stats["chamadas"] += 1
        items = data.get("buildings") or []
        qf = int(data.get("quantity_found") or len(items))
        cheia = len(items) >= MAX_POR_CHAMADA or qf > len(items)
        if cheia and depth < PROFUNDIDADE_MAX:
            meio_lat, meio_lng = (sw_lat + ne_lat) / 2, (sw_lng + ne_lng) / 2
            log.info("fatia d=%s %s/%s, dividindo", depth, len(items), qf)
            for q in (
                (sw_lat, sw_lng, meio_lat, meio_lng),
                (sw_lat, meio_lng, meio_lat, ne_lng),
                (meio_lat, sw_lng, ne_lat, meio_lng),
                (meio_lat, meio_lng, ne_lat, ne_lng),
            ):
                fatia(*q, depth + 1)
            return
        if cheia:
            stats["folhas_truncadas"] += 1
            log.warning("fatia no limite de profundidade ainda cheia (%s/%s)", len(items), qf)
        for b in items:
            if not isinstance(b, dict) or b.get("id") is None:
                continue
            found[int(b["id"])] = b

    fatia(*bbox, 0)
    out = {
        i: b for i, b in found.items()
        if ((b.get("address") or {}).get("city", cidade) == cidade)
    }
    log.info("descoberta de %s: %s prédios em %s chamadas", cidade, len(out), stats["chamadas"])
    return out, stats
