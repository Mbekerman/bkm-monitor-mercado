"""Unidade que o Órulo manda duas vezes, com e sem torre."""
from coletar.mapeamento import juntar_duplicadas


def u(torre, numero, situacao="vendida", preco=None, andar=1):
    return {"torre": torre, "numero": numero, "situacao": situacao, "preco": preco,
            "preco_original": None, "andar": andar, "area_privativa_m2": 40.0}


def test_junta_bloco_colado_no_numero():
    r = juntar_duplicadas([u("Bloco 01", "101"), u(None, "101Bloco01", "disponivel", 590000.0, andar=0)])
    assert len(r) == 1
    assert (r[0]["torre"], r[0]["numero"], r[0]["situacao"], r[0]["preco"], r[0]["andar"]) == ("Bloco 01", "101", "disponivel", 590000.0, 1)


def test_junta_sigla_e_hifen():
    r = juntar_duplicadas([u("TA", "101"), u(None, "101TA"), u("B", "102", "disponivel", 1.0), u(None, "102-B")])
    assert [(x["torre"], x["numero"], x["situacao"]) for x in r] == [("TA", "101", "vendida"), ("B", "102", "disponivel")]


def test_a_venda_da_torre_fica():
    r = juntar_duplicadas([u("A", "11", "disponivel", 2.0), u(None, "11A", "vendida")])
    assert (r[0]["situacao"], r[0]["preco"]) == ("disponivel", 2.0)


def test_sem_par_ou_ambiguo_fica_como_veio():
    lista = [u(None, "305"), u("1", "1A"), u("11", "A"), u(None, "11A")]
    assert juntar_duplicadas(lista) == lista


def test_sem_torre_nenhuma_nao_mexe():
    lista = [u(None, "101"), u(None, "102")]
    assert juntar_duplicadas(lista) is lista
