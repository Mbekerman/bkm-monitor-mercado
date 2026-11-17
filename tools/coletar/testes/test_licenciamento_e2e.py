"""Etapa dos alvarás contra o Postgres local e as funções reais (migration alvara_sistematico)."""
from __future__ import annotations

import httpx
import psycopg
import pytest

from coletar.banco import BancoPostgres
from coletar.licenciamento import rodar_alvaras
from testes.pg_local import Cluster
from testes.test_licenciamento import CSV, LOTE, OWS


@pytest.fixture(scope="module")
def cluster():
    c = Cluster()
    try:
        yield c
    finally:
        c.parar()


def test_extrato_novo_lote_conferencia_e_marcacao(cluster):
    with psycopg.connect(cluster.dsn, autocommit=True) as c:
        sp = c.execute("select id from public.im_municipios where nome = 'São Paulo'").fetchone()[0]
        inc = c.execute("insert into public.im_incorporadoras (nome) values ('Inc') returning id").fetchone()[0]
        emp = c.execute(
            "insert into public.im_empreendimentos (incorporadora_id, nome, latitude, longitude, origem, municipio_id,"
            " link_fonte, programa_social) values (%s, 'Novo HIS', -23.505, -46.605, 'importado', %s,"
            " 'https://www.orulo.com.br/buildings/888001', 'nao') returning id", (inc, sp)).fetchone()[0]
        c.execute("insert into public.im_historico_orulo_empreendimentos (orulo_id, nome, municipio, uf, lat, lon, unidades)"
                  " values (888001, 'Novo HIS', 'São Paulo', 'SP', -23.505, -46.605, 100)")
    lote_p1 = {**LOTE, "properties": {"cd_setor_fiscal": "008", "cd_quadra_fiscal": "020", "cd_lote": "0127"}}
    pedidos = []

    def h(req: httpx.Request):
        pedidos.append(str(req.url))
        if "package_show" in str(req.url):
            return httpx.Response(200, json={"result": {"resources": [
                {"id": "x2", "name": "Extrato - Aprova Digital - 2º sem./2026", "url": "https://p/x2.csv",
                 "last_modified": "2027-02-20T10:00:00"}]}})
        if req.url.path.endswith("x2.csv"):
            return httpx.Response(200, content=CSV)
        if "geosampa" in str(req.url):
            return httpx.Response(200, json={"features": [lote_p1]})
        return httpx.Response(404)

    banco = BancoPostgres(cluster.dsn, cluster.pasta_bruto)
    try:
        r = rodar_alvaras(banco, http=httpx.Client(transport=httpx.MockTransport(h)), ows=OWS)
        assert r.processos == 2 and r.lotes_gravados == 1 and not r.avisos
        assert r.marcacao["marcados"] == 1 and r.marcacao["his"] == 1 and r.marcacao["programa_social_nao"] == 1
        # segunda vez: o extrato já está carregado, nada é baixado de novo
        pedidos.clear()
        r2 = rodar_alvaras(banco, http=httpx.Client(transport=httpx.MockTransport(h)), ows=OWS)
        assert r2.extratos_novos == [] and not any("x2.csv" in p for p in pedidos)
    finally:
        banco.fechar()
    with psycopg.connect(cluster.dsn) as c:
        row = c.execute("select his_alvara, hmp_alvara, processo_alvara, programa_social from public.im_empreendimentos"
                        " where id = %s", (emp,)).fetchone()
    assert row == (True, False, "P1", "nao")
