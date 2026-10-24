from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Municipio:
    nome: str
    uf: str
    # sw_lat, sw_lng, ne_lat, ne_lng. Retângulo usado para fatiar o mapa.
    bbox: tuple[float, float, float, float]


@dataclass
class ContextoColeta:
    tipo: str  # completa_mensal | lancamentos
    referencia: date
    municipios: list[Municipio]
    # id externo do empreendimento → situação vigente. Vazio na mensal.
    conhecidos: dict[str, str] = field(default_factory=dict)


@dataclass
class ResultadoFonte:
    empreendimentos: list[dict]
    bruto: dict
    esperados: int
    falhas: list[dict] = field(default_factory=list)
    forcar_incompleta: bool = False
    falhou: bool = False
    motivo_falha: str | None = None
    avisos: list[str] = field(default_factory=list)


class FonteMercado(Protocol):
    nome: str

    def coletar(self, ctx: ContextoColeta) -> ResultadoFonte: ...


def precisa_unidades(ctx: ContextoColeta, id_externo: str, situacao: str) -> bool:
    """Mensal traz todas as unidades. Lançamentos, só as dos novos e as de quem saiu de breve lançamento."""
    if ctx.tipo == "completa_mensal":
        return True
    anterior = ctx.conhecidos.get(str(id_externo))
    if anterior is None:
        return True
    return anterior == "breve_lancamento" and situacao != "breve_lancamento"
