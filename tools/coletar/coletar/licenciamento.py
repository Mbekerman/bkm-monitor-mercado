"""Etapa dos alvarás: processos de licenciamento de São Paulo e a marcação HIS/HMP no empreendimento.

Roda depois da coleta mensal, só para São Paulo (cli.etapa_alvaras):

1. Lista os extratos do Aprova Digital nos dados abertos da prefeitura (conjunto "ad", semestral).
   Só baixa extrato novo ou modificado desde a última carga (im_licenciamento_extratos) e grava
   os processos de edificação nova (os que trazem unidades) em im_licenciamento_processos.
2. Acha o lote fiscal (GeoSampa, pelo proxy /geosampa do BKM Terrenos, como o sistema faz) dos
   empreendimentos de São Paulo que ainda não têm (im_lotes_pendentes). Poucos por mês, dois
   pedidos por vez; 403/429 para a consulta e avisa, sem trocar de IP nem de caminho.
3. Refaz a conferência Órulo × processo (im_conferir_unidades) e leva HIS/HMP do processo para o
   cadastro do empreendimento (im_marcar_alvara). programa_social e as unidades não mudam.

São PROCESSOS PROTOCOLADOS (unidades pedidas), não alvarás emitidos: a base aberta de alvarás
aprovados foi descontinuada em 2019. Mesma regra de tools/licenciamento/processos.py, sem pandas.

Uso manual:
  python -m coletar.licenciamento [--pasta DIR_COM_EXTRATOS] [--sem-lotes]
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import io
import logging
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

log = logging.getLogger("coletar")

CKAN = "https://dados.prefeitura.sp.gov.br/api/3/action/package_show?id=ad"
OWS = "https://terrenos-web-469931692112.southamerica-east1.run.app/geosampa/geoserver/geoportal/ows"
RAIO_M = 30
LOTE_GRAVACAO = 500

SINONIMOS = {
    "processo": ["processo"],
    "protocolo": ["protocolo"],
    "relacionado": ["processoprotocolo_relacionado", "processo_relacionado", "processo_vinculado"],
    "data_autuacao": ["data_autuacao"],
    "sql": ["sql", "sql_incra"],
    "assunto": ["assunto"],
    "endereco": ["endereco", "enderecos_concatenados"],
    "categoria": ["categoria", "categoria_uso"],
    "subcategoria": ["subcategoria_concatenado", "subcategoria"],
    "area_terreno": ["area_terreno"],
    "area_construir": ["area_construir"],
    "blocos": ["blocos"],
    "pavimentos": ["pavimentos"],
    "unidades": ["unidades_residenciais"],
}


class Bloqueio(Exception):
    """O GeoSampa (pelo proxy) respondeu 403/429: a consulta de lotes para."""


@dataclass
class Resumo:
    extratos_novos: list[str] = field(default_factory=list)
    processos: int = 0
    lotes_pendentes: int = 0
    lotes_gravados: int = 0
    lotes_erro: int = 0
    conferencia: dict | None = None
    marcacao: dict | None = None
    avisos: list[str] = field(default_factory=list)


# --- extratos -----------------------------------------------------------------------------

def chave(c: str) -> str:
    c = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\W", "", c)


def _texto(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def ler(nome: str, dados: bytes) -> list[dict]:
    """Linhas do extrato (CSV com ; ou xlsx) como dicionários de texto."""
    if nome.lower().endswith(".xlsx") or dados[:2] == b"PK":
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        linhas = ws.iter_rows(values_only=True)
        cab = [(_texto(c) or f"col{i}") for i, c in enumerate(next(linhas, []))]
        out = [{cab[i]: _texto(v) for i, v in enumerate(row) if i < len(cab)} for row in linhas]
        wb.close()
        return out
    for enc in ("utf-8-sig", "latin1"):
        try:
            texto = dados.decode(enc)
        except UnicodeDecodeError:
            continue
        return [{k: _texto(v) for k, v in row.items() if k is not None}
                for row in csv.DictReader(io.StringIO(texto), delimiter=";")]
    raise ValueError(nome)


def lotes(sql) -> list[str]:
    """'008.020.0127-4 | 08602000073' -> ['0080200127', '0860200007']: setor, quadra e lote, sem o dígito."""
    out = []
    for p in re.split(r"[|,;/]", str(sql or "")):
        d = re.sub(r"\D", "", p)
        if 10 <= len(d) <= 11:
            out.append(d.zfill(11)[:10])
    return sorted(set(out))


def _data(s) -> str | None:
    s = (s or "")[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):  # já numérico (xlsx)
        return float(s)
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _inteiro_inicial(s) -> int | None:
    m = re.match(r"^\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m else None


def normalizar(linhas: list[dict], origem: str) -> list[dict]:
    """Só edificação nova, com HIS/HMP/R2v pela subcategoria, como tools/licenciamento/processos.py."""
    if not linhas:
        return []
    cols = {chave(c): c for c in linhas[0].keys()}
    mapa = {alvo: next((cols[n] for n in nomes if n in cols), None) for alvo, nomes in SINONIMOS.items()}
    out = []
    for row in linhas:
        r = {alvo: (row.get(c) if c else None) for alvo, c in mapa.items()}
        ass = r["assunto"] or ""
        if not (re.search("edifica", ass, re.I) and re.search("nova", ass, re.I)) or not r["processo"]:
            continue
        sub = r["subcategoria"] or ""
        un = r["unidades"]
        out.append({
            "processo": r["processo"], "protocolo": r["protocolo"], "relacionado": r["relacionado"],
            "data_autuacao": _data(r["data_autuacao"]), "assunto": ass, "endereco": r["endereco"],
            "categoria": r["categoria"], "subcategoria": r["subcategoria"],
            "his": "HIS" in sub, "hmp": "HMP" in sub, "r2v": bool(re.search("r2v", sub, re.I)),
            "social_no_assunto": bool(re.search(r"\(HIS", ass, re.I)) and not re.search("exceto", ass, re.I),
            "modificativo": bool(re.search("modificativo", ass, re.I)),
            "unidades": int(un) if un and re.fullmatch(r"\s*\d+\s*", str(un)) else None,
            "area_terreno": _num(r["area_terreno"]), "area_construir": _num(r["area_construir"]),
            "blocos": _inteiro_inicial(r["blocos"]), "pavimentos": _inteiro_inicial(r["pavimentos"]),
            "lotes": lotes(r["sql"]), "origem": origem,
        })
    return juntar(out)


def juntar(procs: list[dict]) -> list[dict]:
    """O mesmo processo em várias linhas: soma os lotes; o resto vale pela linha mais recente."""
    por: dict[str, dict] = {}
    for p in sorted(procs, key=lambda x: x["data_autuacao"] or ""):
        ant = por.get(p["processo"])
        if ant:
            p = {**ant, **{k: v for k, v in p.items() if v is not None}, "lotes": sorted(set(ant["lotes"]) | set(p["lotes"]))}
        por[p["processo"]] = p
    return list(por.values())


def extratos_publicados(http: httpx.Client) -> list[dict]:
    r = http.get(CKAN, timeout=60)
    r.raise_for_status()
    out = []
    for rec in r.json()["result"]["resources"]:
        if "extrato" not in (rec.get("name") or "").lower():
            continue
        out.append({"recurso_id": rec["id"], "nome": rec["name"], "url": rec["url"],
                    "modificado_em": rec.get("last_modified") or rec.get("created")})
    return out


def _novo(ext: dict, carregados: dict[str, dict]) -> bool:
    ant = carregados.get(ext["recurso_id"])
    if not ant:
        return True
    def ts(v):
        if not v:
            return None
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None, microsecond=0)
    a, b = ts(ant.get("modificado_em")), ts(ext.get("modificado_em"))
    return b is not None and (a is None or b > a)


def carregar_extratos(banco, http: httpx.Client, resumo: Resumo, pasta: str | None = None) -> None:
    carregados = {e["recurso_id"]: e for e in banco.extratos_carregados()}
    if pasta:
        fontes = [{"recurso_id": f"local:{f}", "nome": f, "url": f"file://{f}", "modificado_em": None,
                   "_dados": open(os.path.join(pasta, f), "rb").read()}
                  for f in sorted(os.listdir(pasta)) if re.search(r"\.(csv|xlsx)$", f)]
    else:
        fontes = [e for e in extratos_publicados(http) if _novo(e, carregados)]
    if not fontes:
        log.info("alvarás: nenhum extrato novo do Aprova Digital")
        return
    for ext in fontes:
        dados = ext.pop("_dados", None)
        if dados is None:
            r = http.get(ext["url"], timeout=600, follow_redirects=True)
            r.raise_for_status()
            dados = r.content
        procs = normalizar(ler(ext["url"].rsplit("/", 1)[-1], dados), ext["nome"])
        for i in range(0, len(procs), LOTE_GRAVACAO):
            banco.registrar_processos(procs[i:i + LOTE_GRAVACAO])
        banco.registrar_extrato({**ext, "processos": len(procs)})
        resumo.extratos_novos.append(ext["nome"])
        resumo.processos += len(procs)
        log.info("alvarás: extrato %s, %s processos de edificação nova", ext["nome"], len(procs))


# --- lotes (GeoSampa pelo proxy do BKM Terrenos) ---------------------------------------------

def _consulta(http: httpx.Client, ows: str, filtro: str) -> list[dict]:
    p = dict(service="WFS", version="2.0.0", request="GetFeature", outputFormat="application/json",
             typeNames="geoportal:lote_cidadao", srsName="EPSG:4326", count="50",
             propertyName="cd_setor_fiscal,cd_quadra_fiscal,cd_lote,ge_poligono", CQL_FILTER=filtro)
    r = http.get(ows, params=p, timeout=60)
    if r.status_code in (403, 429):
        raise Bloqueio(f"GeoSampa respondeu {r.status_code}")
    r.raise_for_status()
    return r.json().get("features", [])


def dist_m(lat: float, lon: float, geom: dict) -> float:
    """Distância aproximada do ponto à aresta mais próxima do polígono (em metros)."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110570
    px, py = lon * kx, lat * ky
    best = 1e9
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        for anel in poly:
            pts = [(x * kx, y * ky) for x, y in anel]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx, dy = x2 - x1, y2 - y1
                t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
                best = min(best, math.hypot(px - x1 - t * dx, py - y1 - t * dy))
    return best


def _cod(p: dict) -> str:
    return f"{p['cd_setor_fiscal']}{p['cd_quadra_fiscal']}{p['cd_lote']}"


def achar_lote(http: httpx.Client, ows: str, e: dict) -> dict | None:
    """{orulo_id, lote, distancia_m}; lote nulo = nada a até 30 m. None = erro (tenta no mês seguinte)."""
    lat, lon = float(e["lat"]), float(e["lon"])
    pt = f"SRID=4326;POINT({lon} {lat})"
    for _ in range(3):
        try:
            fs = _consulta(http, ows, f"INTERSECTS(ge_poligono,{pt})")
            if fs:
                return {"orulo_id": e["orulo_id"], "lote": _cod(fs[0]["properties"]), "distancia_m": 0}
            fs = _consulta(http, ows, f"DWITHIN(ge_poligono,{pt},{RAIO_M},meters)")
            if not fs:
                return {"orulo_id": e["orulo_id"], "lote": None, "distancia_m": None}
            d, f = min(((dist_m(lat, lon, f["geometry"]), f) for f in fs), key=lambda x: x[0])
            if d > RAIO_M:
                return {"orulo_id": e["orulo_id"], "lote": None, "distancia_m": None}
            return {"orulo_id": e["orulo_id"], "lote": _cod(f["properties"]), "distancia_m": round(d, 1)}
        except Bloqueio:
            raise
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    return None


def consultar_lotes(banco, http: httpx.Client, resumo: Resumo, ows: str = OWS, workers: int = 2) -> None:
    pend = banco.lotes_pendentes()
    resumo.lotes_pendentes = len(pend)
    if not pend:
        return
    achados = []
    with cf.ThreadPoolExecutor(max(1, workers)) as ex:
        futuros = [ex.submit(achar_lote, http, ows, e) for e in pend]
        for fut in futuros:
            try:
                r = fut.result()
            except Bloqueio as b:
                resumo.avisos.append(f"lotes parados: {b}")
                for f in futuros:
                    f.cancel()
                break
            if r is None:
                resumo.lotes_erro += 1
            else:
                achados.append(r)
    if achados:
        banco.registrar_lotes(achados)
    resumo.lotes_gravados = len(achados)
    log.info("alvarás: lotes de %s empreendimentos novos de SP, %s gravados, %s com erro",
             len(pend), len(achados), resumo.lotes_erro)


# --- etapa ------------------------------------------------------------------------------------

def rodar_alvaras(banco, *, http: httpx.Client | None = None, ows: str | None = None, pasta: str | None = None,
                  lotes_ligados: bool = True) -> Resumo:
    resumo = Resumo()
    proprio = http is None
    http = http or httpx.Client(headers={"User-Agent": "bkm-monitor-mercado/1.0"}, timeout=60)
    try:
        try:
            carregar_extratos(banco, http, resumo, pasta)
        except Exception as e:  # noqa: BLE001 — sem extrato novo, segue com a conferência
            resumo.avisos.append(f"extratos: {type(e).__name__}: {e}")
        if lotes_ligados:
            try:
                consultar_lotes(banco, http, resumo, ows or os.environ.get("LICENCIAMENTO_OWS", OWS))
            except Exception as e:  # noqa: BLE001
                resumo.avisos.append(f"lotes: {type(e).__name__}: {e}")
        resumo.conferencia = banco.conferir_unidades()
        resumo.marcacao = banco.marcar_alvara()
    finally:
        if proprio:
            http.close()
    for a in resumo.avisos:
        log.warning("AVISO: alvarás: %s", a)
    log.info("alvarás: conferência %s; marcação %s", resumo.conferencia, resumo.marcacao)
    return resumo


def main(argv=None) -> int:
    import argparse
    from coletar.cli import criar_banco
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="coletar.licenciamento", description="Alvarás de SP -> marcação HIS/HMP")
    p.add_argument("--pasta", default=None, help="pasta com extratos já baixados (.csv/.xlsx)")
    p.add_argument("--sem-lotes", action="store_true", help="não consulta lotes no GeoSampa")
    a = p.parse_args(argv)
    banco = criar_banco()
    try:
        r = rodar_alvaras(banco, pasta=a.pasta, lotes_ligados=not a.sem_lotes)
    finally:
        banco.fechar()
    return 1 if r.avisos else 0


if __name__ == "__main__":
    raise SystemExit(main())
