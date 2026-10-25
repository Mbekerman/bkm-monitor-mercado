"""Conversão do JSON do Órulo para o formato que `im_importar` lê.

Datas `dd/mm/aaaa` (com ou sem hora) viram ISO. Número em texto vira número.
Preço `0.1` (sentinela de breve lançamento) e preço vazio viram null.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime

_SITUACAO_EMP = {
    "lancamento": "lancamento",
    "breve lancamento": "breve_lancamento",
    "breve_lancamento": "breve_lancamento",
    "em construcao": "em_obras",
    "em_obras": "em_obras",
    "under_construction": "em_obras",
    "pronto novo": "pronto",
    "pronto": "pronto",
    "ready": "pronto",
    "usado": "usado",
    "used": "usado",
    "nao_informado": "nao_informado",
    "nao informado": "nao_informado",
}

_SITUACAO_UNIDADE = {
    "disponivel": "disponivel",
    "available": "disponivel",
    "reservada": "reservada",
    "reserved": "reservada",
    "em negociacao": "reservada",
    "under_negotiation": "reservada",
    "vendida": "vendida",
    "sold": "vendida",
    "venda aprovada": "vendida",
    "approved_sale": "vendida",
    "venda suspensa": "indisponivel",
    "suspended_sale": "indisponivel",
    "fora de venda": "indisponivel",
    "out_of_sale": "indisponivel",
    "reserva tecnica": "indisponivel",
    "technical_reserve": "indisponivel",
    "permutada": "indisponivel",
    "exchanged": "indisponivel",
    "alugada": "indisponivel",
    "rented": "indisponivel",
    "indisponivel": "indisponivel",
    "nao_informado": "nao_informado",
}

_TIPO_EMP = {
    "vertical", "horizontal", "unidade_avulsa", "sobrado", "loteamento", "industrial", "galpao", "nao_informado",
}

_FINALIDADE = {
    "residencial": "residencial",
    "residential": "residencial",
    "comercial": "comercial",
    "commercial": "comercial",
    "misto": "misto",
    "mixed": "misto",
    "nao_informado": "nao_informado",
}

_DATA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def norm(texto) -> str:
    if texto is None:
        return ""
    s = str(texto).strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def texto(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def numero(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    s = str(v).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def inteiro(v):
    n = numero(v)
    if n is None:
        return None
    return int(n)


def preco(v):
    """Preço 0,1 é a sentinela de breve lançamento: sai null, nunca como valor."""
    n = numero(v)
    if n is None:
        return None
    if abs(n - 0.1) < 1e-9 or n <= 0:
        return None
    return n


def area(v):
    n = numero(v)
    if n is None or n <= 0:
        return None
    return n


def data_iso(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    m = _ISO.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATA.match(s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def situacao_empreendimento(status, stage=None) -> str:
    # O estágio de lançamento ganha do status de obra: "Em construção" + "Lançamento" é lançamento.
    estagio = _SITUACAO_EMP.get(norm(stage))
    if estagio in ("lancamento", "breve_lancamento"):
        return estagio
    return _SITUACAO_EMP.get(norm(status), "nao_informado")


def situacao_unidade(status) -> str:
    return _SITUACAO_UNIDADE.get(norm(status), "nao_informado")


def tipo_empreendimento(v) -> str:
    n = norm(v).replace(" ", "_")
    if n in _TIPO_EMP:
        return n
    return "nao_informado"


def finalidade(v) -> str:
    return _FINALIDADE.get(norm(v), "nao_informado")


def programa_social(opportunity) -> str:
    if not isinstance(opportunity, dict) or "social_housing_program" not in opportunity:
        return "nao_informado"
    return "sim" if opportunity.get("social_housing_program") else "nao"


def endereco(address) -> str | None:
    if not isinstance(address, dict):
        return None
    rua = " ".join(p for p in (texto(address.get("street_type")), texto(address.get("street"))) if p)
    num = address.get("number")
    if rua and num not in (None, ""):
        return f"{rua}, {num}"
    return rua or None


def _tipologias(bruto: dict) -> dict[str, dict]:
    out = {}
    for t in bruto.get("typologies") or []:
        if isinstance(t, dict) and t.get("id") is not None:
            out[str(t["id"])] = t
    return out


def tipo_unidade(unidade: dict, tipologias: dict[str, dict]):
    if texto(unidade.get("type")):
        return texto(unidade.get("type"))
    tid = unidade.get("typology_id")
    tip = tipologias.get(str(tid)) if tid is not None else None
    if tip:
        return texto(tip.get("type"))
    return None


def mapear_unidade(unidade: dict, tipologias: dict[str, dict] | None = None) -> dict:
    tips = tipologias or {}
    return {
        "id_externo": None,
        "torre": texto(unidade.get("section")),
        "numero": texto(unidade.get("reference")),
        "andar": inteiro(unidade.get("floor")),
        "area_privativa_m2": area(unidade.get("private_area")),
        "dormitorios": inteiro(unidade.get("bedrooms")),
        "suites": inteiro(unidade.get("suites")),
        "vagas": inteiro(unidade.get("parking")),
        "tipologia_fonte": None if unidade.get("typology_id") is None else str(unidade.get("typology_id")),
        "tipo_unidade": tipo_unidade(unidade, tips),
        "situacao": situacao_unidade(unidade.get("status")),
        "preco": preco(unidade.get("price")),
        "preco_original": preco(unidade.get("original_price")),
    }


def mapear_empreendimento(lista: dict | None, detalhe: dict | None, unidades: list | None, *, incluir_unidades: bool) -> dict:
    bruto = {}
    if lista:
        bruto.update(lista)
    if detalhe:
        bruto.update(detalhe)
    address = bruto.get("address") if isinstance(bruto.get("address"), dict) else {}
    dev = bruto.get("developer") if isinstance(bruto.get("developer"), dict) else {}
    tips = _tipologias(bruto)
    emp = {
        "id_externo": None if bruto.get("id") is None else str(bruto.get("id")),
        "nome": texto(bruto.get("name")),
        "incorporadora": {
            "id_externo": None if dev.get("id") is None else str(dev.get("id")),
            "nome": texto(dev.get("name")),
            "cnpj": None,
        },
        "endereco": endereco(address),
        "bairro": texto(address.get("area")),
        "municipio": texto(address.get("city")),
        "uf": texto(address.get("state")),
        "latitude": numero(address.get("latitude")),
        "longitude": numero(address.get("longitude")),
        "situacao": situacao_empreendimento(bruto.get("status"), bruto.get("stage")),
        "tipo": tipo_empreendimento(bruto.get("type")),
        "finalidade": finalidade(bruto.get("finality")),
        "data_lancamento": data_iso(bruto.get("launch_date")),
        "previsao_entrega": data_iso(bruto.get("opening_date")),
        "torres": inteiro(bruto.get("number_of_towers")),
        "programa_social": programa_social(bruto.get("opportunity")),
        "unidades_informadas": inteiro(bruto.get("total_units")),
        "estoque_informado": inteiro(bruto.get("stock")),
        "tabela_atualizada_em": data_iso(bruto.get("last_updated_pricetable") or bruto.get("last_updated_pricetable_at")),
        "link_fonte": texto(bruto.get("orulo_url")),
    }
    if incluir_unidades:
        emp["unidades"] = [mapear_unidade(u, tips) for u in (unidades or []) if isinstance(u, dict)]
    return emp
