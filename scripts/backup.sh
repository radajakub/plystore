#!/usr/bin/env bash
# Dump the schemas of plystore's database from a running Postgres container into a folder, one file per schema and run:
#   scripts/backup.sh <postgres container> <backup folder> [schema ...]
# e.g. scripts/backup.sh plystore-postgres-1 ~/plystore-backups (local compose), or on kamino with the container of the stack's postgres service
# (docker ps -qf name=plystore_postgres). A dump holds one schema whole (every table of a schema is its own, records and texts too, and its
# migration version); files are pg_dump's custom format, <schema>-<UTC time>.dump, written under a temporary name and renamed when complete. Old backups
# are never removed here: keeping or pruning them, and copying them off the machine, is up to the caller.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <postgres container> <backup folder> [schema ...]" >&2
  exit 2
fi
container=$1
folder=$2
shift 2
schemas=("$@")
if [ ${#schemas[@]} -eq 0 ]; then
  schemas=(plybench plyagents plybench_analysis)
fi
user=${PGUSER:-plystore}
database=${PGDATABASE:-plystore}
stamp=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$folder"
for schema in "${schemas[@]}"; do
  target="$folder/$schema-$stamp.dump"
  docker exec "$container" pg_dump -U "$user" -Fc -d "$database" -n "$schema" > "$target.tmp"
  mv "$target.tmp" "$target"
  echo "$target $(wc -c < "$target" | tr -d ' ') bytes"
done
