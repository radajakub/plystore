from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from plystore.keys import LOG, kind
from plystore.store import Store, encode, encode_record

# Moving records between backends: the import of plydata's folders into Postgres is copy(FileStore(plydata/<repo>), postgres). Nothing in
# the target is ever overwritten: a key already there with the same content is skipped, one with other content is a conflict, left as it is;
# so a copy can run again after a failure, and a log copied in part goes on from where it stopped. Documents move in batches (one read, one
# write and one read-back per batch); a batch that fails as a whole is done again key by key, so one bad document fails alone.

BATCH = 200


@dataclass
class CopyReport:
    copied: list[str] = field(default_factory=list)
    same: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        return f"{len(self.copied)} copied, {len(self.same)} already there, {len(self.conflicts)} conflicts, {len(self.failed)} failed"


def _copy_log(source: Store, target: Store, key: str, report: CopyReport) -> None:
    records = source.read_log(key)
    there = target.read_log(key)
    if records[: len(there)] != there:
        report.conflicts.append(key)
        return
    for record in records[len(there) :]:
        target.append(key, record)
    (report.copied if len(records) > len(there) else report.same).append(key)


def _copy_one(source: Store, target: Store, key: str, report: CopyReport) -> None:
    value = source.get(key)
    if target.exists(key):
        (report.same if target.get(key) == value else report.conflicts).append(key)
        return
    target.put(key, value)
    if target.get(key) != value:
        report.failed[key] = "read back differs from the source"
        return
    report.copied.append(key)


def _copy_batch(source: Store, target: Store, keys: list[str], report: CopyReport) -> None:
    values = source.get_many(keys)
    there = target.get_many(keys)
    new = {}
    for key in keys:
        if key not in values:
            report.failed[key] = "listed in the source but not readable there"
        elif key in there:
            (report.same if there[key] == values[key] else report.conflicts).append(key)
        else:
            new[key] = values[key]
    if not new:
        return
    target.put_many(new)
    back = target.get_many(list(new))
    for key, value in new.items():
        if back.get(key) == value:
            report.copied.append(key)
        else:
            report.failed[key] = "read back differs from the source"


def copy(source: Store, target: Store, prefix: str = "", batch: int = BATCH) -> CopyReport:
    report = CopyReport()
    keys = source.keys(prefix)
    for key in [key for key in keys if kind(key) == LOG]:
        try:
            _copy_log(source, target, key, report)
        except Exception as error:
            report.failed[key] = f"{type(error).__name__}: {error}"
    documents = [key for key in keys if kind(key) != LOG]
    for start in range(0, len(documents), batch):
        chunk = documents[start : start + batch]
        try:
            _copy_batch(source, target, chunk, report)
            continue
        except Exception:
            pass
        # the batch as a whole failed (a document no store takes, keys of two routes): key by key, so each is reported on its own
        done = set(report.copied) | set(report.same) | set(report.conflicts) | set(report.failed)
        for key in chunk:
            if key in done:
                continue
            try:
                _copy_one(source, target, key, report)
            except Exception as error:
                report.failed[key] = f"{type(error).__name__}: {error}"
    return report


@dataclass
class ScanReport:
    kinds: Counter[str] = field(default_factory=Counter)
    folders: Counter[str] = field(default_factory=Counter)
    problems: dict[str, str] = field(default_factory=dict)


def scan(source: Store, prefix: str = "", depth: int = 3) -> ScanReport:
    # what a copy of the prefix would move, by kind and by folder (the first `depth` parts), and every document no backend would take
    report = ScanReport()
    for key in source.keys(prefix):
        found = kind(key)
        report.kinds[found] += 1
        parts = key.split("/")
        report.folders["/".join(parts[: min(depth, len(parts) - 1)]) or "."] += 1
        try:
            if found == LOG:
                for record in source.read_log(key):
                    encode_record(key, record)
            else:
                encode(key, source.get(key))
        except Exception as error:
            report.problems[key] = f"{type(error).__name__}: {error}"
    return report
