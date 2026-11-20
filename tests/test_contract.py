from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from records import DOCUMENTS, LOGS

from plystore import DocumentExists, FileStore, MemoryStore, Route, RoutedStore, Store
from plystore.mapping import same

# One contract for every backend: a writer cannot tell which one it was given.

BACKENDS: dict[str, Callable[[Path], Store]] = {
    "memory": lambda _: MemoryStore(),
    "files": lambda root: FileStore(root),
    "routed": lambda root: RoutedStore([Route("", MemoryStore()), Route("plots", FileStore(root))]),
    "routed over old data": lambda root: RoutedStore([Route("", MemoryStore(), also_read=(FileStore(root / "old"),))]),
}


@pytest.fixture(params=[*BACKENDS, pytest.param("postgres", marks=pytest.mark.postgres)])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    if request.param == "postgres":
        return request.getfixturevalue("postgres")
    return BACKENDS[request.param](tmp_path)


def test_a_document_reads_back_as_written(store: Store) -> None:
    game = {"game_round": 1, "steps": [{"seq": 1, "move": "a"}], "text": "žluťoučký"}
    store.put("results/x/game_1.json", game)
    store.put("plots/x/review.md", "# review\n")
    assert store.get("results/x/game_1.json") == game
    assert store.get("plots/x/review.md") == "# review\n"
    assert store.get("results/x/game_2.json") is None
    assert store.exists("results/x/game_1.json") and not store.exists("results/x/game_2.json")


def test_a_document_is_written_once_unless_replaced(store: Store) -> None:
    store.put("results/x/metadata.json", {"completed": [1]})
    with pytest.raises(DocumentExists):
        store.put("results/x/metadata.json", {"completed": [1, 2]})
    store.put("results/x/metadata.json", {"completed": [1, 2]}, replace=True)
    assert store.get("results/x/metadata.json") == {"completed": [1, 2]}


def test_a_stored_value_does_not_change_with_the_callers_object(store: Store) -> None:
    value = {"moves": [1]}
    store.put("results/x/game_1.json", value)
    value["moves"].append(2)
    assert store.get("results/x/game_1.json") == {"moves": [1]}


def test_keys_are_listed_by_whole_parts_and_sorted(store: Store) -> None:
    for key in ["results/ttt/b/game_2.json", "results/ttt/a/game_1.json", "results/ttt2/game_1.json", "plots/ttt/chart.svg"]:
        store.put(key, "<svg/>" if key.endswith(".svg") else {})
    store.append("results/ttt/blocks.jsonl", {"n": 1})
    assert store.keys("results/ttt") == ["results/ttt/a/game_1.json", "results/ttt/b/game_2.json", "results/ttt/blocks.jsonl"]
    assert len(store.keys()) == 5
    assert store.keys("results/missing") == []


def test_put_many_writes_all_or_none(store: Store) -> None:
    store.put("results/x/learner/game_2/state/actor.json", {"old": True})
    with pytest.raises(DocumentExists):
        store.put_many({"results/x/learner/game_2/state/critic.json": {}, "results/x/learner/game_2/state/actor.json": {}})
    assert store.keys("results/x/learner") == ["results/x/learner/game_2/state/actor.json"]
    state = {"results/x/learner/game_3/state/actor.json": {"a": 1}, "results/x/learner/game_3/state/critic.json": {"c": 2}}
    store.put_many(state)
    assert {key: store.get(key) for key in store.keys("results/x/learner/game_3")} == state


def test_a_log_keeps_its_records_in_order(store: Store) -> None:
    assert store.read_log("results/blocks.jsonl") == []
    for n in range(3):
        store.append("results/blocks.jsonl", {"n": n, "text": "ä"})
    assert store.read_log("results/blocks.jsonl") == [{"n": 0, "text": "ä"}, {"n": 1, "text": "ä"}, {"n": 2, "text": "ä"}]


def test_appends_from_threads_never_interleave(store: Store) -> None:
    def write(worker: int) -> None:
        for n in range(50):
            store.append("results/log.jsonl", {"worker": worker, "n": n, "pad": "x" * 2000})

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = store.read_log("results/log.jsonl")
    assert len(records) == 200
    for worker in range(4):
        assert [record["n"] for record in records if record["worker"] == worker] == list(range(50))


def test_what_a_database_cannot_hold_is_refused_everywhere(store: Store) -> None:
    for value in [{"x": float("nan")}, {"x": float("inf")}, {"x": "a\x00b"}, {"x": object()}]:
        with pytest.raises(ValueError):
            store.put("results/x/game_1.json", value)
    with pytest.raises(ValueError):
        store.append("results/log.jsonl", {"x": float("nan")})
    with pytest.raises(ValueError):
        store.put("results/x/notes.md", {"not": "text"})
    # NUL and lone surrogates, in values, keys, lists, logs and text documents
    for value in [{"x": "\ud800"}, {"k\x00": 1}, {"x": ["a", {"y": "\udfff"}]}]:
        with pytest.raises(ValueError, match="NUL"):
            store.put("results/x/game_1.json", value)
    with pytest.raises(ValueError, match="NUL"):
        store.append("results/log.jsonl", {"x": "a\x00b"})
    with pytest.raises(ValueError, match="NUL"):
        store.put("results/x/notes.md", "a\ud83d")
    assert store.keys() == []


def test_text_that_only_looks_like_nul_or_a_surrogate_is_kept(store: Store) -> None:
    # an escape written out as text, a character outside the BMP and an accent are not NUL or lone surrogates
    value = {"x": "a literal \\u0000 and \\ud800", "emoji": "\U0001f600", "accent": "é"}
    store.put("results/x/game_1.json", value)
    store.append("results/log.jsonl", value)
    store.put("results/x/notes.md", "\\u0000 \U0001f600")
    assert store.get("results/x/game_1.json") == value and store.read_log("results/log.jsonl") == [value]
    assert store.get("results/x/notes.md") == "\\u0000 \U0001f600"


@pytest.mark.parametrize("key", ["", "/abs.json", "a//b.json", "a/../b.json", "a/./b.json", "a\\b.json", ".hidden/x.json", "a/x.json.tmp", "a/x.json.lock"])
def test_a_key_is_a_relative_name(store: Store, key: str) -> None:
    with pytest.raises(ValueError):
        store.put(key, {})


def test_logs_and_documents_use_their_own_calls(store: Store) -> None:
    with pytest.raises(ValueError):
        store.put("results/log.jsonl", {})
    with pytest.raises(ValueError):
        store.get("results/log.jsonl")
    with pytest.raises(ValueError):
        store.append("results/x/game_1.json", {})


def test_delete_removes_one_prefix_and_never_everything(store: Store) -> None:
    store.put("results/ttt/game_1.json", {})
    store.put("results/ttt2/game_1.json", {})
    store.append("results/ttt/blocks.jsonl", {})
    with pytest.raises(ValueError):
        store.delete("")
    assert store.delete("results/ttt") == 2
    assert store.keys() == ["results/ttt2/game_1.json"]


def test_get_many_reads_the_documents_that_exist(store: Store) -> None:
    store.put("results/x/game_1.json", {"r": 1})
    store.put("results/x/game_2.json", {"r": 2})
    store.put("plots/x/review.md", "text")
    assert store.get_many(["results/x/game_2.json", "results/x/missing.json", "plots/x/review.md", "results/x/game_1.json"]) == {
        "results/x/game_2.json": {"r": 2},
        "plots/x/review.md": "text",
        "results/x/game_1.json": {"r": 1},
    }
    assert store.get_many([]) == {}
    with pytest.raises(ValueError):
        store.get_many(["results/log.jsonl"])


def test_records_of_every_format_read_back_as_written(store: Store) -> None:
    # equal as JSON with types (1 is not 1.0); a backend may change the order of an object's keys
    store.put_many(DOCUMENTS)
    for key, records in LOGS.items():
        for record in records:
            store.append(key, record)
    assert store.keys() == sorted([*DOCUMENTS, *LOGS])
    assert same(store.get_many(list(DOCUMENTS)), {key: value for key, value in DOCUMENTS.items()})
    for key, records in LOGS.items():
        assert same(store.read_log(key), records)


def test_records_that_do_not_follow_their_format_read_back_as_written(store: Store) -> None:
    odd = {
        "results/x/game_1.json": {"game_round": 2.5, "i_player": 5, "steps": [{}, "x"], "ending": None, "seed": 2**70},
        "results/x/game_2.json": [1, 2],
        "results/x/game_3.json": "a string",
        "results/x/metadata.json": {"summary": {"win_rate": {"n": 1.5}}, "completed": [1, None]},
        "results/training/t/r/epoch_1/checkpoint/state.json": {"actor": {"k": {"red": {}}}, "critic": [1]},
    }
    for key, value in odd.items():
        store.put(key, value)
    store.append("results/llm_blocks.jsonl", ["not", "an", "object"])
    store.append("results/llm_blocks.jsonl", {"time": 1})
    for key, value in odd.items():
        assert same(store.get(key), value)
    assert same(store.read_log("results/llm_blocks.jsonl"), [["not", "an", "object"], {"time": 1}])
