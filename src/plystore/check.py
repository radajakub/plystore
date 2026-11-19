from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from plystore import mapping
from plystore.formats import find
from plystore.keys import LOG, kind
from plystore.store import Store, encode, encode_record

# The round trip of every record under a prefix, without a database: split by its format and rebuilt, it must equal what was read. Run over a
# data folder before an import (plystore-check), and over the records a repo's own code writes before a release (the plytools contract).


@dataclass
class CheckReport:
    records: Counter[str] = field(default_factory=Counter)  # by format; "unmapped" for keys no format matches
    whole: Counter[str] = field(default_factory=Counter)  # by format: records not the shape the format keeps, kept whole
    leftovers: Counter[str] = field(default_factory=Counter)  # table.field of values that went to extra, in how many records
    differ: list[str] = field(default_factory=list)  # keys whose rebuilt record is not the one read
    refused: dict[str, str] = field(default_factory=dict)  # keys no store would take (NaN, NUL, ...)
    unmapped: Counter[str] = field(default_factory=Counter)  # by folder (two parts) and suffix

    @property
    def ok(self) -> bool:
        return not self.differ and not self.refused


def check(store: Store, prefix: str = "") -> CheckReport:
    report = CheckReport()
    for key in store.keys(prefix):
        log = kind(key) == LOG
        records = store.read_log(key) if log else [store.get(key)]
        try:
            for record in records:
                encode_record(key, record) if log else encode(key, record)
        except ValueError as error:
            report.refused[key] = str(error)
            continue
        fmt = find(key)
        if fmt is None or fmt.log != log:
            report.records["unmapped"] += len(records)
            parts = key.split("/")
            report.unmapped["/".join(parts[:2]) + " *" + (parts[-1].rsplit(".", 1)[-1] if "." in parts[-1] else "")] += len(records)
            continue
        report.records[fmt.name] += len(records)
        for record in records:
            row = mapping.split(fmt, record)
            if row is None:
                report.whole[fmt.name] += 1
            elif not mapping.same(mapping.rebuild(fmt, row), record):
                report.differ.append(key)
            else:
                report.leftovers.update(set(mapping.leftovers(row)))
    return report
