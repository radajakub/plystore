from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from plystore.games import GAME_NAME, GameRow, rows_from
from plystore.keys import LOG, DocumentExists, check, check_prefix, kind, under
from plystore.store import check_log, check_prefix_to_delete, decode, encode, encode_record

# Everything in a dict, for tests and for runs that leave nothing behind. Documents are kept encoded, so a value is checked and copied as the
# other backends do: a caller changing its object later does not change what was stored.


class MemoryStore:
    def __init__(self) -> None:
        self._documents: dict[str, str] = {}
        self._logs: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        text = encode(check(key), value)
        with self._lock:
            if not replace and key in self._documents:
                raise DocumentExists(key)
            self._documents[key] = text

    def put_many(self, values: Mapping[str, Any]) -> None:
        texts = {check(key): encode(key, value) for key, value in values.items()}
        with self._lock:
            if there := [key for key in texts if key in self._documents]:
                raise DocumentExists(", ".join(there))
            self._documents.update(texts)

    def get(self, key: str) -> Any | None:
        if kind(check(key)) == LOG:
            raise ValueError(f"{key} is a log: read it with read_log")
        text = self._documents.get(key)
        return None if text is None else decode(key, text)

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        return {key: value for key in keys if (value := self.get(key)) is not None}

    def games(self, prefix: str = "") -> list[GameRow]:
        return rows_from(self.get_many([key for key in self.keys(prefix) if GAME_NAME.search(key)]))

    def exists(self, key: str) -> bool:
        return check(key) in self._documents or key in self._logs

    def keys(self, prefix: str = "") -> list[str]:
        check_prefix(prefix)
        with self._lock:
            return sorted(key for key in [*self._documents, *self._logs] if under(key, prefix))

    def append(self, key: str, record: Any) -> None:
        line = encode_record(check(key), record)
        with self._lock:
            self._logs.setdefault(key, []).append(line)

    def read_log(self, key: str) -> list[Any]:
        check_log(check(key))
        with self._lock:
            return [json.loads(line) for line in self._logs.get(key, [])]

    def delete(self, prefix: str) -> int:
        check_prefix_to_delete(check_prefix(prefix))
        with self._lock:
            gone = [key for key in [*self._documents, *self._logs] if under(key, prefix)]
            for key in gone:
                self._documents.pop(key, None)
                self._logs.pop(key, None)
        return len(gone)
