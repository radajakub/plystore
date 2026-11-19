from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text

# The schema's migrations (alembic, versions/): upgrade a schema to the head the formats were made for, check that a schema is at it, and
# generate the next revision after a format changed. The version table lives in each schema, so schemas migrate one by one.

FOLDER = Path(__file__).parent


def _config(conn: Connection, schema: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(FOLDER))
    config.attributes["connection"] = conn
    config.attributes["schema"] = schema
    return config


def head() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(FOLDER))
    return ScriptDirectory.from_config(config).get_current_head()


def current(conn: Connection, schema: str) -> str | None:
    if conn.execute(text("SELECT to_regclass(:table)"), {"table": f'"{schema}".alembic_version'}).scalar() is None:
        return None
    return conn.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar()


def check(conn: Connection, schema: str) -> None:
    there, wanted = current(conn, schema), head()
    if there != wanted:
        raise RuntimeError(f"schema {schema} is at migration {there}, this plystore writes for {wanted}: run plystore-admin migrate --schema {schema} (or update plystore)")


def upgrade(conn: Connection, schema: str) -> None:
    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    command.upgrade(_config(conn, schema), "head")


def revision(conn: Connection, schema: str, message: str) -> None:
    # autogenerate against a schema at head: what the formats changed since then
    command.revision(_config(conn, schema), message=message, autogenerate=True)
