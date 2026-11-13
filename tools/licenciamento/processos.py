"""Processos de licenciamento de São Paulo (Aprova Digital, dados abertos da prefeitura).

Baixa os extratos semestrais do conjunto "ad" (dados.prefeitura.sp.gov.br) e grava um CSV
normalizado só com os processos de edificação nova, que são os que trazem unidades.

Uso:
  python3 tools/licenciamento/processos.py <pasta_saida> [pasta_com_extratos_já_baixados]

A base aberta é de PROCESSOS PROTOCOLADOS (data de autuação, SQL, assunto, categorias e
unidades pedidas), não de alvarás emitidos: a base de alvarás aprovados foi descontinuada em
2019 e o site da SMUL recusa acesso automatizado. Por isso o nome é "processo".
"""
import io, json, os, re, sys, urllib.request
import pandas as pd

CKAN = 'https://dados.prefeitura.sp.gov.br/api/3/action/package_show?id=ad'

SINONIMOS = {
    'processo': ['processo'],
    'protocolo': ['protocolo'],
    'relacionado': ['processoprotocolo_relacionado', 'processo_relacionado', 'processo_vinculado'],
    'data_autuacao': ['data_autuacao'],
    'sql': ['sql', 'sql_incra'],
    'assunto': ['assunto'],
    'endereco': ['endereco', 'enderecos_concatenados'],
    'categoria': ['categoria', 'categoria_uso'],
    'subcategoria': ['subcategoria_concatenado', 'subcategoria'],
    'area_terreno': ['area_terreno'],
    'area_construir': ['area_construir'],
    'blocos': ['blocos'],
    'pavimentos': ['pavimentos'],
    'unidades': ['unidades_residenciais'],
}


def chave(c):
    c = c.lower().replace('ç', 'c').replace('ã', 'a').replace('á', 'a').replace('é', 'e')
    return re.sub(r'\W', '', c)


def ler(nome, dados):
    if nome.lower().endswith('.xlsx'):
        return pd.read_excel(io.BytesIO(dados), dtype=str)
    for enc in ('utf-8-sig', 'latin1'):
        try:
            return pd.read_csv(io.BytesIO(dados), sep=';', dtype=str, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(nome)


def baixar():
    pac = json.load(urllib.request.urlopen(CKAN, timeout=60))['result']
    for r in pac['resources']:
        if 'extrato' not in r['name'].lower(): continue
        yield r['name'], r['url'].rsplit('/', 1)[-1], urllib.request.urlopen(r['url'], timeout=300).read()


def locais(pasta):
    for f in sorted(os.listdir(pasta)):
        if re.search(r'\.(csv|xlsx)$', f) and f not in ('emps.csv', 'cruzamento.csv'):
            yield f, f, open(os.path.join(pasta, f), 'rb').read()


def normalizar(df, origem):
    cols = {chave(c): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for alvo, nomes in SINONIMOS.items():
        achou = next((cols[n] for n in nomes if n in cols), None)
        out[alvo] = df[achou] if achou else None
    out['origem'] = origem
    return out


def lotes(sql):
    """'008.020.0127-4 | 08602000073' -> ['0080200127', '0860200007']: setor, quadra e lote, sem o dígito."""
    out = []
    for p in re.split(r'[|,;/]', str(sql or '')):
        d = re.sub(r'\D', '', p)
        if 10 <= len(d) <= 11:
            out.append(d.zfill(11)[:10])
    return sorted(set(out))


def num(s):
    s = s.astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='coerce')


def main(saida, pasta=None):
    fontes = locais(pasta) if pasta else baixar()
    a = pd.concat([normalizar(ler(arq, dados), nome) for nome, arq, dados in fontes], ignore_index=True)
    a = a[a.assunto.str.contains('Edifica', case=False, na=False) & a.assunto.str.contains('Nova', case=False, na=False)].copy()
    d = a.data_autuacao.astype(str).str[:10]
    a['data_autuacao'] = pd.to_datetime(d, format='%Y-%m-%d', errors='coerce').fillna(
        pd.to_datetime(d, format='%d/%m/%Y', errors='coerce')).dt.date
    a['unidades'] = pd.to_numeric(a.unidades.str.extract(r'^\s*(\d+)\s*$')[0], errors='coerce').astype('Int64')
    for c in ('area_terreno', 'area_construir'): a[c] = num(a[c])
    for c in ('blocos', 'pavimentos'): a[c] = pd.to_numeric(a[c].astype(str).str.extract(r'^\s*(\d+)')[0], errors='coerce').astype('Int64')
    sub = a.subcategoria.fillna('')
    ass = a.assunto.fillna('')
    a['his'] = sub.str.contains('HIS'); a['hmp'] = sub.str.contains('HMP'); a['r2v'] = sub.str.contains('R2v', case=False)
    a['social_no_assunto'] = ass.str.contains(r'\(HIS', case=False) & ~ass.str.contains('exceto', case=False)
    a['modificativo'] = ass.str.contains('Modificativo', case=False)
    a['lotes'] = a.sql.apply(lotes)
    # o mesmo processo pode vir em duas linhas (um SQL por linha, ou em dois extratos): junta os lotes
    g = a.sort_values('data_autuacao').groupby('processo', as_index=False)
    a = g.agg({**{c: 'last' for c in a.columns if c not in ('processo', 'lotes')},
               'lotes': lambda s: sorted(set(x for l in s for x in l))})
    a['lotes'] = a.lotes.apply(lambda l: '{' + ','.join(l) + '}')
    os.makedirs(saida, exist_ok=True)
    cols = ['processo', 'protocolo', 'relacionado', 'data_autuacao', 'assunto', 'endereco', 'categoria', 'subcategoria',
            'his', 'hmp', 'r2v', 'social_no_assunto', 'modificativo', 'unidades', 'area_terreno', 'area_construir',
            'blocos', 'pavimentos', 'lotes', 'origem']
    a[cols].to_csv(os.path.join(saida, 'processos.csv'), index=False)
    print(f'{len(a)} processos de edificação nova, {a.unidades.notna().sum()} com unidades, '
          f'{a.data_autuacao.min()} a {a.data_autuacao.max()}')


if __name__ == '__main__':
    if len(sys.argv) not in (2, 3): sys.exit(__doc__)
    main(*sys.argv[1:])
