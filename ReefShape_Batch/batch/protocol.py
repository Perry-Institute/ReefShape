"""
The line protocol spoken by headless workers back to the batch GUI.

A worker's stdout is not ours to control: Metashape prints its own banner,
progress chatter and task logs, and there is no way to silence it. So rather
than trying to parse that, workers emit structured events as single lines with
a distinctive prefix, and the GUI picks those out while treating everything
else as log text.

    @@RS {"ev": "step", "name": "Building mesh"}
    @@RS {"ev": "progress", "pct": 42}

One JSON object per line, flushed immediately. Line-oriented because that is
what survives being interleaved with another process's writes to the same
pipe; a multi-line format would be corrupted by the first Metashape log line
that lands in the middle of it.

Imported by both sides. No Qt, no Metashape -- the worker runs inside
Metashape's interpreter and the GUI runs outside it, and this is one of the
few things they can genuinely share.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

PREFIX = "@@RS "

# Event names.
STEP = "step"          # a named stage started
PROGRESS = "progress"  # 0-100 within the current stage
WARN = "warn"          # non-fatal; collected and shown at the end
INFO = "info"          # log line worth surfacing in the GUI
DONE = "done"          # finished successfully
FAILED = "failed"      # finished unsuccessfully


def emit(event: str, **fields) -> None:
    """Write one protocol line to stdout and flush.

    Flushing matters: without it, Python block-buffers stdout when it is a
    pipe rather than a terminal, and the GUI would receive a job's entire
    progress history in one burst when the process exits -- which is exactly
    when it stops being useful.
    """
    payload = dict(fields)
    payload["ev"] = event
    try:
        sys.stdout.write(PREFIX + json.dumps(payload, default=str) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        # The parent may have gone away, or a field may be unserializable.
        # Neither is worth killing a twelve-hour processing job over.
        pass


def parse(line: str) -> Optional[dict]:
    """Return the event dict if `line` is a protocol line, else None.

    Anything that is not a well-formed protocol line is treated as ordinary
    log output by the caller, so a malformed line degrades to a log entry
    rather than an error.
    """
    if not line.startswith(PREFIX):
        return None
    try:
        payload = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and "ev" in payload else None
