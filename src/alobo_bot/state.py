"""Small JSON files under the data directory, read back on restart.

The service keeps a little durable state — the request/search counters behind
``/status`` and the recent-search result cache — so a restart (a redeploy, a
container bounce) resumes rather than starting from zero. This module is the one
place that touches it, and it is deliberately forgiving: a missing, unreadable or
corrupt file is treated as empty and the process starts fresh, and every write is
atomic (a temp file renamed into place) so a crash mid-write cannot leave a
half-written file behind. Durable state is a convenience, never a liability — a
read-only mount or a full disk must not take the service down.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any


def read_json(path: pathlib.Path) -> Any:
    """The parsed file, or None when it is missing, unreadable or not JSON."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json(path: pathlib.Path, data: Any) -> bool:
    """Atomically write *data* as JSON to *path*; False when the write failed."""
    temp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
        return True
    except OSError:
        if temp_name is not None:
            pathlib.Path(temp_name).unlink(missing_ok=True)
        return False
