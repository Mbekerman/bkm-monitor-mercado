"""Em partes dá o mesmo banco que uma chamada só, com as funções reais (Postgres local)."""
from __future__ import annotations

import copy
from datetime import date

import psycopg
import pytest

from coletar.banco import BancoPostgres
from coletar.importacao import Importador, chave_rotulo, norm_rotulo
from testes.pg_local import Cluster


@pytest.fixture(scope="module")
def cluster():
    c = Cluster()
    try:
        with psycopg.connect(c.dsn, autocommit=True) as conn:
            conn.execute("create database parte_a template postgres")
            conn.execute("create database parte_b template postgres")
        yield c
    finally:
        c.parar()


def _dsn(cluster, db):
    return cluster.dsn.replace("dbname=postgres", f"dbname={db}")


def _u(torre, numero, tipo="Apartamento", preco=500000.0, situacao="disponivel", **kw):
    return {"id_externo": None, "torre": torre, "numero": numero, "andar": kw.get("andar"),
            "area_privativa_m2": kw.get("area", 60.0), "dormitorios": kw.get("dormitorios", 2),
            "suites": 1, "vagas": kw.get("vagas", 1), "tipologia_fonte": None, "tipo_unidade": tipo,
            "situacao": situacao, "preco": preco, "preco_original": None}


def _emp(ide, unidades, **kw):
    return {
        "id_externo": str(ide), "nome": kw.get("nome", f"Residencial {ide}"),
        "incorporadora": {"id_externo": "900", "nome": "Incorporadora Teste", "cnpj": None},
        "endereco": "Rua A, 1", "bairro": "Centro", "municipio": "São Paulo", "uf": "SP",
        "latitude": -23.55 + ide / 1000, "longitude": -46.63, "situacao": "lancamento",
        "tipo": "vertical", "finalidade": kw.get("finalidade", "residencial"),
        "data_lancamento": "2026-08-01", "previsao_entrega": "2029-01-01", "torres": 2,
        "programa_social": "nao", "unidades_informadas": len(unidades), "estoque_informado": None,
        "link_fonte": None, "unidades": unidades,
    }


def _setembro():
    e1 = [_u("Loja", str(i), "Loja", preco=None) for i in range(40)]
    e1 += [_u("Torre A", str(100 + i), andar=1 + i // 4) for i in range(60)]
    # mesma unidade duas vezes na lista (torre escrita diferente), preços diferentes
    e1 += [_u("A", "170", preco=650000.0, situacao="reservada"), _u("Torre A", "170", preco=777000.0)]
    e1 += [_u("Torre B", f"{i}", "Studio", preco=300000.0 + i) for i in range(1, 6)]
    # duplex só nesta unidade, repetida no começo e no meio: o representante é o grupo inteiro
    e1.insert(0, _u("D", "1", "Duplex", preco=900000.0, situacao="reservada"))
    e1.append(_u("Loja", "99", "Loja", preco=None))
    # só lojas e um garden no fim: o filtro depende do garden
    e3 = [_u("L", str(i), "Loja") for i in range(45)] + [_u("G", "1", "Garden")]
    # unidade recusada pelo banco no meio (vagas negativa)
    e4 = [_u("A", str(i)) for i in range(30)]
    e4[17]["vagas"] = -1
    e5 = [_u("A", str(i)) for i in range(30)]  # comercial: fora do filtro
    e6 = [_u("A", str(i), preco=400000.0) for i in range(8)]
    return [
        _emp(1, e1[:50] + [_u("Torre D", "1", "Duplex", preco=950000.0)] + e1[50:]), _emp(3, e3), _emp(4, e4), _emp(5, e5, finalidade="comercial"), _emp(6, e6),
    ]


def _outubro():
    emps = copy.deepcopy(_setembro())
    e1 = [u for u in emps[0]["unidades"] if u["tipo_unidade"] != "Duplex"]
    for u in e1[40:60]:
        u["situacao"] = "vendida"           # vendidas
    for u in e1[60:70]:
        u["preco"] = (u["preco"] or 0) + 10000.0  # reajuste
    del e1[70:80]                           # somem (removidas no fechamento)
    e1.append(_u("Torre C", "1", "Cobertura", preco=1500000.0))
    duplex = [u for u in emps[0]["unidades"] if u["tipo_unidade"] == "Duplex"]
    emps[0]["unidades"] = [duplex[0], *e1[:50], duplex[1], *e1[50:]]
    emps[2]["unidades"][17]["vagas"] = 2    # agora entra
    emps[4]["unidades"][0]["situacao"] = "vendida"
    return emps


def _rodar(banco, ref, emps, partes):
    cid = banco.abrir("orulo_login", "completa_mensal", ref, f"mercado/{ref}.json.gz")
    if partes:
        imp = Importador(banco, cid, unidades_por_chamada=7, minimo=2, empreendimentos_por_chamada=3)
        imp.importar(emps)
    else:
        for e in emps:
            banco.importar(cid, [e])
    return banco.fechar_coleta(cid, None)


def _foto(dsn):
    with psycopg.connect(dsn) as conn:
        q = {
            "coletas": """select tipo, referencia, situacao, coletados, unidades, esperados, tipos_desconhecidos,
                                 (select jsonb_agg(f order by f->>'id_externo', f->>'motivo') from jsonb_array_elements(falhas) f)
                            from public.im_coletas c order by referencia""",
            "emps": """select i.id_externo, e.nome, e.unidades, e.situacao, e.nr, c.referencia, p.referencia
                         from public.im_empreendimentos e
                         join public.im_ids_externos i on i.entidade_id = e.id and i.entidade = 'empreendimento'
                         join public.im_coletas c on c.id = e.ultima_coleta_id
                         join public.im_coletas p on p.id = e.primeira_coleta_id order by 1""",
            "emp_estados": """select i.id_externo, s.situacao, s.no_filtro, s.unidades_informadas, d.referencia, a.referencia
                                from public.im_empreendimento_estados s
                                join public.im_ids_externos i on i.entidade_id = s.empreendimento_id
                                join public.im_coletas d on d.id = s.desde_coleta_id
                                left join public.im_coletas a on a.id = s.ate_coleta_id order by 1, 5""",
            "unidades": """select i.id_externo, u.torre_norm, u.numero_norm, u.torre, u.numero, u.andar, u.vagas,
                                  u.tipo_unidade, u.categoria, u.residencial, u.preco_primeiro, u.preco_primeiro_em,
                                  u.preco_de_lancamento, u.marcas, p.referencia, l.referencia, r.referencia,
                                  (select v.numero_norm from public.im_unidades v where v.id = u.vinculo_pendente_id)
                             from public.im_unidades u
                             join public.im_ids_externos i on i.entidade_id = u.empreendimento_id
                             join public.im_coletas p on p.id = u.primeira_coleta_id
                             left join public.im_coletas l on l.id = u.ultima_coleta_id
                             left join public.im_coletas r on r.id = u.removida_coleta_id order by 1, 2, 3""",
            "un_estados": """select i.id_externo, u.torre_norm, u.numero_norm, s.situacao, s.preco, s.preco_original,
                                    s.marcas, d.referencia, a.referencia
                               from public.im_unidade_estados s
                               join public.im_unidades u on u.id = s.unidade_id
                               join public.im_ids_externos i on i.entidade_id = u.empreendimento_id
                               join public.im_coletas d on d.id = s.desde_coleta_id
                               left join public.im_coletas a on a.id = s.ate_coleta_id order by 1, 2, 3, 8""",
            "vendas": """select i.id_externo, u.torre_norm, u.numero_norm, v.vendida_de, v.vendida_ate, v.preco,
                                v.origem_preco, v.antes_do_monitor, c.referencia, d.referencia
                           from public.im_vendas v
                           join public.im_unidades u on u.id = v.unidade_id
                           join public.im_ids_externos i on i.entidade_id = u.empreendimento_id
                           join public.im_coletas c on c.id = v.coleta_id
                           left join public.im_coletas d on d.id = v.desfeita_coleta_id order by 1, 2, 3""",
        }
        return {k: conn.execute(v).fetchall() for k, v in q.items()}


def test_em_partes_igual_a_chamada_unica(cluster):
    fotos = {}
    for db, partes in (("parte_a", False), ("parte_b", True)):
        banco = BancoPostgres(_dsn(cluster, db), cluster.pasta_bruto)
        try:
            r1 = _rodar(banco, date(2026, 9, 1), _setembro(), partes)
            r2 = _rodar(banco, date(2026, 10, 1), _outubro(), partes)
        finally:
            banco.fechar()
        fotos[db] = (r1, r2, _foto(_dsn(cluster, db)))
    a, b = fotos["parte_a"], fotos["parte_b"]
    assert a[0] == b[0] and a[1] == b[1]
    for tabela in a[2]:
        assert a[2][tabela] == b[2][tabela], tabela
    foto = a[2]
    # o cenário exercita o que importa
    assert a[1]["situacao"] == "completa", a[1]
    assert a[1]["vendas"] > 0 and a[1]["removidas"] == 10, a[1]
    ids = {r[0] for r in foto["emps"]}
    assert "3" in ids and "5" not in ids          # garden segura o filtro; comercial fica fora
    falhas_set = foto["coletas"][0][7]
    assert any(f["id_externo"] == "4" and f["motivo"] == "erro" for f in falhas_set)
    assert "4" not in {r[0] for r in foto["emps"] if r[6].isoformat() == "2026-09-01"}
    a170 = [r for r in foto["unidades"] if r[0] == "1" and r[2] == "170"]
    assert len(a170) == 1 and a170[0][10] == 650000      # primeiro preço visto, na ordem da lista
    est = [r for r in foto["un_estados"] if r[0] == "1" and r[2] == "170" and r[8] is None]
    assert est[0][3] == "disponivel" and est[0][4] == 777000  # última gravada
    d1 = [r for r in foto["un_estados"] if r[0] == "1" and r[1] == "D" and r[8] is None]
    assert d1[0][3] == "disponivel" and d1[0][4] == 950000


def test_norm_rotulo_igual_ao_banco(cluster):
    amostras = ["Torre A", "torre a", "Edifício Bloco B", "BLOCO-B", "Torreão 1", "ED. 2", "ed2",
                "Bloco  C  Torre 3", "Ç ã", "  101  ", "", None, "S204", "T1 NR", "Edificio_x", "Ápice"]
    with psycopg.connect(cluster.dsn) as conn:
        for s in amostras:
            banco = conn.execute("select public.im_norm_rotulo(%s)", (s,)).fetchone()[0]
            assert norm_rotulo(s) == banco, s
            assert chave_rotulo(s, "1")[0] == chave_rotulo(banco, "1")[0]
