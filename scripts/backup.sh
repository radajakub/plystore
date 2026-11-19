#!/usr/bin/env bash
# Dump every plystore database from a running Postgres container into a folder, one file per database and run:
#   scripts/backup.sh <postgres container> <backup folder> [database ...]
# e.g. scripts/backup.sh plystore-postgres-1 ~/plystore-backups (local compose), or on kamino with the container of the stack's postgres service
# (docker ps -qf name=plystore_postgres). Files are pg_dump's custom format, <database>-<UTC time>.dump, written under a temporary name and
# renamed when complete. Old backups are never removed here: keeping or pruning them, and copying them off the machine, is up to the caller.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <postgres container> <backup folder> [database ...]" >&2
  exit 2
fi
container=$1
folder=$2
shift 2
databases=("$@")
if [ ${#databases[@]} -eq 0 ]; then
  databases=(plybench plyagents plybench_analysis)
fi
user=${PGUSER:-plystore}
stamp=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$folder"
for database in "${databases[@]}"; do
  target="$folder/$database-$stamp.dump"
  docker exec "$container" pg_dump -U "$user" -Fc -d "$database" > "$target.tmp"
  mv "$target.tmp" "$target"
  echo "$target $(wc -c < "$target" | tr -d ' ') bytes"
done
