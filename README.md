# plystore

One storage interface for what ply runs write: records (JSON documents, or text) and append-only logs. A writer
(plybench, plyagents, plybench_analysis, plyservice) gets a `Store` and never knows where its data goes; which backend
holds what is configuration only.

- `MemoryStore`: everything in memory, for tests and for runs that leave nothing behind.
- `FileStore`: each record a file under a root folder, as plydata holds them today (same bytes, same layout).
- `RoutedStore`: key prefixes routed to backends (`plots/` to files, the rest elsewhere), with `also_read` stores that
  hold what was written before a switch.
- `PostgresStore` (extra `postgres`): each record in typed tables, one schema per repository.

The client (`plystore`) has no dependencies. The PostgreSQL backend comes with `plystore[postgres]` (SQLAlchemy,
psycopg, Alembic) and is loaded through the `plystore.backends` entry point only when a configuration names it.

plybench does not depend on plystore: it keeps its results through its own small store protocol, as files by default.
With `PLYBENCH_STORE=plystore` it opens this package's configured store instead (`open_store`, through the
`plybench.stores` entry point); plyagents and plybench_analysis write through plybench's store, so the same variable
covers them.

## Keys

A key names a record like a relative path, e.g. `results/benchmarks/ttt_10/a_b/game_1.json`. Its suffix says what it
holds: `.json` a JSON value, `.jsonl` a log of JSON records (`append`, `read_log`), anything else text. Parts may not
be empty, start with `.`, or end in `.tmp` or `.lock` (names the backends keep for themselves).

## The interface

| Call                                   | Does                                                                  |
| -------------------------------------- | --------------------------------------------------------------------- |
| `put(key, value, replace=False)`       | write a record; write-once unless `replace` (`DocumentExists`)        |
| `put_many({key: value})`               | several new records, all or none                                      |
| `get(key)`                             | the record, or `None`                                                 |
| `get_many(keys)`                       | the records of those keys that exist, in one call                     |
| `exists(key)`, `keys(prefix)`          | whether it is there; every key under a prefix, by whole parts, sorted |
| `append(key, record)`, `read_log(key)` | one record at the end of a log; the records in order                  |
| `delete(prefix)`                       | every record under a non-empty prefix (an admin action)               |

What PostgreSQL cannot hold (NaN, infinities, the NUL character, lone surrogates) is refused by every backend, so a run
finds out at once, not after a switch.

## Formats: records in typed tables

plystore follows the formats of the repositories that write; the repositories know nothing of it. `formats/` describes
each kind of record (a plybench game, matchup, training run, learner call list and LLM block log; a plyagents learner
state and update; a plybench_analysis annotation, verdict, decision, judge response and failure, codebook; an experiment
file) by its key pattern and a tree of nodes:

- `Column`: one field into a typed column (`str`, `int`, `float`, `bool`, `json`, lists of `str` or `int`; `shared` for
  long texts that repeat, kept once in `texts`);
- `Flatten`: the fields of a nested object into columns of the same row (`ending.result` → `end_result`);
- `Child`: a list of objects, or a dict of objects one or more levels deep, into rows of another table;
- `Choice`: the first of several shapes that takes the value whole (a memory of texts or a table of values).

A value goes into a column only if it has exactly the column's type; anything else (a null, another type, a field no
node names) stays in the row's `extra` (jsonb). So every record rebuilds to itself, types included, and nothing is
refused. A record no format matches is kept whole (`unmapped`). Tables are generated from the formats
(`postgres/schema.py`), and so are the migrations.

`plystore-check <data folder>` splits and rebuilds every record of a folder without a database and reports what did not
rebuild, what no store would take, and which fields have values outside the typed columns. ply's release checks it over
plydata for plybench, plyagents and plybench_analysis (the `plystore` contracts in plytools).

## PostgreSQL

One database, one schema per repository that ran the work (`plybench`, `plyagents`, `plybench_analysis`); every schema
has every table, so one repository's data is dumped or dropped as a whole. Training runs from the plyagents checkout,
so its plybench records are in the `plyagents` schema.

- `records`: one row per key (its format and time); every root row references it `ON DELETE CASCADE`, and every child
  row its parent, so `delete(prefix)` removes everything under the prefix.
- A format's root table: `key`, `folder` (records link by folder: a game's matchup is the `metadata.json` in its
  folder), `saved`, the columns, `extra`. Formats that keep history (codebooks, experiment files) mark a replaced row
  `replaced` instead of deleting it.
- A child table: `parent_id`, `position`, `part` (when several nodes share it), its dict key columns.
- A write is one transaction: a record with all its rows, or a `put_many` with all its records.

A writer refuses a schema whose migration is not the one its formats were made for. Administration (`plystore-admin`,
URL from `--url` or `PLYSTORE_DATABASE_URL`):

- `migrate --schema plybench plyagents plybench_analysis`: create and upgrade the schemas;
- `revision --message "..."`: generate the next migration after a format changed (in a scratch schema, rolled back);
- `roles --schema ...`: group roles `plystore_<schema>_write` (rows of that schema only) and `plystore_read`; login
  roles are granted them by hand;
- `delete --schema plybench --prefix results/benchmarks/ttt [--yes]`: list, then delete an experiment.

Locally: `make up` starts Postgres on 127.0.0.1:55432 (`docker/compose.yml`); `make down` stops it and keeps the volume.

## Configuration

`open_store(root)` reads the file `PLYSTORE_CONFIG` names; without one every key is a file under `root`, as today.

```toml
[postgres]
schema = "plyagents"
url_env = "PLYSTORE_DATABASE_URL"

[[route]]
prefix = ""
store = "postgres"
also_read = ["files"]

[[route]]
prefix = "plots"
store = "files"
```

## Commands

- `plystore-check <data folder>`: read only; the round trip of every record through its format.
- `plystore-scan <data folder>`: read only; what an import would move (by kind and folder), the files it skips, and
  the records no store would take.
- `plystore-copy <data folder> <config> --target-root <root>`: copy every record into the configured store and read it
  back; never overwrites; a key with other content there is reported as a conflict; safe to run again.

## Releases

`make release VERSION=X.Y.Z` (or ply's `make release REPO=plystore VERSION=X.Y.Z`) tags a GitHub Release; its workflow
publishes to PyPI by trusted publishing (`.github/workflows/publish.yml`). Backups: `scripts/backup.sh` (one dump per
schema), `scripts/restore.sh` and `scripts/compare.sh`; `make backup-check` dumps, restores into a new database, and
compares every table.

## Development

```sh
uv sync
make test           # without Postgres: the Postgres tests are skipped
make up && make test-postgres
make check-data DATA=../plydata/plybench
make demo           # files and Postgres side by side on one plydata folder (DEMO_ARGS=--keep to look around)
make lint
```
