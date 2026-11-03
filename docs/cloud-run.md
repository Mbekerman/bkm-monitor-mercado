# Coletor no Google Cloud Run

Até 3/nov/2026 os crons ficavam no Railway, que bloqueou a conta. Agora são dois **jobs** do Cloud Run,
disparados pelo Cloud Scheduler, no projeto `bkm-terrenos` do Google Cloud (o mesmo do BKM Terrenos),
região `southamerica-east1`. A imagem é a de `tools/coletar/Dockerfile`, montada pelo Cloud Build
(`tools/coletar/cloudbuild.yaml`) a cada push na `main`.

| Job | Agenda (America/Sao_Paulo) | `COLETAR_TIPO` | Tempo máximo | Duração vista |
| --- | --- | --- | --- | --- |
| `coleta-mensal` | `10 0 1 * *` (dia 1, 00:10) | `mensal` | 12 h | 4 h 43 min (1/nov/2026) |
| `coleta-lancamentos` | `0 6 * * 1` (segunda, 06:00) | `lancamentos` | 6 h | 2 h 40 min (2/nov/2026) |

Sem nova tentativa (`--max-retries 0`): uma coleta repetida no mesmo dia abriria outra rodada.

## Montar uma vez (Cloud Shell, no projeto `bkm-terrenos`)

Os valores secretos são digitados no próprio Cloud Shell (o comando espera a digitação; Ctrl+D encerra).
Nunca colar em chat nem em arquivo do repositório.

```bash
gcloud config set project bkm-terrenos
REGIAO=southamerica-east1
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com

# Conta de serviço própria do coletor
gcloud iam service-accounts create coletor-mercado --display-name "Coletor do monitor de mercado"
SA=coletor-mercado@bkm-terrenos.iam.gserviceaccount.com

# Segredos (digite o valor e Ctrl+D; sem quebra de linha no fim)
for s in orulo-email orulo-password supabase-service-role-key; do
  printf 'Valor de %s: ' "$s"; gcloud secrets create "$s" --data-file=- ; echo
  gcloud secrets add-iam-policy-binding "$s" --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
done

# Repositório de imagens e primeira imagem
gcloud artifacts repositories create monitor-mercado --repository-format docker --location $REGIAO
git clone https://github.com/Mbekerman/bkm-monitor-mercado && cd bkm-monitor-mercado
IMG=$REGIAO-docker.pkg.dev/bkm-terrenos/monitor-mercado/coletar:latest
docker build -f tools/coletar/Dockerfile -t $IMG . && docker push $IMG

# Os dois jobs
for tipo in mensal lancamentos; do
  [ $tipo = mensal ] && limite=12h || limite=6h
  gcloud run jobs create coleta-$tipo --image $IMG --region $REGIAO --service-account $SA \
    --tasks 1 --max-retries 0 --task-timeout $limite --cpu 1 --memory 1Gi \
    --set-env-vars COLETAR_TIPO=$tipo,SUPABASE_URL=https://mghqcsycuurjezoiiyfp.supabase.co \
    --set-secrets ORULO_EMAIL=orulo-email:latest,ORULO_PASSWORD=orulo-password:latest,SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest
done

# Agenda: o Scheduler chama o job com a conta do coletor
gcloud projects add-iam-policy-binding bkm-terrenos --member "serviceAccount:$SA" --role roles/run.invoker
for par in "mensal|10 0 1 * *" "lancamentos|0 6 * * 1"; do
  tipo=${par%%|*}; cron=${par#*|}
  gcloud scheduler jobs create http coleta-$tipo --location $REGIAO --schedule "$cron" --time-zone America/Sao_Paulo \
    --uri "https://run.googleapis.com/v2/projects/bkm-terrenos/locations/$REGIAO/jobs/coleta-$tipo:run" \
    --http-method POST --oauth-service-account-email $SA
done
```

## Teste antes da primeira agenda

Uma rodada de um empreendimento só, para conferir login, rede (o IP agora é do Google) e gravação:

```bash
gcloud run jobs execute coleta-lancamentos --region $REGIAO --wait --args="--id,83029"
```

O `entrypoint.sh` já põe `--tipo` e `--data` (dia UTC); `--args` só acrescenta o `--id`. Confira o log
(Cloud Run › Jobs › coleta-lancamentos › Execuções) e a linha nova em `im_coletas`.
Saída 0 = completa, 2 = incompleta (403 ou 429 do Órulo também caem aqui), 3 = falha no login.

## Atualizar o código

Cloud Build › Gatilhos › Criar gatilho: repositório `Mbekerman/bkm-monitor-mercado` (app do Cloud Build no
GitHub), evento push na `^main$`, configuração `tools/coletar/cloudbuild.yaml`. A conta do Cloud Build
precisa de **Administrador do Cloud Run** e de **Usuário da conta de serviço** na `coletor-mercado`.
Sem gatilho, repita o `docker build`/`docker push` acima e `gcloud run jobs update coleta-<tipo> --image $IMG`.
