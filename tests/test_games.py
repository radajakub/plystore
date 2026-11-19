from __future__ import annotations

import pytest

from plystore.service import ServiceStore

# The typed columns of game records (migration 002_games.sql), filled by Postgres on every write.

pytestmark = pytest.mark.postgres


def _rows(service, folder: str) -> list[tuple]:
    with service.database.pool.connection() as connection:
        return connection.execute("SELECT key, folder, round, i_player, o_player, result, moves, seed FROM games WHERE folder = %s ORDER BY round", [folder]).fetchall()


def test_a_game_record_gets_its_typed_columns_and_loses_them_with_the_document(service) -> None:
    store = ServiceStore(service.url, "plystore_test", "test-token")
    game = {"game_round": 2, "i_player": "llm:x", "o_player": "mcts:y", "steps": [{}, {}, {}], "ending": {"result": -1}, "seed": 7}
    store.put("results/ttt/a_b/game_2.json", game)
    store.put("results/ttt/a_b/metadata.json", {"game_round": 1, "steps": []})
    assert _rows(service, "results/ttt/a_b") == [("results/ttt/a_b/game_2.json", "results/ttt/a_b", 2, "llm:x", "mcts:y", -1, 3, 7)]
    store.delete("results/ttt")
    assert _rows(service, "results/ttt/a_b") == []


def test_an_odd_game_record_is_stored_with_empty_columns(service) -> None:
    store = ServiceStore(service.url, "plystore_test", "test-token")
    store.put("results/x/game_1.json", {"game_round": "one", "steps": [], "ending": None, "seed": 10**30})
    store.put("results/x/game_2.json", {"game_round": 2})
    (row,) = _rows(service, "results/x")
    assert row[2] is None and row[5] is None and row[6] == 0 and row[7] == 10**30
    assert store.get("results/x/game_2.json") == {"game_round": 2}
