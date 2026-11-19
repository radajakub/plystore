from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from plystore.games import GameRow
from plystore.keys import JSON, LOG, DocumentExists, check, check_prefix, kind
from plystore.store import check_log, check_prefix_to_delete, encode, encode_record

# The client of the plystore service (server/app.py): one database of it, over HTTP with the standard library only, so a writer needs nothing
# else installed. Values are checked and encoded here first, so a refused value fails the same way it does in the other backends, before any
# request. One kept-alive connection per thread.


class ServiceStore:
    def __init__(self, url: str, database: str, token: str, timeout: float = 60.0) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"a service URL is http(s)://host[:port], got {url!r}")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port
        self._base = parts.path.rstrip("/") + f"/v1/{quote(database, safe='')}"
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._timeout = timeout
        self._local = threading.local()

    def _connection(self) -> http.client.HTTPConnection:
        if getattr(self._local, "connection", None) is None:
            opener = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            self._local.connection = opener(self._host, self._port, timeout=self._timeout)
        return self._local.connection

    def _request(self, method: str, path: str, body: str | None = None) -> tuple[int, str]:
        # a kept-alive connection the server closed meanwhile is opened again, once
        for attempt in range(2):
            connection = self._connection()
            try:
                connection.request(method, self._base + path, body=body.encode() if body is not None else None, headers=self._headers)
                response = connection.getresponse()
                return response.status, response.read().decode()
            except (http.client.HTTPException, ConnectionError):
                connection.close()
                self._local.connection = None
                if attempt == 1:
                    raise
        raise AssertionError("unreachable")

    def _fail(self, status: int, body: str) -> None:
        try:
            detail = json.loads(body).get("detail", body)
        except ValueError:
            detail = body
        if status == 409:
            raise DocumentExists(str(detail).removeprefix("exists: "))
        if status == 400:
            raise ValueError(detail)
        raise RuntimeError(f"plystore service answered {status}: {detail}")

    def _key_path(self, area: str, key: str) -> str:
        return f"/{area}/{quote(check(key), safe='/')}"

    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        encoded = encode(check(key), value)
        body = '{"value":' + (encoded if kind(key) == JSON else json.dumps(encoded)) + "}"
        status, answer = self._request("PUT", self._key_path("documents", key) + ("?replace=true" if replace else ""), body)
        if status != 201:
            self._fail(status, answer)

    def put_many(self, values: Mapping[str, Any]) -> None:
        if not values:
            return
        parts = []
        for key, value in values.items():
            encoded = encode(check(key), value)
            parts.append(json.dumps(key) + ":" + (encoded if kind(key) == JSON else json.dumps(encoded)))
        status, answer = self._request("POST", "/batch", '{"values":{' + ",".join(parts) + "}}")
        if status != 201:
            self._fail(status, answer)

    def get(self, key: str) -> Any | None:
        if kind(check(key)) == LOG:
            raise ValueError(f"{key} is a log: read it with read_log")
        status, answer = self._request("GET", self._key_path("documents", key))
        if status == 404:
            return None
        if status != 200:
            self._fail(status, answer)
        return json.loads(answer)["value"]

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        for key in keys:
            if kind(check(key)) == LOG:
                raise ValueError(f"{key} is a log: read it with read_log")
        if not keys:
            return {}
        status, answer = self._request("POST", "/get", json.dumps({"keys": list(keys)}))
        if status != 200:
            self._fail(status, answer)
        # a stored JSON null reads as missing, as get and the other backends give it
        return {key: value for key, value in json.loads(answer)["values"].items() if value is not None}

    def games(self, prefix: str = "") -> list[GameRow]:
        status, answer = self._request("GET", "/games?" + urlencode({"prefix": check_prefix(prefix)}))
        if status != 200:
            self._fail(status, answer)
        return [GameRow(**row) for row in json.loads(answer)["games"]]

    def exists(self, key: str) -> bool:
        status, answer = self._request("GET", self._key_path("exists", key))
        if status != 200:
            self._fail(status, answer)
        return bool(json.loads(answer)["exists"])

    def keys(self, prefix: str = "") -> list[str]:
        status, answer = self._request("GET", "/keys?" + urlencode({"prefix": check_prefix(prefix)}))
        if status != 200:
            self._fail(status, answer)
        return list(json.loads(answer)["keys"])

    def append(self, key: str, record: Any) -> None:
        line = encode_record(check(key), record)
        status, answer = self._request("POST", self._key_path("logs", key), '{"record":' + line + "}")
        if status != 201:
            self._fail(status, answer)

    def read_log(self, key: str) -> list[Any]:
        status, answer = self._request("GET", self._key_path("logs", check_log(check(key))))
        if status != 200:
            self._fail(status, answer)
        return list(json.loads(answer)["records"])

    def delete(self, prefix: str) -> int:
        check_prefix_to_delete(check_prefix(prefix))
        status, answer = self._request("DELETE", f"/prefix/{quote(prefix, safe='/')}")
        if status != 200:
            self._fail(status, answer)
        return int(json.loads(answer)["deleted"])
