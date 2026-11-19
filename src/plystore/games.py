from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

# The typed columns of game records (plybench's GameTracker.to_dict in a document named game_N.json): what the service keeps in its games table
# (server/migrations/002_games.sql) and every other backend computes from the documents by the same rules, so a reader gets the same rows from
# any backend. A value of an unexpected type gives None; a record without game_round or a steps list is not a game.

GAME_NAME = re.compile(r"(^|/)game_[0-9]+\.json$")
INT_LIMIT = 2147483647


@dataclass(frozen=True)
class GameRow:
    key: str
    folder: str
    round: int | None
    i_player: str | None
    o_player: str | None
    result: int | None
    moves: int
    seed: int | float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _integer(value: Any) -> int | None:
    # as Postgres rounds a numeric: half away from zero; None beyond the integer range
    if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) >= INT_LIMIT:
        return None
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def _text(value: Any) -> str | None:
    # as Postgres' ->> gives it: a string as it is, null as None, a number or boolean as its JSON text (plybench's records hold strings)
    return value if isinstance(value, str) else None if value is None else json.dumps(value)


def game_row(key: str, data: Any) -> GameRow | None:
    if not GAME_NAME.search(key) or not isinstance(data, dict) or not isinstance(data.get("steps"), list) or "game_round" not in data:
        return None
    ending = data.get("ending")
    seed = data.get("seed")
    return GameRow(
        key=key,
        folder=key.rsplit("/", 1)[0],
        round=_integer(data.get("game_round")),
        i_player=_text(data.get("i_player")),
        o_player=_text(data.get("o_player")),
        result=_integer(ending.get("result")) if isinstance(ending, dict) else None,
        moves=len(data["steps"]),
        seed=seed if isinstance(seed, (int, float)) and not isinstance(seed, bool) else None,
    )


def rows_from(documents: Mapping[str, Any]) -> list[GameRow]:
    return sorted((row for key, value in documents.items() if (row := game_row(key, value)) is not None), key=lambda row: row.key)


@dataclass(frozen=True)
class FolderSummary:
    folder: str
    games: int
    won: int
    drawn: int
    lost: int
    mean_moves: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(rows: Iterable[GameRow]) -> list[FolderSummary]:
    # per folder (a matchup, or a phase of a training epoch), from the first player's side: result 1 won, 0 drawn, -1 lost
    by_folder: dict[str, list[GameRow]] = {}
    for row in rows:
        by_folder.setdefault(row.folder, []).append(row)
    return [
        FolderSummary(
            folder=folder,
            games=len(games),
            won=sum(1 for row in games if row.result == 1),
            drawn=sum(1 for row in games if row.result == 0),
            lost=sum(1 for row in games if row.result == -1),
            mean_moves=sum(row.moves for row in games) / len(games),
        )
        for folder, games in sorted(by_folder.items())
    ]
