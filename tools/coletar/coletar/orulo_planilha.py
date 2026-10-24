"""Leitor de planilha exportada pelo Órulo (xlsx ou csv).

Não há amostra real. O mapa de colunas abaixo é o ponto de partida e pode ser
trocado com --mapa (JSON campo → nome da coluna). O relatório da rodada avisa
que o leitor ainda precisa ser validado com uma planilha de verdade.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from coletar.erros import ColetaErro
from coletar.fonte import ContextoColeta, ResultadoFonte
from coletar.mapeamento import (
    area, data_iso, finalidade, inteiro, norm, preco,
    situacao_empreendimento, situacao_unidade, texto, tipo_empreendimento,
)

AVISO_PLANILHA = (
    "O leitor de planilha precisa ser validado com uma planilha real do Órulo; "
    "o mapeamento de colunas ainda não foi conferido com uma amostra."
)

# Campo do JSON da spec → nomes de coluna aceitos, o primeiro que existir no cabeçalho.
COLUNAS = {
    "id_empreendimento": ["id", "id_externo", "codigo", "id_empreendimento", "id empreendimento"],
    "nome": ["nome", "empreendimento", "nome_empreendimento"],
    "incorporadora_nome": ["incorporadora", "construtora", "incorporadora_nome"],
    "incorporadora_id": ["id_incorporadora", "incorporadora_id"],
    "endereco": ["endereco"],
    "bairro": ["bairro"],
    "municipio": ["municipio", "cidade"],
    "uf": ["uf", "estado"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lng", "long"],
    "situacao": ["situacao", "status", "estagio"],
    "tipo": ["tipo"],
    "finalidade": ["finalidade"],
    "data_lancamento": ["data_lancamento", "lancamento"],
    "previsao_entrega": ["previsao_entrega", "entrega", "data_entrega"],
    "torres": ["torres"],
    "programa_social": ["programa_social"],
    "unidades_informadas": ["unidades", "total_unidades", "unidades_informadas"],
    "estoque_informado": ["estoque", "estoque_informado"],
    "tabela_atualizada_em": ["tabela_atualizada_em", "atualizacao_tabela"],
    "link_fonte": ["link", "url", "link_fonte"],
    "torre": ["torre", "bloco"],
    "numero": ["numero", "unidade", "apartamento"],
    "andar": ["andar"],
    "area_privativa_m2": ["area_privativa_m2", "area_privativa", "area"],
    "dormitorios": ["dormitorios", "quartos", "dormitorio"],
    "suites": ["suites", "suite"],
    "vagas": ["vagas", "vaga", "garagem"],
    "tipologia_fonte": ["tipologia", "tipologia_id", "tipologia_fonte"],
    "tipo_unidade": ["tipo_unidade", "tipo da unidade"],
    "situacao_unidade": ["situacao_unidade", "status_unidade", "situacao da unidade"],
    "preco": ["preco", "valor", "preco_unidade"],
    "preco_original": ["preco_original", "preco_tabela"],
}


def carregar_mapa(caminho: str | None) -> dict:
    if not caminho:
        return {}
    data = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ColetaErro("o mapa de colunas é um objeto JSON de campo para coluna")
    return {str(k): str(v) for k, v in data.items()}


def _ler_csv(caminho: Path) -> list[dict]:
    amostra = caminho.read_text(encoding="utf-8-sig")
    if not amostra.strip():
        return []
    try:
        delim = csv.Sniffer().sniff(amostra[:2048], delimiters=",;\t").delimiter
    except csv.Error:
        delim = ","
    return list(csv.DictReader(amostra.splitlines(), delimiter=delim))


def _ler_xlsx(caminho: Path) -> list[dict]:
    try:
        import openpyxl
    except ImportError as e:
        raise ColetaErro("leitura de xlsx precisa de openpyxl") from e
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    try:
        ws = wb.active
        linhas = ws.iter_rows(values_only=True)
        cabecalho = next(linhas, None)
        if not cabecalho:
            return []
        nomes = ["" if c is None else str(c).strip() for c in cabecalho]
        saida = []
        for linha in linhas:
            if linha is None or all(c is None or str(c).strip() == "" for c in linha):
                continue
            saida.append({nomes[i]: linha[i] if i < len(linha) else None for i in range(len(nomes)) if nomes[i]})
        return saida
    finally:
        wb.close()


def ler_tabela(caminho: Path) -> list[dict]:
    sufixo = caminho.suffix.lower()
    if sufixo == ".csv":
        return _ler_csv(caminho)
    if sufixo in (".xlsx", ".xlsm"):
        return _ler_xlsx(caminho)
    raise ColetaErro("o arquivo da planilha deve ser .csv ou .xlsx")


def _resolver(headers: list[str], mapa: dict) -> dict[str, str | None]:
    por_norm = {norm(h): h for h in headers if h}
    resolved = {}
    for campo, aliases in COLUNAS.items():
        escolha = None
        nomes = [mapa[campo]] if campo in mapa else aliases
        for alias in nomes:
            if norm(alias) in por_norm:
                escolha = por_norm[norm(alias)]
                break
        resolved[campo] = escolha
    return resolved


def _celula(row: dict, col: str | None):
    if not col:
        return None
    return row.get(col)


class OruloPlanilha:
    nome = "orulo_planilha"

    def __init__(self, arquivo: str, mapa: dict | None = None):
        self.arquivo = Path(arquivo)
        self.mapa = mapa or {}

    def coletar(self, ctx: ContextoColeta) -> ResultadoFonte:
        if not self.arquivo.is_file():
            raise ColetaErro(f"arquivo não encontrado: {self.arquivo}")
        linhas = ler_tabela(self.arquivo)
        if not linhas:
            return ResultadoFonte(
                empreendimentos=[], bruto={"fonte": self.nome, "linhas": []},
                esperados=0, avisos=[AVISO_PLANILHA],
            )
        cols = _resolver(list(linhas[0].keys()), self.mapa)
        permitidos = {norm(m.nome) for m in ctx.municipios}
        grupos: dict[str, dict] = {}
        ordem: list[str] = []
        for i, row in enumerate(linhas):
            cidade = texto(_celula(row, cols["municipio"]))
            if cidade and norm(cidade) not in permitidos:
                continue
            id_ext = texto(_celula(row, cols["id_empreendimento"])) or f"linha-{i + 2}"
            if id_ext not in grupos:
                grupos[id_ext] = {"row": row, "unidades": []}
                ordem.append(id_ext)
            unidade = self._unidade(row, cols)
            if unidade is not None:
                grupos[id_ext]["unidades"].append(unidade)
        emps = []
        for id_ext in ordem:
            g = grupos[id_ext]
            emps.append(self._empreendimento(id_ext, g["row"], cols, g["unidades"]))
        return ResultadoFonte(
            empreendimentos=emps,
            bruto={"fonte": self.nome, "arquivo": self.arquivo.name, "linhas": _jsonavel(linhas)},
            esperados=len(emps),
            avisos=[AVISO_PLANILHA],
        )

    def _empreendimento(self, id_ext: str, row: dict, cols: dict, unidades: list) -> dict:
        programa = _programa(_celula(row, cols["programa_social"]))
        emp = {
            "id_externo": None if id_ext.startswith("linha-") else id_ext,
            "nome": texto(_celula(row, cols["nome"])),
            "incorporadora": {
                "id_externo": texto(_celula(row, cols["incorporadora_id"])),
                "nome": texto(_celula(row, cols["incorporadora_nome"])),
                "cnpj": None,
            },
            "endereco": texto(_celula(row, cols["endereco"])),
            "bairro": texto(_celula(row, cols["bairro"])),
            "municipio": texto(_celula(row, cols["municipio"])),
            "uf": texto(_celula(row, cols["uf"])),
            "latitude": _num_coord(_celula(row, cols["latitude"])),
            "longitude": _num_coord(_celula(row, cols["longitude"])),
            "situacao": situacao_empreendimento(_celula(row, cols["situacao"])),
            "tipo": tipo_empreendimento(_celula(row, cols["tipo"])),
            "finalidade": finalidade(_celula(row, cols["finalidade"])),
            "data_lancamento": data_iso(_celula(row, cols["data_lancamento"])),
            "previsao_entrega": data_iso(_celula(row, cols["previsao_entrega"])),
            "torres": inteiro(_celula(row, cols["torres"])),
            "programa_social": programa,
            "unidades_informadas": inteiro(_celula(row, cols["unidades_informadas"])),
            "estoque_informado": inteiro(_celula(row, cols["estoque_informado"])),
            "tabela_atualizada_em": data_iso(_celula(row, cols["tabela_atualizada_em"])),
            "link_fonte": texto(_celula(row, cols["link_fonte"])),
        }
        if unidades:
            emp["unidades"] = unidades
        return emp

    def _unidade(self, row: dict, cols: dict):
        campos = ("torre", "numero", "andar", "area_privativa_m2", "preco", "situacao_unidade", "tipo_unidade")
        if not any(texto(_celula(row, cols[c])) if c != "andar" else _celula(row, cols[c]) not in (None, "") for c in campos):
            return None
        # andar 0 conta como preenchido
        return {
            "id_externo": None,
            "torre": texto(_celula(row, cols["torre"])),
            "numero": texto(_celula(row, cols["numero"])),
            "andar": inteiro(_celula(row, cols["andar"])),
            "area_privativa_m2": area(_celula(row, cols["area_privativa_m2"])),
            "dormitorios": inteiro(_celula(row, cols["dormitorios"])),
            "suites": inteiro(_celula(row, cols["suites"])),
            "vagas": inteiro(_celula(row, cols["vagas"])),
            "tipologia_fonte": texto(_celula(row, cols["tipologia_fonte"])),
            "tipo_unidade": texto(_celula(row, cols["tipo_unidade"])),
            "situacao": situacao_unidade(_celula(row, cols["situacao_unidade"]) or _celula(row, cols["situacao"])),
            "preco": preco(_celula(row, cols["preco"])),
            "preco_original": preco(_celula(row, cols["preco_original"])),
        }


def _programa(valor) -> str:
    n = norm(valor)
    if n in ("sim", "s", "true", "1"):
        return "sim"
    if n in ("nao", "n", "false", "0"):
        return "nao"
    return "nao_informado"


def _num_coord(v):
    from coletar.mapeamento import numero
    return numero(v)


def _jsonavel(linhas: list[dict]) -> list[dict]:
    saida = []
    for row in linhas:
        item = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            else:
                item[k] = v
        saida.append(item)
    return saida
