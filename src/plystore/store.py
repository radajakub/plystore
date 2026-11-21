from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from plystore.keys import JSON, LOG, kind

# The interface every writer and reader uses. One contract for every backend (tests/test_contract.py): a value a backend accepts, every backend
# accepts; so what Postgres cannot hold (NaN, infinities, the NUL character, lone surrogates) is refused everywhere, and a run never learns it only after a switch.


class Store(Protocol):
    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        # a .json key takes a JSON value, any other non-log key a str; write-once unless replace (DocumentExists)
        ...

    def put_many(self, values: Mapping[str, Any]) -> None:
        # several new documents, all or none (a learner's state folder); refuses all if one exists
        ...

    def get(self, key: str) -> Any | None:
        # the document, None when there is none
        ...

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        # the documents of those keys that exist, in one call (a whole matchup for a report); missing keys are left out
        ...

    def exists(self, key: str) -> bool: ...

    def keys(self, prefix: str = "") -> list[str]:
        # every key under the prefix (by whole parts), sorted
        ...

    def append(self, key: str, record: Any) -> None:
        # one JSON record at the end of a .jsonl log; appends from processes at the same time never interleave
        ...

    def append_many(self, key: str, records: Sequence[Any]) -> None:
        # several records at the end of one log, all or none, in one call (an import of a log); never interleaved with other appends
        ...

    def read_log(self, key: str) -> list[Any]:
        # the records of a log in order; [] when there is none
        ...

    def delete(self, prefix: str) -> int:
        # every document under a non-empty prefix (an admin action, never a run's); returns how many
        ...


def _bad_text(value: Any) -> bool:
    # a str Postgres cannot hold: the NUL character, or a lone surrogate (valid in JSON's \u escapes, not in UTF-8)
    if isinstance(value, str):
        return "\x00" in value or not value.isascii() and any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, dict):
        return any(_bad_text(k) or _bad_text(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_bad_text(item) for item in value)
    return False


def encode(key: str, value: Any) -> str:
    # the text a document is kept as; JSON as json.dumps writes it by default (the bytes plybench's game files have today)
    found = kind(key)
    if found == LOG:
        raise ValueError(f"{key} is a log: append records to it")
    if found == JSON:
        try:
            text = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{key}: not storable JSON ({error})") from error
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError(f"{key} is text: give a str, not {type(value).__name__}")
    if _bad_text(value):
        raise ValueError(f"{key}: contains the NUL character or a lone surrogate, which the database cannot hold")
    return text


def decode(key: str, text: str) -> Any:
    return json.loads(text) if kind(key) == JSON else text


def check_log(key: str) -> str:
    if kind(key) != LOG:
        raise ValueError(f"{key} is not a log (.jsonl)")
    return key


def encode_record(key: str, record: Any) -> str:
    # one log line, as plybench's appended logs write it today
    check_log(key)
    try:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{key}: not storable JSON ({error})") from error
    if _bad_text(record):
        raise ValueError(f"{key}: contains the NUL character or a lone surrogate, which the database cannot hold")
    return line


def check_prefix_to_delete(prefix: str) -> None:
    if prefix == "":
        raise ValueError("delete needs a prefix: deleting everything is not an action of the store")
