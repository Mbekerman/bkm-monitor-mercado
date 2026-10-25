"""Andar e vagas: o Órulo erra o `floor` e usa `parking: 0` para "não informado"."""
from __future__ import annotations

import pytest

from coletar.mapeamento import (
    andar_unidade, declara_sem_vaga, mapear_empreendimento, mapear_unidade, vagas_informadas, vagas_unidade,
)


def _u(reference, floor=None, parking=0, section=None):
    return {"reference": reference, "floor": floor, "parking": parking, "section": section,
            "status": "Disponível", "price": 300000}


@pytest.mark.parametrize("ref,floor,maximo,esperado", [
    ("1708", 17, 20, 17),            # bate com o prédio
    ("10001TORREB", 100, 15, None),  # Spin Zona Sul: torre no número, 100 > 15 pavimentos
    ("0907", 90, 15, None),          # zero à esquerda: não dá para tirar o andar com segurança
    ("0907", 90, None, None),        # sem pavimentos: 90 > 45 e o número é ambíguo
    ("811TC", 81, 20, None),         # letras no número
    ("207", 20, 14, 2),              # 20 > 14+1: cai para o número, 207 -> andar 2
])
def test_andar(ref, floor, maximo, esperado):
    assert andar_unidade(_u(ref, floor), maximo) == esperado


def test_andar_cobertura_mais_um():
    assert andar_unidade(_u("1601", 16), 15) == 16
    assert andar_unidade(_u("1701", 17), 15) is None


def test_andar_sem_pavimentos_usa_45():
    assert andar_unidade(_u("4501", 45), None) == 45
    assert andar_unidade(_u("4601", 46), None) is None
    assert andar_unidade(_u("1708", 17), None) == 17


def test_andar_terreo():
    assert andar_unidade(_u("1", 0), 4) == 0


def test_andar_negativo_sem_marca_nao_deriva_do_numero():
    # 101 com floor -1 e 201 com floor -2 são sinal invertido, não subsolo.
    assert andar_unidade(_u("101", -1), 17) is None
    assert andar_unidade(_u("201", -2), 20) is None


def test_andar_subsolo_explicito():
    assert andar_unidade(_u("S1", -1), 17) == -1
    assert andar_unidade(_u("-1", -1), 17) == -1
    assert andar_unidade(_u("101", -1, section="Subsolo"), 17) == -1


def test_andar_subsolo_deriva_da_marca_quando_o_floor_nao_bate():
    assert andar_unidade(_u("S2", -1), 17) == -2
    assert andar_unidade(_u("SS", -4), 17) == -1
    assert andar_unidade(_u("-101", -1), 17) == -1
    assert andar_unidade(_u("12", 3, section="Subsolo"), 10) == 3


@pytest.mark.parametrize("ref,floor,maximo,esperado", [
    ("101S", -1, 9, -1),     # Poente Flor de Maio: S colado no fim
    ("201S", -1, 9, -2),     # o número manda: 201 é -2
    ("S204", -2, 1, -2),     # Mitz Vila Sônia - NR
    ("S114", -1, 1, -1),
    ("s101", -1, 9, -1),     # minúscula também vale
    ("S0105", -1, 9, -1),    # 4 dígitos com zero: andar 01
    ("S08", -1, 9, -1),      # 2 dígitos: o número não traz o andar, vale o floor
    ("S08", -9, 9, None),    # ...desde que plausível
    ("S1", -3, 9, -1),       # 1 dígito é o próprio nível
    ("S1204", -1, 20, None), # -12: fundo demais, vazio
    ("11S04", -1, 9, None),  # Metrocasa: S no meio do número é ambíguo
    ("U1S05", -1, 15, None),
    ("-1201", -1, 20, None),
])
def test_andar_s_colado_no_numero(ref, floor, maximo, esperado):
    assert andar_unidade(_u(ref, floor), maximo) == esperado


@pytest.mark.parametrize("ref,floor,section,esperado", [
    ("S703", 7, None, 7),           # Netcorp Tower - Studios: S de studio, floor positivo
    ("S2001", 2, None, 2),
    ("101", -1, "TORRE SUL", None), # S dentro de palavra não é marca
    ("101", -1, "SALA", None),
    ("STUDIO101", -1, None, None),
    ("101", -1, "BLOCO 02", None),
])
def test_andar_s_que_nao_e_subsolo(ref, floor, section, esperado):
    assert andar_unidade(_u(ref, floor, section=section), 20) == esperado


def test_andar_vazio_continua_vazio():
    assert andar_unidade(_u("1708", None), 20) is None


def test_andar_derivado_tambem_precisa_caber():
    # 1708 daria 17, mas o prédio tem 10 pavimentos
    assert andar_unidade(_u("1708", 170), 10) is None


def test_vagas_unidade_com_vaga_sempre_vale():
    assert vagas_unidade(_u("1", parking=2), False) == 2
    assert vagas_unidade(_u("1", parking=2), True) == 2


def test_vagas_zero_so_vale_com_cadastro():
    assert vagas_unidade(_u("1", parking=0), True) == 0
    assert vagas_unidade(_u("1", parking=0), False) is None
    assert vagas_unidade(_u("1", parking=None), True) is None


def test_vagas_informadas_pelas_unidades():
    assert vagas_informadas({}, [_u("1", parking=0), _u("2", parking=1)]) is True
    assert vagas_informadas({"max_parking": 0}, [_u("1"), _u("2")]) is False


def test_vagas_informadas_pela_tipologia_ou_predio():
    assert vagas_informadas({"typologies": [{"id": 1, "parking": 1}]}, [_u("1")]) is True
    assert vagas_informadas({"max_parking": 2}, [_u("1")]) is True


@pytest.mark.parametrize("texto,sim", [
    ("Lazer completo. Sem vaga. Preço sujeito a alteração.", True),
    ("*Algumas unidades não possuem vagas.", True),
    ("Este empreendimento não possui garagem", True),
    ("Nenhuma vaga de garagem", True),
    ("Plantas de 1 a 3 dormitórios, com ou sem vaga", True),
    ("Vaga de garagem coberta", False),
    ("Sem varanda", False),
])
def test_texto_sem_vaga(texto, sim):
    assert declara_sem_vaga({"description": texto}) is sim
    assert vagas_informadas({"description": texto}, [_u("1")]) is sim


def test_texto_sem_vaga_em_features():
    assert declara_sem_vaga({"features": ["Piscina", "Sem vaga de garagem"]}) is True


def test_mapear_empreendimento_aplica_regras():
    detalhe = {"id": 80453, "name": "Spin Zona Sul", "number_of_floors": 15, "max_parking": 0,
               "typologies": [{"id": 1, "parking": 0}]}
    uns = [_u("10001TORREB", 100, 0, "B"), _u("1504", 15, 0, "A"), _u("0907", 90, 0, "A")]
    emp = mapear_empreendimento(None, detalhe, uns, incluir_unidades=True)
    assert [u["andar"] for u in emp["unidades"]] == [None, 15, None]
    # prédio inteiro zerado: zero vira não informado
    assert [u["vagas"] for u in emp["unidades"]] == [None, None, None]

    uns[1]["parking"] = 1
    emp = mapear_empreendimento(None, detalhe, uns, incluir_unidades=True)
    assert [u["vagas"] for u in emp["unidades"]] == [0, 1, 0]


def test_mapear_unidade_sem_contexto():
    u = mapear_unidade(_u("1708", 17, 0))
    assert u["andar"] == 17 and u["vagas"] is None
    u = mapear_unidade(_u("1708", 17, 0), vagas_cadastradas=True)
    assert u["vagas"] == 0
