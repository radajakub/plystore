from __future__ import annotations

import fcntl
import json
import os
import posixpath
import shutil
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from plystore.keys import LOG, DocumentExists, check, check_prefix, kind, reserved
from plystore.store import check_log, check_prefix_to_delete, decode, encode, encode_record

# Each document a file under a root folder, its key the relative path: the layout plydata has today, so old folders read as they are and a run
# with this backend writes what it wrote before. Writes as plybench does them now: a temporary file, fsync, then a rename; logs appended under flock.

STAGING = ".plystore-staging"


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / check(key)

    def _temporary(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary

    def put(self, key: str, value: Any, *, replace: bool = False) -> None:
        path = self._path(key)
        temporary = self._temporary(path, encode(key, value))
        try:
            if replace:
                os.replace(temporary, path)
            else:
                # a hard link fails on an existing name: write-once without a window between the check and the write
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise DocumentExists(key) from None
        finally:
            temporary.unlink(missing_ok=True)

    def put_many(self, values: Mapping[str, Any]) -> None:
        texts = {check(key): encode(key, value) for key, value in values.items()}
        if not texts:
            return
        if there := [key for key in texts if (self.root / key).exists()]:
            raise DocumentExists(", ".join(there))
        staging = self.root / STAGING / uuid.uuid4().hex
        try:
            for key, text in texts.items():
                path = staging / key
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            folders = [posixpath.dirname(key) for key in texts]
            folder = "" if "" in folders else posixpath.commonpath(folders)
            target = self.root / folder
            # a new folder holding all of them appears at once by one rename; otherwise each is linked in, and taken out again if one is there
            if folder and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(staging / folder, target)
                    return
                except OSError:
                    pass
            linked: list[Path] = []
            try:
                for key in texts:
                    path = self.root / key
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.link(staging / key, path)
                    linked.append(path)
            except FileExistsError:
                for path in linked:
                    path.unlink(missing_ok=True)
                raise DocumentExists(", ".join(key for key in texts if (self.root / key).exists())) from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                os.rmdir(self.root / STAGING)
            except OSError:
                pass

    def get(self, key: str) -> Any | None:
        if kind(check(key)) == LOG:
            raise ValueError(f"{key} is a log: read it with read_log")
        path = self.root / key
        return decode(key, path.read_text(encoding="utf-8")) if path.is_file() else None

    def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        return {key: value for key in keys if (value := self.get(key)) is not None}

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def keys(self, prefix: str = "") -> list[str]:
        base = self.root / check_prefix(prefix) if prefix else self.root
        if base.is_file():
            return [prefix]
        found = []
        for folder, subfolders, files in os.walk(base):
            subfolders[:] = [name for name in subfolders if not name.startswith(".")]
            for name in files:
                key = (Path(folder) / name).relative_to(self.root).as_posix()
                if not reserved(key):
                    found.append(key)
        return sorted(found)

    def append(self, key: str, record: Any) -> None:
        self.append_many(key, [record])

    def append_many(self, key: str, records: Sequence[Any]) -> None:
        lines = [encode_record(check(key), record) for record in records]
        if not lines:
            return
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write("".join(line + "\n" for line in lines))
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)

    def read_log(self, key: str) -> list[Any]:
        path = self.root / check_log(check(key))
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").split("\n")
        records = []
        for at, line in enumerate(lines):
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # a last line without its newline was cut by a crash while it was written: not a record
                if at == len(lines) - 1:
                    break
                raise
        return records

    def delete(self, prefix: str) -> int:
        check_prefix_to_delete(check_prefix(prefix))
        gone = self.keys(prefix)
        for key in gone:
            (self.root / key).unlink()
        base = self.root / prefix
        if base.is_dir():
            for folder, _, _ in os.walk(base, topdown=False):
                try:
                    os.rmdir(folder)
                except OSError:
                    pass
        return len(gone)
