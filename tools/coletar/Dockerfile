# Imagem do coletor, para os dois jobs do Cloud Run (coleta-mensal e coleta-lancamentos).
# Montada pelo Cloud Build (tools/coletar/cloudbuild.yaml).
FROM python:3.12-slim
WORKDIR /app
COPY tools/coletar /app
RUN pip install --no-cache-dir . && chmod +x /app/entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/entrypoint.sh"]
