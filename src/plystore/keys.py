from __future__ import annotations

# A key names one document like a relative path ("results/benchmarks/x/game_1.json"); its suffix says what the document holds:
# .json a JSON value, .jsonl an append-only log of JSON records, anything else text. Writers name documents; only a backend knows where they live.

JSON = "json"
LOG = "log"
TEXT = "text"


class DocumentExists(Exception):
    # a write-once document is there already (put without replace, put_many)
    pass


def reserved(key: str) -> bool:
    # names a backend keeps for itself (temporary and lock files, hidden folders): never a document, never listed
    return any(part.startswith(".") for part in key.split("/")) or key.endswith((".tmp", ".lock"))


def check(key: str) -> str:
    if not key or "\\" in key or "" in key.split("/") or reserved(key):
        raise ValueError(f"invalid key {key!r}: a relative name of parts joined by '/', none empty or starting with '.', not ending in .tmp or .lock")
    return key


def check_prefix(prefix: str) -> str:
    # "" is every key; otherwise a key-shaped folder name
    return prefix if prefix == "" else check(prefix)


def under(key: str, prefix: str) -> bool:
    # by whole parts: "results/ttt" holds "results/ttt/game_1.json", not "results/ttt2/game_1.json"
    return prefix == "" or key == prefix or key.startswith(prefix + "/")


def kind(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    return JSON if name.endswith(".json") else LOG if name.endswith(".jsonl") else TEXT
