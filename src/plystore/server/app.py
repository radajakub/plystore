from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from plystore.keys import JSON, LOG, DocumentExists, check, check_prefix, kind
from plystore.server.db import Database, open_databases
from plystore.store import check_log, check_prefix_to_delete, encode, encode_record

# The plystore service: the Store calls over HTTP, one database per repository under /v1/{database}. The service checks every key and value
# with the client's own rules (keys.py, store.py), so a value one backend accepts every backend accepts. A bearer token guards every route but
# /health. Configured by the environment:
#   PLYSTORE_DATABASE_URL  the Postgres server, without a database (postgresql://user:password@host:5432)
#   PLYSTORE_DATABASES     the databases, comma-separated (default plybench,plyagents,plybench_analysis)
#   PLYSTORE_TOKEN or PLYSTORE_TOKEN_FILE  the token clients send
#   PLYSTORE_PORT          the port (default 8100)

DATABASES = "plybench,plyagents,plybench_analysis"


def _setting(name: str) -> str:
    if value := os.environ.get(name):
        return value
    if path := os.environ.get(f"{name}_FILE"):
        return Path(path).read_text().strip()
    raise RuntimeError(f"set {name} (or {name}_FILE)")


def _json(body: str, status: int = 200) -> Response:
    return Response(content=body, status_code=status, media_type="application/json")


def _value(request_body: bytes, key: str) -> tuple[str | None, str | None]:
    # the body {"value": ...}, checked and encoded by the client's rules: (JSON text, None) or (None, text)
    try:
        value = json.loads(request_body)["value"]
        encoded = encode(key, value)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    return (encoded, None) if kind(key) == JSON else (None, encoded)


def create_app(databases: dict[str, Database], token: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for database in databases.values():
            database.close()

    app = FastAPI(title="plystore", lifespan=lifespan)

    def authorized(request: Request) -> None:
        sent = request.headers.get("authorization", "")
        if not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "a valid bearer token is required")

    def database(name: str) -> Database:
        if name not in databases:
            raise HTTPException(404, f"no database {name!r}; known: {', '.join(databases)}")
        return databases[name]

    def key_of(key: str) -> str:
        try:
            return check(key)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    def prefix_of(prefix: str) -> str:
        try:
            return check_prefix(prefix)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "databases": list(databases)}

    @app.put("/v1/{name}/documents/{key:path}", dependencies=[Depends(authorized)])
    async def put(name: str, key: str, request: Request, replace: bool = False) -> Response:
        data, text = _value(await request.body(), key_of(key))
        try:
            database(name).put(key, data, text, replace)
        except DocumentExists as error:
            raise HTTPException(409, f"exists: {error}") from error
        return _json("{}", 201)

    @app.post("/v1/{name}/batch", dependencies=[Depends(authorized)])
    async def put_many(name: str, request: Request) -> Response:
        try:
            values = json.loads(await request.body())["values"]
            rows = []
            for key, value in values.items():
                encoded = encode(check(key), value)
                rows.append((key, encoded, None) if kind(key) == JSON else (key, None, encoded))
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise HTTPException(400, str(error)) from error
        try:
            database(name).put_many(rows)
        except DocumentExists as error:
            raise HTTPException(409, f"exists: {error}") from error
        return _json("{}", 201)

    @app.get("/v1/{name}/documents/{key:path}", dependencies=[Depends(authorized)])
    def get(name: str, key: str) -> Response:
        if kind(key_of(key)) == LOG:
            raise HTTPException(400, f"{key} is a log: read it with read_log")
        found = database(name).get(key)
        if found is None:
            raise HTTPException(404, f"no document {key}")
        data, text = found
        return _json('{"value":' + (data if data is not None else json.dumps(text)) + "}")

    @app.post("/v1/{name}/get", dependencies=[Depends(authorized)])
    async def get_many(name: str, request: Request) -> Response:
        try:
            keys = [check(key) for key in json.loads(await request.body())["keys"]]
            if any(kind(key) == LOG for key in keys):
                raise ValueError("logs are read with read_log")
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error
        rows = database(name).get_many(keys)
        return _json('{"values":{' + ",".join(json.dumps(key) + ":" + (data if data is not None else json.dumps(text)) for key, data, text in rows) + "}}")

    @app.get("/v1/{name}/exists/{key:path}", dependencies=[Depends(authorized)])
    def exists(name: str, key: str) -> dict[str, bool]:
        return {"exists": database(name).exists(key_of(key))}

    @app.get("/v1/{name}/keys", dependencies=[Depends(authorized)])
    def keys(name: str, prefix: str = "") -> dict[str, list[str]]:
        return {"keys": database(name).keys(prefix_of(prefix))}

    @app.get("/v1/{name}/games", dependencies=[Depends(authorized)])
    def games(name: str, prefix: str = "") -> dict[str, Any]:
        return {"games": database(name).games(prefix_of(prefix))}

    @app.post("/v1/{name}/logs/{key:path}", dependencies=[Depends(authorized)])
    async def append(name: str, key: str, request: Request) -> Response:
        try:
            record = json.loads(await request.body())["record"]
            line = encode_record(key_of(key), record)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error
        database(name).append(key, line)
        return _json("{}", 201)

    @app.get("/v1/{name}/logs/{key:path}", dependencies=[Depends(authorized)])
    def read_log(name: str, key: str) -> Response:
        try:
            check_log(key_of(key))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return _json('{"records":[' + ",".join(database(name).read_log(key)) + "]}")

    @app.delete("/v1/{name}/prefix/{prefix:path}", dependencies=[Depends(authorized)])
    def delete(name: str, prefix: str) -> dict[str, int]:
        try:
            check_prefix_to_delete(prefix_of(prefix))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"deleted": database(name).delete(prefix)}

    return app


def main() -> None:
    names = [name.strip() for name in os.environ.get("PLYSTORE_DATABASES", DATABASES).split(",") if name.strip()]
    app = create_app(open_databases(_setting("PLYSTORE_DATABASE_URL"), names), _setting("PLYSTORE_TOKEN"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PLYSTORE_PORT", "8100")))
