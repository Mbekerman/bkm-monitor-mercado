# Imagem do coletor, para os jobs do Cloud Run coleta-mensal e coleta-lancamentos.
# O job não passa argumentos. O CLI lê COLETAR_TIPO e a data de hoje em America/Sao_Paulo.
# Não faz deploy sozinho.
FROM python:3.12-slim
WORKDIR /app
COPY tools/coletar /app
RUN pip install --no-cache-dir . && chmod +x /app/entrypoint.sh
ENV PYTHONUNBUFFERED=1
# Exec form: o Cloud Run acrescenta os args do job (vazios na agenda) a este
# entrypoint. A imagem base traz CMD ["python3"]; ao definir ENTRYPOINT o
# builder zera esse CMD. Sem isso, o processo seria
# /app/entrypoint.sh python3 e o "python3" iria parar no argparse.
ENTRYPOINT ["/app/entrypoint.sh"]
