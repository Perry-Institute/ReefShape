"""
GUI-side wrapper around `worker/probe_project.py`.

Opens an existing Metashape project in a short-lived headless process and
returns its structure, so the re-photography job editor can offer real chunk
and marker lists instead of asking the user to type labels from memory.

Results are cached on (path, mtime, size). Configuring a batch means opening
the same project repeatedly -- pick the reference chunk, then the damaged
markers, then come back and change your mind -- and a second-and-a-bit of
process spin-up on every one of those interactions would make the editor feel
broken.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

from . import metashape_locate


# Generous: the probe itself is fast, but Metashape's startup competes with
# whatever processing jobs are already running, and a false timeout here would
# look to the user like a corrupt project.
PROBE_TIMEOUT_SECONDS = 180

_WORKER = "probe_project.py"


class ProbeError(Exception):
    """Raised when a project could not be inspected."""


def worker_dir() -> str:
    """Directory holding the headless worker scripts.

    Resolved relative to this file so the app runs from a checkout, a copied
    folder, or wherever the user has put it, with no install step.
    """
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "worker")


def worker_path(name: str) -> str:
    path = os.path.join(worker_dir(), name)
    if not os.path.isfile(path):
        raise ProbeError(
            "ReefShape Batch is missing its worker script: {}\n\n"
            "The installation looks incomplete -- re-copy the ReefShape_Batch "
            "folder.".format(path))
    return path


class ProjectInfo:
    """Parsed result of probing one project."""

    def __init__(self, data: dict):
        self.raw = data
        self.path: str = data.get("path", "")
        self.chunks: List[dict] = data.get("chunks", []) or []
        self.metashape_version: str = data.get("metashape_version", "")

    def chunk_labels(self) -> List[str]:
        return [c.get("label", "") for c in self.chunks]

    def chunk(self, label: str) -> Optional[dict]:
        for c in self.chunks:
            if c.get("label") == label:
                return c
        return None

    def marker_labels(self, chunk_label: str) -> List[str]:
        chunk = self.chunk(chunk_label)
        if not chunk:
            return []
        return [m.get("label", "") for m in chunk.get("markers", [])]

    def suggested_reference_chunk(self) -> Optional[str]:
        """Best guess at which chunk a new timepoint should align to.

        A reference chunk is only useful if it is fully processed and
        georeferenced -- Align Timepoints exports its *estimated* marker
        positions, which requires a solved transform. So prefer complete
        chunks, and among those the latest, since chunk labels are dates in
        the ReefShape convention (YYYYMMDD) and the most recent timepoint is
        the closest match for a new one.
        """
        usable = [c for c in self.chunks
                  if c.get("n_markers", 0) > 0 and c.get("has_orthomosaic")]
        if not usable:
            usable = [c for c in self.chunks if c.get("n_markers", 0) > 0]
        if not usable:
            return None
        return sorted(usable, key=lambda c: str(c.get("label", "")))[-1].get("label")


# path -> (signature, ProjectInfo)
_cache: Dict[str, tuple] = {}


def _signature(path: str):
    """Cheap change detector for the cache.

    mtime alone is not enough: a .psx is a small pointer file whose timestamp
    can change while the referenced data does not, and vice versa. Size is a
    free second signal. Cache misses are cheap anyway -- the cost of being
    wrong here is a stale chunk list, which is worse than a wasted probe.
    """
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def clear_cache(path: Optional[str] = None) -> None:
    if path is None:
        _cache.clear()
    else:
        _cache.pop(os.path.normcase(os.path.abspath(path)), None)


def probe_project(project_path: str, install=None,
                  use_cache: bool = True) -> ProjectInfo:
    """Inspect `project_path` and return its structure.

    Raises ProbeError with a message fit to show the user directly.
    """
    if not project_path or not os.path.isfile(project_path):
        raise ProbeError("Project file does not exist:\n{}".format(project_path))

    key = os.path.normcase(os.path.abspath(project_path))
    signature = _signature(project_path)
    if use_cache and key in _cache:
        cached_signature, info = _cache[key]
        if cached_signature == signature:
            return info

    if install is None:
        try:
            install = metashape_locate.find_install()
        except metashape_locate.MetashapeNotFound as exc:
            raise ProbeError(str(exc))

    script = worker_path(_WORKER)

    # Metashape writes the result to a file rather than stdout because stdout
    # is already full of its own startup banner (version, CPU, RAM, GPU
    # enumeration) and there is no way to suppress it.
    handle, out_path = tempfile.mkstemp(prefix="reefshape_probe_", suffix=".json")
    os.close(handle)

    try:
        completed = subprocess.run(
            [str(install.executable), "-r", script, project_path, out_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            **_no_window(),
        )

        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # No parsable result: the process died before it could write one.
            # Its console output is the only diagnostic we have, and the tail
            # holds the traceback.
            tail = (completed.stdout or "").strip().splitlines()[-15:]
            raise ProbeError(
                "Could not read the project.\n\nMetashape exited with code {}."
                "\n\n{}".format(completed.returncode, "\n".join(tail)))

        if not data.get("ok"):
            raise ProbeError("Could not read the project:\n\n{}".format(
                data.get("error", "unknown error")))

        info = ProjectInfo(data)
        _cache[key] = (signature, info)
        return info

    except subprocess.TimeoutExpired:
        raise ProbeError(
            "Timed out after {} seconds reading:\n{}\n\nThe project may be "
            "very large, on a slow network drive, or locked by another "
            "process.".format(PROBE_TIMEOUT_SECONDS, project_path))
    except OSError as exc:
        raise ProbeError("Could not start Metashape:\n{}".format(exc))
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _no_window() -> dict:
    """Keep Windows from flashing a console window for each worker.

    A batch spawns these constantly; without this, configuring a job pops a
    black console box on screen every time the user picks a project.
    """
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW}
