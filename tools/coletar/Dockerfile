# Imagem do coletor, para os jobs do Cloud Run coleta-mensal e coleta-lancamentos.
# O job não passa argumentos. O CLI lê COLETAR_TIPO e a data de hoje em America/Sao_Paulo.
# Não faz deploy sozinho.
FROM python:3.12-slim
WORKDIR /app
# libgl1/libglib2.0-0: o opencv do OCR das tabelas de preço (rapidocr) precisa.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY tools/coletar /app
RUN pip install --no-cache-dir ".[ocr]" && chmod +x /app/entrypoint.sh \
    && python -c "import pymupdf, rapidocr_onnxruntime"
ENV PYTHONUNBUFFERED=1 OCR_THREADS=1
# Exec form: o Cloud Run acrescenta os args do job (vazios na agenda) a este
# entrypoint. A imagem base traz CMD ["python3"]; ao definir ENTRYPOINT o
# builder zera esse CMD. Sem isso, o processo seria
# /app/entrypoint.sh python3 e o "python3" iria parar no argparse.
ENTRYPOINT ["/app/entrypoint.sh"]
