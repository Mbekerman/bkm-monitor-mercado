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
    # Ids pedidos na linha de comando. Quando preenchido, a fonte não varre a listagem.
    ids: tuple[str, ...] = ()


def deve_abrir(ctx: ContextoColeta, id_externo: str) -> bool:
    """Diz se este empreendimento entra na rodada com detalhe e unidades.

    A mensal abre todos os da listagem. A semanal de lançamentos abre só os que
    ainda não estão no banco: quem já existe fica para a mensal. Com ids
    explícitos, abre exatamente esses, mesmo que já existam.
    """
    if ctx.ids:
        return str(id_externo) in ctx.ids
    if ctx.tipo == "completa_mensal":
        return True
    return str(id_externo) not in ctx.conhecidos


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
    """Unidades acompanham a abertura do empreendimento.

    A situação vigente não reabre a semanal: quem saiu de breve lançamento
    espera a mensal.
    """
    del situacao
    return deve_abrir(ctx, id_externo)
