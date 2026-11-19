#!/usr/bin/env bash
# Whether two plystore databases hold the same data: row counts and a checksum over every document, log record and game row, in key order.
#   scripts/compare.sh <postgres container> <database> <other database>
# Used after a restore (make backup-check); prints both summaries and exits 1 when they differ.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <postgres container> <database> <other database>" >&2
  exit 2
fi
container=$1
user=${PGUSER:-plystore}
query="SELECT
  (SELECT count(*) || ' documents ' || coalesce(md5(string_agg(key || ':' || md5(coalesce(data::text, '') || '|' || coalesce(text, '')), ',' ORDER BY key)), '-') FROM documents),
  (SELECT count(*) || ' log records ' || coalesce(md5(string_agg(key || ':' || md5(record::text), ',' ORDER BY id)), '-') FROM logs),
  (SELECT count(*) || ' history rows ' || coalesce(md5(string_agg(key || ':' || md5(coalesce(data::text, '') || '|' || coalesce(text, '')), ',' ORDER BY key, replaced)), '-') FROM history),
  (SELECT count(*) || ' game rows ' || coalesce(md5(string_agg(key || ':' || folder || ':' || coalesce(result::text, ''), ',' ORDER BY key)), '-') FROM games)"

first=$(docker exec "$container" psql -U "$user" -d "$2" -tA -F ' | ' -c "$query")
second=$(docker exec "$container" psql -U "$user" -d "$3" -tA -F ' | ' -c "$query")
echo "$2: $first"
echo "$3: $second"
if [ "$first" != "$second" ]; then
  echo "the databases differ" >&2
  exit 1
fi
echo "same"
