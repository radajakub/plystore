from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, RowMapping, create_engine, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from plystore import mapping
from plystore.formats import FORMATS, find
from plystore.keys import JSON, LOG, DocumentExists, check, check_prefix, kind
from plystore.postgres import migrations
from plystore.postgres.schema import METADATA, UNMAPPED, UNMAPPED_LINES, definitions
from plystore.store import check_log, check_prefix_to_delete, encode, encode_record

# The Store over typed tables in one schema of a PostgreSQL database (the repo's: plybench, plyagents, plybench_analysis). Each record is split
# by its format (formats/) into rows and rebuilt from them on read, as the round trip checks (plystore-check); a record no format keeps is kept
# whole. A write is one transaction: the record with all its rows, or a put_many with all its records, is there entirely or not at all.
# The store refuses a schema whose migration is not the one its formats were made for (plystore-admin migrate).

CHUNK = 5000  # ids in one IN (...) query
WHOLE = "__plystore_value__"  # a log record that is not an object, kept whole in the extra of a row of its log's table
SCHEMA = re.compile(r"[a-z_][a-z0-9_]*")


def engine_for(url: str, schema: str) -> Engine:
    if not SCHEMA.fullmatch(schema):
        raise ValueError(f"invalid schema name {schema!r}")
    driver = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg://", url)
    return create_engine(driver, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True)


def from_config(root: Path, table: Mapping[str, Any]) -> PostgresStore:
    # the backend a configuration's [postgres] table names (config.py, the plystore.backends entry point): the schema, and the environment
    # variable holding the database URL (with its password), PLYSTORE_DATABASE_URL by default
    url_env = table.get("url_env", "PLYSTORE_DATABASE_URL")
    if not os.environ.get(url_env):
        raise ValueError(f"the database URL is read from {url_env}, which is not set")
    return PostgresStore(os.environ[url_env], table["schema"])


class PostgresStore:
    def __init__(self, url: str, schema: str, check_version: bool = True) -> None:
        self.schema = schema
        self.engine = engine_for(url, schema)
        self.formats = {fmt.name: fmt for fmt in FORMATS}
        self.definitions = definitions()
        self.tables = METADATA.tables
        self.children: dict[str, list[str]] = defaultdict(list)
        for table in self.definitions.values():
            if table.parent is not None:
                self.children[table.parent].append(table.name)
        if check_version:
            with self.engine.connect() as conn:
                migrations.check(conn, schema)

    # --- writing ---

    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        self._write({check(key): value}, replace)

    def put_many(self, values: Mapping[str, Any]) -> None:
        self._write({check(key): value for key, value in values.items()}, replace=False)

    def _write(self, values: Mapping[str, Any], replace: bool) -> None:
        rows = {key: self._document(key, value) for key, value in values.items()}
        if not rows:
            return
        records = self.tables["records"]
        with self.engine.begin() as conn:
            new = set(
                conn.execute(pg_insert(records).on_conflict_do_nothing().returning(records.c.key), [{"key": key, "format": row.table} for key, row in rows.items()]).scalars()
            )
            if there := sorted(set(rows) - new):
                if not replace:
                    raise DocumentExists(", ".join(there))
                for key in there:
                    self._retire(conn, key, rows[key].table)
            self._insert(conn, list(rows.items()))

    def _document(self, key: str, value: Any) -> mapping.Row:
        encode(key, value)  # the checks every backend makes: suffix and type, NaN and infinities, NUL and lone surrogates
        fmt = find(key)
        if fmt is not None and not fmt.log and (row := mapping.split(fmt, value)) is not None:
            return row
        return mapping.Row(UNMAPPED.name, {"data" if kind(key) == JSON else "text": value} if value is not None else {})

    def _retire(self, conn: Connection, key: str, table: str) -> None:
        # the record's current rows give way to new ones: kept as history where its format keeps it, else deleted with their child rows
        records = self.tables["records"]
        old = conn.execute(select(records.c.format).where(records.c.key == key).with_for_update()).scalar_one()
        if old in self.formats and self.formats[old].log:
            raise ValueError(f"{key} is a log")
        old_table = self.tables[old]
        if self.definitions[old].history:
            conn.execute(update(old_table).where(old_table.c.key == key, old_table.c.replaced.is_(None)).values(replaced=func.now()))
        else:
            conn.execute(delete(old_table).where(old_table.c.key == key))
        conn.execute(update(records).where(records.c.key == key).values(format=table, saved=func.now()))

    def _insert(self, conn: Connection, items: list[tuple[str, mapping.Row]]) -> None:
        # level by level, one INSERT per table and level (RETURNING the ids, in order, for the next level's parent_id)
        shared: dict[str, str] = {}
        level: list[tuple[mapping.Row, dict[str, Any]]] = [(row, {"key": key, "folder": key.rsplit("/", 1)[0] if "/" in key else ""}) for key, row in items]
        while level:
            groups: dict[str, list[tuple[mapping.Row, dict[str, Any]]]] = defaultdict(list)
            for row, fixed in level:
                groups[row.table].append((row, fixed))
            level = []
            for name, group in groups.items():
                table = self.tables[name]
                params = [self._params(self.definitions[name], row, fixed, shared) for row, fixed in group]
                ids = conn.execute(insert(table).returning(table.c.id, sort_by_parameter_order=True), params).scalars().all()
                level += [(child, {"parent_id": row_id}) for (row, _), row_id in zip(group, ids, strict=True) for child in row.children]
        if shared:
            texts = self.tables["texts"]
            conn.execute(pg_insert(texts).on_conflict_do_nothing(), [{"hash": digest, "text": value} for digest, value in shared.items()])

    def _params(self, table: mapping.Table, row: mapping.Row, fixed: dict[str, Any], shared: dict[str, str]) -> dict[str, Any]:
        params = dict(fixed)
        if table.parent is not None:
            params["position"] = row.values["position"]
            if table.part:
                params["part"] = row.values.get("part", "")
        for name, kind_ in table.columns.items():
            value = row.values.get(name)
            if kind_ == mapping.SHARED and value is not None:
                digest = hashlib.sha256(value.encode()).hexdigest()
                shared[digest] = value
                value = digest
            params[name] = value
        params["extra"] = row.extra or None
        return params

    def append(self, key: str, record: Any) -> None:
        encode_record(check(key), record)
        fmt = find(key)
        if fmt is not None and fmt.log:
            row = mapping.split(fmt, record) or mapping.Row(fmt.name, extra={WHOLE: record})
        else:
            row = mapping.Row(UNMAPPED_LINES.name, {"data": record} if record is not None else {})
        records = self.tables["records"]
        with self.engine.begin() as conn:
            conn.execute(pg_insert(records).values(key=key, format=row.table).on_conflict_do_nothing())
            there = conn.execute(select(records.c.format).where(records.c.key == key)).scalar_one()
            if there != row.table:
                raise ValueError(f"{key} is not a log here (kept as {there})")
            self._insert(conn, [(key, row)])

    def delete(self, prefix: str) -> int:
        check_prefix_to_delete(check_prefix(prefix))
        records = self.tables["records"]
        with self.engine.begin() as conn:
            return conn.execute(delete(records).where(_under(records.c.key, prefix))).rowcount

    # --- reading ---

    def get(self, key: str) -> Any | None:
        if kind(check(key)) == LOG:
            raise ValueError(f"{key} is a log: read it with read_log")
        return self.get_many([key]).get(key)

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        for key in keys:
            if kind(check(key)) == LOG:
                raise ValueError(f"{key} is a log: read it with read_log")
        records = self.tables["records"]
        found: dict[str, Any] = {}
        with self.engine.connect() as conn:
            by_format: dict[str, list[str]] = defaultdict(list)
            for chunk in _chunks(list(dict.fromkeys(keys))):
                for key, fmt in conn.execute(select(records.c.key, records.c.format).where(records.c.key.in_(chunk))):
                    by_format[fmt].append(key)
            for fmt, wanted in by_format.items():
                for key, rows in self._load(conn, fmt, wanted).items():
                    found[key] = self._value(key, fmt, rows[0])
        return {key: found[key] for key in keys if key in found and found[key] is not None}

    def exists(self, key: str) -> bool:
        records = self.tables["records"]
        with self.engine.connect() as conn:
            return conn.execute(select(records.c.key).where(records.c.key == check(key))).first() is not None

    def keys(self, prefix: str = "") -> list[str]:
        records = self.tables["records"]
        query = select(records.c.key).order_by(records.c.key)
        if check_prefix(prefix):
            query = query.where(_under(records.c.key, prefix))
        with self.engine.connect() as conn:
            return list(conn.execute(query).scalars())

    def read_log(self, key: str) -> list[Any]:
        check_log(check(key))
        records = self.tables["records"]
        with self.engine.connect() as conn:
            fmt = conn.execute(select(records.c.format).where(records.c.key == key)).scalar()
            if fmt is None:
                return []
            return [self._value(key, fmt, row) for row in self._load(conn, fmt, [key]).get(key, [])]

    def _value(self, key: str, table: str, row: mapping.Row) -> Any:
        if table in (UNMAPPED.name, UNMAPPED_LINES.name):
            return row.values.get("data" if kind(key) in (JSON, LOG) else "text")
        if WHOLE in row.extra and len(row.extra) == 1 and not row.values:
            return row.extra[WHOLE]
        return mapping.rebuild(self.formats[table], row)

    def _load(self, conn: Connection, name: str, keys: list[str]) -> dict[str, list[mapping.Row]]:
        # the current root rows of these keys (every row of a log, in order), with all their child rows
        table = self.tables[name]
        definition = self.definitions[name]
        loaded: dict[str, list[mapping.Row]] = defaultdict(list)
        parents: dict[int, mapping.Row] = {}
        pending: list[tuple[mapping.Row, str, str]] = []
        for chunk in _chunks(keys):
            query = select(table).where(table.c.key.in_(chunk)).order_by(table.c.id)
            if definition.history:
                query = query.where(table.c.replaced.is_(None))
            for record in conn.execute(query).mappings():
                row = self._row(definition, record, pending)
                parents[record["id"]] = row
                loaded[record["key"]].append(row)
        self._load_children(conn, name, parents, pending)
        self._resolve(conn, pending)
        return loaded

    def _load_children(self, conn: Connection, name: str, parents: dict[int, mapping.Row], pending: list[tuple[mapping.Row, str, str]]) -> None:
        for child_name in self.children[name]:
            table = self.tables[child_name]
            definition = self.definitions[child_name]
            found: dict[int, mapping.Row] = {}
            for chunk in _chunks(list(parents)):
                for record in conn.execute(select(table).where(table.c.parent_id.in_(chunk)).order_by(table.c.id)).mappings():
                    row = self._row(definition, record, pending)
                    parents[record["parent_id"]].children.append(row)
                    found[record["id"]] = row
            if found:
                self._load_children(conn, child_name, found, pending)

    def _row(self, definition: mapping.Table, record: RowMapping, pending: list[tuple[mapping.Row, str, str]]) -> mapping.Row:
        row = mapping.Row(definition.name, extra=record["extra"] or {})
        if definition.parent is not None:
            row.values["position"] = record["position"]
            if definition.part:
                row.values["part"] = record["part"]
        for name, kind_ in definition.columns.items():
            value = record[name]
            if value is None:
                continue
            row.values[name] = value
            if kind_ == mapping.SHARED:
                pending.append((row, name, value))
        return row

    def _resolve(self, conn: Connection, pending: list[tuple[mapping.Row, str, str]]) -> None:
        # SHARED columns hold hashes: the texts, in one query per chunk
        texts = self.tables["texts"]
        found: dict[str, str] = {}
        for chunk in _chunks(list({digest for _, _, digest in pending})):
            found.update((digest, value) for digest, value in conn.execute(select(texts.c.hash, texts.c.text).where(texts.c.hash.in_(chunk))))
        for row, name, digest in pending:
            row.values[name] = found[digest]

    def truncate(self) -> None:
        # every table of the schema emptied (tests only)
        names = ", ".join(f'"{name}"' for name in self.tables)
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def _under(column: Any, prefix: str) -> Any:
    # the key or a key under it by whole parts: [prefix/, prefix0) in byte order ('0' follows '/')
    return or_(column == prefix, (column >= prefix + "/") & (column < prefix + "0"))


def _chunks(items: list[Any]) -> Iterator[list[Any]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]
