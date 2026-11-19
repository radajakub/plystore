from __future__ import annotations

import json
from pathlib import Path

from plystore import FileStore, MemoryStore, copy, scan


def _plydata(root: Path) -> FileStore:
    store = FileStore(root)
    store.put("results/x/game_1.json", {"game_round": 1})
    store.put("results/x/metadata.json", {"completed": [1]})
    store.put("analysis/ttt/reasoning/review.md", "# review\n")
    for n in range(3):
        store.append("results/llm_blocks.jsonl", {"n": n})
    return store


def test_an_import_copies_every_document_and_can_run_again(tmp_path: Path) -> None:
    source, target = _plydata(tmp_path), MemoryStore()
    first = copy(source, target)
    assert len(first.copied) == 4 and not (first.conflicts or first.failed)
    assert {key: target.get(key) for key in target.keys() if not key.endswith(".jsonl")} == {key: source.get(key) for key in source.keys() if not key.endswith(".jsonl")}
    assert target.read_log("results/llm_blocks.jsonl") == [{"n": 0}, {"n": 1}, {"n": 2}]
    again = copy(source, target)
    assert len(again.same) == 4 and not again.copied


def test_a_log_copied_in_part_goes_on_and_a_different_document_is_left_alone(tmp_path: Path) -> None:
    source, target = _plydata(tmp_path), MemoryStore()
    target.append("results/llm_blocks.jsonl", {"n": 0})
    target.put("results/x/metadata.json", {"completed": []})
    report = copy(source, target)
    assert target.read_log("results/llm_blocks.jsonl") == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert report.conflicts == ["results/x/metadata.json"]
    assert target.get("results/x/metadata.json") == {"completed": []}


def test_a_scan_finds_what_a_database_would_refuse(tmp_path: Path) -> None:
    store = _plydata(tmp_path)
    (tmp_path / "results/x/game_2.json").write_text(json.dumps({"value": float("nan")}))
    (tmp_path / "results/x/game_3.json").write_text("{not json")
    report = scan(store)
    assert report.kinds == {"json": 4, "text": 1, "log": 1}
    assert set(report.problems) == {"results/x/game_2.json", "results/x/game_3.json"}
    assert report.folders["results/x"] == 4
    failed = copy(store, MemoryStore()).failed
    assert set(failed) == {"results/x/game_2.json", "results/x/game_3.json"}


def test_a_batch_with_one_bad_document_copies_the_rest(tmp_path: Path) -> None:
    store = _plydata(tmp_path)
    (tmp_path / "results/x/game_2.json").write_text(json.dumps({"value": float("nan")}))
    target = MemoryStore()
    report = copy(store, target, batch=2)
    assert set(report.failed) == {"results/x/game_2.json"}
    assert len(report.copied) == 4 and target.get("results/x/game_1.json") == {"game_round": 1}
