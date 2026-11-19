from __future__ import annotations

import os
import tomllib
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from plystore.files import FileStore
from plystore.memory import MemoryStore
from plystore.routed import Route, RoutedStore
from plystore.store import Store

# Where a repository's outputs go is configuration only: the file PLYSTORE_CONFIG names (or the one given), read once at start. Without one,
# every key is a file under the root, as runs write today. A configuration routes key prefixes to backends, e.g.
#
#   [postgres]
#   schema = "plyagents"
#   url_env = "PLYSTORE_DATABASE_URL"   # the environment variable holding the database URL (this one by default)
#
#   [[route]]
#   prefix = ""
#   store = "postgres"
#   also_read = ["files"]
#
#   [[route]]
#   prefix = "plots"
#   store = "files"
#
# Backends: "files" (under the root), "memory", and each table naming a backend of the plystore.backends entry point group: "postgres" comes
# with the plystore[postgres] extra. A route's also_read lists backends to read from where its own has nothing (what was written before a
# switch), in that order.

ENV = "PLYSTORE_CONFIG"
GROUP = "plystore.backends"


def open_store(root: Path, config: Path | None = None) -> Store:
    path = config if config is not None else Path(os.environ[ENV]) if os.environ.get(ENV) else None
    if path is None:
        return FileStore(root)
    data: dict[str, Any] = tomllib.loads(path.read_text())
    backends: dict[str, Store] = {"files": FileStore(root), "memory": MemoryStore()}
    for name, table in data.items():
        if name == "route":
            continue
        found = entry_points(group=GROUP, name=name)
        if not found:
            raise ValueError(f"{path}: no backend {name!r} is installed (postgres comes with plystore[postgres])")
        backends[name] = next(iter(found)).load()(root, table)

    def backend(name: str) -> Store:
        if name not in backends:
            raise ValueError(f"{path}: unknown store {name!r}; known: {', '.join(backends)}")
        return backends[name]

    routes = [Route(route.get("prefix", ""), backend(route["store"]), tuple(backend(name) for name in route.get("also_read", []))) for route in data.get("route", [])]
    return RoutedStore(routes)
