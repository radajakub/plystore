#!/usr/bin/env bash
# Whether one schema holds the same data in two databases: per table, the row count and a checksum over its rows in a fixed order.
#   scripts/compare.sh <postgres container> <schema> <database> <other database>
# Used after a restore (make backup-check); prints the tables that differ and exits 1 when any does.
set -euo pipefail

if [ $# -ne 4 ]; then
  echo "usage: $0 <postgres container> <schema> <database> <other database>" >&2
  exit 2
fi
container=$1
schema=$2
user=${PGUSER:-plystore}

summary() {
  local tables
  tables=$(docker exec "$container" psql -U "$user" -d "$1" -tAc "SELECT tablename FROM pg_tables WHERE schemaname = '$schema' ORDER BY tablename")
  for table in $tables; do
    docker exec "$container" psql -U "$user" -d "$1" -tAc "SELECT '$table ' || count(*) || ' ' || coalesce(md5(string_agg(t::text, ',' ORDER BY t::text)), '-') FROM \"$schema\".\"$table\" t"
  done
}

first=$(summary "$3")
second=$(summary "$4")
if [ "$first" != "$second" ]; then
  diff <(echo "$first") <(echo "$second") >&2 || true
  echo "the databases differ" >&2
  exit 1
fi
echo "$schema: $(echo "$first" | wc -l | tr -d ' ') tables, the same in $3 and $4"
