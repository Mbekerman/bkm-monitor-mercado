"""Etapa das tabelas de preço com Órulo e banco falsos."""
import coletar.tabelas as tab
from coletar.erros import OruloBlocked


class Banco:
    def __init__(self, pendentes):
        self.pendentes = pendentes
        self.registros = []

    def tabelas_pendentes(self, municipios, limite=None):
        return self.pendentes

    def unidades_para_tabela(self, emp):
        return [{"id": f"{emp}-u1", "numero": "101", "numero_norm": "101", "torre": None, "removida": False}]

    def registrar_tabela_preco(self, dados):
        self.registros.append(dados)
        return {"unidades": 1, "categorias": {"his2": 1}}


class Orulo:
    def __init__(self, arquivos, bloquear_em=None):
        self.arquivos_por = arquivos
        self.bloquear_em = bloquear_em
        self.pedidos = []

    def login(self):
        pass

    def fechar(self):
        pass

    def arquivos(self, bid):
        self.pedidos.append(("det", bid))
        if bid == self.bloquear_em:
            raise OruloBlocked("429")
        return self.arquivos_por.get(bid, [])

    def url_arquivo(self, bid, fid):
        self.pedidos.append(("url", bid, fid))
        return f"https://tabelas.example/{fid}.pdf"

    def baixar(self, url):
        self.pedidos.append(("pdf", url))
        return b"%PDF"


def _leitura(status):
    def ler(pdf, unidades):
        return {"status": status, "resto_fonte": "fora_da_tabela" if status != "sem_categoria" else "tabela_sem_categoria",
                "metodo": "ocr", "paginas": 3,
                "atribuicoes": {unidades[0]["id"]: ("his2", "tabela_unidade")} if status == "ok" else {},
                "categorias_no_texto": {}, "ambiguas": 0, "conflitos": 0, "regras_grupo": 0, "nota": None}
    return ler


def test_registra_sem_tabela_ok_e_para_no_bloqueio(monkeypatch):
    monkeypatch.setattr(tab, "ler_tabela", _leitura("ok"))
    banco = Banco([
        {"empreendimento_id": "e1", "id_externo": "1", "nome": "A"},
        {"empreendimento_id": "e2", "id_externo": "2", "nome": "B"},
        {"empreendimento_id": "e3", "id_externo": "3", "nome": "C"},
        {"empreendimento_id": "e4", "id_externo": "4", "nome": "D"},
    ])
    orulo = Orulo({"2": [{"id": "9", "type": "Tabela", "name": "tabela_b_novembro_2026.pdf"}]}, bloquear_em="3")
    r = tab.rodar_tabelas(banco, orulo, ["São Paulo"])
    assert [x["status"] for x in banco.registros] == ["sem_tabela", "ok"]
    assert banco.registros[0]["resto_fonte"] == "sem_tabela"
    ok = banco.registros[1]
    assert ok["arquivo_id"] == "9" and ok["referencia"] == "2026-11-01"
    assert ok["unidades"] == [{"id": "e2-u1", "categoria": "his2", "fonte": "tabela_unidade"}]
    assert r.parou and "bloqueou" in r.parou
    assert ("det", "4") not in orulo.pedidos  # parou no bloqueio, sem tentar o resto


def test_tenta_a_segunda_tabela_quando_a_primeira_nao_tem_categoria(monkeypatch):
    chamadas = []

    def ler(pdf, unidades):
        chamadas.append(1)
        return _leitura("sem_categoria" if len(chamadas) == 1 else "ok")(pdf, unidades)

    monkeypatch.setattr(tab, "ler_tabela", ler)
    banco = Banco([{"empreendimento_id": "e1", "id_externo": "1", "nome": "A"}])
    orulo = Orulo({"1": [
        {"id": "5", "type": "Tabela", "name": "tabela_a_novembro_2026.pdf"},
        {"id": "4", "type": "Tabela", "name": "TABELA A.pdf"},
    ]})
    tab.rodar_tabelas(banco, orulo, None)
    assert len(banco.registros) == 1
    assert banco.registros[0]["arquivo_id"] == "4" and banco.registros[0]["status"] == "ok"


def test_mensal_chama_tabelas_so_com_fonte_propria(monkeypatch):
    import coletar.cli as cli
    chamadas = []
    monkeypatch.setattr(cli, "etapa_tabelas", lambda banco, municipios: chamadas.append(municipios))
    monkeypatch.setenv("COLETAR_TABELAS", "0")
    # Com fonte injetada (testes), a etapa não roda.
    class Fonte:
        nome = "orulo_login"

        def coletar(self, ctx):
            from coletar.fonte import ResultadoFonte
            return ResultadoFonte(empreendimentos=[], bruto={"itens": []}, esperados=0, falhas=[],
                                  forcar_incompleta=False, falhou=False, motivo_falha=None)

    class BancoMin:
        coleta_aberta = None

        def abrir(self, *a):
            return "c1"

        def gravar_bruto(self, *a):
            pass

        def fechar_coleta(self, *a, **k):
            return {"situacao": "completa"}

        def agrupar(self):
            return {}

        def anotar_falha(self, *a):
            pass

    assert cli.main(["--tipo", "mensal", "--data", "2026-12-01"], fonte=Fonte(), banco=BancoMin()) == 0
    assert chamadas == []


def test_etapa_tabelas_desligada(monkeypatch):
    import coletar.cli as cli
    import coletar.tabelas as t
    monkeypatch.setenv("COLETAR_TABELAS", "0")
    monkeypatch.setattr(t, "rodar_tabelas", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia rodar")))
    cli.etapa_tabelas(object(), ["São Paulo"])
