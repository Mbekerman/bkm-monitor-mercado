"""Soma o relatório de 36 meses do Órulo (tipologia × mês) por empreendimento × mês no CSV de
public.im_historico_orulo.

Uso:
  unzip relatorio.xlsx -d x && python3 ler_xlsx.py x linhas.csv
  python3 montar.py linhas.csv im_historico_orulo.csv
"""
import sys
import pandas as pd

NAO_RESIDENCIAL = {'Loja', 'Sala', 'Sala com Área Ext.'}
SITUACAO = {'Lançamento': 'lancamento', 'Em construção': 'em_obras', 'Pronto': 'pronto'}

d = pd.read_csv(sys.argv[1], low_memory=False)
d = d[~d.SUBTIPO.isin(NAO_RESIDENCIAL)]
d['mes'] = pd.to_datetime(d.TIPOLOGIA_DATAATUALIZ_INICIO_MES, unit='D', origin='1899-12-30').dt.strftime('%Y-%m-%d')
g = d.groupby(['BID', 'mes']).agg(
    situacao=('STATUS_EMP', 'first'), unidades=('UTOT_TIP', 'sum'), estoque=('ESTOQUE TIPOLOGIA', 'sum'),
    vendas=('Vendas_qtd', 'sum'), distratos=('Distratos_qtd', 'sum'), tipologias=('TID', 'nunique')).reset_index()
g['situacao'] = g.situacao.map(SITUACAO)
for c in ['unidades', 'estoque', 'vendas', 'distratos']:
    g[c] = g[c].astype(int)
g.rename(columns={'BID': 'orulo_id'})[
    ['orulo_id', 'mes', 'situacao', 'unidades', 'estoque', 'vendas', 'distratos', 'tipologias']
].to_csv(sys.argv[2], index=False)
print(len(g), 'linhas,', g.BID.nunique(), 'empreendimentos')
