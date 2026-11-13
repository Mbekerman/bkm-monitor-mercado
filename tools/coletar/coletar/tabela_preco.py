"""Lê a tabela de preços em PDF do Órulo e acha a categoria social de cada unidade.

Categorias: his1, his2, hmp, r2v (mercado) e nr (não residencial).

1. Texto: camada de texto do PDF por página (pymupdf); página sem texto útil
   passa por OCR (RapidOCR). As palavras viram linhas pela altura (y).
2. Casamento guiado pelas unidades do banco: numa linha, um número que é de
   uma unidade do empreendimento + uma categoria na mesma linha (ou numa
   linha vizinha só com a categoria, na mesma altura) = tabela_unidade.
   Torre/bloco da linha (ou do último título de torre) desempata.
3. Regra por tipo ("Torre A - Finais 01, 02 e 23 - 1º ao 19º andar - HIS1"):
   expandida para as unidades com esses finais nesses andares = tabela_grupo.
4. Nota geral ("todas as unidades são HIS2", "100% HMP") = nota_geral.

Sem rede e sem banco aqui: entra o PDF e a lista de unidades, sai o resultado.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

CATEGORIAS = ("his1", "his2", "hmp", "r2v", "nr")

_NAO_DORM = r"(?!\s*(?:D\b|DS\b|DORM|DTS|QUARTO|QTO|Q\b|SU[IÍ]TE))"
_CAT_RE = [
    ("his2", re.compile(r"(?<![A-Z0-9])HIS\s*[-–_.]?\s*(?:2|II|Z)(?![A-Z0-9])" + _NAO_DORM)),
    ("his1", re.compile(r"(?<![A-Z0-9])HIS\s*[-–_.]?\s*(?:1|I|L)(?![A-Z0-9])" + _NAO_DORM)),
    ("hmp", re.compile(r"(?<![A-Z0-9])HMP(?![A-Z])")),
    ("r2v", re.compile(r"(?<![A-Z0-9])R\s*-?\s*[2Z]\s*V(?![A-Z])")),
    ("nr", re.compile(r"(?<![A-Z0-9])NR\s*-?\s*[123](?:\s*-\s*\d)?(?![A-Z0-9])")),
]
_TORRE_RE = re.compile(r"(?<![A-Z])(?:TORRE|BLOCO|BL|EDIF[IÍ]CIO|ED)\.?\s*[-:]?\s*([A-Z0-9]{1,6})(?![A-Z0-9])")
_TORRE_CURTA_RE = re.compile(r"(?<![A-Z0-9])T\s?([A-Z]|\d{1,2})(?![A-Z0-9])")
_TOKEN_UNIDADE = re.compile(r"^([A-Z]{0,2})(\d{1,5})([A-Z]?)$")
_NUM_TORRE_RE = re.compile(r"^(.*?\d+[A-Z]?)\s*(?:TORRE|BLOCO|TR|BL|T)\s*([A-Z0-9]+)$")


@dataclass
class Linha:
    pagina: int
    y: float
    h: float
    texto: str


@dataclass
class Resultado:
    status: str  # ok | sem_categoria | sem_unidades
    atribuicoes: dict[str, tuple[str, str]] = field(default_factory=dict)  # unidade_id -> (categoria, fonte)
    categorias_no_texto: Counter = field(default_factory=Counter)
    ambiguas: int = 0
    conflitos: int = 0
    regras_grupo: int = 0
    nota: str | None = None

    @property
    def resto_fonte(self) -> str:
        return "tabela_sem_categoria" if self.status == "sem_categoria" else "fora_da_tabela"


# ---------------------------------------------------------------- texto

def _linhas_de_caixas(pagina: int, caixas: list[tuple[float, float, float, float, str]]) -> list[Linha]:
    """caixas: (x0, y0, x1, y1, texto). Agrupa por centro vertical."""
    if not caixas:
        return []
    alturas = [max(1.0, c[3] - c[1]) for c in caixas]
    h_med = statistics.median(alturas)
    ordem = sorted(caixas, key=lambda c: (c[1] + c[3]) / 2)
    grupos: list[list] = []
    for c in ordem:
        yc = (c[1] + c[3]) / 2
        if grupos and abs(yc - grupos[-1][0]) <= 0.45 * h_med:
            g = grupos[-1]
            g[1].append(c)
            g[0] = sum((k[1] + k[3]) / 2 for k in g[1]) / len(g[1])
        else:
            grupos.append([yc, [c]])
    saida = []
    for yc, cs in grupos:
        cs.sort(key=lambda c: c[0])
        partes = []
        ultimo_x1 = None
        for c in cs:
            if ultimo_x1 is not None and c[0] - ultimo_x1 > 0.8 * h_med:
                partes.append(" | ")
            elif partes:
                partes.append(" ")
            partes.append(c[4])
            ultimo_x1 = c[2]
        saida.append(Linha(pagina, yc, h_med, "".join(partes)))
    return saida


_ocr = None


def _motor_ocr():
    """RapidOCR com OCR_THREADS threads (padrão 2) e sem o classificador de ângulo
    (tabela vem de pé). Import pesado: só quando alguma página precisa."""
    global _ocr
    if _ocr is None:
        import os

        import onnxruntime as ort
        from rapidocr_onnxruntime import RapidOCR
        from rapidocr_onnxruntime import utils as rutils
        n = max(1, int(os.environ.get("OCR_THREADS", "2") or 2))

        class _Opcoes(ort.SessionOptions):
            def __init__(self):
                super().__init__()
                self.intra_op_num_threads = n
                self.inter_op_num_threads = 1

        rutils.SessionOptions = _Opcoes
        _ocr = RapidOCR()
        _ocr.use_angle_cls = False
    return _ocr


def extrair_linhas(pdf: bytes, *, max_paginas: int | None = None, ocr: bool = True) -> tuple[list[Linha], str, int]:
    """Devolve (linhas, metodo, paginas). metodo: pdftotext (camada de texto) ou ocr."""
    import os

    import pymupdf
    if max_paginas is None:
        max_paginas = int(os.environ.get("TABELA_MAX_PAGINAS", "80") or 80)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    n = len(doc)
    linhas: list[Linha] = []
    usou_ocr = False
    for i in range(min(n, max_paginas)):
        pg = doc[i]
        palavras = pg.get_text("words")
        com_digito = sum(1 for w in palavras if any(ch.isdigit() for ch in w[4]))
        if com_digito >= 15 or not ocr:
            linhas += _linhas_de_caixas(i + 1, [(w[0], w[1], w[2], w[3], w[4]) for w in palavras])
            continue
        usou_ocr = True
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        res, _ = _motor_ocr()(pix.tobytes("png"))
        caixas = []
        for box, txt, _conf in res or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            caixas.append((min(xs), min(ys), max(xs), max(ys), txt))
        linhas += _linhas_de_caixas(i + 1, caixas)
    doc.close()
    return linhas, ("ocr" if usou_ocr else "pdftotext"), n


# ---------------------------------------------------------------- unidades

def _norm_num(s: str) -> str:
    m = _TOKEN_UNIDADE.match(s)
    if not m:
        return s
    pre, dig, suf = m.groups()
    return f"{pre}{int(dig)}{suf}"


def _norm_torre(s: str | None) -> str:
    t = re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    t = re.sub(r"^(TORRE|BLOCO|BL|TR|EDIFICIO|ED)", "", t)
    if t.isdigit():
        t = str(int(t))
    return t


@dataclass
class _Un:
    id: str
    torre: str
    num: str
    andar: int | None
    removida: bool


def preparar_unidades(unidades: list[dict]) -> list[_Un]:
    saida = []
    for u in unidades:
        torre = _norm_torre(u.get("torre_norm") or u.get("torre"))
        num = re.sub(r"[^A-Z0-9]", "", (u.get("numero_norm") or u.get("numero") or "").upper())
        if not torre:
            m = _NUM_TORRE_RE.match(num)
            if m:
                num, torre = m.group(1), _norm_torre(m.group(2))
        num = _norm_num(num)
        andar = u.get("andar")
        saida.append(_Un(str(u["id"]), torre, num, int(andar) if andar is not None else None, bool(u.get("removida"))))
    return saida


# ---------------------------------------------------------------- casamento

def categorias_da_linha(texto: str) -> list[tuple[int, str]]:
    t = texto.upper()
    achadas = []
    ocupado = []
    for cat, rx in _CAT_RE:
        for m in rx.finditer(t):
            if any(a <= m.start() < b for a, b in ocupado):
                continue
            ocupado.append((m.start(), m.end()))
            achadas.append((m.start(), cat))
    achadas.sort()
    return achadas


def _torres_da_linha(texto: str, torres: set[str]) -> list[str]:
    t = texto.upper()
    achadas = [_norm_torre(m.group(1)) for m in _TORRE_RE.finditer(t)]
    achadas += [_norm_torre(m.group(1)) for m in _TORRE_CURTA_RE.finditer(t)]
    for cel in re.split(r"\s*\|\s*", t):
        c = _norm_torre(cel.strip())
        if cel.strip() and c in torres and len(cel.strip()) <= 12:
            achadas.append(c)
    mi = re.match(r"^\s*([A-Z0-9]{1,2})(?:\s*[-–]\s*|\s+)\d{3,4}(?!\d)", t)
    if mi:
        cap = mi.group(1)
        if cap in ("I", "L"):
            cap = "1"
        achadas.append(_norm_torre(cap))
    vistos = []
    for a in achadas:
        if a in torres and a not in vistos:
            vistos.append(a)
    return vistos


def _numeros_da_linha(texto: str, por_num: dict, curtos_ok: bool, torres: set[str] | None = None) -> list[tuple[int, str, str | None]]:
    """(posição, número normalizado, torre sugerida pelo próprio token ou None)."""
    saida = []
    toks = list(re.finditer(r"[^\s|]+", texto.upper()))
    for k, m in enumerate(toks):
        bruto = m.group(0)
        if re.search(r"\d[.,]\d", bruto) or "°" in bruto or "º" in bruto or "$" in bruto or "/" in bruto:
            continue
        ant = toks[k - 1].group(0) if k > 0 else ""
        prox = toks[k + 1].group(0) if k + 1 < len(toks) else ""
        if ant in ("R$", "RS", "R", "R$_") or re.match(r"^\d{3}([.,]\d{2})?$", prox) and re.search(r"[.,]\d{2}$", prox):
            continue  # dinheiro com espaço de milhar: "R$ 307 900,00"
        limpo = bruto.strip(":;()[]*'\"")
        candidatos = [(limpo, None)]
        # "1-0206" / "10206": torre colada no número
        mt = re.match(r"^([A-Z0-9]{1,2})[-–_]?(0\d{2,4})$", limpo)
        if mt and torres and _norm_torre(mt.group(1)) in torres:
            candidatos.append((mt.group(2), _norm_torre(mt.group(1))))
        candidatos += [(limpo.replace("-", ""), None)] + [(p, None) for p in re.split(r"[-–_]", limpo)]
        for c, torre in candidatos:
            mm = _TOKEN_UNIDADE.match(c)
            if not mm:
                continue
            dig = mm.group(2)
            if len(dig) <= 2 and not curtos_ok:
                continue
            n = _norm_num(c)
            if n in por_num:
                saida.append((m.start(), n, torre))
                break
    return saida


_GRUPO_RE = re.compile(
    r"(?:FINAIS|FINAL|FINALS|(?<![A-Z])F)\s*[:.]?\s*((?:\d{1,2}\s*(?:,|E|/|;|\s)\s*)*\d{1,2})"
    r"\s*(\(\s*\+\s*[\d\s,E]+\))?"
    r"\s*[-–]?\s*(?:DO\s*|DE\s*)?(\d{1,2})\s*[°ºO]?\s*(?:ANDAR\s*)?(?:AO|A|ATÉ|ATE|À)\s*(\d{1,2})\s*[°ºO]?"
)
_GRUPO_TORRE_RE = re.compile(r"(?:TORRE\s*|(?<![A-Z0-9])T)([A-Z]|\d{1,2})\s*[-–]")
_NOTA_RE = re.compile(
    r"(?:TODAS\s+AS\s+UNIDADES|100\s*%\s*(?:DAS\s+UNIDADES)?)\s*(?:DO\s+EMPREENDIMENTO\s*)?"
    r"(?:S[AÃ]O|SER[AÃ]O|DE|EM|COMO|CLASSIFICADAS\s+COMO|ENQUADRADAS\s+COMO|\s)*\s*"
    r"(HIS\s*[-–]?\s*[12]|HMP|R2V)"
)


def classificar(linhas: list[Linha], unidades: list[dict]) -> Resultado:
    uns = preparar_unidades(unidades)
    por_num: dict[str, list[_Un]] = defaultdict(list)
    for u in uns:
        if u.num:
            por_num[u.num].append(u)
    torres = {u.torre for u in uns if u.torre}
    digitos = [len(re.sub(r"\D", "", u.num)) for u in uns if u.num]
    curtos_ok = bool(digitos) and statistics.median(digitos) <= 2

    res = Resultado(status="sem_categoria")
    for ln in linhas:
        for _, c in categorias_da_linha(ln.texto):
            res.categorias_no_texto[c] += 1
    if not res.categorias_no_texto:
        return res
    if not uns:
        res.status = "sem_unidades"
        return res

    votos: dict[str, Counter] = defaultdict(Counter)


    # Categoria no título ("Tabela de Vendas - HIS2 - Out/26"): vale para as
    # unidades listadas abaixo dele quando a linha não traz categoria.
    info = []
    ctx = None
    titulo = None
    for ln in linhas:
        cats = categorias_da_linha(ln.texto)
        nums = _numeros_da_linha(ln.texto, por_num, curtos_ok, torres)
        tor = _torres_da_linha(ln.texto, torres)
        if tor and not nums and len(tor) == 1:
            ctx = tor[0]
        if not nums and "TABELA" in ln.texto.upper():
            distintas_t = {c for _, c in cats}
            if len(distintas_t) == 1:
                titulo = next(iter(distintas_t))
        info.append((ln, cats, nums, tor, ctx, titulo))

    def resolver(num: str, tor: list[str], ctx_t: str | None) -> list[_Un]:
        cands = por_num.get(num) or []
        if not cands:
            return []
        if len({u.torre for u in cands}) <= 1:
            return cands
        for filtro in (tor, [ctx_t] if ctx_t else []):
            if filtro:
                f = [u for u in cands if u.torre in filtro]
                if f and len({u.torre for u in f}) == 1:
                    return f
        res.ambiguas += 1
        return []

    usadas_cat = set()
    for i, (ln, cats, nums, tor, ctx_t, titulo_cat) in enumerate(info):
        if not nums:
            continue
        cats_l = cats
        tor_l = tor
        if not cats:
            # categoria numa linha vizinha (mesma linha da tabela quebrada pelo OCR)
            melhor = None
            for j in (i + 1, i - 1, i + 2, i - 2):
                if 0 <= j < len(info) and j not in usadas_cat:
                    lj, cj, nj, tj, _, _ = info[j]
                    if lj.pagina == ln.pagina and cj and not nj and len({c for _, c in cj}) == 1:
                        dy = abs(lj.y - ln.y)
                        if dy <= 1.6 * ln.h and (melhor is None or dy < melhor[0]):
                            melhor = (dy, j)
            if melhor is None:
                if titulo_cat is None:
                    continue
                cats_l = [(0, titulo_cat)]
            else:
                usadas_cat.add(melhor[1])
                cats_l = info[melhor[1]][1]
                tor_l = tor or info[melhor[1]][3]
        distintas = {c for _, c in cats_l}
        if len(distintas) == 1:
            if len({n for _, n, _ in nums}) > 4:
                continue  # grade de disponibilidade com legenda: não arrisca
            cat = next(iter(distintas))
            for n in {(n, t) for _, n, t in nums}:
                for u in resolver(n[0], [n[1]] if n[1] else tor_l, ctx_t):
                    votos[u.id][cat] += 1
        elif len(nums) == len(cats_l) and len(nums) > 1:
            # várias unidades lado a lado, cada uma com a sua categoria
            for (pn, n, t), (pc, cat) in zip(nums, cats_l):
                for u in resolver(n, [t] if t else tor_l, ctx_t):
                    votos[u.id][cat] += 1

    for uid, cont in votos.items():
        top = cont.most_common(2)
        if len(top) > 1 and top[0][1] == top[1][1]:
            res.conflitos += 1
            continue
        res.atribuicoes[uid] = (top[0][0], "tabela_unidade")

    # regras por tipo: finais + andares
    regras = _regras_de_grupo(linhas, torres)
    res.regras_grupo = len(regras)
    if regras:
        grupo_votos: dict[str, set] = defaultdict(set)
        for torre, finais, extras, a0, a1, cat in regras:
            for u in uns:
                if u.id in res.atribuicoes:
                    continue
                if torre and torres and u.torre and u.torre != torre:
                    continue
                dig = re.sub(r"\D", "", u.num)
                if not dig:
                    continue
                if dig in extras:
                    grupo_votos[u.id].add(cat)
                    continue
                if len(dig) < 3:
                    continue
                final = int(dig[-2:])
                andar = u.andar if u.andar is not None else int(dig[:-2])
                if final in finais and a0 <= andar <= a1:
                    grupo_votos[u.id].add(cat)
        for uid, cs in grupo_votos.items():
            if len(cs) == 1:
                res.atribuicoes[uid] = (next(iter(cs)), "tabela_grupo")
            else:
                res.conflitos += 1

    if not res.atribuicoes:
        texto = " ".join(ln.texto.upper() for ln in linhas)
        notas = {re.sub(r"[\s\-–]", "", m.group(1)) for m in _NOTA_RE.finditer(texto)}
        if len(notas) == 1:
            nota = notas.pop()
            cat = {"HIS1": "his1", "HIS2": "his2", "HMP": "hmp", "R2V": "r2v"}.get(nota)
            if cat:
                res.nota = cat
                for u in uns:
                    res.atribuicoes[u.id] = (cat, "nota_geral")

    res.status = "ok" if res.atribuicoes else "sem_unidades"
    return res


def _regras_de_grupo(linhas: list[Linha], torres: set[str]):
    regras = []
    textos = [ln.texto.upper() for ln in linhas]
    i = 0
    while i < len(textos):
        t = textos[i]
        if re.search(r"FINA|(?<![A-Z])F\s*\d", t) and _GRUPO_RE.search(t):
            junto = t
            if not categorias_da_linha(t) and i + 1 < len(textos):
                junto = t + " " + textos[i + 1]
            m = _GRUPO_RE.search(junto)
            cats = {c for p, c in categorias_da_linha(junto[m.end():])} or {c for p, c in categorias_da_linha(junto)}
            if m and len(cats) == 1:
                finais = {int(x) for x in re.findall(r"\d{1,2}", m.group(1))}
                extras = set(re.findall(r"\d{3,5}", m.group(2) or ""))
                a0, a1 = int(m.group(3)), int(m.group(4))
                if a0 > a1:
                    a0, a1 = a1, a0
                mt = _GRUPO_TORRE_RE.search(junto[: m.start() + 1])
                torre = _norm_torre(mt.group(1)) if mt else None
                if torre and torres and torre not in torres:
                    torre = None if len(torres) <= 1 else torre
                regras.append((torre, finais, extras, a0, a1, next(iter(cats))))
        i += 1
    return regras


# ---------------------------------------------------------------- arquivos

_MESES = {m: i + 1 for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto",
     "setembro", "outubro", "novembro", "dezembro"])}


def referencia_do_nome(nome: str):
    from datetime import date
    s = (nome or "").lower().replace("ç", "c")
    m = re.search(r"(janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)[_\s-]*(\d{4})", s)
    if not m:
        return None
    return date(int(m.group(2)), _MESES[m.group(1)], 1)


def escolher_tabelas(arquivos: list[dict]) -> list[dict]:
    """Tabelas de preço candidatas, da melhor para a pior. Disponibilidade não traz categoria."""
    cands = []
    for f in arquivos or []:
        nome = str(f.get("name") or "")
        tipo = str(f.get("type") or "")
        if tipo != "Tabela" and not re.search(r"tabela|pre[cç]o", nome, re.I):
            continue
        if re.search(r"disponibilidade|espelho", nome, re.I):
            continue
        if not nome.lower().endswith(".pdf"):
            continue
        ref = referencia_do_nome(nome)
        padrao = 1 if re.match(r"^tabela_", nome) else 0
        try:
            fid = int(f.get("id"))
        except (TypeError, ValueError):
            fid = 0
        cands.append(((1 if tipo == "Tabela" else 0, padrao, ref.toordinal() if ref else 0, fid), f))
    cands.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in cands]
