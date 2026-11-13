"""Leitura da tabela de preços: casamento de unidade + categoria, sem PDF nem rede."""
from coletar.tabela_preco import (
    Linha, categorias_da_linha, classificar, escolher_tabelas, referencia_do_nome,
)


def _l(textos, pagina=1):
    return [Linha(pagina, 10.0 * i, 10.0, t) for i, t in enumerate(textos)]


def _u(i, torre, numero, andar=None):
    return {"id": f"u{i}", "torre": torre, "torre_norm": (torre or "").replace("Torre ", ""),
            "numero": numero, "numero_norm": numero, "andar": andar, "removida": False}


def test_categorias_nao_confundem_dormitorios():
    assert [c for _, c in categorias_da_linha("HIS2-R$ 9.726,00 | Torre C | 22 | 2209")] == ["his2"]
    assert [c for _, c in categorias_da_linha("HIS - 2 DORMS | 15.762")] == []
    assert [c for _, c in categorias_da_linha("HIS-1 | 1 Dormitorio")] == ["his1"]
    assert [c for _, c in categorias_da_linha("R2V -R$ 999.999.999,00")] == ["r2v"]
    assert [c for _, c in categorias_da_linha("LOJA 01 | NR1-3 | R$")] == ["nr"]
    assert [c for _, c in categorias_da_linha("EHIS e EHMP")] == []


def test_linha_por_unidade_com_torre_e_linha_quebrada():
    uns = [_u(1, "Torre C", "2209"), _u(2, "Torre D", "2209"), _u(3, "Torre D", "1404"), _u(4, "Torre C", "1705")]
    linhas = _l([
        "HIS2-R$ 9.726,00 | Torre C | 22 | 2209 | 31,680 | R$ 247.496,75",
        "R2V-R$999.999.999,00 | Torre D | 22 | 2209 | 34,340 | R$ 299.941,09",
        "Torre D | 14 | 1404 | 31,680",
        "HIS1 - R$ 4.863,00 | R$ 232.547,54",
        "Torre C | 17 | 1705 | 34,340",
        "R2V-R$999.999.999,00 | R$ 277.781,78",
    ])
    r = classificar(linhas, uns)
    assert r.status == "ok"
    assert r.atribuicoes == {
        "u1": ("his2", "tabela_unidade"), "u2": ("r2v", "tabela_unidade"),
        "u3": ("his1", "tabela_unidade"), "u4": ("r2v", "tabela_unidade"),
    }


def test_dinheiro_com_espaco_nao_vira_unidade():
    uns = [_u(1, None, "206"), _u(2, None, "307")]
    r = classificar(_l(["0206 | HIS2 | 31,84 | R$ 361 000,00 RS 307 900,00"]), uns)
    assert r.atribuicoes == {"u1": ("his2", "tabela_unidade")}


def test_regra_por_finais_e_andares():
    uns = [_u(i, None, n, int(n[:-2])) for i, n in enumerate(["101", "102", "105", "2001", "2005", "1005"])]
    linhas = _l([
        "R$ 276000.00 | 32 | Torre A-Finais 01, 02 e 23 -01°ao 19°Andar -2 Dorms -HIS1 | 266000.00",
        "Torre A -Finais 01,02 - 20°ao 24°Andar -2 Dorms -HIS2 | 275000.00",
        "Torre A-Finais 05 (+1005) -01°ao 09°Andar -2 Dorms Com Terraco -",
        "44 | HIS2 | 275000.00",
    ])
    r = classificar(linhas, uns)
    assert r.atribuicoes["u0"] == ("his1", "tabela_grupo")
    assert r.atribuicoes["u1"] == ("his1", "tabela_grupo")
    assert r.atribuicoes["u2"] == ("his2", "tabela_grupo")
    assert r.atribuicoes["u3"] == ("his2", "tabela_grupo")
    assert "u4" not in r.atribuicoes  # final 05 no 20º andar: fora da regra
    assert r.atribuicoes["u5"] == ("his2", "tabela_grupo")  # (+1005)


def test_titulo_da_tabela_vale_para_as_linhas():
    uns = [_u(1, None, "U14"), _u(2, None, "U101")]
    linhas = _l(["Tabela: Tabela de Vendas - HiS2 - Out/26", "U-0014 | 25,04 | Disponivel"]) + \
        _l(["Tabela:Tabela de Vendas -R2V-Out/26", "U-0101 | 38,30 | Disponivel"], pagina=2)
    r = classificar(linhas, uns)
    assert r.atribuicoes == {"u1": ("his2", "tabela_unidade"), "u2": ("r2v", "tabela_unidade")}


def test_sem_categoria_e_nota_geral():
    uns = [_u(1, None, "101")]
    assert classificar(_l(["101 | 35,2 | R$ 260.000,00"]), uns).status == "sem_categoria"
    r = classificar(_l(["Observação: todas as unidades são HIS2.", "R2V não se aplica"]), uns)
    assert r.atribuicoes == {"u1": ("his2", "nota_geral")}


def test_escolher_tabelas():
    arqs = [
        {"id": "1", "type": "Apresentação", "name": "Book.pdf"},
        {"id": "2", "type": "Tabela", "name": "tabela_x_outubro_2026_disponibilidade.pdf"},
        {"id": "3", "type": "Tabela", "name": "TABELA DE PREÇO OUT26.pdf"},
        {"id": "4", "type": "Tabela", "name": "tabela_x_novembro_2026.pdf"},
    ]
    assert [a["id"] for a in escolher_tabelas(arqs)] == ["4", "3"]
    assert referencia_do_nome("tabela_x_marco_2026.pdf").isoformat() == "2026-03-01"
