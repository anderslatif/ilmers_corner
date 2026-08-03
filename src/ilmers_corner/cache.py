"""On-disk response cache.

Note that an unauthenticated conditional request still costs rate-limit quota
even when it returns 304, so ETags alone do not protect the 60/hour budget.
Entries are therefore served straight from disk while they are fresh, and only
revalidated over the network once past that window.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

CACHE_VERSION = 1
# How long an entry may be reused without contacting GitHub at all.
FRESH_SECONDS = 15 * 60
# Beyond that an entry is still kept, but only as an ETag for revalidation.
MAX_AGE_SECONDS = 60 * 60 * 24 * 14


def default_cache_directory() -> Path:
    override = os.environ.get("ILMERS_CORNER_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "ilmers_corner"


@dataclass
class CachedResponse:
    etag: str
    payload: Any
    is_fresh: bool = False


class ResponseCache:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = directory or default_cache_directory()
        self._usable = True

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    def get(self, url: str) -> Optional[CachedResponse]:
        if not self._usable:
            return None
        path = self._path_for(url)
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        if record.get("version") != CACHE_VERSION:
            return None

        age = time.time() - record.get("stored_at", 0)
        if age > MAX_AGE_SECONDS:
            return None
        # Content addressed by commit SHA can never change, so it stays fresh
        # for as long as we keep it.
        fresh = record.get("immutable", False) or age < FRESH_SECONDS
        return CachedResponse(
            etag=record.get("etag", ""),
            payload=record.get("payload"),
            is_fresh=fresh,
        )

    def set(self, url: str, etag: str, payload: Any, *, immutable: bool = False) -> None:
        if not self._usable:
            return
        record = {
            "version": CACHE_VERSION,
            "etag": etag,
            "payload": payload,
            "stored_at": time.time(),
            "immutable": immutable,
        }
        path = self._path_for(url)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Write via a temporary file so an interrupted run cannot leave a
            # half-written entry that later parses as valid JSON.
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(record, handle)
            temporary.replace(path)
        except OSError:
            # A read-only or full disk should degrade to "no caching", never
            # break the command.
            self._usable = False

    def clear(self) -> int:
        removed = 0
        if not self.directory.exists():
            return removed
        for entry in self.directory.glob("*.json"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
        return removed
