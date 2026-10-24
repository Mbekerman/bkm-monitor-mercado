# Coletor do monitor de mercado

O coletor fica em `tools/coletar` (Python 3.11+, pacote `coletar`). A regra continua no banco: o comando só busca a fonte, grava o bruto e chama `im_abrir_coleta`, `im_importar` (lotes de 50), `im_anotar_falha`, `im_falhar_coleta` e `im_fechar_coleta`. A interface é `FonteMercado`. Os adaptadores são `orulo_login` (completo), `orulo_planilha` (leitor à espera de amostra real) e `orulo_api` (esqueleto OAuth2 da API v2). As tabelas que ele lê são `im_ids_externos` e `im_empreendimento_estados`. O prefixo `im_` é minúsculo e sem aspas. Não há `empresa_id`.

Não há credencial no repositório. O coletor não foi publicado no Railway.

## Comando

```bash
cd tools/coletar && pip install -e ".[teste]"
coletar --tipo mensal --data 2026-10-01 --fonte orulo_login --municipio "São Paulo"
coletar --tipo lancamentos --data 2026-10-06
coletar --tipo mensal --data 2026-10-01 --fonte orulo_planilha --arquivo /caminho/tabela.xlsx --mapa mapa.json
```

`--tipo mensal` grava a coleta `completa_mensal`. `--tipo lancamentos` grava `lancamentos`: só a lista, mais as unidades dos empreendimentos novos e dos que saíram de breve lançamento. Sem `--municipio`, entram São Paulo e São José dos Campos. `--fonte` padrão é `orulo_login`.

Saída 0 quando a coleta fecha completa, 2 quando fica incompleta, 3 quando falha (login), 1 em erro de uso.

## Variáveis

| Variável | Uso |
| --- | --- |
| `ORULO_EMAIL`, `ORULO_PASSWORD` | Login do `orulo_login`. Obrigatórias nessa fonte. |
| `ORULO_CLIENT_ID`, `ORULO_CLIENT_SECRET` | OAuth2 client credentials do `orulo_api` (`POST /oauth/token`). Sem as duas, o adaptador não chama a rede. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | API do Supabase. A chave não vai para o repositório. |
| `ORULO_ATRASO_MIN`, `ORULO_ATRASO_MAX` | Espera entre pedidos, em segundos. Padrão 2 e 3. |
| `COLETAR_TIPO` | No cron: `mensal` ou `lancamentos`. |
| `COLETAR_PG_DSN`, `COLETAR_BRUTO_DIR` | Só para um Postgres local, no lugar do Supabase. O teste ponta a ponta usa isso por conta própria. |

`orulo_login` espera de 2 a 3 segundos entre pedidos, refaz o login uma vez em 401 e para em 429 ou 403 (a coleta fecha incompleta). O segundo 401 encerra como `falhou`. O client id do site (`CLIENT_ID` no código) é público; não é senha.

O bruto vai para o bucket privado `im-bruto`, no caminho `mercado/{referencia}-{tipo}.json.gz`.

## Planilha

Não há amostra. O leitor aceita `.csv` (vírgula, ponto e vírgula ou tab) e `.xlsx`. Cada linha é uma unidade; linhas com o mesmo id viram um empreendimento. Linha sem torre, número, área ou preço entra só como empreendimento.

O mapa padrão está em `COLUNAS` (`tools/coletar/coletar/orulo_planilha.py`): `id`/`codigo`, `nome`/`empreendimento`, `cidade`, `uf`, `latitude`, `longitude`, `situacao`/`status`, `tipo`, `finalidade`, `torre`/`bloco`, `numero`/`unidade`, `area`/`area_privativa`, `preco`/`valor`, `tipo_unidade`, `status_unidade`, e os demais campos da spec. `--mapa` é um JSON `{ "nome": "Empreendimento" }` que troca o cabeçalho daquele campo.

Toda rodada desta fonte registra o aviso: o leitor ainda precisa ser validado com uma planilha real do Órulo.

## Cron no Railway

São dois serviços, no projeto Railway deste repositório, os dois com a imagem `tools/coletar/Dockerfile`. Nada disso foi aplicado.

| Serviço | Config | Cron (UTC) | `COLETAR_TIPO` |
| --- | --- | --- | --- |
| Mensal, dia 1 às 03:00 em São Paulo | `tools/coletar/railway.mensal.json` | `0 6 1 * *` | `mensal` |
| Semanal, segunda às 03:00 em São Paulo | `tools/coletar/railway.lancamentos.json` | `0 6 * * 1` | `lancamentos` |

No serviço: caminho do config acima, variáveis da tabela (e-mail e senha no mensal e no semanal, mais `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`). O cron do Railway é UTC; 06:00 UTC é 03:00 em São Paulo, no mesmo dia. `restartPolicyType` é `NEVER` para o cron não ficar em laço.

## Testes

```bash
bash supabase/tests/run.sh
cd tools/coletar && python3 -m pytest -q
```

Os testes do coletor usam as amostras `testes/fixtures/` (`detail_83029.json`, `units_83029.json`, `elastic_sample.json`) e HTTP simulado. A rodada ponta a ponta sobe um Postgres 16 temporário e chama as funções reais. Não há pedido ao Órulo nem a um Supabase remoto.
