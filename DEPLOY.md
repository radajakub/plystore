# plystore on kamino

How the results database is set up on kamino and how the runs are switched to it. Postgres runs as its own Swarm stack
([`docker/docker-stack.yml`](docker/docker-stack.yml)), reachable only on the overlay network `plystore`; plyservice's
stack joins that network and gets the database URL as a secret, and passes it to the commands it runs for a repository
with a store configuration. Every command below runs on kamino unless it says laptop.

Names used below:

```bash
V=/mnt/HC_Volume_103899209             # the volume: the ply checkout, the database's folder, the backups
B=$V/plystore-backups                  # the nightly dumps
R=$(docker ps -qf name=plyservice_runner)    # the runner container: has the secret, the network and the repositories' environments
P=$(docker ps -qf name=plystore_postgres)    # the Postgres container
```

## 1. Before (laptop)

- plystore and plyservice committed and pushed (the stack files, plyservice reading the URL secret).
- plyservice's GitHub token (`plyservice_github_token`) can read the plystore repository, so refresh clones it.
- No job running: step 3 redeploys the runner.

## 2. Network, secrets, folders

```bash
docker network create --driver overlay --attachable plystore
PW=$(openssl rand -hex 24)
printf %s "$PW" | docker secret create plystore_db_password -
printf %s "postgresql://plystore:$PW@plystore_postgres:5432/plystore" | docker secret create plystore_database_url -
unset PW
sudo mkdir -p $V/plystore $B && sudo chown jr $B
```

The password exists only in the two secrets. `$V/plystore` stays empty: Postgres makes it its own on first start.

## 3. Deploy

```bash
# laptop: the new plyservice (network, secret, PLYSERVICE_STORE_CONFIGS) and its runner
make deploy RUNNER=1
```

The deploy ends with a refresh of the checkout there: every checkout moves to plybench 5.1.0 and plystore 0.1.0, and
the plystore checkout appears in the ply folder (if not, MCP `refresh`). The folder `/ply/.plyservice/stores` is still
empty, so every run still writes files.

```bash
sudo mkdir -p /srv/stacks/plystore && sudo cp $V/ply/plystore/docker/docker-stack.yml /srv/stacks/plystore/
docker stack deploy -c /srv/stacks/plystore/docker-stack.yml plystore
docker service ps plystore_postgres          # Running
R=$(docker ps -qf name=plyservice_runner); P=$(docker ps -qf name=plystore_postgres)
docker exec $R bash -c 'export PLYSTORE_DATABASE_URL=$(cat /run/secrets/plystore_database_url); /ply/plybench/.venv/bin/plystore-admin migrate --schema plybench plyagents'
```

One schema per data folder: `plybench` (plybench and plybench_analysis both write to plydata/plybench) and
`plyagents`.

## 4. Import

The import configurations route everything to Postgres without `also_read` (with it, every file would count as
already there and nothing would be copied). They are not in `stores/`, so no run uses them.

```bash
docker exec $R bash -c 'mkdir -p /ply/.plyservice/import && for s in plybench plyagents; do printf "[postgres]\nschema = \"%s\"\n\n[[route]]\nprefix = \"\"\nstore = \"postgres\"\n" $s > /ply/.plyservice/import/$s.toml; done'
```

Only what runs keep in the store: experiments, results, and the analysis records (annotations, verdicts, decisions, judge
responses, codebooks). Logs, review pages and provider batch state under `analysis/` stay files, as does
`experiments/exploration`. About 600,000 records, roughly half an hour; run it in tmux on the host:

```bash
tmux new -s import
docker exec $R bash -c '
export PLYSTORE_DATABASE_URL=$(cat /run/secrets/plystore_database_url)
for s in plybench plyagents; do
  cd /ply/plydata/$s
  for p in experiments/benchmarks experiments/training results analysis/*/reasoning/annotations.jsonl analysis/*/reasoning/consistency.jsonl analysis/*/reasoning/decisions.jsonl analysis/*/reasoning/responses analysis/*/reasoning/codebook*.json; do
    [ -e "$p" ] || continue
    echo "== $s $p"
    /ply/plybench/.venv/bin/plystore-copy /ply/plydata/$s /ply/.plyservice/import/$s.toml --target-root /ply/plydata/$s --prefix "$p"
  done
done' 2>&1 | tee -a $B/import.log
```

Each copy reads every record back. Run the same command again: every line must say `0 copied, N already there, 0
conflicts, 0 failed`.

## 5. Backups

A first dump, and a restore into a new database compared table by table:

```bash
$V/ply/plystore/scripts/backup.sh $P $B plybench plyagents
$V/ply/plystore/scripts/restore.sh $P $(ls -t $B/plybench-*.dump | head -1) plystore_restore_check
$V/ply/plystore/scripts/compare.sh $P plybench plystore plystore_restore_check; docker exec $P dropdb -U plystore plystore_restore_check
```

Nightly, from jr's crontab (`crontab -e`):

```
15 3 * * * /mnt/HC_Volume_103899209/ply/plystore/scripts/backup.sh $(docker ps -qf name=plystore_postgres) /mnt/HC_Volume_103899209/plystore-backups plybench plyagents >> /mnt/HC_Volume_103899209/plystore-backups/backup.log 2>&1
```

Off the machine (laptop, by hand or from its crontab):
`rsync -a kamino.jakubrada.com:/mnt/HC_Volume_103899209/plystore-backups/ ~/plystore-backups/`. Old dumps are never
removed by the script.

## 6. Switch

One configuration per repository in `/ply/.plyservice/stores`; plyservice reads the folder for every command, so the
switch needs no deploy. New results go to Postgres; the files written before stay readable through `also_read`, and a
resumed job finds its old games there and does not write them again.

```bash
docker exec $R bash -c 'mkdir -p /ply/.plyservice/stores && cd /ply/.plyservice/stores && for pair in plybench:plybench plybench_analysis:plybench plyagents:plyagents; do printf "[postgres]\nschema = \"%s\"\n\n[[route]]\nprefix = \"\"\nstore = \"postgres\"\nalso_read = [\"files\"]\n\n[[route]]\nprefix = \"plots\"\nstore = \"files\"\n\n[[route]]\nprefix = \"cache\"\nstore = \"files\"\n" ${pair##*:} > ${pair%%:*}.toml; done'
```

Then the import command of step 4 once more, for what runs wrote as files between the import and the switch.

## 7. A test job

A benchmark of bots only (4 games, a few seconds, no model), saved as a new experiment in the store:

```bash
docker exec -i $R bash -c 'cd /ply/plybench && PLYBENCH_STORE=plystore PLYSTORE_CONFIG=/ply/.plyservice/stores/plybench.toml PLYSTORE_DATABASE_URL=$(cat /run/secrets/plystore_database_url) .venv/bin/plybench-experiment put benchmarks/store_smoke -' <<'EOF'
{"game_configs": [{"value": "tic_tac_toe:", "enabled": true}], "player_configs": [{"value": "random:distribution=uniform", "enabled": true}],
 "opponents": [{"value": "mcts:max_simulations=30,rollout_count=1,uct_c=2.0", "enabled": true}], "num_games": 4, "colour_rule": "first_starts", "seed": 1}
EOF
```

Then through plyservice: plan and submit `plybench` / `benchmarks` / `store_smoke`. It should finish with its log, report
and record pushed and no result file in plydata; `job_games` lists 4 games and `job_trace` reads one; and

```bash
docker exec $P psql -U plystore -d plystore -c "SELECT count(*) FROM plybench.games WHERE key LIKE 'results/benchmarks/store_smoke/%'"   # 4
```

## Rollback

Remove the files in `/ply/.plyservice/stores`: every command writes files again. What was written to Postgres since the
switch stays there; `plystore-copy` copies only from files, so copying it back to files needs `copy(postgres, files)`
from Python (not yet a command).

## Later (you)

- Remove the imported folders from plydata with git (git keeps the old files), then drop `also_read` from the
  configurations.
- Prune old dumps.
