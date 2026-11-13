"""Linha de comando: coletar --tipo mensal|lancamentos --data AAAA-MM-DD.

Sem --tipo, vale COLETAR_TIPO. Sem --data, vale o dia de hoje em America/Sao_Paulo.
Assim o job do Cloud Run, que não passa argumentos, coleta de verdade.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from coletar.banco import BancoPostgres, BancoSupabase
from coletar.descoberta import SJC_BBOX, SP_BBOX
from coletar.erros import ColetaErro
from coletar.fonte import ContextoColeta, Municipio
from coletar.mapeamento import norm
from coletar.orulo_api import OruloApi
from coletar.orulo_login import OruloLogin
from coletar.orulo_planilha import OruloPlanilha, carregar_mapa
from coletar.rodada import executar

MUNICIPIOS = (
    Municipio("São Paulo", "SP", SP_BBOX),
    Municipio("São José dos Campos", "SP", SJC_BBOX),
)

TIPOS = {"mensal": "completa_mensal", "lancamentos": "lancamentos", "completa_mensal": "completa_mensal"}
TIPOS_CLI = ("mensal", "lancamentos")
# Brasil não usa horário de verão desde 2019. Se o sistema não tiver o fuso,
# America/Sao_Paulo é UTC−3 o ano inteiro.
_FUSO_FIXO = timezone(timedelta(hours=-3))


def fuso_sao_paulo():
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        return _FUSO_FIXO


def data_de_hoje(agora: datetime | None = None) -> date:
    """Dia civil em America/Sao_Paulo. `agora` ingênuo é lido nesse fuso."""
    fuso = fuso_sao_paulo()
    momento = agora if agora is not None else datetime.now(fuso)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=fuso)
    return momento.astimezone(fuso).date()


def resolver_tipo(explicito: str | None) -> str:
    if explicito:
        bruto = explicito.strip().lower()
    else:
        bruto = os.environ.get("COLETAR_TIPO", "").strip().lower()
    if bruto not in TIPOS_CLI:
        raise ColetaErro(
            "tipo ausente ou inválido: passe --tipo mensal|lancamentos "
            "ou defina COLETAR_TIPO=mensal|lancamentos"
        )
    return bruto


def resolver_data(explicito: str | None, agora: datetime | None = None) -> date:
    if explicito is None:
        return data_de_hoje(agora)
    try:
        return date.fromisoformat(explicito.strip())
    except ValueError:
        raise ColetaErro("data inválida: use AAAA-MM-DD") from None


def _atraso() -> tuple[float, float]:
    try:
        minimo = float(os.environ.get("ORULO_ATRASO_MIN", "2"))
        maximo = float(os.environ.get("ORULO_ATRASO_MAX", "3"))
    except ValueError:
        minimo, maximo = 2.0, 3.0
    return minimo, maximo


def municipios_pedidos(nomes: list[str] | None) -> list[Municipio]:
    if not nomes:
        return list(MUNICIPIOS)
    por_nome = {norm(m.nome): m for m in MUNICIPIOS}
    saida = []
    for nome in nomes:
        achado = por_nome.get(norm(nome))
        if achado is None:
            raise ColetaErro(f"município fora da lista monitorada: {nome}")
        saida.append(achado)
    return saida


def criar_fonte(nome: str, *, arquivo: str | None, mapa: str | None, transporte=None, sleep=None):
    atraso = _atraso()
    if nome == "orulo_login":
        return OruloLogin(
            email=os.environ.get("ORULO_EMAIL", ""),
            senha=os.environ.get("ORULO_PASSWORD", ""),
            atraso=atraso, transporte=transporte, sleep=sleep,
        )
    if nome == "orulo_planilha":
        if not arquivo:
            raise ColetaErro("orulo_planilha exige --arquivo")
        return OruloPlanilha(arquivo, carregar_mapa(mapa))
    if nome == "orulo_api":
        return OruloApi(
            client_id=os.environ.get("ORULO_CLIENT_ID", ""),
            client_secret=os.environ.get("ORULO_CLIENT_SECRET", ""),
            atraso=atraso, transporte=transporte, sleep=sleep,
        )
    raise ColetaErro(f"fonte desconhecida: {nome}")


def ids_pedidos(valores: list[str] | None) -> tuple[str, ...]:
    if not valores:
        return ()
    saida: list[str] = []
    vistos: set[str] = set()
    for bruto in valores:
        ident = str(bruto).strip()
        if not ident:
            raise ColetaErro("id vazio")
        if ident not in vistos:
            vistos.add(ident)
            saida.append(ident)
    return tuple(saida)


def _marcar_erro_de_rede(banco, erro: BaseException) -> None:
    """Log de uma linha e, se a coleta já abriu, im_falhar_coleta.

    im_falhar_coleta também grava a anotação da falha. Sem coleta aberta
    (erro antes de im_abrir_coleta) não há o que marcar.
    """
    log = logging.getLogger("coletar")
    msg = f"erro de rede: {type(erro).__name__}"
    log.error(msg)
    if banco is None:
        return
    coleta_id = getattr(banco, "coleta_aberta", None)
    if not coleta_id:
        return
    try:
        banco.falhar(coleta_id, msg)
    except (httpx.HTTPError, ColetaErro, OSError) as e:
        log.error("erro de rede ao marcar a coleta: %s", type(e).__name__)
    else:
        banco.coleta_aberta = None


def criar_banco():
    dsn = os.environ.get("COLETAR_PG_DSN")
    if dsn:
        pasta = os.environ.get("COLETAR_BRUTO_DIR", "/tmp/coletar-bruto")
        return BancoPostgres(dsn, pasta)
    return BancoSupabase(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    )


def etapa_tabelas(banco, municipios: list[str]) -> None:
    """Depois da mensal: tabela de preços (categoria social) só dos empreendimentos novos.

    COLETAR_TABELAS=0 desliga. Espera entre pedidos ao Órulo: TABELAS_ATRASO_MIN/MAX
    (padrão 3 a 5 s). Falha aqui não muda o resultado da coleta.
    """
    log = logging.getLogger("coletar")
    if os.environ.get("COLETAR_TABELAS", "1").strip() == "0":
        log.info("tabelas de preço desligadas (COLETAR_TABELAS=0)")
        return
    try:
        from coletar.tabelas import rodar_tabelas
        try:
            atraso = (float(os.environ.get("TABELAS_ATRASO_MIN", "3")), float(os.environ.get("TABELAS_ATRASO_MAX", "5")))
        except ValueError:
            atraso = (3.0, 5.0)
        orulo = OruloLogin(
            email=os.environ.get("ORULO_EMAIL", ""), senha=os.environ.get("ORULO_PASSWORD", ""), atraso=atraso,
        )
        rodar_tabelas(banco, orulo, municipios)
    except Exception as e:  # noqa: BLE001
        log.warning("AVISO: etapa das tabelas de preço falhou: %s: %s", type(e).__name__, e)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coletar", description="Coleta do monitor de mercado")
    p.add_argument(
        "--tipo", choices=list(TIPOS_CLI), default=None,
        help="mensal (completa) ou lancamentos (semanal). Sem a flag, usa COLETAR_TIPO",
    )
    p.add_argument(
        "--data", default=None,
        help="data de referência AAAA-MM-DD. Sem a flag, o dia de hoje em America/Sao_Paulo",
    )
    p.add_argument("--fonte", default="orulo_login", choices=["orulo_login", "orulo_planilha", "orulo_api"])
    p.add_argument("--municipio", action="append", default=None, help="repete para cada cidade; padrão São Paulo e São José dos Campos")
    p.add_argument("--arquivo", default=None, help="planilha .csv ou .xlsx, obrigatório em orulo_planilha")
    p.add_argument("--mapa", default=None, help="JSON de campo para nome de coluna, quando a planilha não usa o cabeçalho padrão")
    p.add_argument(
        "--id", action="append", default=None,
        help="id do empreendimento no Órulo; repete para vários. Pula a listagem e abre cada um (detalhe e unidades)",
    )
    return p


def main(argv=None, *, fonte=None, banco=None, agora: datetime | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        tipo_nome = resolver_tipo(args.tipo)
        referencia = resolver_data(args.data, agora)
    except ColetaErro as e:
        print(str(e), file=sys.stderr)
        return 1
    tipo = TIPOS[tipo_nome]
    logging.getLogger("coletar").info("referência %s, tipo %s", referencia.isoformat(), tipo_nome)
    proprio_banco = banco is None
    fonte_propria = fonte is None
    try:
        municipios = municipios_pedidos(args.municipio)
        ids = ids_pedidos(args.id)
        if ids and args.fonte == "orulo_planilha":
            raise ColetaErro("orulo_planilha não coleta por id")
        if fonte is None:
            fonte = criar_fonte(args.fonte, arquivo=args.arquivo, mapa=args.mapa)
        if banco is None:
            banco = criar_banco()
        ctx = ContextoColeta(
            tipo=tipo,
            referencia=referencia,
            municipios=municipios,
            ids=ids,
        )
        resultado = executar(banco, fonte, ctx)
        if (fonte_propria and args.fonte == "orulo_login" and tipo == "completa_mensal" and not ids
                and resultado.situacao != "falhou"):
            etapa_tabelas(banco, [m.nome for m in municipios])
    except httpx.HTTPError as e:
        _marcar_erro_de_rede(banco, e)
        return 1
    except ColetaErro as e:
        # O cliente HTTP entrega a falha de rede como OruloError, com a
        # ConnectError (ou outra HTTPError) na causa, depois das tentativas.
        if isinstance(e.__cause__, httpx.HTTPError):
            _marcar_erro_de_rede(banco, e.__cause__)
        else:
            print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        if proprio_banco and banco is not None:
            banco.fechar()
    if resultado.situacao == "completa":
        return 0
    if resultado.situacao == "incompleta":
        return 2
    return 3
