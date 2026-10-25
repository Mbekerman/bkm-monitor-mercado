"""O fechamento em conjunto (20261025160056) dá o mesmo banco que o fechamento com laço.

Duas bases: uma só com as migrations até 20261025130000 (versão anterior) e outra com
todas. As mesmas três mensais e uma de lançamentos rodam nas duas; vendas (vista,
estimada pela mediana, desfeita), vínculo, remoção e histórico de estado têm de bater.
"""
from __future__ import annotations

from datetime import date

import pytest

from coletar.banco import BancoPostgres
from coletar.importacao import Importador
from testes.pg_local import Cluster
from testes.test_importacao_e2e import _emp, _foto, _u


@pytest.fixture(scope="module")
def clusters():
    velho, novo = Cluster(ate="20261025130000_nr_andar_vagas.sql"), Cluster()
    try:
        yield velho, novo
    finally:
        velho.parar()
        novo.parar()


def _setembro():
    us = []
    # vendidas sem histórico: estimadas pela mediana (áreas parecidas, mesmos dorms/vagas)
    us += [_u("A", str(100 + i), area=60.0 + i * 0.5, preco=600000.0 + i * 5000) for i in range(12)]
    us += [_u("A", str(200 + i), area=60.0 + i * 0.4, preco=None, situacao="vendida") for i in range(6)]
    # par a 3,6% de área fica fora da mediana; o de 0,7% entra
    us += [_u("E", "1", dormitorios=4, area=70.0, preco=None, situacao="vendida"),
           _u("E", "2", dormitorios=4, area=72.5, preco=2000000.0),
           _u("E", "3", dormitorios=4, area=70.5, preco=700000.0)]
    us += [_u("A", "300", area=200.0, preco=None, situacao="vendida")]          # sem par: preço nulo
    us += [_u("A", "301", area=None, preco=None, situacao="vendida")]           # sem área
    us += [_u("B", str(i), dormitorios=3, vagas=2, area=90.0, preco=900000.0) for i in range(5)]
    us += [_u("B", "10", dormitorios=3, vagas=2, area=90.0, preco=None, situacao="vendida")]
    us += [_u("B", "11", dormitorios=3, vagas=None, area=90.0, preco=None, situacao="vendida")]
    us += [_u("C", str(i), andar=i, area=50.0, preco=500000.0) for i in range(4)]
    us += [_u("L", "1", "Loja", preco=None, situacao="vendida")]                # não residencial
    e2 = [_u("X", str(i), preco=400000.0) for i in range(6)]
    return [_emp(1, us), _emp(2, e2)]


def _outubro():
    emps = _setembro()
    us = emps[0]["unidades"]
    for u in us[:4]:
        u["situacao"] = "vendida"                       # vendas vistas
    us[4]["situacao"] = "reservada"
    us[12]["situacao"] = "disponivel"                   # vendida em setembro volta: desfaz
    us[12]["preco"] = 610000.0
    c3 = [u for u in us if u["torre"] == "C" and u["numero"] == "3"][0]
    us.remove(c3)                                       # some e volta com outro número: vínculo
    us.append(_u("C", "3A", andar=3, area=50.0, preco=505000.0))
    c2 = [u for u in us if u["torre"] == "C" and u["numero"] == "2"][0]
    us.remove(c2)                                       # some de vez: removida
    emps[1]["unidades"][0]["situacao"] = "vendida"
    return emps


def _novembro():
    emps = _outubro()
    us = emps[0]["unidades"]
    us[4]["situacao"] = "vendida"                       # reservada que vendeu
    us[0]["situacao"] = "disponivel"                    # vendida em outubro volta
    us.append(_u("D", "1", "Cobertura", preco=None, situacao="vendida", area=150.0))
    return emps


def _rodar(banco, ref, tipo, emps, partes):
    cid = banco.abrir("orulo_login", tipo, ref, f"mercado/{ref}-{tipo}.json.gz")
    if partes:
        Importador(banco, cid, unidades_por_chamada=6, minimo=2, empreendimentos_por_chamada=2).importar(emps)
    else:
        banco.importar(cid, emps)
    r = banco.fechar_coleta(cid, None)
    return {k: r.get(k) for k in ("situacao", "vendas", "removidas")}


@pytest.mark.parametrize("partes", [False, True])
def test_fechamento_em_conjunto_igual_ao_laco(clusters, partes, tmp_path):
    fotos, rets = [], []
    for i, c in enumerate(clusters):
        db = f"f_{int(partes)}"
        import psycopg
        with psycopg.connect(c.dsn, autocommit=True) as conn:
            conn.execute(f"create database {db} template postgres")
        dsn = c.dsn.replace("dbname=postgres", f"dbname={db}")
        banco = BancoPostgres(dsn, tmp_path / str(i))
        try:
            r = [
                _rodar(banco, date(2026, 9, 1), "completa_mensal", _setembro(), partes),
                _rodar(banco, date(2026, 10, 1), "lancamentos", [_emp(2, [])], partes),
                _rodar(banco, date(2026, 10, 1), "completa_mensal", _outubro(), partes),
                _rodar(banco, date(2026, 11, 1), "completa_mensal", _novembro(), partes),
            ]
        finally:
            banco.fechar()
        rets.append(r)
        fotos.append(_foto(dsn))
    velho, novo = fotos
    assert rets[0] == rets[1]
    for tabela in velho:
        assert velho[tabela] == novo[tabela], tabela
    # o cenário passa por todos os ramos
    assert [x["situacao"] for x in rets[1]] == ["completa"] * 4
    vendas = novo["vendas"]
    assert any(v[7] and v[5] is not None for v in vendas)      # estimada com mediana
    assert any(v[7] and v[5] is None for v in vendas)          # estimada sem par
    e1 = [v for v in vendas if v[1] == "E" and v[2] == "1"]
    assert float(e1[0][5]) == round(700000.0 / 70.5 * 70.0, 2)   # só o par a até 3%
    assert any(not v[7] and v[6] == "ultimo_visto" for v in vendas)
    assert any(v[9] is not None for v in vendas)               # desfeita
    assert rets[1][2]["removidas"] >= 1
    assert any(r[17] is not None for r in novo["unidades"])    # vínculo pendente
