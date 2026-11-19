from __future__ import annotations

import os
import tomllib
from pathlib import Path

from plystore.files import FileStore
from plystore.memory import MemoryStore
from plystore.routed import Route, RoutedStore
from plystore.service import ServiceStore
from plystore.store import Store

# Where a repository's outputs go is configuration only: the file PLYSTORE_CONFIG names (or the one given), read once at start. Without one,
# every key is a file under the root, as runs write today. A configuration routes key prefixes to backends, e.g.
#
#   [service]
#   url = "http://127.0.0.1:8100"
#   database = "plybench"
#   token_env = "PLYSTORE_TOKEN"   # the environment variable holding the token (this one by default)
#
#   [[route]]
#   prefix = ""
#   store = "service"
#   also_read = ["files"]
#
#   [[route]]
#   prefix = "plots"
#   store = "files"
#
# Backends: "files" (under the root), "memory", and "service" when the [service] table is there. A route's also_read lists backends to read
# from where its own has nothing (what was written before a switch), in that order.

ENV = "PLYSTORE_CONFIG"


def open_store(root: Path, config: Path | None = None) -> Store:
    path = config if config is not None else Path(os.environ[ENV]) if os.environ.get(ENV) else None
    if path is None:
        return FileStore(root)
    data = tomllib.loads(path.read_text())
    backends: dict[str, Store] = {"files": FileStore(root), "memory": MemoryStore()}
    if service := data.get("service"):
        token_env = service.get("token_env", "PLYSTORE_TOKEN")
        if not os.environ.get(token_env):
            raise ValueError(f"{path}: the service token is read from {token_env}, which is not set")
        backends["service"] = ServiceStore(service["url"], service["database"], os.environ[token_env])

    def backend(name: str) -> Store:
        if name not in backends:
            raise ValueError(f"{path}: unknown store {name!r}; known: {', '.join(backends)}")
        return backends[name]

    routes = [Route(route.get("prefix", ""), backend(route["store"]), tuple(backend(name) for name in route.get("also_read", []))) for route in data.get("route", [])]
    return RoutedStore(routes)
