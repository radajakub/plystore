from __future__ import annotations

from plystore.mapping import Column


def cols(kind: str, *names: str) -> tuple[Column, ...]:
    # several columns of one type, each named as its field
    return tuple(Column(name, kind) for name in names)
