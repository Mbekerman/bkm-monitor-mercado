"""Importação em partes: empreendimento grande vai em pedaços de unidades.

`im_importar` roda pela API do Supabase (PostgREST), com `statement_timeout`
de 8 s. Cada chamada também recalcula os totais da coleta (`coletados`,
`unidades`, `tipos_desconhecidos`), um custo fixo que cresce com a coleta: com
~400 mil unidades ele passa de 5 s. Um empreendimento de 600 a 3.800 unidades
não cabe numa chamada.

Partir as unidades de um empreendimento em várias chamadas dá o mesmo
resultado de uma chamada só, porque:

- `_im_importar_um` grava empreendimento e unidades com upsert. Mandar o
  mesmo empreendimento de novo na mesma coleta só regrava os mesmos valores
  (estado com `desde_coleta_id` desta coleta é atualizado no lugar);
- venda, unidade removida e vínculo de unidade só são calculados em
  `im_fechar_coleta`, sobre tudo o que a coleta viu, nunca na importação;
- a única conta por empreendimento que olha a lista de unidades é "tem
  unidade residencial?" (filtro do empreendimento). Para ela ser a mesma em
  todas as partes, cada parte leva também unidades representantes, com todos
  os `tipo_unidade` do empreendimento;
- unidades com a mesma torre/número (mesma chave no banco) vão sempre juntas,
  na ordem original. Assim a última gravada e o primeiro preço visto são os
  mesmos da chamada única. Regravar um grupo inteiro de novo (representante)
  não muda nada.

Um caso pediria cuidado: numa chamada só, uma unidade que o banco recusa
(valor fora do tipo ou do check da coluna) desfaz o empreendimento inteiro.
Em partes, só a parte dela seria desfeita. Por isso, antes de partir, o
empreendimento com unidade recusável é mandado só com os representantes e essa
unidade: o banco desfaz tudo e anota o mesmo erro, como antes. Uma parte que
mesmo assim volte com erro é anotada como falha `importacao_parcial`.

Tempo estourado (57014) parte o pedido: lote de vários empreendimentos ao
meio; empreendimento sozinho em pedaços menores, até `minimo` unidades. O
tamanho que funcionou fica valendo para as próximas chamadas e volta a crescer
devagar depois de uma sequência sem erro.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata

import httpx

from coletar.erros import ColetaErro

log = logging.getLogger("coletar")

UNIDADES_POR_CHAMADA = 400
UNIDADES_MINIMO = 25
EMPREENDIMENTOS_POR_CHAMADA = 50
TENTATIVAS_TRANSITORIO = 3
SUCESSOS_PARA_CRESCER = 20

_SMALLINT = (-32768, 32767)
# Limites dos checks de im_unidades (além do smallint).
_LIMITES = {"andar": None, "dormitorios": (0, 20), "suites": (0, None), "vagas": (0, None)}
_TRADUZ = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ",
                        "aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN")
_PALAVRAS_ROTULO = re.compile(r"\b(EDIFICIO|TORRE|BLOCO|ED)\b")
_ESPACOS = re.compile(r"\s+")
_NAO_ALNUM = re.compile(r"[^A-Z0-9]+")


def _unidades(emp: dict) -> list | None:
    u = emp.get("unidades")
    return u if isinstance(u, list) else None


def contar_unidades(emp: dict) -> int:
    return len(_unidades(emp) or [])


def norm_rotulo(v) -> str:
    """Mesma conta de public.im_norm_rotulo."""
    s = str(v or "").translate(_TRADUZ).upper()
    return _ESPACOS.sub("", _PALAVRAS_ROTULO.sub("", s))


def _grosso(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return _NAO_ALNUM.sub("", "".join(c for c in s if not unicodedata.combining(c)).upper())


def chave_rotulo(torre, numero) -> tuple[str, str]:
    """Chave de torre/número para agrupar unidades que o banco trata como a mesma.

    Parte de im_norm_rotulo (norm_rotulo) e ainda tira acento, pontuação e
    caixa: fica mais grossa que a do banco, nunca mais fina. Duas unidades que
    o banco junta caem sempre no mesmo grupo.
    """
    return _grosso(norm_rotulo(torre)), _grosso(norm_rotulo(numero))


def grupos(unidades: list) -> list[list]:
    """Unidades agrupadas pela chave, na ordem da primeira aparição.

    Dentro do grupo a ordem original fica. Unidades de chaves diferentes não
    interferem uma na outra no banco, então só a ordem dentro do grupo importa.
    """
    por_chave: dict[tuple, list] = {}
    for u in unidades:
        por_chave.setdefault(chave_rotulo(u.get("torre"), u.get("numero")), []).append(u)
    return list(por_chave.values())


def unidade_recusada(u: dict) -> str | None:
    """Motivo pelo qual _im_importar_um quebraria nesta unidade, ou None."""
    for campo, faixa in _LIMITES.items():
        v = u.get(campo)
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, int):
            if isinstance(v, str) and re.fullmatch(r"\s*[+-]?\d+\s*", v):
                v = int(v)
            else:
                return f"{campo}={v!r} não é inteiro"
        if not _SMALLINT[0] <= v <= _SMALLINT[1]:
            return f"{campo}={v} fora de smallint"
        if faixa:
            lo, hi = faixa
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                return f"{campo}={v} fora do permitido"
    for campo in ("preco", "preco_original", "area_privativa_m2"):
        v = u.get(campo)
        if v is None or v == "" or isinstance(v, (int, float)) and not isinstance(v, bool):
            continue
        try:
            float(str(v))
        except ValueError:
            return f"{campo}={v!r} não é número"
    return None


def representantes(gs: list[list]) -> list[int]:
    """Índices de grupos que, juntos, têm todos os tipo_unidade do empreendimento.

    Para cada tipo, o menor grupo que o contém. Os grupos escolhidos vão em toda
    parte, inteiros e na ordem original: regravar um grupo inteiro na mesma
    coleta não muda nada, e a conta "tem unidade residencial?" fica igual à da
    lista completa.
    """
    melhor: dict = {}
    for i, g in enumerate(gs):
        for t in {u.get("tipo_unidade") for u in g}:
            if t not in melhor or len(g) < len(gs[melhor[t]]):
                melhor[t] = i
    return sorted(set(melhor.values()))


def _montar(emp: dict, gs: list[list], reps: list[int], idx: list[int]) -> dict:
    extra = [i for i in reps if i not in set(idx)]
    return {**emp, "unidades": [u for i in extra + idx for u in gs[i]]}


def partes(emp: dict, tamanho: int) -> list[dict]:
    """O empreendimento em partes de até `tamanho` unidades (+ grupos representantes).

    Grupo maior que `tamanho` vai inteiro numa parte.
    """
    unidades = _unidades(emp)
    if unidades is None or len(unidades) <= tamanho:
        return [emp]
    gs = grupos(unidades)
    reps = representantes(gs)
    saida, i = [], 0
    while i < len(gs):
        idx = _proxima(gs, reps, i, tamanho)
        saida.append(_montar(emp, gs, reps, idx))
        i += len(idx)
    return saida


def _proxima(gs: list[list], reps: list[int], i: int, tamanho: int) -> list[int]:
    """Grupos a partir de i que cabem em `tamanho`, contando os representantes."""
    rep = set(reps)
    n = sum(len(gs[r]) for r in reps)
    idx: list[int] = []
    while i + len(idx) < len(gs):
        j = i + len(idx)
        k = 0 if j in rep else len(gs[j])
        if idx and n + k > tamanho:
            break
        idx.append(j)
        n += k
    return idx


def _tempo_estourado(e: BaseException) -> bool:
    s = str(e)
    if "57014" in s or "statement timeout" in s or "canceling statement" in s:
        return True
    return type(e).__name__ == "QueryCanceled"


def _transitorio(e: BaseException) -> bool:
    if isinstance(e, httpx.TransportError):
        return True
    if isinstance(e, ColetaErro):
        return bool(re.search(r"retornou (429|5\d\d)\b", str(e)))
    return False


class Importador:
    """Manda empreendimentos para im_importar sem estourar o tempo do PostgREST."""

    def __init__(self, banco, coleta_id: str, *, unidades_por_chamada: int = UNIDADES_POR_CHAMADA,
                 minimo: int = UNIDADES_MINIMO, empreendimentos_por_chamada: int = EMPREENDIMENTOS_POR_CHAMADA,
                 tentativas: int = TENTATIVAS_TRANSITORIO, sleep=time.sleep):
        self.banco = banco
        self.coleta_id = coleta_id
        self.maximo = max(1, unidades_por_chamada)
        self.minimo = max(1, min(minimo, self.maximo))
        self.tamanho = self.maximo
        self.max_emps = max(1, empreendimentos_por_chamada)
        self.tentativas = max(1, tentativas)
        self.sleep = sleep
        self.sucessos = 0
        self.chamadas = 0
        self.erros = 0
        self.parciais: list[str] = []
        self.ultimo: dict = {}

    # -- uma chamada ---------------------------------------------------------
    def _chamar(self, carga: list[dict]) -> dict:
        espera = 2.0
        for tentativa in range(1, self.tentativas + 1):
            try:
                r = self.banco.importar(self.coleta_id, carga) or {}
            except Exception as e:  # noqa: BLE001 - classificado abaixo
                if _tempo_estourado(e) or not _transitorio(e) or tentativa == self.tentativas:
                    raise
                log.warning("im_importar: erro transitório (%s), tentativa %s de %s",
                            str(e)[:120], tentativa, self.tentativas)
                self.sleep(espera)
                espera *= 2
                continue
            self.chamadas += 1
            self.ultimo = r
            self.erros += int(r.get("erros") or 0)
            self.sucessos += 1
            if self.sucessos >= SUCESSOS_PARA_CRESCER and self.tamanho < self.maximo:
                self.tamanho = min(self.maximo, int(self.tamanho * 1.5) + 1)
                self.sucessos = 0
            return r
        raise AssertionError("inalcançável")

    def _encolher(self, para: int) -> None:
        novo = max(self.minimo, para)
        if novo < self.tamanho:
            log.warning("im_importar estourou o tempo; partes de até %s unidades", novo)
        self.tamanho = min(self.tamanho, novo)
        self.sucessos = 0

    # -- empreendimento grande -------------------------------------------------
    def _falha_rapida(self, emp: dict, gs: list[list], reps: list[int]) -> bool:
        """Empreendimento com unidade que o banco recusa: falha inteiro, como numa chamada só.

        Manda só os representantes e o grupo da unidade recusada. O banco
        desfaz o empreendimento e anota o mesmo erro que anotaria com a lista
        completa. Se o banco aceitar (a checagem daqui errou), segue em partes.
        """
        for i, g in enumerate(gs):
            if any(unidade_recusada(u) for u in g):
                r = self._chamar([_montar(emp, gs, reps, [i])])
                if int(r.get("erros") or 0):
                    log.error("empreendimento %s: unidade recusada pelo banco; empreendimento não importado",
                              emp.get("id_externo"))
                    return True
                return False
        return False

    def _em_partes(self, emp: dict) -> None:
        unidades = _unidades(emp) or []
        ide = emp.get("id_externo")
        gs = grupos(unidades)
        reps = representantes(gs)
        if self._falha_rapida(emp, gs, reps):
            return
        i = 0
        n_partes = 0
        feitas = 0
        while i < len(gs):
            tam = self.tamanho
            idx = _proxima(gs, reps, i, tam)
            n = sum(len(gs[j]) for j in idx)
            try:
                r = self._chamar([_montar(emp, gs, reps, idx)])
            except Exception as e:  # noqa: BLE001
                if not (_tempo_estourado(e) or _transitorio(e)) or tam <= self.minimo or len(idx) == 1:
                    if n_partes:
                        self._parcial(ide, f"parou depois de {feitas} de {len(unidades)} unidades: {e}")
                    raise
                self._encolher(min(tam, n) // 2)
                continue
            if int(r.get("erros") or 0):
                self._parcial(ide, f"parte com erro (unidades {feitas}-{feitas + n} de {len(unidades)})")
            i += len(idx)
            feitas += n
            n_partes += 1
        log.info("empreendimento %s: %s unidades em %s partes", ide, len(unidades), n_partes)

    def _parcial(self, ide, detalhe: str) -> None:
        self.parciais.append(str(ide))
        log.error("IMPORTACAO_PARCIAL %s: %s", ide, detalhe[:300])
        try:
            self.banco.anotar_falha(self.coleta_id, ide, "importacao_parcial", detalhe[:500])
        except Exception as e:  # noqa: BLE001 - a anotação não pode esconder o erro original
            log.error("não consegui anotar importacao_parcial de %s: %s", ide, str(e)[:200])

    # -- lote -----------------------------------------------------------------
    def _grupo(self, grupo: list[dict]) -> None:
        try:
            self._chamar(grupo)
            return
        except Exception as e:  # noqa: BLE001
            if not (_tempo_estourado(e) or _transitorio(e)):
                raise
            if len(grupo) > 1:
                log.warning("im_importar de %s empreendimentos estourou o tempo; partindo ao meio", len(grupo))
                self._encolher(sum(contar_unidades(x) for x in grupo) // 2)
                meio = len(grupo) // 2
                self._grupo(grupo[:meio])
                self._grupo(grupo[meio:])
                return
            emp = grupo[0]
            n = contar_unidades(emp)
            if n <= self.minimo:
                raise
            self._encolher(n // 2)
            self._em_partes(emp)

    def importar(self, emps: list[dict]) -> dict:
        """Importa a lista toda. Devolve o retorno da última chamada, com `erros` somados."""
        erros_antes = self.erros
        grupo: list[dict] = []
        n = 0
        for emp in emps:
            k = contar_unidades(emp)
            if k > self.tamanho:
                if grupo:
                    self._grupo(grupo)
                    grupo, n = [], 0
                self._em_partes(emp)
                continue
            if grupo and (n + k > self.tamanho or len(grupo) >= self.max_emps):
                self._grupo(grupo)
                grupo, n = [], 0
            grupo.append(emp)
            n += k
        if grupo:
            self._grupo(grupo)
        return {**self.ultimo, "erros": self.erros - erros_antes}
