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
#   scripts/server.sh files-off     once every file is in Postgres (a copy pass that copies nothing), the configurations without also_read
#   scripts/server.sh slim-plydata  after files-off: what was imported removed from plydata (git rm, one commit, pushed by plyservice)
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

everything_there() {
  # a copy pass that must copy nothing: one summary per copy, and every one copied nothing (no summary at all is a copy that did not run)
  local again summaries clean
  again=$(copy_all </dev/null 2>&1 | tee -a "$PLYSTORE_BACKUPS/import.log" || true)
  summaries=$(echo "$again" | grep -cE '^[0-9]+ copied' || true)
  clean=$(echo "$again" | grep -cE '^0 copied, [0-9]+ already there, 0 conflicts, 0 failed$' || true)
  if [ "$summaries" = 0 ] || [ "$summaries" != "$clean" ]; then
    echo "$again" | grep -vE '^== |^0 copied, [0-9]+ already there, 0 conflicts, 0 failed$' >&2 || true
    return 1
  fi
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
  everything_there || fail "the second pass copied something, found conflicts, or did not run (above); see $PLYSTORE_BACKUPS/import.log"
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

store_configs() {
  # one configuration per repository: everything to Postgres in its folder's schema, plots and cache to files; $1 = also_read: the files written
  # before the switch stay readable (until files-off)
  local folder=$PLY_HOST_ROOT/.plyservice/stores read=""
  [ "$1" = also_read ] && read='also_read = ["files"]\n'
  mkdir -p "$folder"
  for pair in "${STORES[@]}"; do
    printf "[postgres]\nschema = \"%s\"\n\n[[route]]\nprefix = \"\"\nstore = \"postgres\"\n${read}\n[[route]]\nprefix = \"plots\"\nstore = \"files\"\n\n[[route]]\nprefix = \"cache\"\nstore = \"files\"\n" "${pair##*:}" >"$folder/${pair%%:*}.toml"
  done
}

switch() {
  local folder=$PLY_HOST_ROOT/.plyservice/stores
  ask "switch ${STORES[*]%%:*} to Postgres (new results go to the database; files written before stay readable)?" || exit 0
  store_configs also_read
  say "switched; plyservice reads $folder for every command (status shows the store check)"
  RUNNER=$(runner)
  say "catching up: what runs wrote as files before the switch"
  copy_all </dev/null 2>&1 | tee -a "$PLYSTORE_BACKUPS/import.log" || true
}

files_off() {
  # the files' last use: once a copy pass finds every file in Postgres, the configurations stop reading them (also_read), so plydata's copies can go
  local folder=$PLY_HOST_ROOT/.plyservice/stores aside
  grep -qs 'also_read' "$folder"/*.toml || fail "no configuration in $folder reads files: nothing to do"
  RUNNER=$(runner)
  say "a copy pass that must copy nothing (every file already in Postgres); log: $PLYSTORE_BACKUPS/import.log"
  everything_there || fail "the pass copied something or found conflicts (above): see $PLYSTORE_BACKUPS/import.log; run files-off again once it copies nothing"
  aside="$folder.with-files-$(date -u +%Y%m%dT%H%M%SZ)"
  ask "no job running? stop reading the files (the configurations kept in $aside)?" || exit 0
  cp -R "$folder" "$aside"
  store_configs ""
  say "files off: every command reads and writes Postgres only (plots and cache stay files); the plydata copies are no longer read"
}

slim_plydata() {
  # what the import copied (IMPORTED, per data folder) removed from plydata once nothing reads it: git rm (its history keeps it), each data link's
  # folder kept with a .keep (plyservice's check needs the links to resolve), one commit of only that as plyservice, pushed from its container (its
  # key); untracked files in plydata are left alone
  local stores=$PLY_HOST_ROOT/.plyservice/stores data=$PLY_HOST_ROOT/plydata folder pattern count found specs=() folders=() imported
  [ -n "$(ls "$stores"/*.toml 2>/dev/null)" ] || fail "no store configurations in $stores: results are still files"
  ! grep -qs also_read "$stores"/*.toml || fail "the configurations still read the files: scripts/server.sh files-off first"
  [ -z "$(git -C "$data" status --porcelain --untracked-files=no)" ] || fail "plydata has uncommitted changes: commit and push them first"
  found=$(container plyservice_plyservice)
  [ -n "$found" ] || fail "plyservice is not running (it pushes the commit)"
  read -r -a imported <<<"$IMPORTED"
  for pair in "${STORES[@]}"; do
    folder=${pair##*:}
    case " ${folders[*]:-} " in *" $folder "*) continue ;; esac
    folders+=("$folder")
    # a pattern names files or folders: both forms, as a glob that names a folder does not match what is in it
    for pattern in "${imported[@]}"; do specs+=(":(glob)$folder/$pattern" ":(glob)$folder/$pattern/**"); done
    # but not the links' own .keep (written below)
    specs+=(":(exclude)$folder/experiments/.keep" ":(exclude)$folder/results/.keep")
  done
  count=$(git -C "$data" ls-files -- "${specs[@]}" | wc -l | tr -d ' ')
  [ "$count" != 0 ] || { say "plydata holds nothing the import copied"; return 0; }
  # a file written since files-off (by hand, by a run elsewhere) must reach Postgres before its copy here goes
  RUNNER=$(runner)
  say "a copy pass that must copy nothing (every file already in Postgres); log: $PLYSTORE_BACKUPS/import.log"
  everything_there || fail "the pass copied something or found conflicts (above): see $PLYSTORE_BACKUPS/import.log; run slim-plydata again once it copies nothing"
  ask "no job running? remove $count files from plydata (Postgres holds them; git's history keeps them), commit and push?" || exit 0
  # -f: the status is clean (checked above), and without it git looks up each file in HEAD, which rereads a large folder's tree once per file (hours)
  git -C "$data" rm -r -f -q --ignore-unmatch -- "${specs[@]}"
  for folder in "${folders[@]}"; do
    for link in experiments results; do
      mkdir -p "$data/$folder/$link"
      touch "$data/$folder/$link/.keep"
      git -C "$data" add -f "$folder/$link/.keep"
    done
  done
  git -C "$data" -c user.name=plyservice -c user.email=dev.jakubrada@icloud.com commit -q -m "[Data] Results, experiments and analysis records live in plystore's Postgres (slim-plydata)"
  docker exec "$found" git -C /ply/plydata push -q origin HEAD:master || fail "committed, but the push failed: push it with plyservice's push_data, or by hand"
  say "plydata slimmed: $count files removed in $(git -C "$data" rev-parse --short HEAD); on other clones: git pull (the files go there too)"
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
  files-off) files_off ;;
  slim-plydata) slim_plydata ;;
  rollback) rollback ;;
  smoke) smoke ;;
  *)
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
    ;;
esac
