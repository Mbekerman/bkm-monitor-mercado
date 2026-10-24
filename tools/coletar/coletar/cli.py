"""Linha de comando: coletar --tipo mensal|lancamentos --data AAAA-MM-DD."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

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
    proprio_fonte = fonte is None
    try:
        municipios = municipios_pedidos(args.municipio)
        if fonte is None:
            fonte = criar_fonte(args.fonte, arquivo=args.arquivo, mapa=args.mapa)
        if banco is None:
            banco = criar_banco()
        ctx = ContextoColeta(
            tipo=tipo,
            referencia=referencia,
            municipios=municipios,
        )
        resultado = executar(banco, fonte, ctx)
    except (ColetaErro, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        if proprio_banco and banco is not None:
            banco.fechar()
        elif proprio_fonte and fonte is not None and hasattr(fonte, "fechar"):
            # a fonte já fecha no fim de coletar; o banco é que fica aberto
            pass
    if resultado.situacao == "completa":
        return 0
    if resultado.situacao == "incompleta":
        return 2
    return 3
