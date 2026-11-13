"""Lote fiscal de cada empreendimento de São Paulo, pelo ponto, no GeoSampa.

Uso: python3 tools/licenciamento/lotes.py <empreendimentos.csv> <saida.csv> [url_ows]

Entrada: CSV com orulo_id, municipio, lat, lon. Só os de São Paulo são consultados.
Saída: orulo_id, lote (setor+quadra+lote, 10 dígitos), distancia_m (0 = o ponto cai dentro do lote).

O lote é o que contém o ponto; sem lote embaixo (ponto na rua ou na calçada), o mais próximo a
até 30 m. O GeoSampa só responde a navegador vindo do próprio site: a consulta passa pelo proxy
/geosampa do BKM Terrenos (url_ows), como no sistema.
"""
import csv, json, math, sys, urllib.parse, urllib.request
import concurrent.futures as cf

OWS = 'https://terrenos-web-469931692112.southamerica-east1.run.app/geosampa/geoserver/geoportal/ows'
RAIO_M = 30


def consulta(ows, lat, lon, filtro):
    p = dict(service='WFS', version='2.0.0', request='GetFeature', outputFormat='application/json',
             typeNames='geoportal:lote_cidadao', srsName='EPSG:4326', count='50',
             propertyName='cd_setor_fiscal,cd_quadra_fiscal,cd_lote,ge_poligono', CQL_FILTER=filtro)
    return json.load(urllib.request.urlopen(ows + '?' + urllib.parse.urlencode(p), timeout=60))['features']


def dist_m(lat, lon, geom):
    """Distância aproximada do ponto ao vértice/aresta mais próximo do polígono (em metros)."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110570
    px, py = lon * kx, lat * ky
    best = 1e9
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
    for poly in polys:
        for anel in poly:
            pts = [(x * kx, y * ky) for x, y in anel]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx, dy = x2 - x1, y2 - y1
                t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
                best = min(best, math.hypot(px - x1 - t * dx, py - y1 - t * dy))
    return best


def lote(ows, e):
    lat, lon = float(e['lat']), float(e['lon'])
    pt = f'SRID=4326;POINT({lon} {lat})'
    ultimo = None
    for _ in range(3):
        try:
            fs = consulta(ows, lat, lon, f'INTERSECTS(ge_poligono,{pt})')
            if fs:
                p = fs[0]['properties']; return e['orulo_id'], p['cd_setor_fiscal'] + p['cd_quadra_fiscal'] + p['cd_lote'], 0
            fs = consulta(ows, lat, lon, f'DWITHIN(ge_poligono,{pt},{RAIO_M},meters)')
            if not fs: return e['orulo_id'], None, None
            d, f = min(((dist_m(lat, lon, f['geometry']), f) for f in fs), key=lambda x: x[0])
            if d > RAIO_M: return e['orulo_id'], None, None
            p = f['properties']; return e['orulo_id'], p['cd_setor_fiscal'] + p['cd_quadra_fiscal'] + p['cd_lote'], round(d, 1)
        except Exception as x:
            ultimo = x
    return e['orulo_id'], 'erro', str(ultimo)[:100]


def main(entrada, saida, ows=OWS):
    es = [e for e in csv.DictReader(open(entrada)) if e['municipio'] == 'São Paulo']
    with open(saida, 'w', newline='') as f, cf.ThreadPoolExecutor(8) as ex:
        w = csv.writer(f); w.writerow(['orulo_id', 'lote', 'distancia_m'])
        for i, r in enumerate(ex.map(lambda e: lote(ows, e), es)):
            w.writerow(r)
            if i % 500 == 0: print(i, r, flush=True)


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4): sys.exit(__doc__)
    main(*sys.argv[1:])
