"""Gera os CSVs da carga do histórico por tipologia (relatório "variação 36 meses" do Órulo).

Uso: python3 tools/historico/exportar.py "<relatorio.xlsx>" <pasta_saida>

Saída:
  empreendimentos.csv            -> public.im_historico_orulo_empreendimentos
  tipologias_N.csv (até 60 mil)  -> public.im_historico_orulo_tipologias, em partes para o
                                    importador do Table Editor do Supabase

Lê o XML do xlsx em streaming (openpyxl seria lento para 300 mil linhas). Importar primeiro os
empreendimentos e depois as partes das tipologias, na ordem.
"""
import sys, os, re, csv, zipfile
from xml.etree.ElementTree import iterparse
import pandas as pd

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
SITUACAO = {'Lançamento': 'lancamento', 'Em construção': 'em_obras', 'Pronto': 'pronto'}
PARTE = 60000


def ler_xlsx(xlsx):
    with zipfile.ZipFile(xlsx) as z:
        ss = []
        with z.open('xl/sharedStrings.xml') as f:
            for ev, el in iterparse(f):
                if el.tag == NS + 'si':
                    ss.append(''.join(t.text or '' for t in el.iter(NS + 't'))); el.clear()

        def col(ref):
            n = 0
            for ch in re.match(r'([A-Z]+)', ref).group(1): n = n * 26 + ord(ch) - 64
            return n - 1
        linhas = []
        with z.open('xl/worksheets/sheet1.xml') as f:
            for ev, el in iterparse(f):
                if el.tag == NS + 'row':
                    cells = {}
                    for c in el.findall(NS + 'c'):
                        v = c.find(NS + 'v')
                        if v is None: continue
                        cells[col(c.get('r'))] = ss[int(v.text)] if c.get('t') == 's' else v.text
                    n = max(cells) + 1 if cells else 0
                    linhas.append([cells.get(i) for i in range(n)]); el.clear()
    cab = linhas[0]
    return pd.DataFrame([l + [None] * (len(cab) - len(l)) for l in linhas[1:]], columns=cab)


def data_excel(s):
    return pd.to_datetime(pd.to_numeric(s, errors='coerce'), unit='D', origin='1899-12-30')


def inteiro(s):
    return pd.to_numeric(s, errors='coerce').round().astype('Int64')


def numero(s):
    return pd.to_numeric(s, errors='coerce')


def main(xlsx, saida):
    d = ler_xlsx(xlsx)
    d['mes'] = data_excel(d['TIPOLOGIA_DATAATUALIZ_INICIO_MES']).dt.to_period('M').dt.to_timestamp()
    for c in ('LAT', 'LON'):
        d[c.lower()] = pd.to_numeric(d[c].astype(str).str.replace(',', '.'), errors='coerce')
    d = d[d.lat.notna() & d.lon.notna() & d.mes.notna()].copy()
    d['BID'] = inteiro(d['BID']); d['TID'] = inteiro(d['TID'])
    os.makedirs(saida, exist_ok=True)

    e = d.sort_values('mes').groupby('BID').last().reset_index()
    emp = pd.DataFrame({
        'orulo_id': e.BID, 'nome': e.EMP, 'incorporadora': e.INCORP, 'tipo': e.TIPO_EMP,
        'bairro': e.BAIRRO, 'municipio': e.CIDADE, 'uf': e.UF, 'lat': e.lat.round(6), 'lon': e.lon.round(6),
        'unidades': numero(e.UTOT), 'lancamento': data_excel(e.DLANCAMENTO).dt.date,
        'entrega': data_excel(e.DENTREGA).dt.date})
    emp.to_csv(os.path.join(saida, 'empreendimentos.csv'), index=False)

    t = pd.DataFrame({
        'orulo_tid': d.TID, 'orulo_id': d.BID, 'mes': d.mes.dt.date,
        'situacao': d.STATUS_EMP.map(SITUACAO), 'finalidade': d.FINALIDADE, 'subtipo': d.SUBTIPO,
        'area_privativa': numero(d['APRIV TIPOLOGIA']), 'dormitorios': inteiro(d.DORMS),
        'suites': inteiro(d.SUITES), 'vagas': inteiro(d.VAGAS), 'unidades': numero(d.UTOT_TIP),
        'estoque': inteiro(d['ESTOQUE TIPOLOGIA']), 'estoque_mes_anterior': inteiro(d['Estoque no mes anterior']),
        'vendas': inteiro(d.Vendas_qtd), 'distratos': inteiro(d.Distratos_qtd),
        'valor_vendas': numero(d.Venda).round(2), 'vgv_estoque': numero(d.VGV).round(2),
        'preco_m2': numero(d['PM2 Medio']).round(2)})
    dup = t.duplicated(['orulo_tid', 'mes']).sum()
    if dup: sys.exit(f'{dup} linhas repetidas de tipologia e mês')
    t = t.sort_values(['orulo_id', 'orulo_tid', 'mes'])
    for i in range(0, len(t), PARTE):
        t.iloc[i:i + PARTE].to_csv(os.path.join(saida, f'tipologias_{i // PARTE + 1}.csv'), index=False)
    print(f'{len(emp)} empreendimentos, {len(t)} linhas de tipologia em {(len(t) - 1) // PARTE + 1} partes, '
          f'{t.mes.min()} a {t.mes.max()}')


if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
