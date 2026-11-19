#!/usr/bin/env bash
# Restore one schema dump (scripts/backup.sh) into a new database of a running Postgres container:
#   scripts/restore.sh <postgres container> <dump file> <new database>
# Refuses a database that exists: a restore never writes over data. To put a restored schema in place of a live one, stop the writers,
# rename the schemas in psql (ALTER SCHEMA ... RENAME TO ...) or move the restored database in place, and start them again.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <postgres container> <dump file> <new database>" >&2
  exit 2
fi
container=$1
dump=$2
database=$3
user=${PGUSER:-plystore}

exists=$(docker exec "$container" psql -U "$user" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$database'")
if [ "$exists" = "1" ]; then
  echo "database $database exists; restore into a new name" >&2
  exit 1
fi
docker exec "$container" createdb -U "$user" "$database"
docker exec -i "$container" pg_restore -U "$user" -d "$database" --no-owner --exit-on-error < "$dump"
echo "restored $dump into $database"
