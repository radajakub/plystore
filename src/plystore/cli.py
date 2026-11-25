from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from plystore.check import check
from plystore.config import open_store
from plystore.copy import copy, scan
from plystore.files import FileStore
from plystore.keys import reserved


def scan_main() -> None:
    # read only: what an import of a data folder (plydata/<repo>) would move, what it skips, and what no backend would take
    parser = argparse.ArgumentParser(description="Scan a data folder before an import: documents by kind and folder, skipped files, documents no store would take.")
    parser.add_argument("root", type=Path, help="the data folder, e.g. plydata/plybench")
    parser.add_argument("--prefix", default="", help="only keys under this prefix")
    parser.add_argument("--depth", type=int, default=3, help="folder parts to group by")
    args = parser.parse_args()
    report = scan(FileStore(args.root), args.prefix, args.depth)
    skipped = []
    for folder, _, files in os.walk(args.root / args.prefix if args.prefix else args.root):
        skipped += [key for name in files if reserved(key := (Path(folder) / name).relative_to(args.root).as_posix())]
    print(f"{sum(report.kinds.values())} documents: " + ", ".join(f"{count} {name}" for name, count in report.kinds.most_common()))
    for folder, count in sorted(report.folders.items()):
        print(f"  {count:>8}  {folder}")
    print(f"{len(skipped)} files skipped (hidden, temporary or lock files)")
    for key in skipped[:20]:
        print(f"  {key}")
    print(f"{len(report.problems)} documents no store would take")
    for key, problem in list(report.problems.items())[:50]:
        print(f"  {key}: {problem}")


def copy_main() -> None:
    parser = argparse.ArgumentParser(description="Copy the documents of a data folder into the store a configuration names; never overwrites.")
    parser.add_argument("root", type=Path, help="the data folder to copy from, e.g. plydata/plybench")
    parser.add_argument("config", type=Path, help="the plystore configuration of the target (its files backend is rooted at --target-root)")
    parser.add_argument("--target-root", type=Path, required=True, help="the root of the target's files backend")
    parser.add_argument("--prefix", default="", help="only keys under this prefix")
    args = parser.parse_args()
    report = copy(FileStore(args.root), open_store(args.target_root, args.config), args.prefix)
    print(report.summary())
    for key in report.conflicts[:50]:
        print(f"  conflict: {key}")
    for key, problem in list(report.failed.items())[:50]:
        print(f"  failed: {key}: {problem}")
    if report.conflicts or report.failed:
        raise SystemExit(1)


def export_main() -> None:
    # read only on the store: copy's other direction, the records of the configured store (a server's database, a restored dump) into a data folder laid out as plydata
    parser = argparse.ArgumentParser(description="Copy the records under some prefixes of the store a configuration names into a data folder; never overwrites.")
    parser.add_argument("config", type=Path, help="the plystore configuration of the source, e.g. a postgres schema, with PLYSTORE_DATABASE_URL set")
    parser.add_argument("out", type=Path, help="the data folder to write, e.g. exports/plybench")
    parser.add_argument("--prefix", nargs="+", required=True, help="the keys to export, by whole parts, e.g. analysis/ttt/reasoning/annotations.jsonl results/benchmarks/ttt")
    parser.add_argument("--source-root", type=Path, default=Path("."), help="the root of the source's files backend, if its configuration routes any prefix to files")
    args = parser.parse_args()
    source, target = open_store(args.source_root, args.config), FileStore(args.out)
    failed = False
    for prefix in args.prefix:
        report = copy(source, target, prefix)
        print(f"{prefix}: {report.summary()}")
        for key in report.conflicts[:50]:
            print(f"  conflict: {key}")
        for key, problem in list(report.failed.items())[:50]:
            print(f"  failed: {key}: {problem}")
        failed = failed or bool(report.conflicts or report.failed)
    if failed:
        raise SystemExit(1)


# how often plystore-check says how far it is, in keys
PROGRESS_EVERY = 5000


def check_main() -> None:
    # read only: the round trip of every record of a data folder through its format, without a database
    parser = argparse.ArgumentParser(description="Check that every record of a data folder rebuilds to itself from its typed rows (no database needed).")
    parser.add_argument("root", type=Path, help="the data folder, e.g. plydata/plybench")
    parser.add_argument("--prefix", default="", help="only keys under this prefix")
    args = parser.parse_args()
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        # the first key (listing them all can take a while), then every PROGRESS_EVERY keys and the last
        if done == 1 or done % PROGRESS_EVERY == 0 or done == total:
            print(f"checking {args.root}: key {done}/{total} ({time.monotonic() - started:.0f} s)", flush=True)

    print(f"listing the keys under {args.root}{f'/{args.prefix}' if args.prefix else ''}", flush=True)
    report = check(FileStore(args.root), args.prefix, progress)
    print(f"{sum(report.records.values())} records")
    for name, count in report.records.most_common():
        whole = f", {report.whole[name]} kept whole (not the format's shape)" if report.whole[name] else ""
        print(f"  {count:>8}  {name}{whole}")
    print(f"{len(report.leftovers)} fields with values outside the typed columns (records holding them)")
    for name, count in report.leftovers.most_common(40):
        print(f"  {count:>8}  {name}")
    print("unmapped, by folder and suffix")
    for name, count in report.unmapped.most_common(20):
        print(f"  {count:>8}  {name}")
    print(f"{len(report.differ)} records that do not rebuild to themselves")
    for key in report.differ[:20]:
        print(f"  {key}")
    print(f"{len(report.refused)} records no store would take")
    for key, problem in list(report.refused.items())[:20]:
        print(f"  {key}: {problem}")
    if not report.ok:
        raise SystemExit(1)
