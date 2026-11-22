# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `scripts/server.sh files-off` (a copy pass that must copy nothing, then the store configurations without `also_read`)
  and `slim-plydata` (the same pass, then what the import copied removed from plydata in one commit, pushed by
  plyservice).

### Fixed

- `scripts/compare.sh` (the backup check): a checksum per table without one table-sized string, which passed Postgres'
  1 GB limit on a large table; a query that fails now fails the comparison.

## [0.1.0] - 2026-11-21

### Added

- `Store`: one interface for records and append-only logs; `MemoryStore`, `FileStore` (plydata's layout and bytes),
  `RoutedStore` (prefixes to backends, `also_read` for data written before a switch); `open_store` from a
  `PLYSTORE_CONFIG` file, files when there is none, other backends through the `plystore.backends` entry points.
- Formats (`formats/`): the records of plybench, plyagents and plybench_analysis described as typed tables; `mapping`
  splits a record into rows and rebuilds it, anything that does not fit a column kept in `extra`.
- `PostgresStore` (extra `postgres`): records in typed tables, one schema per repository, history for codebooks and
  experiment files, shared texts, one transaction per write; migrations generated from the formats (Alembic);
  `plystore-admin` (migrate, revision, roles, delete); a local compose file.
- `plystore-check`: the round trip of every record of a data folder, without a database.
- `copy` and `scan`, with the commands `plystore-copy` and `plystore-scan`, for importing a data folder; `copy` moves
  records in batches of 200, reads them back, and falls back to one by one for a batch that fails; a log in batches of
  2,000 records (`append_many`, one transaction each).
- Backup scripts: one dump per schema, restore into a new database, compare every table.
