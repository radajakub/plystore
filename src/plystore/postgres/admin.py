from __future__ import annotations

import argparse
import os
from collections import Counter

from sqlalchemy import text

from plystore.postgres import migrations
from plystore.postgres.store import PostgresStore, engine_for

# What only an administrator does, with a role that owns the schemas: migrate them, generate the next migration, create the roles writers and
# readers log in through, and delete an experiment. Runs never do any of these. The database URL comes from --url or PLYSTORE_DATABASE_URL.


def _roles(schema: str) -> list[str]:
    # group roles (no login): writers of a schema may add, replace and remove rows there (a replace removes the old rows) but not create, drop
    # or alter tables, and nothing in other schemas; readers may read every schema they are given. Login roles are granted these by hand.
    write, read = f"plystore_{schema}_write", "plystore_read"
    statements = [f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$" for role in (write, read)]
    for role, rights in ((write, "SELECT, INSERT, UPDATE, DELETE"), (read, "SELECT")):
        statements += [
            f'GRANT USAGE ON SCHEMA "{schema}" TO {role}',
            f'GRANT {rights} ON ALL TABLES IN SCHEMA "{schema}" TO {role}',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT {rights} ON TABLES TO {role}',
        ]
    statements += [f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO {write}', f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE ON SEQUENCES TO {write}']
    return statements


def main() -> None:
    parser = argparse.ArgumentParser(description="Administer plystore's PostgreSQL schemas (one per repo).")
    parser.add_argument("--url", default=os.environ.get("PLYSTORE_DATABASE_URL"), help="the database URL (default: PLYSTORE_DATABASE_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate", help="create the schemas if missing and upgrade them to the migration of this plystore")
    migrate.add_argument("--schema", nargs="+", required=True)
    revision = commands.add_parser("revision", help="generate the next migration from the formats (in a scratch schema, rolled back)")
    revision.add_argument("--message", required=True)
    roles = commands.add_parser("roles", help="create the group roles of a schema and grant them its tables")
    roles.add_argument("--schema", nargs="+", required=True)
    remove = commands.add_parser("delete", help="delete every record under a key prefix (lists them first; deletes only with --yes)")
    remove.add_argument("--schema", required=True)
    remove.add_argument("--prefix", required=True)
    remove.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.url:
        parser.error("give --url or set PLYSTORE_DATABASE_URL")

    if args.command == "migrate":
        for schema in args.schema:
            with engine_for(args.url, schema).begin() as conn:
                before = migrations.current(conn, schema)
                migrations.upgrade(conn, schema)
                print(f"{schema}: {before} -> {migrations.current(conn, schema)}")
    elif args.command == "revision":
        with engine_for(args.url, "plystore_revision").connect() as conn:
            conn.begin()
            migrations.upgrade(conn, "plystore_revision")
            migrations.revision(conn, "plystore_revision", args.message)
            conn.rollback()
    elif args.command == "roles":
        for schema in args.schema:
            with engine_for(args.url, schema).begin() as conn:
                for statement in _roles(schema):
                    conn.execute(text(statement))
            print(f"{schema}: roles plystore_{schema}_write and plystore_read granted")
    else:
        store = PostgresStore(args.url, args.schema)
        keys = store.keys(args.prefix)
        folders = Counter("/".join(key.split("/")[: len(args.prefix.split("/")) + 2]) for key in keys)
        print(f"{len(keys)} records under {args.prefix} in {args.schema}")
        for folder, count in sorted(folders.items()):
            print(f"  {count:>8}  {folder}")
        if not args.yes:
            print("nothing deleted: run again with --yes to delete them")
            return
        print(f"deleted {store.delete(args.prefix)} records")
