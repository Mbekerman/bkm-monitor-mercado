# Coletor do monitor de mercado

O coletor fica em `tools/coletar` (Python 3.11+, pacote `coletar`). A regra continua no banco: o comando só busca a fonte, grava o bruto e chama `im_abrir_coleta`, `im_importar` (lotes de até 50 empreendimentos e 400 unidades; empreendimento maior vai em partes, ver abaixo), `im_anotar_falha`, `im_falhar_coleta` e `im_fechar_coleta`. A interface é `FonteMercado`. Os adaptadores são `orulo_login` (completo), `orulo_planilha` (leitor à espera de amostra real) e `orulo_api` (esqueleto OAuth2 da API v2). As tabelas que ele lê são `im_ids_externos` e `im_empreendimento_estados`. O prefixo `im_` é minúsculo e sem aspas. Não há `empresa_id`.

Não há credencial no repositório. Os crons rodam no Google Cloud Run, nos jobs `coleta-mensal` e `coleta-lancamentos` (projeto `bkm-monitor`, ver [cloud-run.md](cloud-run.md)).

## Comando

```bash
cd tools/coletar && pip install -e ".[teste]"
coletar --tipo mensal --data 2026-10-01 --fonte orulo_login --municipio "São Paulo"
coletar --tipo lancamentos --data 2026-10-06
coletar --tipo lancamentos --data 2026-10-06 --id 83029 --id 83030
coletar --tipo mensal --data 2026-10-01 --fonte orulo_planilha --arquivo /caminho/tabela.xlsx --mapa mapa.json
# o job do Cloud Run não passa flag; o tipo vem do ambiente e a data é hoje em São Paulo
COLETAR_TIPO=lancamentos coletar
COLETAR_TIPO=mensal coletar --id 83029
```

Para uma rodada com São Paulo, São José dos Campos e as 17 cidades vizinhas, a flag repete. Sem `--municipio`, a rodada continua só as duas de sempre. Este repositório não altera o job do Cloud Run; a lista é o argumento a configurar em `coleta-mensal` e `coleta-lancamentos` quando a coleta das 19 for ligada.

```bash
coletar --tipo mensal \
  --municipio "São Paulo" \
  --municipio "São José dos Campos" \
  --municipio "Guarulhos" \
  --municipio "Santo André" \
  --municipio "Osasco" \
  --municipio "São Caetano do Sul" \
  --municipio "São Bernardo do Campo" \
  --municipio "Taboão da Serra" \
  --municipio "Diadema" \
  --municipio "Barueri" \
  --municipio "Poá" \
  --municipio "Santana de Parnaíba" \
  --municipio "Cotia" \
  --municipio "Mauá" \
  --municipio "Ferraz de Vasconcelos" \
  --municipio "Itaquaquecetuba" \
  --municipio "Itapecerica da Serra" \
  --municipio "Cajamar" \
  --municipio "Embu das Artes"
```

`--tipo` e `--data` podem faltar. Sem `--tipo`, vale `COLETAR_TIPO` (`mensal` ou `lancamentos`). Sem `--data`, a referência é o dia de hoje em `America/Sao_Paulo`, nos dois tipos. Flag na linha de comando ganha do ambiente e do relógio. A mensal não volta para o mês anterior nem é empurrada para o dia 1: a agenda já dispara no dia 1 às 00:10, então "hoje" já é o dia 1. `referencia` é a data da foto e faz parte da chave única `(fonte, tipo, referencia)`. `im_abrir_coleta` desfaz e reabre uma coleta que já exista nessa data; usar o mês anterior reabriria a mensal fechada.

`--tipo mensal` grava a coleta `completa_mensal` e abre cada empreendimento da listagem: detalhe e unidades. `--tipo lancamentos` grava `lancamentos` e só varre a listagem, para achar o que ainda não está no banco. Cada empreendimento novo é aberto um a um, com `detalhe` e unidades (preço de lançamento, tipologias, total de unidades, estoque, datas). Quem já existe não é buscado de novo; a mensal cobre. A espera entre pedidos continua de 2 a 3 segundos.

`--id` repete e pula a listagem. Serve para testar ou recolher um empreendimento, mesmo que ele já esteja no banco. Cada id passa pela rodada normal (`im_abrir_coleta`, importação, `im_fechar_coleta`). Não vale para `orulo_planilha`.

Sem `--municipio`, entram São Paulo e São José dos Campos. `--fonte` padrão é `orulo_login`. A flag aceita também Guarulhos, Santo André, Osasco, São Caetano do Sul, São Bernardo do Campo, Taboão da Serra, Diadema, Barueri, Poá, Santana de Parnaíba, Cotia, Mauá, Ferraz de Vasconcelos, Itaquaquecetuba, Itapecerica da Serra, Cajamar e Embu das Artes. O nome é comparado sem acento e sem diferença de maiúscula. Cidade fora dessa lista sai com código 1 e a mensagem `município fora da lista monitorada`.

Cada cidade vai para o `/api/elastic` com `state=SP`, `city` igual ao nome oficial (o mesmo de `address.city` no Órulo) e o retângulo que fatia o mapa. Não há id numérico de cidade. O código IBGE fica no catálogo (`coletar/municipios.py`) para o banco; o coletor não o envia. São Paulo (`3550308`) e São José dos Campos (`3549904`) seguem com os retângulos de sempre. O das 17 vizinhas é a extrema da malha municipal do IBGE (qualidade intermediária), arredondada para fora em duas casas.

| Município | IBGE | Retângulo (sw_lat, sw_lng, ne_lat, ne_lng) |
| --- | --- | --- |
| São Paulo | 3550308 | -23,80, -46,83, -23,35, -46,36 (o de sempre; a malha do IBGE desce até cerca de -24,01) |
| São José dos Campos | 3549904 | -23,30, -46,20, -22,85, -45,70 (o de sempre) |
| Guarulhos | 3518800 | -23,51, -46,58, -23,27, -46,33 |
| Santo André | 3547809 | -23,82, -46,57, -23,60, -46,27 |
| Osasco | 3534401 | -23,61, -46,83, -23,46, -46,74 |
| São Caetano do Sul | 3548807 | -23,66, -46,59, -23,60, -46,54 |
| São Bernardo do Campo | 3548708 | -23,97, -46,66, -23,64, -46,41 |
| Taboão da Serra | 3552809 | -23,65, -46,82, -23,59, -46,75 |
| Diadema | 3513801 | -23,74, -46,66, -23,65, -46,57 |
| Barueri | 3505708 | -23,56, -46,97, -23,47, -46,79 |
| Poá | 3539806 | -23,58, -46,37, -23,50, -46,32 |
| Santana de Parnaíba | 3547304 | -23,51, -47,04, -23,38, -46,80 |
| Cotia | 3513009 | -23,81, -47,09, -23,56, -46,80 |
| Mauá | 3529401 | -23,72, -46,50, -23,61, -46,38 |
| Ferraz de Vasconcelos | 3515707 | -23,62, -46,40, -23,51, -46,35 |
| Itaquaquecetuba | 3523107 | -23,52, -46,40, -23,42, -46,27 |
| Itapecerica da Serra | 3522208 | -23,81, -46,94, -23,66, -46,76 |
| Cajamar | 3509205 | -23,43, -46,95, -23,27, -46,81 |
| Embu das Artes | 3515004 | -23,69, -46,92, -23,61, -46,79 |

O banco ainda só reconhece São Paulo e São José dos Campos (`im_municipios` e `im_config.municipios`). `im_municipio_por_nome` devolve nulo para as outras, e `_im_passa_filtro` recusa município nulo ou fora de `im_config`. O empreendimento entra na descoberta e sai como `fora_do_filtro`, sem ser gravado. Esta pasta não traz migration. Para a importação passar a gravar, falta incluir as 17 linhas e somar os ids ao array de `im_config`, sem tirar os dois que já estão:

```sql
insert into public.im_municipios (codigo_ibge, nome, uf) values
  ('3518800', 'Guarulhos', 'SP'),
  ('3547809', 'Santo André', 'SP'),
  ('3534401', 'Osasco', 'SP'),
  ('3548807', 'São Caetano do Sul', 'SP'),
  ('3548708', 'São Bernardo do Campo', 'SP'),
  ('3552809', 'Taboão da Serra', 'SP'),
  ('3513801', 'Diadema', 'SP'),
  ('3505708', 'Barueri', 'SP'),
  ('3539806', 'Poá', 'SP'),
  ('3547304', 'Santana de Parnaíba', 'SP'),
  ('3513009', 'Cotia', 'SP'),
  ('3529401', 'Mauá', 'SP'),
  ('3515707', 'Ferraz de Vasconcelos', 'SP'),
  ('3523107', 'Itaquaquecetuba', 'SP'),
  ('3522208', 'Itapecerica da Serra', 'SP'),
  ('3509205', 'Cajamar', 'SP'),
  ('3515004', 'Embu das Artes', 'SP')
on conflict (codigo_ibge) do nothing;

update public.im_config c
   set municipios = (
     select coalesce(array_agg(s.id order by s.codigo_ibge), '{}')
       from (
         select m.id, m.codigo_ibge
           from public.im_municipios m
          where m.id = any(c.municipios)
         union
         select m.id, m.codigo_ibge
           from public.im_municipios m
          where m.codigo_ibge in (
            '3518800','3547809','3534401','3548807','3548708','3552809','3513801',
            '3505708','3539806','3547304','3513009','3529401','3515707','3523107',
            '3522208','3509205','3515004')
       ) s
   )
 where c.unico;
```

A etapa de tabela de preços (categoria social) continua só para São Paulo, mesmo numa mensal com as 19 cidades. `python -m coletar.tabelas --municipio "São Paulo"` segue igual. Outra cidade nessa flag é ignorada; se nenhuma for São Paulo, o comando sai com 1.

Depois das tabelas, a mensal roda a etapa dos alvarás (`coletar/licenciamento.py`), também só para São Paulo e só quando a rodada inclui a cidade:

1. Lista os extratos do Aprova Digital nos dados abertos da prefeitura (conjunto `ad`, publicação semestral) e baixa só o que é novo ou mudou desde a última carga (`im_licenciamento_extratos`). Grava os processos de edificação nova em `im_licenciamento_processos` (`im_registrar_processos`; o mesmo processo em dois extratos soma os lotes). São processos protocolados, não alvarás emitidos.
2. Consulta o lote fiscal dos empreendimentos de São Paulo que ainda não têm (`im_lotes_pendentes` → `im_registrar_lotes`), no GeoSampa pelo proxy `/geosampa` do BKM Terrenos, dois pedidos por vez. 403 ou 429 para a consulta e avisa no log; não troca de IP nem de caminho. Erro num empreendimento fica para o mês seguinte.
3. Refaz a conferência (`im_conferir_unidades`) e leva HIS/HMP do processo para o cadastro (`im_marcar_alvara`): colunas `his_alvara`, `hmp_alvara`, `processo_alvara`, `situacao_alvara` e `alvara_em` de `im_empreendimentos`, para o prédio conferido, em fase ou suspeito cujo processo tem HIS ou HMP. Quem deixa de casar volta a nulo. `programa_social` (do Órulo, chave dos grupos) e `im_unidades.categoria_social` (da tabela de preços) não mudam.

`COLETAR_ALVARAS=0` desliga a etapa. Falha nela não muda o resultado da coleta. À mão: `python -m coletar.licenciamento [--pasta DIR] [--sem-lotes]`. `LICENCIAMENTO_OWS` troca o endereço do proxy do GeoSampa.

Saída 0 quando a coleta fecha completa, 2 quando fica incompleta, 3 quando falha (login), 1 em erro de uso, de configuração ou de rede. Sem `--tipo` e sem `COLETAR_TIPO` válido, ou com `--data` que não seja `AAAA-MM-DD`, sai 1 e não abre coleta: o Cloud Run marca a execução como falha. Flag desconhecida continua saindo 2, pelo argparse. `--help` sai 0 e não coleta; o job não deve receber `--help`, senão a execução fica verde sem ter coletado. Erro de rede (`httpx.HTTPError`, inclusive falha de conexão) marca a coleta aberta como `falhou` e sai com 1, sem traceback.

## Variáveis

| Variável | Uso |
| --- | --- |
| `ORULO_EMAIL`, `ORULO_PASSWORD` | Login do `orulo_login`. Obrigatórias nessa fonte. |
| `ORULO_CLIENT_ID`, `ORULO_CLIENT_SECRET` | OAuth2 client credentials do `orulo_api` (`POST /oauth/token`). Sem as duas, o adaptador não chama a rede. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | API do Supabase. A chave não vai para o repositório. |
| `ORULO_ATRASO_MIN`, `ORULO_ATRASO_MAX` | Espera entre pedidos, em segundos. Padrão 2 e 3. |
| `COLETAR_TIPO` | `mensal` ou `lancamentos`, quando `--tipo` não vem na linha de comando. É o que os jobs do Cloud Run definem. |
| `COLETAR_UNIDADES_POR_CHAMADA`, `COLETAR_UNIDADES_MINIMO` | Unidades por chamada de `im_importar` (padrão 400) e o menor pedaço (padrão 25). Ver "Importação em partes". |
| `COLETAR_TABELAS`, `COLETAR_ALVARAS` | `0` desliga a etapa das tabelas de preço ou a dos alvarás depois da mensal. Padrão ligadas. |
| `LICENCIAMENTO_OWS` | Endereço do proxy do GeoSampa usado na consulta de lotes. Padrão: o do BKM Terrenos. |
| `COLETAR_PG_DSN`, `COLETAR_BRUTO_DIR` | Só para um Postgres local, no lugar do Supabase. O teste ponta a ponta usa isso por conta própria. |

`orulo_login` espera de 2 a 3 segundos entre pedidos, refaz o login uma vez em 401 e para em 429 ou 403 (a coleta fecha incompleta). O segundo 401 encerra como `falhou`. O client id do site (`CLIENT_ID` no código) é público; não é senha.

O bruto vai para o bucket privado `im-bruto`, no caminho `mercado/{referencia}-{tipo}.json.gz`.

## Importação em partes

`im_importar` passa pela API do Supabase, com limite de 8 s por chamada. Até a migration `fechar_em_conjunto`, cada chamada também recalculava os totais da coleta, um custo que crescia com a coleta (com ~400 mil unidades passava de 5 s). Agora os totais (`coletados`, `unidades`, `tipos_desconhecidos`) só são calculados em `im_fechar_coleta`, e `im_importar` devolve `coletados` e `unidades` nulos. Mesmo assim o coletor (`coletar/importacao.py`) manda até 400 unidades por chamada: empreendimentos pequenos vão juntos, e o empreendimento maior vai em partes de unidades. Se o banco estoura o tempo (57014), o pedaço cai pela metade, até 25 unidades, e o tamanho menor fica valendo. Erro transitório (5xx, 429, queda de rede) é tentado de novo até 3 vezes.

O resultado é o mesmo de uma chamada só: venda, unidade removida e vínculo só saem em `im_fechar_coleta`; unidades com a mesma torre/número vão sempre juntas e na ordem; e cada parte leva representantes de todos os tipos de unidade, para o filtro "tem unidade residencial" dar igual. Empreendimento com unidade que o banco recusa (valor fora da coluna) falha inteiro, como antes. Uma parte que mesmo assim volte com erro vira a falha `importacao_parcial` e aviso no log. Empreendimento com `importacao_parcial` não tem unidade marcada como removida no fechamento: a unidade que faltou pode estar só na parte que falhou.

## Fechamento

`im_fechar_coleta(coleta, esperados, forcar_incompleta)` faz tudo em comandos de conjunto (antes era um laço por unidade, que levava horas numa carga mensal de ~400 mil unidades): totais, vínculo, venda vista (`ultimo_visto`), venda anterior ao monitor (`estimado`, pela mediana do m² das disponíveis do mesmo empreendimento, dormitórios e vagas, área até 3% diferente), venda desfeita e unidade removida. Mesmo resultado da versão com laço (`testes/test_fechamento_e2e.py`). Numa base desse tamanho passa dos 8 s que a API dá a cada comando (o fechamento de 2026-10 levou cerca de 2 minutos). Por isso `im_fechar_coleta` tem `statement_timeout` próprio de 10 min (migration `fechar_timeout`; os papéis `service_role`, `authenticated` e `anon` ficam como estão), e o coletor espera a resposta por até 11 min. O gateway da API do Supabase corta a requisição HTTP em ~125 s com 504, mas o banco continua até o fim. Quando a resposta se perde (502/503/504, leitura estourada, conexão caída), o coletor não chama `im_falhar_coleta`: lê `im_coletas` a cada 15 s até a coleta sair de `rodando` (até 12 min) e usa a situação e os totais gravados; vendas e removidas vêm de contagens. Se ela continuar `rodando`, sai com 1 e a coleta fica aberta para conferir à mão.

Cobertura: `esperados` é o número de empreendimentos visitados pelo coletor, inclusive os que ficaram fora do filtro. O fechamento desconta os que têm falha `fora_do_filtro` e não entraram nesta coleta (`esperados_no_filtro`). A coleta fica `incompleta` se `coletados` < 90% de `esperados_no_filtro` (`cobertura_minima_pct`) ou < 90% da mensal anterior. O CLI continua mandando os visitados.

## Planilha

Não há amostra. O leitor aceita `.csv` (vírgula, ponto e vírgula ou tab) e `.xlsx`. Cada linha é uma unidade; linhas com o mesmo id viram um empreendimento. Linha sem torre, número, área ou preço entra só como empreendimento.

O mapa padrão está em `COLUNAS` (`tools/coletar/coletar/orulo_planilha.py`): `id`/`codigo`, `nome`/`empreendimento`, `cidade`, `uf`, `latitude`, `longitude`, `situacao`/`status`, `tipo`, `finalidade`, `torre`/`bloco`, `numero`/`unidade`, `area`/`area_privativa`, `preco`/`valor`, `tipo_unidade`, `status_unidade`, e os demais campos da spec. `--mapa` é um JSON `{ "nome": "Empreendimento" }` que troca o cabeçalho daquele campo.

Toda rodada desta fonte registra o aviso: o leitor ainda precisa ser validado com uma planilha real do Órulo.

## Agenda

São dois jobs do Cloud Run, os dois com a imagem `tools/coletar/Dockerfile`, sem argumentos no container. O Cloud Scheduler dispara no fuso de São Paulo. Montagem e o teste de uma execução estão em [cloud-run.md](cloud-run.md).

| Job | Agenda (America/Sao_Paulo) | Em UTC | `COLETAR_TIPO` |
| --- | --- | --- | --- |
| `coleta-mensal` | `10 0 1 * *` (dia 1, 00:10) | 03:10 | `mensal` |
| `coleta-lancamentos` | `0 6 * * 1` (segunda, 06:00) | 09:00 | `lancamentos` |

`ORULO_EMAIL`, `ORULO_PASSWORD`, `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` vêm do Secret Manager. `COLETAR_TIPO` é variável do job. Sem `--data`, a referência é o dia da execução em São Paulo: no dia 1 às 00:10 isso já é o dia 1; na segunda às 06:00 é essa segunda. Os dois horários caem depois da meia-noite UTC, então a data UTC antiga coincidia; entre 00:00 e 02:59 UTC o dia em São Paulo ainda é o anterior, e o padrão agora segue o fuso da agenda.

## Testes

```bash
bash supabase/tests/run.sh
cd tools/coletar && python3 -m pytest -q
```

Os testes do coletor usam as amostras `testes/fixtures/` (`detail_83029.json`, `units_83029.json`, `elastic_sample.json`) e HTTP simulado. A rodada ponta a ponta sobe um Postgres 16 temporário e chama as funções reais. Não há pedido ao Órulo nem a um Supabase remoto.

## Unidade repetida pelo Órulo (13/nov/2026)

Em parte dos empreendimentos, a lista de unidades do Órulo traz a mesma unidade duas vezes: com a torre (torre "Bloco 01", número "101") e sem torre, com a torre colada no número ("101Bloco01", "101TA", "101-A"). No máximo uma das duas está à venda. `juntar_duplicadas` (`coletar/mapeamento.py`) junta as duas antes de importar: fica a da torre, com a situação e o preço da que está à venda, e os campos vazios dela vêm da outra. Unidade sem torre sem par, ou com mais de um par possível, segue como veio. Em 13/nov eram 192 empreendimentos e 47,8 mil unidades a mais no banco; a migration `20261113010000_unidades_duplicadas.sql` juntou as que já estavam gravadas e refez os grupos desses empreendimentos. Depois da junção, 151 dos 192 batem com o total do relatório do Órulo (até 2%).
