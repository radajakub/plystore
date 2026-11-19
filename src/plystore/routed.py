from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from plystore.keys import DocumentExists, check, check_prefix, under
from plystore.store import Store, check_prefix_to_delete

# Many backends behind one Store: each key goes to the route with the longest prefix that holds it (plots/ to files, the rest to Postgres).
# A route's also_read stores hold what was written before a switch (plydata's files once runs write to Postgres): reads fall back to them,
# a write-once key there counts as written, and a log continues after their records. Writes and deletes go to the route's own store only.


@dataclass(frozen=True)
class Route:
    prefix: str
    store: Store
    also_read: tuple[Store, ...] = ()


class RoutedStore:
    def __init__(self, routes: Sequence[Route]) -> None:
        prefixes = [check_prefix(route.prefix) for route in routes]
        if "" not in prefixes or len(set(prefixes)) != len(prefixes):
            raise ValueError(f"routes need one route for '' (every key) and distinct prefixes, got {prefixes}")
        self.routes = sorted(routes, key=lambda route: len(route.prefix), reverse=True)

    def route(self, key: str) -> Route:
        return next(route for route in self.routes if under(check(key), route.prefix))

    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        route = self.route(key)
        if not replace and any(store.exists(key) for store in route.also_read):
            raise DocumentExists(key)
        route.store.put(key, value, replace=replace)

    def put_many(self, values: Mapping[str, Any]) -> None:
        routes = {id(self.route(key)): self.route(key) for key in values}
        if len(routes) > 1:
            raise ValueError("put_many writes all or none in one store: its keys must share a route")
        for route in routes.values():
            if there := [key for key in values if any(store.exists(key) for store in route.also_read)]:
                raise DocumentExists(", ".join(there))
            route.store.put_many(values)

    def get(self, key: str) -> Any | None:
        route = self.route(key)
        for store in (route.store, *route.also_read):
            if (value := store.get(key)) is not None:
                return value
        return None

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        # one call per route and store: the route's own store first, its also_read stores for what is still missing
        found: dict[str, Any] = {}
        by_route: dict[int, tuple[Route, list[str]]] = {}
        for key in keys:
            route = self.route(key)
            by_route.setdefault(id(route), (route, []))[1].append(key)
        for route, wanted in by_route.values():
            for store in (route.store, *route.also_read):
                missing = [key for key in wanted if key not in found]
                if not missing:
                    break
                found.update(store.get_many(missing))
        return {key: found[key] for key in keys if key in found}

    def exists(self, key: str) -> bool:
        route = self.route(key)
        return any(store.exists(key) for store in (route.store, *route.also_read))

    def keys(self, prefix: str = "") -> list[str]:
        check_prefix(prefix)
        found: set[str] = set()
        for route in self.routes:
            if not (under(route.prefix, prefix) or under(prefix, route.prefix)):
                continue
            narrower = route.prefix if under(route.prefix, prefix) else prefix
            for store in (route.store, *route.also_read):
                # a store shared by two routes lists the other's keys too: each key counts only for the route that owns it
                found.update(key for key in store.keys(narrower) if under(key, prefix) and self.route(key) is route)
        return sorted(found)

    def append(self, key: str, record: Any) -> None:
        self.route(key).store.append(key, record)

    def read_log(self, key: str) -> list[Any]:
        # the records written before the switch first, then the new ones
        route = self.route(key)
        return [record for store in (*reversed(route.also_read), route.store) for record in store.read_log(key)]

    def delete(self, prefix: str) -> int:
        check_prefix_to_delete(check_prefix(prefix))
        gone = 0
        for route in self.routes:
            if under(route.prefix, prefix) or under(prefix, route.prefix):
                narrower = route.prefix if under(route.prefix, prefix) else prefix
                gone += route.store.delete(narrower)
        return gone
