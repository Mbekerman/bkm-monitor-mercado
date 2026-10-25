"""Importação em partes (coletar.importacao)."""
from __future__ import annotations

import collections

import httpx
import pytest

from coletar.erros import ColetaErro
from coletar.importacao import (
    Importador, chave_rotulo, grupos, norm_rotulo, partes, representantes, unidade_recusada,
)

TIMEOUT = ColetaErro('im_importar retornou 500: {"code":"57014","message":"canceling statement due to statement timeout"}')


def _u(torre, numero, tipo="Apartamento", **kw):
    return {"id_externo": None, "torre": torre, "numero": numero, "tipo_unidade": tipo,
            "andar": kw.get("andar"), "vagas": kw.get("vagas"), "dormitorios": kw.get("dormitorios"),
            "suites": None, "area_privativa_m2": 50.0, "preco": kw.get("preco", 500000.0),
            "preco_original": None, "situacao": kw.get("situacao", "disponivel"), "tipologia_fonte": None}


def _emp(ide, unidades):
    return {"id_externo": str(ide), "nome": f"E{ide}", "unidades": unidades}


class BancoFalso:
    """Estoura o tempo quando a chamada leva mais de `limite` unidades."""

    def __init__(self, limite=10**9, recusa=None, transitorios=0):
        self.limite = limite
        self.recusa = recusa or (lambda u: False)
        self.transitorios = transitorios
        self.chamadas = []
        self.recebidas = collections.defaultdict(list)
        self.falhas = []

    def importar(self, coleta, emps):
        if self.transitorios:
            self.transitorios -= 1
            raise httpx.ReadTimeout("lento")
        n = sum(len(e.get("unidades") or []) for e in emps)
        if n > self.limite:
            raise TIMEOUT
        self.chamadas.append([(e["id_externo"], len(e.get("unidades") or [])) for e in emps])
        erros = 0
        for e in emps:
            if any(self.recusa(u) for u in e.get("unidades") or []):
                erros += 1
                continue
            self.recebidas[e["id_externo"]].extend(e.get("unidades") or [])
        return {"coletados": len(self.recebidas), "unidades": 0, "ignorados": 0, "erros": erros}

    def anotar_falha(self, coleta, ide, motivo, detalhe):
        self.falhas.append((ide, motivo))


def _imp(banco, **kw):
    kw.setdefault("sleep", lambda s: None)
    return Importador(banco, "c1", **kw)


def test_norm_rotulo_como_o_banco():
    assert norm_rotulo("Torre A") == "A"
    assert norm_rotulo("Edifício  Bloco b") == "B"
    assert norm_rotulo("torreão 1") == "TORREAO1"
    assert norm_rotulo(None) == ""
    assert chave_rotulo("Torre A", "101") == chave_rotulo("A", "1 01")


def test_grupos_mantem_repetidas_juntas_e_na_ordem():
    us = [_u("A", "101", preco=1.0), _u("B", "1"), _u("Torre A", "101", preco=2.0), _u("C", "1")]
    gs = grupos(us)
    assert [len(g) for g in gs] == [2, 1, 1]
    assert [u["preco"] for u in gs[0]] == [1.0, 2.0]


def test_partes_cobrem_tudo_com_representantes():
    us = [_u("L", str(i), "Loja") for i in range(30)] + [_u("A", str(i)) for i in range(5)]
    us += [_u("A", "1", preco=9.0)]  # repete A/1
    us += [_u("G", "1", "Garden")]
    emp = _emp(1, us)
    ps = partes(emp, 8)
    assert len(ps) > 3
    reps_tipos = {"Loja", "Apartamento", "Garden"}
    vistos = []
    for p in ps:
        assert {u["tipo_unidade"] for u in p["unidades"]} == reps_tipos
        assert p["nome"] == "E1"
        vistos.extend(id(u) for u in p["unidades"])
    assert {id(u) for u in us} <= set(vistos)
    # A/1 e a repetida chegam juntas e na ordem original em toda parte que as leva
    for p in ps:
        a1 = [u for u in p["unidades"] if u["torre"] == "A" and u["numero"] == "1"]
        assert a1 in ([], [us[31], us[35]])
    assert partes(_emp(2, us[:5]), 8) == [_emp(2, us[:5])]
    assert partes({"id_externo": "3"}, 8) == [{"id_externo": "3"}]


def test_representantes_preferem_grupo_pequeno():
    us = [_u("A", "1"), _u("A", "1"), _u("B", "2"), _u("C", "3", "Studio")]
    gs = grupos(us)
    assert representantes(gs) == [1, 2]


def test_unidade_recusada():
    assert unidade_recusada(_u("A", "1")) is None
    assert unidade_recusada(_u("A", "1", andar=100110))
    assert unidade_recusada(_u("A", "1", vagas=-1))
    assert unidade_recusada(_u("A", "1", dormitorios=21))
    assert unidade_recusada(_u("A", "1", andar=3.0))
    assert unidade_recusada(_u("A", "1", andar="3")) is None
    assert unidade_recusada({**_u("A", "1"), "preco": "abc"})


def test_pequenos_vao_juntos_e_grande_em_partes():
    banco = BancoFalso()
    emps = [_emp(i, [_u("A", str(j)) for j in range(10)]) for i in range(5)]
    emps.append(_emp(99, [_u("A", str(j)) for j in range(95)]))
    r = _imp(banco, unidades_por_chamada=30, empreendimentos_por_chamada=50).importar(emps)
    assert r["erros"] == 0
    assert banco.chamadas[0] == [("0", 10), ("1", 10), ("2", 10)]
    assert banco.chamadas[1] == [("3", 10), ("4", 10)]
    grandes = [c for c in banco.chamadas if c[0][0] == "99"]
    assert [n for ((_, n),) in grandes] == [30, 30, 30, 8]
    assert {id(u) for u in banco.recebidas["99"]} == {id(u) for u in emps[-1]["unidades"]}


def test_tempo_estourado_encolhe_e_fica_menor():
    banco = BancoFalso(limite=40)
    emp = _emp(7, [_u("A", str(j)) for j in range(200)] + [_u("S", "1", "Studio")])
    imp = _imp(banco, unidades_por_chamada=400, minimo=5)
    imp.importar([emp])
    assert imp.tamanho <= 40
    assert {id(u) for u in banco.recebidas["7"]} == {id(u) for u in emp["unidades"]}
    assert all(n <= 40 for c in banco.chamadas for (_, n) in c)
    # sem anotação de parcial, sem erro
    assert banco.falhas == [] and imp.parciais == []


def test_lote_de_varios_parte_ao_meio_no_tempo():
    banco = BancoFalso(limite=25)
    emps = [_emp(i, [_u("A", str(j)) for j in range(10)]) for i in range(4)]
    _imp(banco, unidades_por_chamada=100).importar(emps)
    assert sorted(banco.recebidas) == ["0", "1", "2", "3"]


def test_minimo_estoura_e_anota_parcial():
    banco = BancoFalso(limite=3)
    emp = _emp(5, [_u("A", str(j)) for j in range(20)])
    imp = _imp(banco, unidades_por_chamada=10, minimo=5)
    with pytest.raises(ColetaErro):
        imp.importar([emp])


def test_transitorio_tenta_de_novo():
    banco = BancoFalso(transitorios=2)
    imp = _imp(banco, tentativas=3)
    imp.importar([_emp(1, [_u("A", "1")])])
    assert banco.recebidas["1"]
    banco = BancoFalso(transitorios=3)
    with pytest.raises(httpx.ReadTimeout):
        _imp(banco, tentativas=3).importar([_emp(1, [_u("A", "1")])])


def test_erro_que_nao_e_tempo_sobe_direto():
    class Quebra(BancoFalso):
        def importar(self, coleta, emps):
            raise ColetaErro('im_importar retornou 400: {"code":"23514","message":"A coleta não está aberta"}')
    with pytest.raises(ColetaErro, match="23514"):
        _imp(Quebra(), unidades_por_chamada=5).importar([_emp(1, [_u("A", str(j)) for j in range(20)])])


def test_unidade_recusada_falha_o_empreendimento_inteiro_de_uma_vez():
    banco = BancoFalso(recusa=lambda u: u.get("vagas") == -1)
    us = [_u("A", str(j)) for j in range(50)]
    us[30]["vagas"] = -1
    imp = _imp(banco, unidades_por_chamada=10)
    r = imp.importar([_emp(8, us)])
    assert r["erros"] == 1
    assert len(banco.chamadas) == 1
    assert banco.recebidas["8"] == []
    assert imp.parciais == []


def test_checagem_errada_segue_em_partes():
    # a checagem acha a unidade recusável, mas o banco aceita: importa tudo em partes
    banco = BancoFalso()
    us = [_u("A", str(j)) for j in range(50)]
    us[30]["andar"] = 40000
    _imp(banco, unidades_por_chamada=10).importar([_emp(8, us)])
    assert {id(u) for u in banco.recebidas["8"]} == {id(u) for u in us}


def test_parte_com_erro_anota_parcial():
    calls = {"n": 0}

    class ErroNaTerceira(BancoFalso):
        def importar(self, coleta, emps):
            calls["n"] += 1
            r = super().importar(coleta, emps)
            if calls["n"] == 3:
                r["erros"] = 1
            return r
    banco = ErroNaTerceira()
    imp = _imp(banco, unidades_por_chamada=10)
    imp.importar([_emp(4, [_u("A", str(j)) for j in range(50)])])
    assert imp.parciais == ["4"]
    assert banco.falhas == [("4", "importacao_parcial")]


def test_rodada_parte_empreendimento_grande(monkeypatch):
    from testes.test_rodada import BancoMem, FonteFixa, _ctx
    from coletar.rodada import executar

    class Banco(BancoMem):
        def importar(self, coleta, emps):
            self.lotes.append([len(e.get("unidades") or []) for e in emps])
            return {"coletados": 1, "unidades": 0, "ignorados": 0, "erros": 0}

    monkeypatch.setenv("COLETAR_UNIDADES_POR_CHAMADA", "100")
    banco = Banco()
    emps = [_emp(1, [_u("A", str(j)) for j in range(250)]), _emp(2, [_u("A", "1")]), {"id_externo": "3"}]
    resultado = executar(banco, FonteFixa(emps), _ctx())
    assert resultado.situacao == "completa"
    assert banco.lotes == [[100], [100], [52], [1, 0]]  # 250 + representante nas partes 2 e 3
