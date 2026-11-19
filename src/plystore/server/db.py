from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from plystore.keys import DocumentExists

# The PostgreSQL side of the service: one database per repository (plybench, plyagents, plybench_analysis), created and migrated at start, each
# with its own connection pool. Every statement here takes encoded documents (the text the client's encode gave) and returns text, so JSON is
# parsed once, by Postgres, on the way in, and passed through as it is on the way out.

MIGRATIONS = Path(__file__).parent / "migrations"


def _range(prefix: str) -> tuple[str, list[str]]:
    # every key under a prefix by whole parts: the key itself, or a key in [prefix + "/", prefix + "0"), "0" being the byte after "/"
    if prefix == "":
        return "TRUE", []
    return "(key = %s OR (key >= %s AND key < %s))", [prefix, prefix + "/", prefix + "0"]


class Database:
    def __init__(self, conninfo: str) -> None:
        self.pool = ConnectionPool(conninfo, min_size=1, max_size=8, open=True)

    def close(self) -> None:
        self.pool.close()

    def migrate(self) -> list[str]:
        applied = []
        with self.pool.connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied timestamptz NOT NULL DEFAULT now())")
            done = {row[0] for row in connection.execute("SELECT name FROM schema_migrations")}
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if path.name not in done:
                    with connection.transaction():
                        connection.execute(path.read_text().encode())
                        connection.execute("INSERT INTO schema_migrations (name) VALUES (%s)", [path.name])
                    applied.append(path.name)
        return applied

    def put(self, key: str, data: str | None, text: str | None, replace: bool) -> None:
        with self.pool.connection() as connection, connection.transaction():
            if not replace:
                inserted = connection.execute("INSERT INTO documents (key, data, text) VALUES (%s, %s::jsonb, %s) ON CONFLICT (key) DO NOTHING", [key, data, text])
                if inserted.rowcount == 0:
                    raise DocumentExists(key)
                return
            # the version replaced goes to history; the same content again changes nothing
            inserted = connection.execute("INSERT INTO documents (key, data, text) VALUES (%s, %s::jsonb, %s) ON CONFLICT (key) DO NOTHING", [key, data, text])
            if inserted.rowcount == 1:
                return
            old = connection.execute("SELECT data = %s::jsonb, text = %s FROM documents WHERE key = %s FOR UPDATE", [data, text, key]).fetchone()
            if old is not None and not (old[0] or old[1]):
                connection.execute("INSERT INTO history (key, data, text, created) SELECT key, data, text, created FROM documents WHERE key = %s", [key])
                connection.execute("UPDATE documents SET data = %s::jsonb, text = %s, created = now() WHERE key = %s", [data, text, key])

    def put_many(self, rows: list[tuple[str, str | None, str | None]]) -> None:
        keys = [row[0] for row in rows]
        with self.pool.connection() as connection, connection.transaction():
            inserted = connection.execute(
                "INSERT INTO documents (key, data, text) SELECT key, data::jsonb, text FROM unnest(%s::text[], %s::text[], %s::text[]) AS t(key, data, text) "
                "ON CONFLICT (key) DO NOTHING RETURNING key",
                [keys, [row[1] for row in rows], [row[2] for row in rows]],
            ).fetchall()
            if len(inserted) != len(rows):
                # all or none: the transaction is rolled back by the exception
                raise DocumentExists(", ".join(sorted(set(keys) - {row[0] for row in inserted})))

    def get(self, key: str) -> tuple[str | None, str | None] | None:
        with self.pool.connection() as connection:
            row = connection.execute("SELECT data::text, text FROM documents WHERE key = %s", [key]).fetchone()
        return None if row is None else (row[0], row[1])

    def get_many(self, keys: list[str]) -> list[tuple[str, str | None, str | None]]:
        with self.pool.connection() as connection:
            return [(row[0], row[1], row[2]) for row in connection.execute("SELECT key, data::text, text FROM documents WHERE key = ANY(%s)", [keys])]

    def exists(self, key: str) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute("SELECT EXISTS (SELECT 1 FROM documents WHERE key = %s) OR EXISTS (SELECT 1 FROM logs WHERE key = %s)", [key, key]).fetchone()
        return bool(row and row[0])

    def keys(self, prefix: str) -> list[str]:
        where, values = _range(prefix)
        query = f"SELECT key FROM documents WHERE {where} UNION SELECT DISTINCT key FROM logs WHERE {where} ORDER BY 1"
        with self.pool.connection() as connection:
            return [row[0] for row in connection.execute(query.encode(), values + values)]

    def games(self, prefix: str) -> list[dict[str, Any]]:
        where, values = _range(prefix)
        query = f"SELECT key, folder, round, i_player, o_player, result, moves, seed FROM games WHERE {where} ORDER BY key"
        with self.pool.connection() as connection:
            rows = connection.execute(query.encode(), values).fetchall()
        # a numeric seed back as the JSON number it was: an integer as int
        return [
            {
                "key": row[0],
                "folder": row[1],
                "round": row[2],
                "i_player": row[3],
                "o_player": row[4],
                "result": row[5],
                "moves": row[6],
                "seed": None if row[7] is None else int(row[7]) if row[7] == row[7].to_integral_value() else float(row[7]),
            }
            for row in rows
        ]

    def append(self, key: str, record: str) -> None:
        with self.pool.connection() as connection:
            connection.execute("INSERT INTO logs (key, record) VALUES (%s, %s::jsonb)", [key, record])

    def read_log(self, key: str) -> list[str]:
        with self.pool.connection() as connection:
            return [row[0] for row in connection.execute("SELECT record::text FROM logs WHERE key = %s ORDER BY id", [key])]

    def delete(self, prefix: str) -> int:
        where, values = _range(prefix)
        with self.pool.connection() as connection, connection.transaction():
            documents = connection.execute(f"DELETE FROM documents WHERE {where}".encode(), values).rowcount
            logs = connection.execute(f"DELETE FROM logs WHERE {where} RETURNING key".encode(), values).fetchall()
            connection.execute(f"DELETE FROM history WHERE {where}".encode(), values)
        return documents + len({row[0] for row in logs})

    def truncate(self) -> None:
        # tests only: an empty database between tests
        with self.pool.connection() as connection:
            connection.execute("TRUNCATE documents, history, logs, games")


def open_databases(server: str, names: list[str]) -> dict[str, Database]:
    # each named database, created on the server when missing, then migrated
    with psycopg.connect(make_conninfo(server, dbname="postgres"), autocommit=True) as connection:
        there = {row[0] for row in connection.execute("SELECT datname FROM pg_database")}
        for name in names:
            if name not in there:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    databases = {name: Database(make_conninfo(server, dbname=name)) for name in names}
    for database in databases.values():
        database.migrate()
    return databases
