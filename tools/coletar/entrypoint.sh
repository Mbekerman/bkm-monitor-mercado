#!/bin/sh
# Job do Cloud Run, disparado pelo Cloud Scheduler. O job não passa argumentos:
# o CLI lê COLETAR_TIPO e usa a data de hoje em America/Sao_Paulo.
# --tipo e --data na linha de comando ganham do ambiente. Outros argumentos
# (por exemplo --id) são repassados.
#
# python:3.12-slim termina com CMD ["python3"]. Definir ENTRYPOINT zera esse
# CMD no builder atual; se "python3" ainda chegar sozinho, o argparse imprime
# o usage e a coleta não roda. Descartamos só esse caso.
set -eu
if [ "$#" -eq 1 ] && [ "$1" = "python3" ]; then
  shift
fi
exec python -m coletar "$@"
