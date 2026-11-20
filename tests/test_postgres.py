from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import SCHEMA
from records import DOCUMENTS, RUN
from sqlalchemy import text

from plystore.postgres import migrations, schema
from plystore.postgres.store import PostgresStore

# What only the database backend does: rows in typed tables, history of replaced records, deleting with every child row, texts kept once,
# and refusing a schema of another migration. The contract every backend shares is in test_contract.py.

pytestmark = pytest.mark.postgres

GAME = "results/benchmarks/ttt/tic_tac_toe_100/llm_x_optimal/game_1.json"
METADATA = "results/benchmarks/ttt/tic_tac_toe_100/llm_x_optimal/metadata.json"
CODEBOOK = "analysis/ttt/reasoning/codebook.final-v1.manual.json"


def count(store: PostgresStore, sql: str) -> int:
    with store.engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def test_a_record_is_kept_in_typed_rows(postgres: PostgresStore) -> None:
    postgres.put(GAME, DOCUMENTS[GAME])
    with postgres.engine.connect() as conn:
        game = conn.execute(text("SELECT folder, game_round, i_player, end_result, has_ending, extra FROM games")).one()
        steps = conn.execute(text("SELECT seq, move, input_tokens, data_reasoning_trace FROM game_steps ORDER BY position")).all()
    assert tuple(game) == ("results/benchmarks/ttt/tic_tac_toe_100/llm_x_optimal", 1, "llm:x", 2, True, None)
    assert [tuple(step) for step in steps] == [(1, "x(1,1)", 300, "centre"), (2, "o(0,0)", None, None), (3, "FAIL: Refusal (none)", None, None)]
    assert count(postgres, "SELECT count(*) FROM step_calls") == 2


def test_games_join_their_matchup_by_folder(postgres: PostgresStore) -> None:
    postgres.put_many({GAME: DOCUMENTS[GAME], METADATA: DOCUMENTS[METADATA]})
    sql = "SELECT m.i_config, count(*), avg((g.end_result = 0)::int) FROM games g JOIN matchups m USING (folder) GROUP BY m.i_config"
    with postgres.engine.connect() as conn:
        assert [tuple(row) for row in conn.execute(text(sql))] == [("llm:x", 1, 0)]


def test_a_replaced_record_drops_its_old_rows_unless_its_format_keeps_history(postgres: PostgresStore) -> None:
    postgres.put(METADATA, DOCUMENTS[METADATA])
    postgres.put(METADATA, {**DOCUMENTS[METADATA], "completed": [1, 2, 3, 4]}, replace=True)
    assert count(postgres, "SELECT count(*) FROM matchups") == 1
    assert count(postgres, "SELECT count(*) FROM matchup_metrics") == 2
    first = DOCUMENTS[CODEBOOK]
    postgres.put(CODEBOOK, first)
    postgres.put(CODEBOOK, {**first, "version": "next", "codes": first["codes"][:1]}, replace=True)
    postgres.put(CODEBOOK, {**first, "version": "last", "codes": []}, replace=True)
    assert (postgres.get(CODEBOOK) or {}).get("version") == "last"
    with postgres.engine.connect() as conn:
        saves = conn.execute(text("SELECT c.version, count(k.id) FROM codebooks c LEFT JOIN codebook_codes k ON k.parent_id = c.id GROUP BY c.id ORDER BY c.id")).all()
    assert [tuple(save) for save in saves] == [("94d9f9cc399f", 2), ("next", 1), ("last", 0)]


def test_delete_removes_every_row_under_the_prefix(postgres: PostgresStore) -> None:
    run = {key: value for key, value in DOCUMENTS.items() if key.startswith(RUN)}
    postgres.put_many(run)
    assert count(postgres, "SELECT count(*) FROM learner_states") > 0
    assert postgres.delete(RUN) == len(run)
    for table in [
        "records",
        "games",
        "game_steps",
        "game_players",
        "matchups",
        "matchup_sessions_packages",
        "learner_states",
        "learner_updates",
        "update_writes",
    ]:
        assert count(postgres, f"SELECT count(*) FROM {table}") == 0, table


def test_a_shared_text_is_kept_once(postgres: PostgresStore) -> None:
    postgres.put(GAME, DOCUMENTS[GAME])
    postgres.put(GAME.replace("game_1", "game_2"), DOCUMENTS[GAME])
    # the step's system message and the two calls' (the same text): one row
    assert count(postgres, "SELECT count(*) FROM texts") == 2
    assert count(postgres, "SELECT count(DISTINCT data_system_message) FROM game_steps") == 1


def test_a_schema_of_another_migration_is_refused(postgres: PostgresStore, postgres_url: str) -> None:
    with pytest.raises(RuntimeError, match="plystore-admin migrate"):
        PostgresStore(postgres_url, "plystore_not_migrated")
    with postgres.engine.begin() as conn:
        conn.execute(text(f"UPDATE {SCHEMA}.alembic_version SET version_num = 'other'"))
    try:
        with pytest.raises(RuntimeError, match="at migration other"):
            PostgresStore(postgres_url, SCHEMA)
    finally:
        with postgres.engine.begin() as conn:
            conn.execute(text(f"UPDATE {SCHEMA}.alembic_version SET version_num = :head"), {"head": migrations.head()})


def test_the_migrations_create_what_the_formats_describe(postgres: PostgresStore) -> None:
    # a format changed without a new migration (plystore-admin revision) fails here
    with postgres.engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_name": lambda name, kind, _: not (kind == "table" and name == "alembic_version")})
        assert compare_metadata(context, schema.METADATA) == []
