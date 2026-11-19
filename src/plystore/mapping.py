from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

# How a record (a JSON document a writer puts, or one record of a log) is kept in typed tables and rebuilt from them. A format is a tree of nodes:
# a Column takes one field into a typed column, a Flatten takes the fields of a nested object into columns of the same row, a Child takes a list
# of objects (or a dict of objects, one or more levels deep) into rows of another table, a Choice takes the first of several shapes that fits.
# Whatever no node takes exactly (another type, a null, a field no node names) stays in the row's `extra`, so every record rebuilds to itself:
# the columns hold what fits, nothing is lost and nothing is refused. Pure Python: the round trip is checked without a database (plystore-check).

STR = "str"
SHARED = "shared"  # a str kept once per distinct text (system prompts, memory entries): the row holds its hash
INT = "int"
FLOAT = "float"
BOOL = "bool"
JSON = "json"  # any JSON value but null, kept as jsonb
STRS = "strs"
INTS = "ints"

INT_LIMIT = 2**63  # bigint
# columns every table has (or keeps for itself): no format names a column like these
RESERVED = {"id", "parent_id", "position", "part", "extra", "key", "folder", "saved", "replaced"}


def fits(kind: str, value: Any) -> bool:
    # exactly that type: a bool is not an int, an int is not a float (1 and 1.0 are different JSON), a null fits nothing
    if kind in (STR, SHARED):
        return isinstance(value, str)
    if kind == INT:
        return type(value) is int and -INT_LIMIT <= value < INT_LIMIT
    if kind == FLOAT:
        return type(value) is float
    if kind == BOOL:
        return type(value) is bool
    if kind == JSON:
        return value is not None
    if kind == STRS:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == INTS:
        return isinstance(value, list) and all(fits(INT, item) for item in value)
    raise ValueError(f"unknown column type {kind}")


@dataclass(frozen=True)
class Column:
    field: str
    type: str
    name: str = ""  # the column, when not the field's own name


@dataclass(frozen=True)
class Flatten:
    field: str
    prefix: str  # of the nested object's columns, e.g. "end_" for ending.result -> end_result
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Child:
    field: str
    table: str
    nodes: tuple[Node, ...]
    by: tuple[str, ...] = ()  # () a list of objects; else a dict of objects, len(by) levels deep, its keys in these columns
    part: str = ""  # when several Child nodes share a table: the value of its `part` column that marks this node's rows


@dataclass(frozen=True)
class Choice:
    field: str
    options: tuple[Flatten | Child, ...]  # each for this same field; the first that takes the value whole wins


Node = Column | Flatten | Child | Choice


@dataclass(frozen=True)
class Format:
    name: str  # also its root table
    pattern: str  # a regular expression over the key
    nodes: tuple[Node, ...]
    log: bool = False  # the key is a .jsonl log and each record is a row
    history: bool = False  # a replaced record keeps its earlier versions (codebooks, experiment configs)
    items: str = ""  # the record is a list of objects, kept as rows of this table

    def matches(self, key: str) -> bool:
        return re.fullmatch(self.pattern, key) is not None


@dataclass
class Row:
    table: str
    values: dict[str, Any] = field(default_factory=dict)  # by column; an absent value is a missing column (NULL), never None
    extra: dict[str, Any] = field(default_factory=dict)
    children: list[Row] = field(default_factory=list)


def column(node: Column, prefix: str) -> str:
    return prefix + (node.name or node.field)


def marker(node: Flatten | Child | Choice, prefix: str) -> str:
    # the column saying the field was there and taken: has_<field> (a bool), or shape_<field> (the 1-based option of a Choice)
    return ("shape_" if isinstance(node, Choice) else "has_") + prefix + node.field


_MISSED = object()


def _split(nodes: tuple[Node, ...], obj: dict[str, Any], row: Row, prefix: str) -> dict[str, Any]:
    # takes the object's fields into the row; returns what no node took (a flattened object that was taken in part leaves the rest under its field)
    named = {node.field: node for node in nodes}
    rest: dict[str, Any] = {}
    for name, value in obj.items():
        node = named.get(name)
        left = _MISSED if node is None else _take(node, value, row, prefix)
        if left is _MISSED:
            rest[name] = value
        elif left:
            rest[name] = left
    return rest


def _take(node: Node, value: Any, row: Row, prefix: str, mark: bool = True) -> Any:
    if isinstance(node, Column):
        if not fits(node.type, value):
            return _MISSED
        row.values[column(node, prefix)] = value
        return {}
    if isinstance(node, Choice):
        for index, option in enumerate(node.options, 1):
            trial = Row(row.table)
            left = _take(option, value, trial, prefix, mark=False)
            if left is not _MISSED and not _held(left) and complete(trial):
                row.values.update(trial.values)
                row.children += trial.children
                row.values[marker(node, prefix)] = index
                return {}
        return _MISSED
    if isinstance(node, Flatten):
        if not isinstance(value, dict):
            return _MISSED
        left = _split(node.nodes, value, row, prefix + node.prefix)
    else:
        rows = _rows(node, value)
        if rows is None:
            return _MISSED
        row.children += rows
        left = {}
    if mark:
        row.values[marker(node, prefix)] = True
    return left


def _rows(node: Child, value: Any) -> list[Row] | None:
    # the child rows of a list (or nested dict) of objects; None when the value is not that shape
    items = _items(value, len(node.by))
    if items is None:
        return None
    rows = []
    for position, (keys, obj) in enumerate(items):
        row = Row(node.table, {"position": position, **dict(zip(node.by, keys))})
        if node.part:
            row.values["part"] = node.part
        row.extra = _split(node.nodes, obj, row, "")
        rows.append(row)
    return rows


def _items(value: Any, depth: int) -> list[tuple[tuple[str, ...], dict[str, Any]]] | None:
    # (keys, object) of each item, depth levels of dict keys deep; None when an item is not an object, or an inner dict is empty (its key would be lost)
    if depth == 0:
        return [((), item) for item in value] if isinstance(value, list) and all(isinstance(item, dict) for item in value) else None
    if not isinstance(value, dict):
        return None
    items: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for key, inner in value.items():
        if depth == 1:
            if not isinstance(inner, dict):
                return None
            items.append(((key,), inner))
        else:
            deeper = _items(inner, depth - 1)
            if not deeper:
                return None
            items += [((key, *keys), obj) for keys, obj in deeper]
    return items


def _held(value: Any) -> bool:
    # a value that says something: not a null, nor an object of nulls (what is left of a flattened object whose null fields stayed behind)
    return value is not None and (not isinstance(value, dict) or any(_held(inner) for inner in value.values()))


def complete(row: Row) -> bool:
    # nothing but nulls of the record went to extra, here or in the child rows
    return not _held(row.extra) and all(complete(child) for child in row.children)


def split(fmt: Format, record: Any) -> Row | None:
    # the rows of one record; None when the record is not the shape the format keeps (not an object, or not a list for an items format)
    if fmt.items:
        record = {"items": record}
    if not isinstance(record, dict):
        return None
    row = Row(fmt.name)
    row.extra = _split(_nodes(fmt), record, row, "")
    return row


def _nodes(fmt: Format) -> tuple[Node, ...]:
    return (Child("items", fmt.items, fmt.nodes),) if fmt.items else fmt.nodes


def _build(nodes: tuple[Node, ...], row: Row, prefix: str, rest: dict[str, Any]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for node in nodes:
        if isinstance(node, Column):
            if (value := row.values.get(column(node, prefix))) is not None:
                obj[node.field] = value
        elif isinstance(node, Choice):
            if shape := row.values.get(marker(node, prefix)):
                obj[node.field] = _build_one(node.options[shape - 1], row, prefix, rest.get(node.field))
        elif row.values.get(marker(node, prefix)):
            obj[node.field] = _build_one(node, row, prefix, rest.get(node.field))
    for name, value in rest.items():
        if name not in obj:
            obj[name] = value
    return obj


def _build_one(node: Flatten | Child, row: Row, prefix: str, left: Any) -> Any:
    if isinstance(node, Flatten):
        return _build(node.nodes, row, prefix + node.prefix, left or {})
    rows = sorted((child for child in row.children if child.table == node.table and child.values.get("part", "") == node.part), key=lambda child: child.values["position"])
    if not node.by:
        return [_build(node.nodes, child, "", child.extra) for child in rows]
    built: dict[str, Any] = {}
    for child in rows:
        target = built
        for name in node.by[:-1]:
            target = target.setdefault(child.values[name], {})
        target[child.values[node.by[-1]]] = _build(node.nodes, child, "", child.extra)
    return built


def rebuild(fmt: Format, row: Row) -> Any:
    record = _build(_nodes(fmt), row, "", row.extra)
    return record["items"] if fmt.items else record


def same(a: Any, b: Any) -> bool:
    # equal as JSON, types included (1 is not 1.0 nor True); key order aside
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def leftovers(row: Row) -> Iterator[str]:
    # where values went to extra, as table.field: how well a format fits the data (plystore-check). Nulls are not counted: a column holds no
    # null (NULL is "absent"), so an explicit null always waits in extra and says nothing about the fit
    for name, value in row.extra.items():
        if _held(value):
            yield f"{row.table}.{name}"
    for child in row.children:
        yield from leftovers(child)


def leftover_counts(rows: Iterable[Row]) -> Counter[str]:
    return Counter(name for row in rows for name in set(leftovers(row)))


# --- the tables a set of formats needs, for the database backend and its migrations ---


@dataclass
class Table:
    name: str
    parent: str | None  # None for a root table (a format's)
    columns: dict[str, str] = field(default_factory=dict)  # column -> type, markers as BOOL or "shape"
    by: tuple[str, ...] = ()
    part: bool = False
    log: bool = False
    history: bool = False


def tables(formats: Iterable[Format]) -> dict[str, Table]:
    found: dict[str, Table] = {}
    for fmt in formats:
        root = Table(fmt.name, None, log=fmt.log, history=fmt.history)
        _add(found, root)
        _columns(found, root, _nodes(fmt), "")
    return found


def _add(found: dict[str, Table], table: Table) -> Table:
    if table.name in found:
        there = found[table.name]
        if (there.parent, there.by, there.log, there.history) != (table.parent, table.by, table.log, table.history):
            raise ValueError(f"table {table.name} is used in two places with different shapes")
        there.part = there.part or table.part
        return there
    found[table.name] = table
    return table


def _columns(found: dict[str, Table], table: Table, nodes: tuple[Node, ...], prefix: str) -> None:
    for node in nodes:
        if isinstance(node, Column):
            _column(table, column(node, prefix), node.type)
        elif isinstance(node, Flatten):
            _column(table, marker(node, prefix), BOOL)
            _columns(found, table, node.nodes, prefix + node.prefix)
        elif isinstance(node, Child):
            _column(table, marker(node, prefix), BOOL)
            _child(found, table, node)
        else:
            _column(table, marker(node, prefix), "shape")
            for option in node.options:
                if isinstance(option, Flatten):
                    _columns(found, table, option.nodes, prefix + option.prefix)
                else:
                    _child(found, table, option)


def _child(found: dict[str, Table], parent: Table, node: Child) -> None:
    child = _add(found, Table(node.table, parent.name, by=node.by, part=bool(node.part)))
    for name in node.by:
        _column(child, name, STR)
    _columns(found, child, node.nodes, "")


def _column(table: Table, name: str, kind: str) -> None:
    if name in RESERVED or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"table {table.name}: {name!r} cannot be a column")
    if table.columns.setdefault(name, kind) != kind:
        raise ValueError(f"table {table.name}: column {name} has two types ({table.columns[name]}, {kind})")
