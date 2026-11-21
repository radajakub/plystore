from __future__ import annotations

import argparse
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import text

from plystore import FileStore, copy
from plystore.mapping import same
from plystore.postgres import migrations
from plystore.postgres.store import PostgresStore, engine_for

# The two backends side by side on real data (make demo): one folder of plydata is copied into a FileStore in a temporary folder and into the
# schema plystore_demo of the local Postgres (make up), then the same calls run on both. Reads plydata only; writes only the temporary folder
# and that schema, which it empties first. With --keep both stay for a look around (the commands are printed at the end).

SCHEMA = "plystore_demo"
HERE = Path(__file__).resolve().parent.parent


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def timed(label: str, call: Callable[[], Any]) -> Any:
    start = time.perf_counter()
    result = call()
    print(f"  {label}: {1000 * (time.perf_counter() - start):.1f} ms")
    return result


def rows(store: PostgresStore, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with store.engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), params)]


def win_rates(files: FileStore, prefix: str) -> dict[str, tuple[int, float, float]]:
    # what a reader computes from files today: every game record read and parsed
    # a player's games in either seat; it won where the result for its seat is a win (the ending's result is the first seat's: 0 win, 1 loss)
    games: dict[str, list[tuple[int, int]]] = {}
    for key in files.keys(prefix):
        if key.rsplit("/", 1)[-1].startswith("game_"):
            game = files.get(key) or {}
            result = game.get("ending", {}).get("result")
            for seat, won in (("i_player", 0), ("o_player", 1)):
                games.setdefault(game.get(seat, "?"), []).append((int(result == won), len(game.get("steps", []))))
    return {player: (len(found), sum(w for w, _ in found) / len(found), sum(m for _, m in found) / len(found)) for player, found in sorted(games.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Files and Postgres side by side on one folder of real data.")
    parser.add_argument("--data", type=Path, default=HERE.parent / "plydata" / "plybench", help="a plydata folder (read only)")
    parser.add_argument("--prefix", default="results/benchmarks/ttt/tic_tac_toe_100", help="the folder of it to copy")
    parser.add_argument("--experiment", default="experiments/benchmarks/ttt.json", help="an experiment file of it, for the history step")
    parser.add_argument("--url", default=os.environ.get("PLYSTORE_DATABASE_URL", "postgresql://plystore:plystore@127.0.0.1:55432/plystore"))
    parser.add_argument("--keep", action="store_true", help="keep the files and the schema, do not delete them in the last step")
    args = parser.parse_args()

    source = FileStore(args.data)
    folder = Path(tempfile.mkdtemp(prefix="plystore-demo-"))
    files = FileStore(folder)
    with engine_for(args.url, SCHEMA).begin() as conn:
        migrations.upgrade(conn, SCHEMA)
    postgres = PostgresStore(args.url, SCHEMA)
    postgres.truncate()

    section(f"1. the same records into both backends: {args.prefix}")
    for name, store in (("files", files), ("postgres", postgres)):
        start = time.perf_counter()
        report = copy(source, store, args.prefix)
        print(f"  {name}: {report.summary()} in {time.perf_counter() - start:.1f} s (every record read back and compared)")
    keys = files.keys(args.prefix)
    game_key = next(key for key in keys if key.endswith("/game_1.json"))
    folder_of_game = game_key.rsplit("/", 1)[0]

    section("2. where they are")
    size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
    print(f"  files: {len(keys)} files, {size / 1e6:.1f} MB under {folder}")
    print(f"    {game_key}")
    print(f"    {game_key.replace('game_1', 'game_2')}")
    print(f"    {folder_of_game}/metadata.json  ...")
    counts = {name: rows(postgres, f'SELECT count(*) FROM "{name}"')[0][0] for name in postgres.tables}
    tables = sorted(((name, count) for name, count in counts.items() if count), key=lambda item: -item[1])
    database = rows(
        postgres, "SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema", schema=SCHEMA
    )
    print(f"  postgres: {database[0][0]} in schema {SCHEMA}, rows per table:")
    for name, count in tables:
        print(f"    {count:>8}  {name}")
    print(f"  (texts: each distinct system prompt once, referenced by hash from {counts['game_steps']} steps)")
    print("  one game, as a row of games (typed columns) with its rows in game_steps:")
    game = rows(postgres, "SELECT id, game_round, i_player, o_player, end_result, seed, extra FROM games WHERE key = :key", key=game_key)[0]
    print(f"    games: round={game[1]} i_player={game[2][:50]!r} o_player={game[3]!r} end_result={game[4]} seed={game[5]} extra={game[6]}")
    for step in rows(
        postgres,
        "SELECT seq, player_hash, move, input_tokens, output_tokens, left(data_reasoning_trace, 50) FROM game_steps WHERE parent_id = :id ORDER BY position LIMIT 4",
        id=game[0],
    ):
        print(f"    game_steps: seq={step[0]} player={step[1]} move={step[2]!r} tokens={step[3]}/{step[4]} reasoning={step[5]!r}")

    section("3. reading records back: the same JSON from both")
    one_file = timed("one game from files", lambda: files.get(game_key))
    one_postgres = timed("one game from postgres (rebuilt from its rows)", lambda: postgres.get(game_key))
    print(f"  equal: {same(one_file, one_postgres)}")
    all_file = timed(f"all {len(keys)} records from files", lambda: files.get_many(keys))
    all_postgres = timed(f"all {len(keys)} records from postgres", lambda: postgres.get_many(keys))
    print(f"  equal: {same(all_file, all_postgres)}")

    section("4. a question over every game: win rate and mean moves per player")
    from_files = timed("files: read and parse every game in Python", lambda: win_rates(files, args.prefix))
    query = """
        WITH seats AS (
            SELECT i_player AS player, (end_result = 0)::int AS won, id FROM games WHERE folder LIKE :like
            UNION ALL SELECT o_player, (end_result = 1)::int, id FROM games WHERE folder LIKE :like)
        SELECT player, count(*), avg(won)::float, avg((SELECT count(*) FROM game_steps s WHERE s.parent_id = seats.id))::float
        FROM seats GROUP BY player ORDER BY player"""
    from_sql = timed("postgres: one SQL query over the typed columns", lambda: rows(postgres, query, like=args.prefix + "/%"))
    for player, count, rate, moves in from_sql:
        print(f"    {count:>5} games  win rate {rate:.3f}  moves {moves:.2f}  {player[:70]}")
    agree = len(from_files) == len(from_sql) and all(
        from_files[player][0] == count and math.isclose(from_files[player][1], rate) and math.isclose(from_files[player][2], moves) for player, count, rate, moves in from_sql
    )
    print(f"  equal (up to float rounding): {agree}")

    section("5. replacing a record")
    metadata_key = f"{folder_of_game}/metadata.json"
    metadata = files.get(metadata_key) or {}
    changed = {**metadata, "completed": [*metadata.get("completed", []), 999]}
    for store in (files, postgres):
        store.put(metadata_key, changed, replace=True)
    print(
        f"  a matchup's metadata.json (replaced after every game): files overwrite it; postgres drops its old rows: {rows(postgres, 'SELECT count(*) FROM matchups WHERE key = :key', key=metadata_key)[0][0]} row"
    )
    experiment = source.get(args.experiment)
    for version, num_games in enumerate((None, 200, 300)):
        value = experiment if num_games is None else {**(experiment or {}), "num_games": num_games}
        for store in (files, postgres):
            store.put(args.experiment, value, replace=version > 0)
    print(f"  {args.experiment} saved 3 times (num_games edited): files keep the last only ({(files.get(args.experiment) or {}).get('num_games')})")
    print("  postgres keeps every save (experiment configs and codebooks keep history):")
    for saved, replaced, num_games in rows(postgres, "SELECT saved, replaced, num_games FROM experiment_configs WHERE key = :key ORDER BY id", key=args.experiment):
        print(f"    saved {saved:%H:%M:%S.%f}  num_games {num_games}  {'replaced ' + format(replaced, '%H:%M:%S.%f') if replaced else 'current'}")

    section("6. rules both refuse the same way")
    for label, call in (
        ("write a game twice", lambda store: store.put(game_key, one_file)),
        ("a NaN", lambda store: store.put(f"{folder_of_game}/game_0.json", {"x": float("nan")})),
    ):
        outcomes = []
        for store in (files, postgres):
            try:
                call(store)
                outcomes.append("written")
            except Exception as error:
                outcomes.append(type(error).__name__)
        print(f"  {label}: files {outcomes[0]}, postgres {outcomes[1]}")
    try:
        PostgresStore(args.url.replace("55432", "55433"), SCHEMA)
        print("  postgres down: opened")
    except Exception as error:
        print(f"  postgres down (wrong port): a writer fails at once ({type(error).__name__}); files do not need anything running")

    section("7. deleting an experiment")
    print(f"  under {args.prefix}: files {len(files.keys(args.prefix))}, postgres {len(postgres.keys(args.prefix))} records")
    if args.keep:
        print("  kept (--keep). Look around:")
        print(f"    files:    ls {folder}/{args.prefix}")
        print(f"    postgres: docker exec -it -e PGOPTIONS='-c search_path={SCHEMA}' plystore-postgres-1 psql -U plystore -d plystore")
        print(f"    delete:   PLYSTORE_DATABASE_URL={args.url} uv run plystore-admin delete --schema {SCHEMA} --prefix {args.prefix}")
        return
    print(f"  files: {files.delete(args.prefix)} deleted; postgres: {postgres.delete(args.prefix)} deleted, and with them every row of their tables:")
    left = rows(postgres, "SELECT (SELECT count(*) FROM games), (SELECT count(*) FROM game_steps), (SELECT count(*) FROM matchups), (SELECT count(*) FROM matchup_metrics)")[0]
    print(f"    games {left[0]}, game_steps {left[1]}, matchups {left[2]}, matchup_metrics {left[3]}")
    shutil.rmtree(folder)
    postgres.truncate()


if __name__ == "__main__":
    main()
