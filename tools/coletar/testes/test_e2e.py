"""Rodada ponta a ponta contra o Postgres local e as funções reais do schema public.

Não fala com o Órulo nem com Supabase remoto: o HTTP é simulado e o banco é um cluster temporário.
"""
from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import httpx
import psycopg
import pytest

from coletar.banco import BancoPostgres
from coletar.cli import main
from coletar.fonte import ContextoColeta, Municipio
from coletar.orulo_login import OruloLogin
from coletar.rodada import executar
from testes.pg_local import Cluster

FIX = Path(__file__).parent / "fixtures"
ATRASO = (0, 0)


def _noop(_s):
    return None


@pytest.fixture(scope="module")
def cluster():
    c = Cluster()
    try:
        yield c
    finally:
        c.parar()


def _fonte(handler) -> OruloLogin:
    return OruloLogin(
        email="pessoa@bkm.test", senha="nao-e-segredo", atraso=ATRASO, sleep=_noop,
        transporte=httpx.MockTransport(handler),
    )


def _handler_mensal():
    detalhe = json.loads((FIX / "detail_83029.json").read_text())
    unidades = json.loads((FIX / "units_83029.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in ("auth.orulo.com.br", "www.orulo.com.br"):
            raise AssertionError(f"host inesperado: {host}")
        if host == "auth.orulo.com.br":
            return httpx.Response(200, json={"access_token": "tok-local"})
        if request.url.path == "/api/elastic":
            return httpx.Response(200, json={
                "buildings": [{
                    "id": 83029,
                    "name": detalhe["name"],
                    "status": detalhe["status"],
                    "stage": detalhe["stage"],
                    "type": detalhe["type"],
                    "finality": detalhe["finality"],
                    "address": detalhe["address"],
                }],
                "quantity_found": 1,
            })
        if request.url.path == "/api/portal/buildings/83029":
            return httpx.Response(200, json=detalhe)
        if request.url.path == "/api/portal/buildings/83029/units":
            return httpx.Response(200, json=unidades)
        return httpx.Response(404)

    return handler


def _handler_lancamentos(urls: list[str]):
    detalhe = json.loads((FIX / "detail_83029.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url.path)
        if request.url.host == "auth.orulo.com.br":
            return httpx.Response(200, json={"access_token": "tok-local"})
        if request.url.path == "/api/elastic":
            return httpx.Response(200, json={
                "buildings": [{
                    "id": 83029,
                    "name": detalhe["name"],
                    "status": detalhe["status"],
                    "stage": detalhe["stage"],
                    "type": detalhe["type"],
                    "finality": detalhe["finality"],
                    "address": detalhe["address"],
                    "total_units": detalhe["total_units"],
                    "stock": detalhe["stock"],
                }],
                "quantity_found": 1,
            })
        if "units" in request.url.path:
            raise AssertionError("lançamentos de empreendimento já conhecido não pede unidades")
        return httpx.Response(404)

    return handler


def test_rodada_contra_as_funcoes_do_banco(cluster):
    banco = BancoPostgres(cluster.dsn, cluster.pasta_bruto)
    try:
        ctx = ContextoColeta(
            tipo="completa_mensal",
            referencia=date(2026, 10, 24),
            municipios=[Municipio("São Paulo", "SP", (-23.80, -46.83, -23.35, -46.36))],
        )
        codigo = main(
            ["--tipo", "mensal", "--data", "2026-10-24", "--fonte", "orulo_login",
             "--municipio", "São Paulo"],
            fonte=_fonte(_handler_mensal()), banco=banco,
        )
        assert codigo == 0
        with psycopg.connect(cluster.dsn, autocommit=True) as conn:
            coleta = conn.execute(
                """
                select situacao, coletados, unidades, bruto_caminho, esperados
                  from public.im_coletas where tipo = 'completa_mensal'
                """,
            ).fetchone()
            assert coleta[0] == "completa"
            assert coleta[1] == 1
            assert coleta[2] == 574
            assert coleta[3] == "mercado/2026-10-24-completa_mensal.json.gz"
            assert coleta[4] == 1
            tipos = conn.execute(
                """
                select count(*) filter (where tipo_unidade = 'apartamento'),
                       count(*) filter (where situacao = 'vendida'),
                       count(*) filter (where situacao = 'disponivel')
                  from public.im_unidades u
                  join public.im_unidade_estados e on e.unidade_id = u.id and e.ate_coleta_id is null
                """,
            ).fetchone()
            assert tipos == (574, 119, 455)
            vendas = conn.execute(
                "select count(*) from public.im_vendas where antes_do_monitor",
            ).fetchone()[0]
            assert vendas == 119
            sit = conn.execute(
                """
                select situacao, no_filtro from public.im_empreendimento_estados s
                  join public.im_ids_externos i on i.entidade_id = s.empreendimento_id
                 where i.id_externo = '83029' and s.ate_coleta_id is null
                """,
            ).fetchone()
            assert sit == ("lancamento", True)
            guardado = conn.execute(
                "select name from storage.objects where bucket_id = 'im-bruto' and name = %s",
                (coleta[3],),
            ).fetchone()
            assert guardado is not None
        bruto = json.loads(gzip.decompress((cluster.pasta_bruto / coleta[3]).read_bytes()))
        assert bruto["itens"][0]["id"] == "83029"
        assert bruto["itens"][0]["detalhe"]["name"] == "Ares do Horto Pau Brasil"

        urls = []
        ctx_l = ContextoColeta(
            tipo="lancamentos",
            referencia=date(2026, 10, 27),
            municipios=ctx.municipios,
        )
        resultado = executar(banco, _fonte(_handler_lancamentos(urls)), ctx_l)
        assert resultado.situacao == "completa"
        assert resultado.vendas == 0
        assert not any("units" in u for u in urls)
        with psycopg.connect(cluster.dsn, autocommit=True) as conn:
            ainda = conn.execute(
                """
                select s.no_filtro from public.im_empreendimento_estados s
                  join public.im_ids_externos i on i.entidade_id = s.empreendimento_id and i.id_externo = '83029'
                 where s.ate_coleta_id is null
                """,
            ).fetchone()[0]
            assert ainda is True
    finally:
        banco.fechar()
