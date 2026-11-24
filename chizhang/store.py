"""Tiny JSON store with atomic writes.

The whole dataset is a few kilobytes, so the simplest correct design is to
keep it in memory and rewrite the file on every change. Writes go to a
temporary file first and are then moved into place, so a crash mid-write
cannot leave a half-written log behind.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1


def default_path() -> Path:
    return Path.home() / ".chizhang" / "store.json"


def empty_data() -> dict:
    return {"version": SCHEMA_VERSION, "profile": {}, "entries": [], "weights": []}


class Store:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._read()

    # -- disk -----------------------------------------------------------
    def _read(self) -> dict:
        if not self.path.exists():
            return empty_data()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Keep the damaged file for inspection instead of silently
            # overwriting a year of food logs with an empty object.
            try:
                shutil.copy2(self.path, self.path.with_suffix(".corrupt.json"))
            except OSError:
                pass
            return empty_data()

        data = empty_data()
        if isinstance(raw, dict):
            data["profile"] = raw.get("profile") or {}
            data["entries"] = [e for e in raw.get("entries", []) if isinstance(e, dict)]
            data["weights"] = [w for w in raw.get("weights", []) if isinstance(w, dict)]
        return data

    def _write(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        handle, temp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    # -- public API -----------------------------------------------------
    def snapshot(self) -> dict:
        """Deep-ish copy safe to hand to readers while writers run."""
        with self._lock:
            return {
                "version": self._data["version"],
                "profile": dict(self._data["profile"]),
                "entries": [dict(e) for e in self._data["entries"]],
                "weights": [dict(w) for w in self._data["weights"]],
            }

    def mutate(self, fn: Callable[[dict], object]):
        """Apply ``fn`` to the live data, persist, return its result."""
        with self._lock:
            result = fn(self._data)
            self._write(self._data)
            return result

    def replace_all(self, data: dict) -> None:
        with self._lock:
            self._data = data
            self._write(self._data)
