#!/bin/sh
# Job do Cloud Run, disparado pelo Cloud Scheduler. COLETAR_TIPO=mensal ou lancamentos, definido no job.
# A data é o dia UTC. coleta-mensal roda no dia 1 às 00:10 em São Paulo (03:10 UTC).
# coleta-lancamentos roda na segunda às 06:00 em São Paulo (09:00 UTC).
set -eu
if [ -z "${COLETAR_TIPO:-}" ]; then
  echo "Defina COLETAR_TIPO=mensal ou COLETAR_TIPO=lancamentos" >&2
  exit 1
fi
DATA="$(date -u +%F)"
exec python -m coletar --tipo "$COLETAR_TIPO" --data "$DATA" "$@"
