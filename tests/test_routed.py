from __future__ import annotations

from pathlib import Path

import pytest

from plystore import DocumentExists, FileStore, MemoryStore, Route, RoutedStore, open_store


def test_each_key_goes_to_the_route_with_the_longest_prefix(tmp_path: Path) -> None:
    rest, plots = MemoryStore(), FileStore(tmp_path)
    store = RoutedStore([Route("", rest), Route("plots", plots), Route("plots/keep", rest)])
    store.put("results/game_1.json", {})
    store.put("plots/chart.svg", "<svg/>")
    store.put("plots/keep/chart.svg", "<svg/>")
    assert rest.keys() == ["plots/keep/chart.svg", "results/game_1.json"]
    assert plots.keys() == ["plots/chart.svg"]
    assert store.keys("plots") == ["plots/chart.svg", "plots/keep/chart.svg"]


def test_a_store_shared_by_two_routes_lists_each_key_once(tmp_path: Path) -> None:
    files = FileStore(tmp_path)
    store = RoutedStore([Route("", files), Route("plots", files)])
    store.put("plots/chart.svg", "<svg/>")
    store.put("results/game_1.json", {})
    assert store.keys() == ["plots/chart.svg", "results/game_1.json"]


def test_after_a_switch_old_data_still_reads_and_still_counts_as_written(tmp_path: Path) -> None:
    old = FileStore(tmp_path)
    old.put("results/x/game_1.json", {"round": 1})
    old.append("results/x/log.jsonl", {"n": 0})
    new = MemoryStore()
    store = RoutedStore([Route("", new, also_read=(old,))])
    # a resumed matchup sees its old games, cannot write them again, and adds the next one to the new store only
    assert store.keys("results/x") == ["results/x/game_1.json", "results/x/log.jsonl"]
    assert store.get("results/x/game_1.json") == {"round": 1}
    with pytest.raises(DocumentExists):
        store.put("results/x/game_1.json", {"round": 1})
    store.put("results/x/game_2.json", {"round": 2})
    store.append("results/x/log.jsonl", {"n": 1})
    assert new.keys() == ["results/x/game_2.json", "results/x/log.jsonl"]
    assert store.read_log("results/x/log.jsonl") == [{"n": 0}, {"n": 1}]
    assert old.keys() == ["results/x/game_1.json", "results/x/log.jsonl"]


def test_routes_need_one_for_every_key() -> None:
    with pytest.raises(ValueError):
        RoutedStore([Route("plots", MemoryStore())])
    with pytest.raises(ValueError):
        RoutedStore([Route("", MemoryStore()), Route("", MemoryStore())])


def test_put_many_stays_in_one_route() -> None:
    store = RoutedStore([Route("", MemoryStore()), Route("plots", MemoryStore())])
    with pytest.raises(ValueError):
        store.put_many({"results/a.json": {}, "plots/b.svg": ""})


def test_without_a_configuration_everything_is_a_file_as_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLYSTORE_CONFIG", raising=False)
    store = open_store(tmp_path)
    store.put("results/x/game_1.json", {})
    assert (tmp_path / "results/x/game_1.json").exists()


def test_a_configuration_routes_prefixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "plystore.toml"
    config.write_text('[[route]]\nprefix = ""\nstore = "memory"\nalso_read = ["files"]\n\n[[route]]\nprefix = "plots"\nstore = "files"\n')
    monkeypatch.setenv("PLYSTORE_CONFIG", str(config))
    root = tmp_path / "data"
    FileStore(root).put("results/old.json", {"old": True})
    store = open_store(root)
    store.put("results/new.json", {})
    store.put("plots/chart.svg", "<svg/>")
    assert store.get("results/old.json") == {"old": True}
    assert not (root / "results/new.json").exists() and (root / "plots/chart.svg").exists()


def test_an_unknown_backend_is_named(tmp_path: Path) -> None:
    config = tmp_path / "plystore.toml"
    config.write_text('[[route]]\nprefix = ""\nstore = "postgres"\n')
    with pytest.raises(ValueError, match="postgres"):
        open_store(tmp_path, config)
