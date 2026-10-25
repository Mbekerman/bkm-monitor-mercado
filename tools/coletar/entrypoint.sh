#!/bin/sh
# Cron do Railway. COLETAR_TIPO=mensal ou lancamentos, definido no serviço.
# A data é o dia UTC. coleta-mensal roda às 03:10 UTC do dia 1 (00:10 em São Paulo).
# coleta-lancamentos roda às 09:00 UTC de segunda (06:00 em São Paulo).
set -eu
if [ -z "${COLETAR_TIPO:-}" ]; then
  echo "Defina COLETAR_TIPO=mensal ou COLETAR_TIPO=lancamentos" >&2
  exit 1
fi
DATA="$(date -u +%F)"
exec python -m coletar --tipo "$COLETAR_TIPO" --data "$DATA" "$@"
