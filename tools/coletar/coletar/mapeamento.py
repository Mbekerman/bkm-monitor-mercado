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


# Andar acima disto, sem o número de pavimentos do prédio, é tratado como implausível.
ANDAR_MAX_SEM_PAVIMENTOS = 45

# Número de unidade só com dígitos, 3 ou 4, sem zero à esquerda: 1708 é andar 17.
_NUMERO_COM_ANDAR = re.compile(r"^[1-9]\d{2,3}$")
# Referência que já nasce negativa: "-1" é o nível, "-101" é a unidade 01 do -1.
_REF_SUBSOLO = re.compile(r"^-(\d+)$")
_TOKEN_SS_NUM = re.compile(r"^ss(\d+)$")
# S colado no número da unidade marca subsolo: "S1", "S08", "S204", "101S".
_TOKEN_S_NUM = re.compile(r"^s(\d+)$|^(\d+)s$")
_TOKENS_SUBSOLO = frozenset({"ss", "sub", "subsolo"})
# Subsolo mais fundo aceito. Abaixo disto o nível é tratado como implausível.
ANDAR_MIN = -5

# Texto do empreendimento que diz, com todas as letras, que não há vaga.
_SEM_VAGA = re.compile(
    r"\bsem\s+(?:vagas?|garage(?:m|ns))\b"
    r"|\bnao\s+(?:possui|possuem|tem|temos|ha|oferece|dispoe)\s+(?:de\s+)?(?:vagas?|garage(?:m|ns))\b"
    r"|\bnenhuma\s+vaga\b"
)


def andar_maximo(bruto: dict | None) -> int | None:
    """Pavimentos do prédio (number_of_floors), ou None se o Órulo não informa."""
    n = inteiro((bruto or {}).get("number_of_floors"))
    return n if n is not None and n > 0 else None


def _andar_plausivel(andar: int | None, maximo: int | None) -> bool:
    if andar is None or andar < 0:
        return False
    # +1 deixa passar cobertura/rooftop acima do último pavimento informado
    limite = maximo + 1 if maximo else ANDAR_MAX_SEM_PAVIMENTOS
    return andar <= limite


def _nivel_referencia_negativa(digitos: str) -> int | None:
    """'-1' é o andar -1. '-101' segue a regra do número: unidade 01 do andar -1."""
    n = int(digitos)
    if n <= 0:
        return None
    nivel = n // 100 if len(digitos) >= 3 else n
    return -nivel if 0 < nivel <= -ANDAR_MIN else None


def _nivel_s(digitos: str, andar_orulo: int | None) -> int | None:
    """Nível de uma unidade com S colado no número: "S204" e "204S" são o -2.

    Com 3 ou 4 dígitos o andar sai do número como no positivo (101 -> -1,
    1104 -> -11). Com 1 dígito ele é o próprio nível (S2 -> -2). Com 2 dígitos
    ("S08") o número não traz o andar, e vale o floor negativo do Órulo.
    Nível abaixo de ANDAR_MIN é implausível e fica vazio.
    """
    if len(digitos) >= 3:
        nivel = -(int(digitos) // 100)
    elif len(digitos) == 1:
        nivel = -int(digitos)
    else:
        nivel = andar_orulo if andar_orulo is not None and andar_orulo < 0 else None
    if nivel is None or not ANDAR_MIN <= nivel < 0:
        return None
    return nivel


def _marca_subsolo(bruto: str | None, *, aceita_menos: bool, andar_orulo: int | None = None) -> int | None:
    """Nível abaixo do térreo escrito na referência ou na torre, ou None.

    Vale referência que começa com '-', o S colado no número (S1, S08, S204,
    101S; ver _nivel_s), o token inteiro SS/SS1, subsolo, sub-solo ou SUB, sem
    acento e sem distinção de maiúsculas. SS, SUB e subsolo, sem número, são o
    andar -1. S no meio do número ("11S04", "U1S05") não é lido: fica vazio.
    """
    if not bruto:
        return None
    if aceita_menos:
        m = _REF_SUBSOLO.fullmatch(bruto.replace(" ", ""))
        if m:
            return _nivel_referencia_negativa(m.group(1))
    normalizado = norm(bruto)
    tokens = re.findall(r"[a-z0-9]+", normalizado)
    for tok in tokens:
        m = _TOKEN_SS_NUM.fullmatch(tok)
        if m and int(m.group(1)) > 0:
            nivel = -int(m.group(1))
            return nivel if nivel >= ANDAR_MIN else None
    for tok in tokens:
        m = _TOKEN_S_NUM.fullmatch(tok)
        if m and int(m.group(1) or m.group(2)) > 0:
            return _nivel_s(m.group(1) or m.group(2), andar_orulo)
    if any(tok in _TOKENS_SUBSOLO for tok in tokens) or re.search(r"\bsub[\s-]+solo\b", normalizado):
        return -1
    return None


def andar_unidade(unidade: dict, maximo: int | None) -> int | None:
    """Andar da unidade.

    O `floor` do Órulo é derivado do número da unidade e erra quando o número
    traz a torre ou zero à esquerda ("0907" vira 90, "10001TORREB" vira 100).
    Andar positivo ou térreo vale quando cabe no prédio (pavimentos + 1, ou 45
    se o Órulo não informa). Senão, só se tira o andar do número quando ele é
    inequívoco (3 ou 4 dígitos, sem zero à esquerda) e cabe no prédio.

    Andar negativo só entra com marca de subsolo na referência ou na torre,
    e só quando o `floor` do Órulo é negativo (S com floor positivo é studio,
    "S703" no 7º andar). O nível sai da marca: S colado no número segue o
    número ("101S" e "S101" são -1, "S204" é -2), SS e subsolo sem número viram
    -1. Sem marca, o negativo fica vazio: 101 com floor -1 é sinal invertido.
    """
    andar = inteiro(unidade.get("floor"))
    if andar is not None and andar < 0:
        nivel = _marca_subsolo(texto(unidade.get("reference")), aceita_menos=True, andar_orulo=andar)
        if nivel is None:
            nivel = _marca_subsolo(texto(unidade.get("section")), aceita_menos=False, andar_orulo=andar)
        if nivel is None:
            return None
        if andar == nivel:
            return andar
        return nivel
    if andar is None:
        return None
    if _andar_plausivel(andar, maximo):
        return andar
    ref = (texto(unidade.get("reference")) or "").replace(" ", "")
    if _NUMERO_COM_ANDAR.match(ref):
        derivado = int(ref) // 100
        if _andar_plausivel(derivado, maximo):
            return derivado
    return None


def _textos(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _textos(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _textos(x)


def declara_sem_vaga(bruto: dict | None) -> bool:
    """True quando a descrição ou as características dizem que não há vaga."""
    b = bruto or {}
    for campo in ("description", "features", "building_features", "unit_features"):
        for t in _textos(b.get(campo)):
            if _SEM_VAGA.search(norm(t)):
                return True
    return False


def vagas_informadas(bruto: dict | None, unidades: list | None) -> bool:
    """Diz se o Órulo tem o cadastro de vagas deste empreendimento.

    O Órulo manda `parking: 0` tanto para "sem vaga" quanto para "não
    informado". O zero só vale como zero quando há sinal de que as vagas foram
    cadastradas: outra unidade ou tipologia com vaga, max_parking do prédio
    acima de zero, ou o texto dizendo que não há vaga.
    """
    b = bruto or {}
    if any((inteiro(u.get("parking")) or 0) > 0 for u in (unidades or []) if isinstance(u, dict)):
        return True
    if any((inteiro(t.get("parking")) or 0) > 0 for t in (b.get("typologies") or []) if isinstance(t, dict)):
        return True
    if (inteiro(b.get("max_parking")) or 0) > 0:
        return True
    return declara_sem_vaga(b)


def vagas_unidade(unidade: dict, informadas: bool) -> int | None:
    vagas = inteiro(unidade.get("parking"))
    if vagas is None or vagas < 0:
        return None
    if vagas == 0 and not informadas:
        return None
    return vagas


def mapear_unidade(
    unidade: dict,
    tipologias: dict[str, dict] | None = None,
    *,
    andar_max: int | None = None,
    vagas_cadastradas: bool | None = None,
) -> dict:
    """Uma unidade do Órulo.

    `andar_max` e `vagas_cadastradas` vêm do empreendimento (ver
    mapear_empreendimento). Sem esse contexto, andar usa o limite genérico e
    zero vaga sai vazio, porque não dá para separar "sem vaga" de "não
    informado".
    """
    tips = tipologias or {}
    if vagas_cadastradas is None:
        vagas_cadastradas = (inteiro(unidade.get("parking")) or 0) > 0
    return {
        "id_externo": None,
        "torre": texto(unidade.get("section")),
        "numero": texto(unidade.get("reference")),
        "andar": andar_unidade(unidade, andar_max),
        "area_privativa_m2": area(unidade.get("private_area")),
        "dormitorios": inteiro(unidade.get("bedrooms")),
        "suites": inteiro(unidade.get("suites")),
        "vagas": vagas_unidade(unidade, vagas_cadastradas),
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
        uns = [u for u in (unidades or []) if isinstance(u, dict)]
        maximo = andar_maximo(bruto)
        informadas = vagas_informadas(bruto, uns)
        emp["unidades"] = juntar_duplicadas([
            mapear_unidade(u, tips, andar_max=maximo, vagas_cadastradas=informadas) for u in uns
        ])
    return emp


_A_VENDA = ("disponivel", "reservada")


def _rotulo_cru(v) -> str:
    """Torre ou número em maiúsculas, sem acento, espaço nem pontuação."""
    s = unicodedata.normalize("NFKD", str(v or ""))
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).upper())


def juntar_duplicadas(unidades: list[dict]) -> list[dict]:
    """Junta a unidade que o Órulo manda duas vezes.

    Em parte dos empreendimentos, a lista traz cada unidade com a torre
    (torre "Bloco 01", número "101") e de novo sem torre, com a torre colada
    no número ("101Bloco01", "101TA", "101-A"). As duas são a mesma unidade, e
    no máximo uma delas está à venda. Fica a da torre, com a situação e o preço
    da que está à venda; os campos vazios dela vêm da outra. Sem par, ou com
    mais de um par possível, a unidade sem torre fica como veio.
    """
    com_torre: dict[str, list[int]] = {}
    for i, u in enumerate(unidades):
        t, n = _rotulo_cru(u.get("torre")), _rotulo_cru(u.get("numero"))
        if t and n:
            for k in {n + t, t + n}:
                com_torre.setdefault(k, []).append(i)
    if not com_torre:
        return unidades
    tirar: set[int] = set()
    usados: set[int] = set()
    saida = [dict(u) for u in unidades]
    for i, u in enumerate(unidades):
        if _rotulo_cru(u.get("torre")):
            continue
        pares = com_torre.get(_rotulo_cru(u.get("numero")), [])
        if len(set(pares)) != 1 or pares[0] in usados:
            continue
        j = pares[0]
        usados.add(j)
        tirar.add(i)
        alvo = saida[j]
        if u.get("situacao") in _A_VENDA and alvo.get("situacao") not in _A_VENDA:
            for c in ("situacao", "preco", "preco_original"):
                alvo[c] = u.get(c)
        for c, v in u.items():
            if alvo.get(c) is None and v is not None:
                alvo[c] = v
    return [u for i, u in enumerate(saida) if i not in tirar]
