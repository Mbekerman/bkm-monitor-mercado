"""Linha de comando: coletar --tipo mensal|lancamentos --data AAAA-MM-DD."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coletar", description="Coleta do monitor de mercado")
    p.add_argument("--tipo", required=True, choices=["mensal", "lancamentos"], help="mensal (completa) ou lancamentos (semanal)")
    p.add_argument("--data", required=True, help="data de referência AAAA-MM-DD")
    p.add_argument("--fonte", default="orulo_login", choices=["orulo_login", "orulo_planilha", "orulo_api"])
    p.add_argument("--municipio", action="append", default=None, help="repete para cada cidade; padrão São Paulo e São José dos Campos")
    p.add_argument("--arquivo", default=None, help="planilha .csv ou .xlsx, obrigatório em orulo_planilha")
    p.add_argument("--mapa", default=None, help="JSON de campo para nome de coluna, quando a planilha não usa o cabeçalho padrão")
    p.add_argument(
        "--id", action="append", default=None,
        help="id do empreendimento no Órulo; repete para vários. Pula a listagem e abre cada um (detalhe e unidades)",
    )
    return p


def main(argv=None, *, fonte=None, banco=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        referencia = date.fromisoformat(args.data)
    except ValueError:
        print("data inválida: use AAAA-MM-DD", file=sys.stderr)
        return 1
    tipo = TIPOS[args.tipo]
    proprio_banco = banco is None
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
