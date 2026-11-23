# plystore on a server

The results database on a server with Docker Swarm and plyservice (kamino). Postgres runs as its own stack
([`docker/docker-stack.yml`](docker/docker-stack.yml)), reachable only on the overlay network `plystore`; plyservice's
stack joins that network, gets the database URL as a secret, and passes it to the commands it runs for a repository with
a store configuration in `/ply/.plyservice/stores`. [`scripts/server.sh`](scripts/server.sh) does every step on the
server: it looks first, changes only what is missing, and asks before each change, so it runs again safely.
`scripts/server.sh status` shows what is there at any time.

## From nothing

Before: Docker Swarm on the server, and plyservice set up as its README says (its own secrets, the ply checkout). The
commands run on the server in the ply folder, unless they say laptop.

```bash
git clone https://github.com/radajakub/plystore.git      # the stack file and the scripts; refresh keeps it up to date
plystore/scripts/server.sh setup     # the network, the secrets (a new random password), the folders, Postgres, the nightly dump
make deploy RUNNER=1                 # laptop, no job running: plyservice and its runner join the network and get the secret
plystore/scripts/server.sh import    # in tmux, about half an hour: the schemas, then plydata copied in, then a second pass that must copy nothing
plystore/scripts/server.sh backup-check   # a dump of every schema, restored into a new database and compared table by table
plystore/scripts/server.sh switch    # new results go to Postgres; then a copy of what was written as files meanwhile
plystore/scripts/server.sh smoke     # the experiment benchmarks/store_smoke (4 games of bots), for a first job
```

Then plan and submit `plybench` / `benchmarks` / `store_smoke` through plyservice: it should finish with its log, report
and record pushed and no result file in plydata, and `job_games` lists its 4 games. Copy the dumps off the server
(laptop, by hand or from its crontab): `rsync -a kamino.jakubrada.com:/mnt/HC_Volume_103899209/plystore-backups/ ~/plystore-backups/`.

`make deploy` ends with plyservice's checks: a failing one (the `store` check: a configuration that names no schema, no
database URL, a database that does not answer, an environment without plystore) is printed with what to do, and jobs are
refused until it passes. A repository not cloned on the server is printed with the command that clones it.

## What the steps decide

- One schema per data folder: `plybench` (plybench and plybench_analysis both write to plydata/plybench) and `plyagents`.
- The import copies what runs keep in the store: experiments (benchmarks and training), results, and the analysis records
  (annotations, verdicts, decisions, judge responses, codebooks). Logs, review pages, provider batch state and
  `experiments/exploration` stay files. Its configurations (`/ply/.plyservice/import`) route everything to Postgres
  without `also_read`: with it, every file would count as already there and nothing would be copied.
- The switch writes one configuration per repository: everything to Postgres, the files written before readable through
  `also_read` (a resumed job finds its old games and does not write them again), `plots` and `cache` to files.
  plyservice reads the folder for every command, so the switch needs no deploy.
- The password exists only in the two secrets; the database's folder and the dumps sit beside the ply folder
  (`PLYSTORE_DATA`, `PLYSTORE_BACKUPS` to put them elsewhere).

## Updating plystore later

A plystore release cascades from the ply folder (`make release REPO=plystore VERSION=X.Y.Z`): plybench relocks and is
released, then plybench_analysis and plyagents relock. On the server nothing changes until a deploy refreshes the
checkouts.

- **No new migration** (the release's CHANGELOG says when there is one): `make deploy` as usual.
- **A new migration:** plystore writes only to a schema at its own migration, older and newer refused alike, so the code
  and the schemas move together:
  1. no job running (a running job keeps its old environment and would be refused after the migration);
  2. `make deploy` (every environment gets the new plystore);
  3. `plystore/scripts/server.sh backup-check`, then `plystore/scripts/server.sh migrate`;
  4. plan again what was planned before: its worktree locks the old plystore.

The Postgres image is `postgres:16-alpine`: `docker service update --image postgres:16-alpine plystore_postgres` (no job
running; the database is down for a few seconds) takes the newest 16.x. A new major (17) needs a dump and a restore into
a new data folder, not an image change.

## Files off and a slim plydata

Once jobs have run on Postgres for a while, the copies in plydata can go. No job running, on the server in the ply folder:

```bash
plystore/scripts/server.sh files-off      # a copy pass that must copy nothing, then the configurations without also_read
plystore/scripts/server.sh slim-plydata   # the same pass again, then what the import copied removed from plydata: one commit, pushed
```

- `files-off` keeps the configurations it replaces beside them (`.plyservice/stores.with-files-<time>`). Compare an
  experiment's check before and after (`check_experiment`: games planned and left): the same counts mean Postgres has
  every game.
- `slim-plydata` removes exactly what the import copied (`IMPORTED` in the script), keeps each data link's folder with a
  `.keep` (plyservice's check needs the links to resolve), leaves untracked files alone, and commits only that, as
  plyservice, pushed from its container. Logs, job records and reports, review pages, provider batch state,
  `experiments/exploration` and `plyagents/archive` stay. Other clones get it with `git pull` (their files go too: analyse
  a downloaded dump instead).
- git's history still holds everything after `slim-plydata`. To shrink it (667 MB to 7 MB in November 2026), with no
  job running and nothing else pushing to plydata, on the laptop in the ply folder:

  ```bash
  plystore/scripts/rewrite-plydata.sh ~/plydata-rewrite.git   # a mirror clone rewritten by git filter-repo, checked, force-pushed after asking
  ```

  It drops every imported path from every commit (also from the root layout before 2026-10-21), checks that master ends
  in the same tree and that no commit touches a dropped path, and pushes only if GitHub's master has not moved. No copy
  of the old history is kept. Commit hashes change: those named in job records no longer resolve. Then replace every
  other clone, on the server in the ply folder:

  ```bash
  docker exec $(docker ps -qf name=plyservice_plyservice) git -C /ply/plydata fetch -q origin && git -C plydata reset -q --hard origin/master && git -C plydata reflog expire --expire=now --all && git -C plydata gc -q --prune=now
  ```

  (`reset --hard` keeps untracked and ignored files; the tree is the same, so no file changes.) On the laptop the same
  without `docker exec` (a fresh clone would lose the ignored cache). Never push from a clone that still has the old
  history: it brings it back.

## Rollback

`plystore/scripts/server.sh rollback` moves the store configurations aside: every command writes files again. What was
written to Postgres since the switch stays there; `plystore-copy` copies only from files, so copying it back to files
needs `copy(postgres, files)` from Python (not yet a command). After `slim-plydata` the files are gone from plydata too
(after `rewrite-plydata.sh` also from its history): a rollback then starts from Postgres only.

## Later (you)

- Prune old dumps.
