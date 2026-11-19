from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Double, ForeignKey, Identity, Index, Integer, MetaData, SmallInteger, Table, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeEngine

from plystore import mapping
from plystore.formats import FORMATS

# The tables of every format (formats/), generated from the formats themselves, so the mapping and the database cannot drift apart; the migrations
# (migrations/versions) are generated from this metadata (plystore-admin revision). Tables carry no schema: a connection sets its search_path
# to the schema of the repo it writes for, and every schema has every table.
#
# - records: one row per key, the catalog (keys, exists, delete). Every root row references its key there, ON DELETE CASCADE, and every child
#   row its parent: deleting a prefix of records deletes everything under it.
# - texts: SHARED columns hold the sha256 of their text; the text is kept here once.
# - a format's root table: key, folder (records link by folder), saved, `replaced` for formats that keep history, the columns, extra.
# - a child table: parent_id, position (order in the list or dict), part (which node of several sharing it), its dict key columns.
# - unmapped / unmapped_lines: documents and log records no format keeps, whole.

KEY = Text(collation="C")  # byte order: prefix ranges and sorting as Python sorts str


def _type(kind: str) -> TypeEngine:
    if kind in (mapping.STR, mapping.SHARED):
        return Text()
    if kind == mapping.INT:
        return BigInteger()
    if kind == mapping.FLOAT:
        return Double()
    if kind == mapping.BOOL:
        return Boolean()
    if kind == mapping.JSON:
        return JSONB(none_as_null=True)
    if kind == mapping.STRS:
        return ARRAY(Text())
    if kind == mapping.INTS:
        return ARRAY(BigInteger())
    if kind == "shape":
        return SmallInteger()
    raise ValueError(f"unknown column type {kind}")


UNMAPPED = mapping.Table("unmapped", None, {"data": mapping.JSON, "text": mapping.STR})
UNMAPPED_LINES = mapping.Table("unmapped_lines", None, {"data": mapping.JSON}, log=True)


def definitions(formats: tuple[mapping.Format, ...] = FORMATS) -> dict[str, mapping.Table]:
    return {**mapping.tables(formats), UNMAPPED.name: UNMAPPED, UNMAPPED_LINES.name: UNMAPPED_LINES}


def build(formats: tuple[mapping.Format, ...] = FORMATS) -> MetaData:
    metadata = MetaData()
    Table(
        "records",
        metadata,
        Column("key", KEY, primary_key=True),
        Column("format", Text, nullable=False),
        Column("saved", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    Table("texts", metadata, Column("hash", Text, primary_key=True), Column("text", Text, nullable=False))
    for table in definitions(formats).values():
        columns: list[Column] = [Column("id", BigInteger, Identity(), primary_key=True)]
        indexes: list[Index] = []
        if table.parent is None:
            columns += [
                Column("key", KEY, ForeignKey("records.key", ondelete="CASCADE"), nullable=False),
                Column("folder", KEY, nullable=False),
                Column("saved", DateTime(timezone=True), nullable=False, server_default=func.now()),
            ]
            if table.history:
                columns.append(Column("replaced", DateTime(timezone=True)))
                indexes.append(Index(f"{table.name}_current", "key", unique=True, postgresql_where=text("replaced IS NULL")))
                indexes.append(Index(f"{table.name}_key", "key"))
            else:
                indexes.append(Index(f"{table.name}_key", "key", unique=not table.log))
            indexes.append(Index(f"{table.name}_folder", "folder"))
        else:
            columns += [Column("parent_id", BigInteger, ForeignKey(f"{table.parent}.id", ondelete="CASCADE"), nullable=False), Column("position", Integer, nullable=False)]
            if table.part:
                columns.append(Column("part", Text, nullable=False, server_default=""))
            indexes.append(Index(f"{table.name}_parent", "parent_id"))
        columns += [Column(name, _type(kind), nullable=not (table.parent is not None and name in table.by)) for name, kind in table.columns.items()]
        columns.append(Column("extra", JSONB(none_as_null=True)))
        Table(table.name, metadata, *columns, *indexes)
    return metadata


METADATA = build()
