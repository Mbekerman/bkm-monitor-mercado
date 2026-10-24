"""Uma rodada: abre, busca, grava o bruto, importa em lotes de 50 e fecha."""
from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import date

from coletar.erros import OruloAuthError, OruloBlocked
from coletar.fonte import ContextoColeta, FonteMercado

log = logging.getLogger("coletar")

LOTE = 50


@dataclass
class ResultadoRodada:
    coleta_id: str
    situacao: str
    caminho: str
    esperados: int | None = None
    coletados: int | None = None
    unidades: int | None = None
    erros: int | None = None
    ignorados: int | None = None
    vendas: int | None = None
    removidas: int | None = None
    avisos: list[str] = field(default_factory=list)


def caminho_bruto(referencia: date, tipo: str) -> str:
    return f"mercado/{referencia.isoformat()}-{tipo}.json.gz"


def _lotes(itens: list, tamanho: int):
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def executar(banco, fonte: FonteMercado, ctx: ContextoColeta) -> ResultadoRodada:
    if ctx.tipo == "lancamentos":
        ctx.conhecidos = banco.conhecidos(fonte.nome)
    caminho = caminho_bruto(ctx.referencia, ctx.tipo)
    coleta_id = banco.abrir(fonte.nome, ctx.tipo, ctx.referencia, caminho)
    log.info("coleta %s aberta (%s, %s, %s)", coleta_id, fonte.nome, ctx.tipo, ctx.referencia.isoformat())
    try:
        resultado = fonte.coletar(ctx)
    except OruloAuthError as e:
        banco.falhar(coleta_id, str(e))
        log.error("coleta %s falhou: %s", coleta_id, e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="falhou", caminho=caminho, avisos=[str(e)])
    except OruloBlocked as e:
        banco.anotar_falha(coleta_id, "", "bloqueado", str(e))
        banco.gravar_bruto(caminho, gzip.compress(b'{"itens":[]}'))
        banco.fechar_coleta(coleta_id, None, forcar_incompleta=True)
        log.warning("AVISO: coleta incompleta (%s)", e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="incompleta", caminho=caminho, avisos=[str(e)])

    conteudo = gzip.compress(json.dumps(resultado.bruto, ensure_ascii=False, default=str).encode("utf-8"))
    ultimo = {}
    try:
        banco.gravar_bruto(caminho, conteudo)
        log.info("bruto gravado em %s (%s bytes)", caminho, len(conteudo))
        if resultado.empreendimentos:
            for lote in _lotes(resultado.empreendimentos, LOTE):
                ultimo = banco.importar(coleta_id, lote) or {}
                log.info(
                    "lote de %s: coletados=%s unidades=%s ignorados=%s erros=%s",
                    len(lote), ultimo.get("coletados"), ultimo.get("unidades"),
                    ultimo.get("ignorados"), ultimo.get("erros"),
                )
        for falha in resultado.falhas:
            banco.anotar_falha(coleta_id, falha.get("id_externo"), falha.get("motivo") or "erro", falha.get("detalhe") or "")
    except Exception as e:
        banco.falhar(coleta_id, f"importação: {e}")
        log.error("coleta %s falhou na importação: %s", coleta_id, e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="falhou", caminho=caminho, avisos=list(resultado.avisos))

    for aviso in resultado.avisos:
        log.warning("AVISO: %s", aviso)

    if resultado.falhou:
        banco.falhar(coleta_id, resultado.motivo_falha or "falha de autenticação")
        log.error("coleta %s falhou: %s", coleta_id, resultado.motivo_falha)
        return ResultadoRodada(
            coleta_id=coleta_id, situacao="falhou", caminho=caminho,
            esperados=resultado.esperados, coletados=ultimo.get("coletados"),
            unidades=ultimo.get("unidades"), erros=ultimo.get("erros"),
            ignorados=ultimo.get("ignorados"), avisos=list(resultado.avisos),
        )

    fechamento = banco.fechar_coleta(
        coleta_id, resultado.esperados, forcar_incompleta=resultado.forcar_incompleta,
    ) or {}
    situacao = fechamento.get("situacao") or "incompleta"
    log.info(
        "coleta %s: situacao=%s coletados=%s unidades=%s esperados=%s vendas=%s removidas=%s erros=%s ignorados=%s",
        coleta_id, situacao, ultimo.get("coletados"), ultimo.get("unidades"), resultado.esperados,
        fechamento.get("vendas"), fechamento.get("removidas"), ultimo.get("erros"), ultimo.get("ignorados"),
    )
    if situacao == "incompleta":
        log.warning(
            "AVISO: coleta incompleta (coletados=%s, esperados=%s)",
            ultimo.get("coletados"), resultado.esperados,
        )
    return ResultadoRodada(
        coleta_id=coleta_id,
        situacao=situacao,
        caminho=caminho,
        esperados=resultado.esperados,
        coletados=ultimo.get("coletados"),
        unidades=ultimo.get("unidades"),
        erros=ultimo.get("erros"),
        ignorados=ultimo.get("ignorados"),
        vendas=fechamento.get("vendas"),
        removidas=fechamento.get("removidas"),
        avisos=list(resultado.avisos),
    )
