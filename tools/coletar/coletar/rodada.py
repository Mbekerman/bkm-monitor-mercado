"""Uma rodada: abre, busca, grava o bruto, importa em lotes e fecha.

A importação passa por coletar.importacao: até LOTE empreendimentos e
UNIDADES_POR_CHAMADA unidades por chamada; empreendimento grande vai em partes.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date

import httpx

from coletar.erros import OruloAuthError, OruloBlocked
from coletar.fonte import ContextoColeta, FonteMercado
from coletar.importacao import UNIDADES_MINIMO, UNIDADES_POR_CHAMADA, Importador

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


def _env_int(nome: str, padrao: int) -> int:
    try:
        v = int(os.environ.get(nome, "") or padrao)
    except ValueError:
        return padrao
    return v if v > 0 else padrao


def importador(banco, coleta_id: str) -> Importador:
    return Importador(
        banco, coleta_id,
        unidades_por_chamada=_env_int("COLETAR_UNIDADES_POR_CHAMADA", UNIDADES_POR_CHAMADA),
        minimo=_env_int("COLETAR_UNIDADES_MINIMO", UNIDADES_MINIMO),
        empreendimentos_por_chamada=LOTE,
    )


def _lotes(itens: list, tamanho: int):
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def executar(banco, fonte: FonteMercado, ctx: ContextoColeta) -> ResultadoRodada:
    if ctx.tipo == "lancamentos":
        ctx.conhecidos = banco.conhecidos(fonte.nome)
    caminho = caminho_bruto(ctx.referencia, ctx.tipo)
    coleta_id = banco.abrir(fonte.nome, ctx.tipo, ctx.referencia, caminho)
    # A main lê isto se um erro de rede escapar daqui, para chamar im_falhar_coleta.
    banco.coleta_aberta = coleta_id
    log.info("coleta %s aberta (%s, %s, %s)", coleta_id, fonte.nome, ctx.tipo, ctx.referencia.isoformat())
    try:
        resultado = fonte.coletar(ctx)
    except OruloAuthError as e:
        banco.falhar(coleta_id, str(e))
        banco.coleta_aberta = None
        log.error("coleta %s falhou: %s", coleta_id, e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="falhou", caminho=caminho, avisos=[str(e)])
    except OruloBlocked as e:
        banco.anotar_falha(coleta_id, "", "bloqueado", str(e))
        banco.gravar_bruto(caminho, gzip.compress(b'{"itens":[]}'))
        banco.fechar_coleta(coleta_id, None, forcar_incompleta=True)
        banco.coleta_aberta = None
        log.warning("AVISO: coleta incompleta (%s)", e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="incompleta", caminho=caminho, avisos=[str(e)])

    conteudo = gzip.compress(json.dumps(resultado.bruto, ensure_ascii=False, default=str).encode("utf-8"))
    ultimo = {}
    try:
        banco.gravar_bruto(caminho, conteudo)
        log.info("bruto gravado em %s (%s bytes)", caminho, len(conteudo))
        if resultado.empreendimentos:
            imp = importador(banco, coleta_id)
            for lote in _lotes(resultado.empreendimentos, LOTE):
                ultimo = imp.importar(lote) or {}
                log.info(
                    "lote de %s: coletados=%s unidades=%s ignorados=%s erros=%s",
                    len(lote), ultimo.get("coletados"), ultimo.get("unidades"),
                    ultimo.get("ignorados"), ultimo.get("erros"),
                )
            if imp.parciais:
                resultado.avisos.append(
                    "empreendimento importado só em parte (falha importacao_parcial): "
                    + ", ".join(sorted(set(imp.parciais)))
                )
        for falha in resultado.falhas:
            banco.anotar_falha(coleta_id, falha.get("id_externo"), falha.get("motivo") or "erro", falha.get("detalhe") or "")
    except httpx.HTTPError:
        raise
    except Exception as e:
        banco.falhar(coleta_id, f"importação: {e}")
        banco.coleta_aberta = None
        log.error("coleta %s falhou na importação: %s", coleta_id, e)
        return ResultadoRodada(coleta_id=coleta_id, situacao="falhou", caminho=caminho, avisos=list(resultado.avisos))

    for aviso in resultado.avisos:
        log.warning("AVISO: %s", aviso)

    if resultado.falhou:
        banco.falhar(coleta_id, resultado.motivo_falha or "falha de autenticação")
        banco.coleta_aberta = None
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
    banco.coleta_aberta = None
    situacao = fechamento.get("situacao") or "incompleta"
    # Os totais saem do fechamento (im_importar não recalcula mais a cada chamada).
    for chave in ("coletados", "unidades"):
        if fechamento.get(chave) is not None:
            ultimo[chave] = fechamento[chave]
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
