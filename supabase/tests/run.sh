#!/usr/bin/env bash
# Sobe um Postgres 16 temporário, cria objetos atlas_* fictícios, aplica o shim
# e as migrations, prova que o atlas não mudou e roda as regras do monitor.
# Uso: bash supabase/tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
export PATH="$PGBIN:$PATH"
DIR="$(mktemp -d)"
PORT="${PORT:-54329}"
if [ "$(id -u)" = "0" ]; then RUN="runuser -u postgres --"; chown postgres "$DIR"; else RUN=""; fi
cleanup() { $RUN pg_ctl -D "$DIR" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT
$RUN initdb -D "$DIR" -U postgres -A trust -E UTF8 --locale=C >/dev/null
$RUN pg_ctl -D "$DIR" -o "-p $PORT -k /tmp" -l "$DIR/log" start -w >/dev/null
PSQL="psql -h /tmp -p $PORT -U postgres -d postgres -v ON_ERROR_STOP=1 -q"

$PSQL -f supabase/tests/shim_supabase.sql
$PSQL -f supabase/tests/atlas_antes.sql
for f in supabase/migrations/*.sql; do
  echo "migration: $(basename "$f")"
  $PSQL -f "$f"
done
$PSQL -f supabase/tests/atlas_depois.sql
$PSQL -f supabase/tests/regras.sql 2>&1 | sed "s/^psql:[^ ]* NOTICE:  //" | grep -E "^(ok |# |FALHOU|TODOS)|ERROR"
