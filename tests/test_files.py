from __future__ import annotations

import json
from pathlib import Path

from plystore import FileStore


def test_a_game_file_has_the_bytes_plybench_writes_today(tmp_path: Path) -> None:
    # plybench's ResultTracker.save_game: json.dump(game, f, indent=None)
    game = {"game_round": 1, "i_player": "llm:x", "steps": [{"seq": 1, "observation": "State:\n  +---+\n", "note": "ž"}]}
    FileStore(tmp_path).put("results/x/game_1.json", game)
    assert (tmp_path / "results/x/game_1.json").read_text() == json.dumps(game, indent=None)


def test_a_log_line_has_the_bytes_plybench_appends_today(tmp_path: Path) -> None:
    # plybench's ThreadSafeAppendable.append: json.dumps(record, ensure_ascii=False) + newline
    record = {"prompt": "ž", "n": 1}
    FileStore(tmp_path).append("results/llm_blocks.jsonl", record)
    assert (tmp_path / "results/llm_blocks.jsonl").read_text() == json.dumps(record, ensure_ascii=False) + "\n"


def test_old_folders_read_as_they_are_and_their_side_files_are_not_documents(tmp_path: Path) -> None:
    folder = tmp_path / "results/benchmarks/ttt_10/a_b"
    folder.mkdir(parents=True)
    (folder / "game_1.json").write_text(json.dumps({"game_round": 1}, indent=2))
    (folder / "metadata.json").write_text("{}")
    (folder / "metadata.json.lock").write_text("")
    (folder / "game_2.tmp").write_text("{")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref")
    store = FileStore(tmp_path)
    assert store.keys() == ["results/benchmarks/ttt_10/a_b/game_1.json", "results/benchmarks/ttt_10/a_b/metadata.json"]
    assert store.get("results/benchmarks/ttt_10/a_b/game_1.json") == {"game_round": 1}


def test_a_log_cut_by_a_crash_reads_without_its_last_line(tmp_path: Path) -> None:
    (tmp_path / "log.jsonl").write_text('{"n": 0}\n{"n": 1}\n{"n": ')
    assert FileStore(tmp_path).read_log("log.jsonl") == [{"n": 0}, {"n": 1}]


def test_put_many_into_a_new_folder_leaves_no_staging_behind(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put_many({"r/learner/game_1/state/a.json": {}, "r/learner/game_1/state/b.json": {}})
    store.put("r/other.json", {})
    store.put_many({"r/learner/game_1/x.json": {}, "r/y.json": {}})
    assert not any(path.name.startswith(".") or path.name.endswith(".tmp") for path in tmp_path.rglob("*"))
    assert len(store.keys()) == 5


def test_delete_leaves_no_empty_folders(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put("results/ttt/a/game_1.json", {})
    store.put("results/keep/game_1.json", {})
    store.delete("results/ttt")
    assert not (tmp_path / "results/ttt").exists() and (tmp_path / "results/keep/game_1.json").exists()
