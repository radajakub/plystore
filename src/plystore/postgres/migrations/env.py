from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, text

from plystore.postgres.schema import METADATA

# Run by alembic for plystore-admin (migrations/__init__.py), never on its own: the caller passes an open connection and the schema to migrate.
# Tables are created unqualified, in the schema the search_path names; the version table is the schema's own, so each schema migrates alone.

connection: Connection = context.config.attributes["connection"]
schema: str = context.config.attributes["schema"]
connection.execute(text(f'SET search_path TO "{schema}"'))
# alembic's own version table is in the schema too: not a table of the formats, never to be dropped by a generated migration
context.configure(
    connection=connection,
    target_metadata=METADATA,
    version_table_schema=schema,
    include_name=lambda name, kind, _: not (kind == "table" and name == "alembic_version"),
)
with context.begin_transaction():
    context.run_migrations()
