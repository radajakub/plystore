# plystore

One storage interface for what ply runs write: named documents and append-only logs. A writer (plybench, plyagents,
plybench_analysis, plyservice) gets a `Store` and never knows where its data goes; which backend holds what is
configuration only.

- `MemoryStore`: everything in memory, for tests and for runs that leave nothing behind.
- `FileStore`: each document a file under a root folder, as plydata holds them today (same bytes, same layout).
- `RoutedStore`: key prefixes routed to backends (`plots/` to files, the rest elsewhere), with `also_read` stores that
  hold what was written before a switch.
- `ServiceStore`: the client of the plystore service (PostgreSQL), standard library only.

## Keys

A key names a document like a relative path, e.g. `results/benchmarks/ttt_10/a_b/game_1.json`. Its suffix says what it
holds: `.json` a JSON value, `.jsonl` a log of JSON records (`append`, `read_log`), anything else text. Parts may not
be empty, start with `.`, or end in `.tmp` or `.lock` (names the backends keep for themselves).

## The interface

| Call                                   | Does                                                                                      |
| -------------------------------------- | ----------------------------------------------------------------------------------------- |
| `put(key, value, replace=False)`       | write a document; write-once unless `replace` (`DocumentExists`)                          |
| `put_many({key: value})`               | several new documents, all or none                                                        |
| `get(key)`                             | the document, or `None`                                                                   |
| `get_many(keys)`                       | the documents of those keys that exist, in one call                                       |
| `exists(key)`, `keys(prefix)`          | whether it is there; every key under a prefix, by whole parts, sorted                     |
| `games(prefix)`                        | the typed columns of every game record under a prefix (`GameRow`); `summarize` per folder |
| `append(key, record)`, `read_log(key)` | one record at the end of a log; the records in order                                      |
| `delete(prefix)`                       | every document under a non-empty prefix (an admin action)                                 |

What PostgreSQL cannot hold (NaN, infinities, the NUL character) is refused by every backend, so a run finds out at
once, not after a switch.

## Configuration

`open_store(root)` reads the file `PLYSTORE_CONFIG` names; without one every key is a file under `root`, as today.

```toml
[[route]]
prefix = ""
store = "memory"
also_read = ["files"]

[[route]]
prefix = "plots"
store = "files"
```

## Commands

- `plystore-scan <data folder>`: read only; what an import would move (by kind and folder), the files it skips, and
  the documents no store would take.
- `plystore-copy <data folder> <config> --target-root <root>`: copy every document into the configured store; never
  overwrites; a key with other content there is reported as a conflict; safe to run again.

## The service

`plystore-serve` (extra `server`: FastAPI, uvicorn, psycopg) serves the Store calls under `/v1/{database}`, one
PostgreSQL database per repository (`plybench`, `plyagents`, `plybench_analysis`), created and migrated at start. It
checks keys and values with the client's own rules. Environment: `PLYSTORE_DATABASE_URL` (the server, without a
database), `PLYSTORE_DATABASES`, `PLYSTORE_TOKEN` (or `PLYSTORE_TOKEN_FILE`), `PLYSTORE_PORT` (8100).

Tables: `documents` (key, JSON or text, created), `history` (earlier versions of replaced documents), `logs`, and
`games`: typed columns of every game record (`folder`, `round`, `i_player`, `o_player`, `result`, `moves`, `seed`),
filled by a trigger on each write, for fast queries over many games; the document stays the record.

Locally: `make up` starts Postgres (127.0.0.1:55432) and the service (127.0.0.1:8100, token `local-token`) with
`docker/compose.yml`; `make down` stops them and keeps the data volume.

## Releases

`make release VERSION=X.Y.Z` (or ply's `make release REPO=plystore VERSION=X.Y.Z`) tags a GitHub Release; its workflow
publishes to PyPI by trusted publishing (`.github/workflows/publish.yml`). Backups: `scripts/backup.sh`,
`scripts/restore.sh` and `scripts/compare.sh`; `make backup-check` dumps, restores into a new database, and compares.

## Development

```sh
uv sync
make test           # without Postgres: the service tests are skipped
make up && make test-postgres
make lint
```
