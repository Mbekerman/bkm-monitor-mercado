# Coletor do monitor de mercado

O coletor fica em `tools/coletar` (Python 3.11+, pacote `coletar`). A regra continua no banco: o comando só busca a fonte, grava o bruto e chama `im_abrir_coleta`, `im_importar` (lotes de até 50 empreendimentos e 400 unidades; empreendimento maior vai em partes, ver abaixo), `im_anotar_falha`, `im_falhar_coleta` e `im_fechar_coleta`. A interface é `FonteMercado`. Os adaptadores são `orulo_login` (completo), `orulo_planilha` (leitor à espera de amostra real) e `orulo_api` (esqueleto OAuth2 da API v2). As tabelas que ele lê são `im_ids_externos` e `im_empreendimento_estados`. O prefixo `im_` é minúsculo e sem aspas. Não há `empresa_id`.

Não há credencial no repositório. Os crons rodam no Railway, nos serviços `coleta-mensal` e `coleta-lancamentos`.

## Comando

```bash
cd tools/coletar && pip install -e ".[teste]"
coletar --tipo mensal --data 2026-10-01 --fonte orulo_login --municipio "São Paulo"
coletar --tipo lancamentos --data 2026-10-06
coletar --tipo lancamentos --data 2026-10-06 --id 83029 --id 83030
coletar --tipo mensal --data 2026-10-01 --fonte orulo_planilha --arquivo /caminho/tabela.xlsx --mapa mapa.json
```

`--tipo mensal` grava a coleta `completa_mensal` e abre cada empreendimento da listagem: detalhe e unidades. `--tipo lancamentos` grava `lancamentos` e só varre a listagem, para achar o que ainda não está no banco. Cada empreendimento novo é aberto um a um, com `detalhe` e unidades (preço de lançamento, tipologias, total de unidades, estoque, datas). Quem já existe não é buscado de novo; a mensal cobre. A espera entre pedidos continua de 2 a 3 segundos.

`--id` repete e pula a listagem. Serve para testar ou recolher um empreendimento, mesmo que ele já esteja no banco. Cada id passa pela rodada normal (`im_abrir_coleta`, importação, `im_fechar_coleta`). Não vale para `orulo_planilha`.

Sem `--municipio`, entram São Paulo e São José dos Campos. `--fonte` padrão é `orulo_login`.

Saída 0 quando a coleta fecha completa, 2 quando fica incompleta, 3 quando falha (login), 1 em erro de uso ou de rede. Erro de rede (`httpx.HTTPError`, inclusive falha de conexão) marca a coleta aberta como `falhou` e sai com 1, sem traceback.

## Variáveis

| Variável | Uso |
| --- | --- |
| `ORULO_EMAIL`, `ORULO_PASSWORD` | Login do `orulo_login`. Obrigatórias nessa fonte. |
| `ORULO_CLIENT_ID`, `ORULO_CLIENT_SECRET` | OAuth2 client credentials do `orulo_api` (`POST /oauth/token`). Sem as duas, o adaptador não chama a rede. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | API do Supabase. A chave não vai para o repositório. |
| `ORULO_ATRASO_MIN`, `ORULO_ATRASO_MAX` | Espera entre pedidos, em segundos. Padrão 2 e 3. |
| `COLETAR_TIPO` | No cron: `mensal` ou `lancamentos`. |
| `COLETAR_UNIDADES_POR_CHAMADA`, `COLETAR_UNIDADES_MINIMO` | Unidades por chamada de `im_importar` (padrão 400) e o menor pedaço (padrão 25). Ver "Importação em partes". |
| `COLETAR_PG_DSN`, `COLETAR_BRUTO_DIR` | Só para um Postgres local, no lugar do Supabase. O teste ponta a ponta usa isso por conta própria. |

`orulo_login` espera de 2 a 3 segundos entre pedidos, refaz o login uma vez em 401 e para em 429 ou 403 (a coleta fecha incompleta). O segundo 401 encerra como `falhou`. O client id do site (`CLIENT_ID` no código) é público; não é senha.

O bruto vai para o bucket privado `im-bruto`, no caminho `mercado/{referencia}-{tipo}.json.gz`.

## Importação em partes

`im_importar` passa pela API do Supabase, com limite de 8 s por chamada. Até a migration `fechar_em_conjunto`, cada chamada também recalculava os totais da coleta, um custo que crescia com a coleta (com ~400 mil unidades passava de 5 s). Agora os totais (`coletados`, `unidades`, `tipos_desconhecidos`) só são calculados em `im_fechar_coleta`, e `im_importar` devolve `coletados` e `unidades` nulos. Mesmo assim o coletor (`coletar/importacao.py`) manda até 400 unidades por chamada: empreendimentos pequenos vão juntos, e o empreendimento maior vai em partes de unidades. Se o banco estoura o tempo (57014), o pedaço cai pela metade, até 25 unidades, e o tamanho menor fica valendo. Erro transitório (5xx, 429, queda de rede) é tentado de novo até 3 vezes.

O resultado é o mesmo de uma chamada só: venda, unidade removida e vínculo só saem em `im_fechar_coleta`; unidades com a mesma torre/número vão sempre juntas e na ordem; e cada parte leva representantes de todos os tipos de unidade, para o filtro "tem unidade residencial" dar igual. Empreendimento com unidade que o banco recusa (valor fora da coluna) falha inteiro, como antes. Uma parte que mesmo assim volte com erro vira a falha `importacao_parcial` e aviso no log. Empreendimento com `importacao_parcial` não tem unidade marcada como removida no fechamento: a unidade que faltou pode estar só na parte que falhou.

## Fechamento

`im_fechar_coleta(coleta, esperados, forcar_incompleta)` faz tudo em comandos de conjunto (antes era um laço por unidade, que levava horas numa carga mensal de ~400 mil unidades): totais, vínculo, venda vista (`ultimo_visto`), venda anterior ao monitor (`estimado`, pela mediana do m² das disponíveis do mesmo empreendimento, dormitórios e vagas, área até 3% diferente), venda desfeita e unidade removida. Mesmo resultado da versão com laço (`testes/test_fechamento_e2e.py`). Numa base desse tamanho passa dos 8 s que a API dá a cada comando (o fechamento de 2026-10 levou cerca de 2 minutos). Por isso `im_fechar_coleta` tem `statement_timeout` próprio de 10 min (migration `fechar_timeout`; os papéis `service_role`, `authenticated` e `anon` ficam como estão), e o coletor espera a resposta por até 11 min. O gateway da API do Supabase corta a requisição HTTP em ~125 s com 504, mas o banco continua até o fim. Quando a resposta se perde (502/503/504, leitura estourada, conexão caída), o coletor não chama `im_falhar_coleta`: lê `im_coletas` a cada 15 s até a coleta sair de `rodando` (até 12 min) e usa a situação e os totais gravados; vendas e removidas vêm de contagens. Se ela continuar `rodando`, sai com 1 e a coleta fica aberta para conferir à mão.

Cobertura: `esperados` é o número de empreendimentos visitados pelo coletor, inclusive os que ficaram fora do filtro. O fechamento desconta os que têm falha `fora_do_filtro` e não entraram nesta coleta (`esperados_no_filtro`). A coleta fica `incompleta` se `coletados` < 90% de `esperados_no_filtro` (`cobertura_minima_pct`) ou < 90% da mensal anterior. O CLI continua mandando os visitados; não há mudança no Railway.

## Planilha

Não há amostra. O leitor aceita `.csv` (vírgula, ponto e vírgula ou tab) e `.xlsx`. Cada linha é uma unidade; linhas com o mesmo id viram um empreendimento. Linha sem torre, número, área ou preço entra só como empreendimento.

O mapa padrão está em `COLUNAS` (`tools/coletar/coletar/orulo_planilha.py`): `id`/`codigo`, `nome`/`empreendimento`, `cidade`, `uf`, `latitude`, `longitude`, `situacao`/`status`, `tipo`, `finalidade`, `torre`/`bloco`, `numero`/`unidade`, `area`/`area_privativa`, `preco`/`valor`, `tipo_unidade`, `status_unidade`, e os demais campos da spec. `--mapa` é um JSON `{ "nome": "Empreendimento" }` que troca o cabeçalho daquele campo.

Toda rodada desta fonte registra o aviso: o leitor ainda precisa ser validado com uma planilha real do Órulo.

## Cron no Railway

São dois serviços, os dois com a imagem `tools/coletar/Dockerfile`. O cron do Railway é UTC.

| Serviço | Config | Cron (UTC) | Horário em São Paulo | `COLETAR_TIPO` |
| --- | --- | --- | --- | --- |
| `coleta-mensal` | `tools/coletar/railway.mensal.json` | `10 3 1 * *` | dia 1 às 00:10 | `mensal` |
| `coleta-lancamentos` | `tools/coletar/railway.lancamentos.json` | `0 9 * * 1` | segunda às 06:00 | `lancamentos` |

No serviço: caminho do config acima, variáveis da tabela (e-mail e senha no mensal e no semanal, mais `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`). `10 3 1 * *` é 03:10 UTC, 00:10 em São Paulo, no dia 1. `0 9 * * 1` é 09:00 UTC, 06:00 em São Paulo, na segunda. `restartPolicyType` é `NEVER` para o cron não ficar em laço.

## Testes

```bash
bash supabase/tests/run.sh
cd tools/coletar && python3 -m pytest -q
```

Os testes do coletor usam as amostras `testes/fixtures/` (`detail_83029.json`, `units_83029.json`, `elastic_sample.json`) e HTTP simulado. A rodada ponta a ponta sobe um Postgres 16 temporário e chama as funções reais. Não há pedido ao Órulo nem a um Supabase remoto.
