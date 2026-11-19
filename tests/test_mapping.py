from __future__ import annotations

from typing import Any

import pytest
from records import DOCUMENTS, LOGS

from plystore import mapping
from plystore.formats import FORMATS, find
from plystore.mapping import FLOAT, INT, JSON, STR, Child, Choice, Column, Flatten, Format, rebuild, same, split

# The round trip a format promises: split into rows, rebuilt from them, the same record (types included). What does not fit a column waits in
# extra, so the promise holds for any record, also one that does not follow the format.


def roundtrip(fmt: Format, record: Any) -> Any:
    row = split(fmt, record)
    assert row is not None
    return rebuild(fmt, row)


def records_of(key: str) -> list[Any]:
    return LOGS[key] if key in LOGS else [DOCUMENTS[key]]


@pytest.mark.parametrize("key", [*DOCUMENTS, *LOGS])
def test_every_record_rebuilds_to_itself(key: str) -> None:
    fmt = find(key)
    if fmt is None:
        return
    for record in records_of(key):
        assert same(roundtrip(fmt, record), record)


def test_every_format_has_a_record_here_and_keys_find_their_format() -> None:
    found = {key: fmt.name for key in [*DOCUMENTS, *LOGS] if (fmt := find(key)) is not None}
    assert {fmt.name for fmt in FORMATS} == set(found.values())
    assert find("analysis/ttt/reasoning/codebook-notes.md") is None and find("analysis/ttt/reasoning/logs/job-1.report.json") is None
    assert find("archive/2026-11-13-tac/results/benchmarks/x/game_1.json") is None
    assert found["results/training/tac/bandit_a/textrl_preset-tac-8ab3d821aa2f_vs_none/replicate_0/epoch_1/checkpoint/state.json"] == "learner_states"


def test_the_typed_columns_take_what_these_records_hold() -> None:
    # all but explicit nulls, a string where a bool belongs, and the op-specific fields of a codebook edit
    rows = [row for key in [*DOCUMENTS, *LOGS] if (fmt := find(key)) for record in records_of(key) if (row := split(fmt, record))]
    assert set(mapping.leftover_counts(rows)) == {"experiment_items.enabled", "codebook_edits.definition"}


GAME = Format(
    "things",
    r"things/.+\.json",
    (
        Column("n", INT),
        Column("x", FLOAT),
        Column("name", STR),
        Flatten("end", "end_", (Column("result", INT), Child("players", "thing_players", (Column("score", FLOAT),), by=("hash",)))),
        Child("steps", "thing_steps", (Column("move", STR), Column("data", JSON))),
        Choice("block", (Child("block", "thing_texts", (Column("text", STR),), by=("key",)), Child("block", "thing_values", (Column("value", FLOAT),), by=("key", "action")))),
    ),
)


@pytest.mark.parametrize(
    "record",
    [
        {"n": 1, "x": 1.0, "name": "a"},
        {"n": None, "x": None},  # an explicit null is not an absent field
        {"n": 1.0, "x": 1, "name": True},  # each the wrong type: kept as they are
        {"n": True, "x": False},
        {"n": 2**63, "x": -(2**63) - 1},  # beyond bigint
        {"end": {"result": 1, "players": {"h": {"score": 0.5, "note": "kept"}}, "other": [1]}},  # flattened in part
        {"end": None},
        {"end": "draw"},
        {"steps": []},
        {"steps": [{"move": "a", "data": {"k": [1, None]}}, {"move": "b", "data": None}]},
        {"steps": [{"move": "a"}, "not an object"]},  # a list with a non-object: kept whole
        {"block": {"k": {"text": "t"}}},
        {"block": {"k": {"red": {"value": 0.5}, "blue": {"value": 0.0}}}},  # the second shape
        {"block": {"k": {}}},  # an empty inner dict: the first shape takes it
        {"block": {"k": {"red": {}}, "j": "x"}},  # neither shape takes it whole
        {"block": {}},
        {"unknown": {"deep": [1, 2]}, "n": 3},
    ],
)
def test_any_record_rebuilds_to_itself(record: dict[str, Any]) -> None:
    assert same(roundtrip(GAME, record), record)


def test_columns_take_only_exact_types_and_the_rest_waits_in_extra() -> None:
    row = split(GAME, {"n": 1.0, "x": 2.0, "name": None, "end": {"result": 1, "other": 2}, "block": {"k": {"red": {"value": 0.5}}}})
    assert row is not None
    assert row.values == {"x": 2.0, "end_result": 1, "has_end": True, "shape_block": 2}
    assert row.extra == {"n": 1.0, "name": None, "end": {"other": 2}}
    assert [(child.table, child.values) for child in row.children] == [("thing_values", {"position": 0, "key": "k", "action": "red", "value": 0.5})]


def test_a_record_of_another_shape_is_not_split() -> None:
    assert split(GAME, [1, 2]) is None
    items = Format("lists", r"lists/.+\.json", (Column("a", INT),), items="list_items")
    assert split(items, {"a": 1}) is not None  # kept whole in extra
    for record in [[], [{"a": 1}, {"a": "x"}], [{"a": 1}, 2]]:
        assert same(roundtrip(items, record), record)


def test_the_tables_of_the_formats() -> None:
    tables = mapping.tables(FORMATS)
    assert tables["game_steps"].parent == "games" and tables["step_calls"].parent == "game_steps"
    assert tables["memory_entries"].part and tables["memory_entries"].by == ("entry_key",)
    assert tables["games"].columns["end_result"] == INT and tables["games"].columns["has_ending"] == mapping.BOOL
    assert tables["learner_states"].columns["shape_actor"] == "shape"
    assert tables["codebooks"].history and tables["annotations"].log


def test_a_format_cannot_name_a_reserved_column_or_use_a_table_two_ways() -> None:
    with pytest.raises(ValueError):
        mapping.tables([Format("a", "a", (Column("key", STR),))])
    with pytest.raises(ValueError):
        mapping.tables([Format("a", "a", (Column("x", STR), Column("x", INT, "x")))])
    with pytest.raises(ValueError):
        mapping.tables([Format("a", "a", (Child("x", "c", ()), Child("y", "c", (), by=("k",))))])
