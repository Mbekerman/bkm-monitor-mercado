"""Converte a única aba de um .xlsx descompactado em CSV, lendo o XML aos poucos (o openpyxl
levaria muitos minutos com 325 mil linhas). Uso: python3 ler_xlsx.py pasta_descompactada saida.csv"""
import sys, re, csv
from xml.etree.ElementTree import iterparse
base=sys.argv[1]; out=sys.argv[2]
ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
ss=[]
for ev,el in iterparse(base+'/xl/sharedStrings.xml'):
    if el.tag==ns+'si':
        ss.append(''.join(t.text or '' for t in el.iter(ns+'t'))); el.clear()
def colidx(ref):
    m=re.match(r'([A-Z]+)',ref).group(1); n=0
    for ch in m: n=n*26+ord(ch)-64
    return n-1
w=csv.writer(open(out,'w',newline=''))
for ev,el in iterparse(base+'/xl/worksheets/sheet1.xml'):
    if el.tag==ns+'row':
        row=['']*56
        for c in el.findall(ns+'c'):
            v=c.find(ns+'v')
            if v is None: continue
            val=v.text
            if c.get('t')=='s': val=ss[int(val)]
            row[colidx(c.get('r'))]=val
        w.writerow(row); el.clear()
