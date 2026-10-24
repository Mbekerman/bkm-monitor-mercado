# Imagem do coletor, para os dois serviços cron do projeto Railway deste repo.
# Não faz deploy sozinho.
FROM python:3.12-slim
WORKDIR /app
COPY tools/coletar /app
RUN pip install --no-cache-dir . && chmod +x /app/entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/entrypoint.sh"]
