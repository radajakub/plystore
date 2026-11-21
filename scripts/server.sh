#!/usr/bin/env bash
# The results database on a server with Docker Swarm and plyservice (kamino): everything DEPLOY.md does, one command per step. Every step looks
# first and changes only what is missing, asking before each change (--yes: without asking), so it runs again safely.
#
#   scripts/server.sh status        what is there: network, secrets, folders, the Postgres stack, schemas, store configurations, the nightly backup
#   scripts/server.sh setup         the network, the secrets (a new random password), the folders, the Postgres stack, the nightly backup
#   scripts/server.sh migrate       create and upgrade the schemas (after `make deploy RUNNER=1`: in the runner, with plybench's environment)
#   scripts/server.sh import        migrate, copy what runs keep from plydata into Postgres, then copy again: the second pass must copy nothing
#   scripts/server.sh backup-check  a dump of every schema, one restored into a new database and compared table by table
#   scripts/server.sh switch        the store configurations (new results go to Postgres), then the import again for what was written meanwhile
#   scripts/server.sh rollback      the store configurations moved aside: every run writes files again
#   scripts/server.sh smoke         the experiment benchmarks/store_smoke (4 games of bots) in the store, for a first job through plyservice
#
# Paths: the ply folder is the one this checkout is in (PLY_HOST_ROOT); the database's folder (PLYSTORE_DATA) and the dumps (PLYSTORE_BACKUPS)
# sit beside it. plyservice's own secrets (tokens, keys) are set up as its README says; this script only adds plystore's.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
PLY_HOST_ROOT=${PLY_HOST_ROOT:-$(dirname "$HERE")}
VOLUME=$(dirname "$PLY_HOST_ROOT")
PLYSTORE_DATA=${PLYSTORE_DATA:-$VOLUME/plystore}
PLYSTORE_BACKUPS=${PLYSTORE_BACKUPS:-$VOLUME/plystore-backups}
NETWORK=plystore
STACK=plystore
# one schema per data folder; each repository writes into the schema of the folder it writes to (plybench_analysis analyses plydata/plybench)
SCHEMAS=(plybench plyagents)
STORES=(plybench:plybench plybench_analysis:plybench plyagents:plyagents)
# what runs keep in the store, per data folder; logs, review pages, provider batch state and experiments/exploration stay files
IMPORTED='experiments/benchmarks experiments/training results analysis/*/reasoning/annotations.jsonl analysis/*/reasoning/consistency.jsonl analysis/*/reasoning/decisions.jsonl analysis/*/reasoning/responses analysis/*/reasoning/codebook*.json'
YES=0

say() { printf '%s\n' "$*"; }
fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}
ask() {
  # yes when --yes was given or the answer is y
  [ "$YES" = 1 ] && return 0
  local answer
  read -r -p "$1 [y/N] " answer </dev/tty
  [ "$answer" = y ] || [ "$answer" = Y ]
}
container() { docker ps -qf "name=$1" 2>/dev/null | head -1 || true; }
postgres() {
  local found
  found=$(container "${STACK}_postgres")
  [ -n "$found" ] || fail "the Postgres stack is not running: scripts/server.sh setup"
  echo "$found"
}
runner() {
  # the runner container: it has the database URL secret, the plystore network and the repositories' environments
  local found
  found=$(container plyservice_runner)
  [ -n "$found" ] || fail "plyservice's runner is not running: make deploy RUNNER=1 (laptop)"
  docker exec "$found" test -s /run/secrets/plystore_database_url || fail "the runner has no database URL secret: scripts/server.sh setup, then make deploy RUNNER=1 (laptop)"
  docker exec "$found" test -x /ply/plybench/.venv/bin/plystore-admin || fail "plybench's environment has no plystore: refresh (make deploy) to a plybench with the store extra"
  echo "$found"
}
in_runner() {
  # a command in the runner, with the database URL of its secret (-i: standard input passed on)
  docker exec -i "$RUNNER" bash -c "export PLYSTORE_DATABASE_URL=\$(cat /run/secrets/plystore_database_url); $1"
}
mkfolder() {
  [ -d "$1" ] && return 0
  ask "create the folder $1?" || fail "the folder $1 is needed"
  mkdir -p "$1" 2>/dev/null || sudo mkdir -p "$1"
  [ -w "$1" ] || [ "$2" != mine ] || sudo chown "$(id -u):$(id -g)" "$1"
}

status() {
  local found
  say "ply folder:   $PLY_HOST_ROOT"
  docker network inspect "$NETWORK" >/dev/null 2>&1 && say "network:      $NETWORK" || say "network:      MISSING ($NETWORK)"
  for secret in plystore_db_password plystore_database_url; do
    docker secret inspect "$secret" >/dev/null 2>&1 && say "secret:       $secret" || say "secret:       MISSING ($secret)"
  done
  [ -d "$PLYSTORE_DATA" ] && say "data:         $PLYSTORE_DATA" || say "data:         MISSING ($PLYSTORE_DATA)"
  [ -d "$PLYSTORE_BACKUPS" ] && say "backups:      $PLYSTORE_BACKUPS ($(find "$PLYSTORE_BACKUPS" -name '*.dump' | wc -l | tr -d ' ') dumps)" || say "backups:      MISSING ($PLYSTORE_BACKUPS)"
  found=$(container "${STACK}_postgres")
  if [ -n "$found" ]; then
    say "postgres:     $(docker inspect -f '{{.State.Health.Status}}' "$found")"
    for schema in "${SCHEMAS[@]}"; do
      say "schema:       $schema at $(docker exec "$found" psql -U plystore -d plystore -tAc "SELECT version_num FROM \"$schema\".alembic_version" 2>/dev/null || echo 'none (scripts/server.sh migrate)')"
    done
  else
    say "postgres:     NOT RUNNING (scripts/server.sh setup)"
  fi
  [ -n "$(container plyservice_runner)" ] && say "runner:       running" || say "runner:       NOT RUNNING (make deploy RUNNER=1)"
  for pair in "${STORES[@]}"; do
    [ -f "$PLY_HOST_ROOT/.plyservice/stores/${pair%%:*}.toml" ] && say "store:        ${pair%%:*} in Postgres" || say "store:        ${pair%%:*} as files (scripts/server.sh switch)"
  done
  crontab -l 2>/dev/null | grep -qF "$HERE/scripts/backup.sh" && say "nightly dump: in the crontab" || say "nightly dump: MISSING (scripts/server.sh setup)"
}

setup() {
  docker info -f '{{.Swarm.LocalNodeState}}' | grep -q active || fail "Docker Swarm is not active on this machine"
  if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    ask "create the overlay network $NETWORK?" && docker network create --driver overlay --attachable "$NETWORK" >/dev/null && say "network $NETWORK created"
  fi
  local password=0 url=0
  docker secret inspect plystore_db_password >/dev/null 2>&1 && password=1
  docker secret inspect plystore_database_url >/dev/null 2>&1 && url=1
  if [ "$password$url" = 10 ] || [ "$password$url" = 01 ]; then
    fail "only one of plystore_db_password and plystore_database_url exists; they must hold the same password: remove the one there (docker secret rm) and run setup again"
  fi
  if [ "$password$url" = 00 ] && ask "create the secrets plystore_db_password and plystore_database_url (a new random password)?"; then
    local secret
    secret=$(openssl rand -hex 24)
    printf %s "$secret" | docker secret create plystore_db_password - >/dev/null
    printf %s "postgresql://plystore:$secret@${STACK}_postgres:5432/plystore" | docker secret create plystore_database_url - >/dev/null
    unset secret
    say "secrets created; the password is only in them"
  fi
  mkfolder "$PLYSTORE_DATA" root
  mkfolder "$PLYSTORE_BACKUPS" mine
  if [ -z "$(container "${STACK}_postgres")" ] && ask "deploy the stack $STACK (Postgres, data in $PLYSTORE_DATA)?"; then
    PLYSTORE_DATA=$PLYSTORE_DATA docker stack deploy -c "$HERE/docker/docker-stack.yml" "$STACK"
    say "waiting for Postgres..."
    local found
    for _ in $(seq 60); do
      found=$(container "${STACK}_postgres")
      [ -n "$found" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$found")" = healthy ] && break
      sleep 2
    done
    [ -n "$(container "${STACK}_postgres")" ] || fail "Postgres did not start: docker service ps ${STACK}_postgres"
    say "Postgres is up"
  fi
  local line="15 3 * * * $HERE/scripts/backup.sh \$(docker ps -qf name=${STACK}_postgres) $PLYSTORE_BACKUPS ${SCHEMAS[*]} >> $PLYSTORE_BACKUPS/backup.log 2>&1"
  if ! crontab -l 2>/dev/null | grep -qF "$HERE/scripts/backup.sh" && ask "add the nightly dump (03:15) to your crontab?"; then
    (
      crontab -l 2>/dev/null || true
      echo "$line"
    ) | crontab -
    say "nightly dump added; copy $PLYSTORE_BACKUPS off this machine (e.g. rsync from the laptop)"
  fi
  say "next: make deploy RUNNER=1 (laptop), then scripts/server.sh import"
}

migrate() {
  RUNNER=$(runner)
  in_runner "/ply/plybench/.venv/bin/plystore-admin migrate --schema ${SCHEMAS[*]}" </dev/null
}

copy_all() {
  # one plystore-copy per imported prefix of every data folder, through the import configurations (no also_read: with it every file would count as
  # already there); prints each summary line
  in_runner "
for pair in ${STORES[*]}; do
  folder=\${pair##*:}
  case \" \$done \" in *\" \$folder \"*) continue ;; esac
  done=\"\$done \$folder\"
  cd /ply/plydata/\$folder
  for p in $IMPORTED; do
    [ -e \"\$p\" ] || continue
    echo \"== \$folder \$p\"
    /ply/plybench/.venv/bin/plystore-copy /ply/plydata/\$folder /ply/.plyservice/import/\$folder.toml --target-root /ply/plydata/\$folder --prefix \"\$p\"
  done
done"
}

import() {
  migrate
  mkdir -p "$PLY_HOST_ROOT/.plyservice/import"
  for schema in "${SCHEMAS[@]}"; do
    printf '[postgres]\nschema = "%s"\n\n[[route]]\nprefix = ""\nstore = "postgres"\n' "$schema" >"$PLY_HOST_ROOT/.plyservice/import/$schema.toml"
  done
  mkdir -p "$PLYSTORE_BACKUPS"
  say "copying (about half an hour the first time; run it in tmux); log: $PLYSTORE_BACKUPS/import.log"
  # a copy that finds a conflict exits non-zero; the second pass below reports it
  copy_all </dev/null 2>&1 | tee -a "$PLYSTORE_BACKUPS/import.log" || true
  say "the second pass: everything must be there already"
  local again
  again=$(copy_all </dev/null 2>&1 | tee -a "$PLYSTORE_BACKUPS/import.log" || true)
  # one summary per copy, and every one copied nothing: no summary at all is a copy that did not run
  local summaries clean
  summaries=$(echo "$again" | grep -cE '^[0-9]+ copied' || true)
  clean=$(echo "$again" | grep -cE '^0 copied, [0-9]+ already there, 0 conflicts, 0 failed$' || true)
  if [ "$summaries" = 0 ] || [ "$summaries" != "$clean" ]; then
    echo "$again" | grep -vE '^== |^0 copied, [0-9]+ already there, 0 conflicts, 0 failed$' >&2 || true
    fail "the second pass copied something, found conflicts, or did not run (above); see $PLYSTORE_BACKUPS/import.log"
  fi
  say "imported: the second pass found everything there"
}

backup_check() {
  local found database dump same
  found=$(postgres)
  "$HERE/scripts/backup.sh" "$found" "$PLYSTORE_BACKUPS" "${SCHEMAS[@]}"
  database="plystore_restore_check_$(date -u +%Y%m%d%H%M%S)"
  for schema in "${SCHEMAS[@]}"; do
    dump=$(ls -t "$PLYSTORE_BACKUPS/$schema-"*.dump | head -1)
    "$HERE/scripts/restore.sh" "$found" "$dump" "$database"
    same=0
    "$HERE/scripts/compare.sh" "$found" "$schema" plystore "$database" || same=$?
    docker exec "$found" dropdb -U plystore "$database"
    [ "$same" = 0 ] || fail "the restored $schema differs from the live one"
  done
  say "backups restore to the same data"
}

switch() {
  local folder=$PLY_HOST_ROOT/.plyservice/stores
  ask "switch ${STORES[*]%%:*} to Postgres (new results go to the database; files written before stay readable)?" || exit 0
  mkdir -p "$folder"
  for pair in "${STORES[@]}"; do
    printf '[postgres]\nschema = "%s"\n\n[[route]]\nprefix = ""\nstore = "postgres"\nalso_read = ["files"]\n\n[[route]]\nprefix = "plots"\nstore = "files"\n\n[[route]]\nprefix = "cache"\nstore = "files"\n' "${pair##*:}" >"$folder/${pair%%:*}.toml"
  done
  say "switched; plyservice reads $folder for every command (status shows the store check)"
  RUNNER=$(runner)
  say "catching up: what runs wrote as files before the switch"
  copy_all </dev/null 2>&1 | tee -a "$PLYSTORE_BACKUPS/import.log" || true
}

rollback() {
  local folder=$PLY_HOST_ROOT/.plyservice/stores aside
  [ -n "$(ls "$folder"/*.toml 2>/dev/null)" ] || fail "no store configurations in $folder: results are files already"
  aside="$folder.off-$(date -u +%Y%m%dT%H%M%SZ)"
  ask "move the store configurations to $aside (every run writes files again)?" || exit 0
  mv "$folder" "$aside"
  say "rolled back; what was written to Postgres since the switch stays there"
}

smoke() {
  RUNNER=$(runner)
  in_runner "cd /ply/plybench && PLYBENCH_STORE=plystore PLYSTORE_CONFIG=/ply/.plyservice/stores/plybench.toml .venv/bin/plybench-experiment put benchmarks/store_smoke -" <<'EOF'
{"game_configs": [{"value": "tic_tac_toe:", "enabled": true}], "player_configs": [{"value": "random:distribution=uniform", "enabled": true}],
 "opponents": [{"value": "mcts:max_simulations=30,rollout_count=1,uct_c=2.0", "enabled": true}], "num_games": 4, "colour_rule": "first_starts", "seed": 1}
EOF
  say "next: plan and submit plybench / benchmarks / store_smoke through plyservice"
}

[ "${2:-}" = --yes ] && YES=1
case "${1:-}" in
  status) status ;;
  setup) setup ;;
  migrate) migrate ;;
  import) RUNNER=$(runner) && import ;;
  backup-check) backup_check ;;
  switch) switch ;;
  rollback) rollback ;;
  smoke) smoke ;;
  *)
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
    ;;
esac
