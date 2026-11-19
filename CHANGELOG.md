# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Store`: one interface for named documents and append-only logs; `MemoryStore`, `FileStore` (plydata's layout and
  bytes), `RoutedStore` (prefixes to backends, `also_read` for data written before a switch); `open_store` from a
  `PLYSTORE_CONFIG` file, files when there is none.
- `copy` and `scan`, with the commands `plystore-copy` and `plystore-scan`, for importing a data folder.
- The service (`plystore-serve`, extra `server`): the Store calls over HTTP on PostgreSQL, one database per repository,
  history of replaced documents; `ServiceStore`, its client; a Dockerfile and a local compose file.
- `get_many` on every backend; `copy` moves documents in batches of 200 and falls back to one by one for a batch that fails.
- The `games` table: typed columns of game records, kept by a trigger.
