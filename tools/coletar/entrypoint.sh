#!/bin/sh
# Cron do Railway. COLETAR_TIPO=mensal ou lancamentos, definido no serviço.
# A data é o dia UTC. O agendamento 06:00 UTC cai às 03:00 em São Paulo, no mesmo dia.
set -eu
if [ -z "${COLETAR_TIPO:-}" ]; then
  echo "Defina COLETAR_TIPO=mensal ou COLETAR_TIPO=lancamentos" >&2
  exit 1
fi
DATA="$(date -u +%F)"
exec python -m coletar --tipo "$COLETAR_TIPO" --data "$DATA" "$@"
