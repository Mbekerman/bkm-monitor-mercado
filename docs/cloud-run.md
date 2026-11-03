# Coletor no Google Cloud Run

Dois jobs no projeto `bkm-monitor`, região `southamerica-east1`. Os `tools/coletar/railway.*.json` são o cron antigo do Railway; o que roda é o Cloud Run. A imagem é `southamerica-east1-docker.pkg.dev/bkm-monitor/monitor/coletar:<tag>`, montada por `tools/coletar/Dockerfile`. O container não recebe argumentos: o entrypoint chama `python -m coletar` e o CLI completa o que faltar. Este repositório não altera o projeto nem os segredos.

| Job | Agenda (America/Sao_Paulo) | `COLETAR_TIPO` |
| --- | --- | --- |
| `coleta-mensal` | `10 0 1 * *` (dia 1, 00:10) | `mensal` |
| `coleta-lancamentos` | `0 6 * * 1` (segunda, 06:00) | `lancamentos` |

O Cloud Scheduler chama o job por HTTP, no fuso `America/Sao_Paulo`.

Variáveis do container:

| Variável | Origem |
| --- | --- |
| `COLETAR_TIPO` | variável do job, `mensal` ou `lancamentos` |
| `ORULO_EMAIL`, `ORULO_PASSWORD` | Secret Manager |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Secret Manager |

## O que roda quando o job não passa argumentos

Sem `--tipo`, o CLI usa `COLETAR_TIPO`. Sem `--data`, a referência é o dia civil de hoje em `America/Sao_Paulo`, para os dois tipos. `--tipo` e `--data` explícitos ganham do ambiente. O log começa com `referência AAAA-MM-DD, tipo mensal` (ou `lancamentos`).

A mensal não usa o mês anterior e não é forçada para o dia 1 além da agenda. Ela já dispara no dia 1 às 00:10, então "hoje" é o dia 1. `im_coletas.referencia` é a data da foto, única junto com a fonte e o tipo. `im_abrir_coleta` desfaz e reabre a linha que já existir nessa data; uma referência no mês anterior reabriria a mensal que já fechou.

O entrypoint antigo gravava a data UTC. Nestes dois horários (00:10 e 06:00 em São Paulo) o dia UTC é o mesmo. Entre 00:00 e 02:59 UTC o dia em São Paulo ainda é o anterior, por isso o padrão segue o fuso da agenda.

Tipo ausente ou fora de `mensal`/`lancamentos`, ou data que não seja `AAAA-MM-DD`, imprime o motivo em stderr e sai com código 1. O Cloud Run marca a execução como falha. Flag desconhecida sai com código 2. `--help` sai com código 0 e não coleta: um argumento `--help` deixa a execução verde à toa.

## Por que a execução de 2026-11-03 não coletou

O CLI exigia `--tipo` e `--data` e não lia `COLETAR_TIPO`. Sem os dois, o argparse imprime `usage: coletar [-h] --tipo {mensal,lancamentos} --data DATA` e sai com código 2. A única saída 0 que imprime esse usage é `--help` / `-h`: o argparse trata a ajuda como sucesso, e o Cloud Run grava `Container called exit(0)`.

A imagem base `python:3.12-slim` termina com `CMD ["python3"]`. O Cloud Run, sem args no job, junta o entrypoint da imagem com o CMD. Se esse `python3` chega ao script, o `"$@"` o entrega ao coletar (`unrecognized arguments: python3`), o usage aparece e nada é gravado. O builder atual zera o CMD da base quando o Dockerfile define `ENTRYPOINT`; o entrypoint ainda descarta um `python3` solto, para o caso de a imagem antiga ou outro builder preservar o CMD.

## Teste depois de publicar a imagem nova

A execução não muda a definição do job. Esta é a mesma chamada da agenda (a semanal inteira, não um empreendimento só):

```bash
gcloud run jobs execute coleta-lancamentos \
  --project=bkm-monitor \
  --region=southamerica-east1 \
  --wait
```

Para conferir login, rede e gravação num empreendimento, sem varrer a listagem, `--args` vale só para essa execução e substitui os argumentos do job (hoje vazios). O CLI preenche tipo e data:

```bash
gcloud run jobs execute coleta-lancamentos \
  --project=bkm-monitor \
  --region=southamerica-east1 \
  --wait \
  --args=--id,83029
```

No log tem de aparecer `referência` e `tipo lancamentos` antes de qualquer `usage: coletar`. Código 0 é coleta completa, 2 incompleta, 3 falha de login, 1 erro de uso, de configuração ou de rede.

Para apontar o job para a imagem nova, sem mexer em variável, segredo ou argumento:

```bash
gcloud run jobs update coleta-lancamentos \
  --project=bkm-monitor \
  --region=southamerica-east1 \
  --image=southamerica-east1-docker.pkg.dev/bkm-monitor/monitor/coletar:TAG
gcloud run jobs update coleta-mensal \
  --project=bkm-monitor \
  --region=southamerica-east1 \
  --image=southamerica-east1-docker.pkg.dev/bkm-monitor/monitor/coletar:TAG
```
