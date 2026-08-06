"""
Persistence for ReefShape Batch.

Three things get saved, to three different places for three different reasons:

  .rsbatch file  -- a queue of jobs, chosen and named by the user. Portable:
                    prepare a season's batch on a laptop, run it on the
                    workstation, keep it as a record of what was processed.

  templates      -- named settings bundles, per user, in the config directory.
                    They outlive any single batch.

  app settings   -- window geometry, parallelism, last-used folders. Also per
                    user, and stored alongside the templates rather than in
                    QSettings so that `store` stays importable by code that has
                    no Qt (the workers, and any future CLI).

Everything is plain JSON. A .rsbatch is meant to be readable and hand-editable
in a pinch -- if a user needs to repoint twenty jobs at a moved drive,
find-and-replace in a text editor should just work.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
from typing import Any, Dict, List, Optional

from . import models
from .metashape_locate import config_dir, load_config, save_config
from .models import Job, Template


BATCH_EXTENSION = ".rsbatch"
BATCH_FILE_FILTER = "ReefShape Batch (*.rsbatch);;All files (*)"


class BatchFileError(Exception):
    """Raised when a .rsbatch file can't be read or isn't one."""


# --------------------------------------------------------------------------
# Batch documents
# --------------------------------------------------------------------------

class Batch:
    """An ordered queue of jobs plus the run settings that go with it.

    `parallelism` and `stop_on_error` live on the batch rather than in app
    settings because they are properties of the work: a batch of small shallow
    plots may happily run three at a time where one of deep large plots must
    run alone.
    """

    def __init__(self):
        self.jobs: List[Job] = []
        self.parallelism: int = 1
        self.stop_on_error: bool = False
        self.path: Optional[str] = None
        self.created: str = datetime.datetime.now().isoformat(timespec="seconds")
        self._dirty: bool = False

    # -- change tracking (drives the "save before closing?" prompt) --

    def mark_dirty(self) -> None:
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    # -- queue editing --

    def add(self, job: Job) -> Job:
        self.jobs.append(job)
        self.mark_dirty()
        return job

    def remove(self, job_id: str) -> None:
        self.jobs = [j for j in self.jobs if j.job_id != job_id]
        self.mark_dirty()

    def find(self, job_id: str) -> Optional[Job]:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None

    def duplicate(self, job_id: str) -> Optional[Job]:
        """Copy a job, giving the copy a fresh id and a clean run state.

        The main way to build a batch by hand: set one plot up exactly right,
        then duplicate and repoint. The copy must never inherit the original's
        status, or a duplicate of a finished job would look already-done.
        """
        source = self.find(job_id)
        if source is None:
            return None
        clone = Job.from_dict(source.to_dict())
        clone.job_id = models.uuid.uuid4().hex[:12]
        clone.reset_for_run()
        if clone.label:
            clone.label += " (copy)"
        index = self.jobs.index(source) + 1
        self.jobs.insert(index, clone)
        self.mark_dirty()
        return clone

    def move(self, job_id: str, delta: int) -> None:
        """Shift a job up or down the queue. Order is run order."""
        job = self.find(job_id)
        if job is None:
            return
        i = self.jobs.index(job)
        j = max(0, min(len(self.jobs) - 1, i + delta))
        if i == j:
            return
        self.jobs.insert(j, self.jobs.pop(i))
        self.mark_dirty()

    # -- queue queries --

    def pending(self) -> List[Job]:
        return [j for j in self.jobs
                if j.enabled and j.status == models.PENDING]

    def apply_template_to_all(self, template: Template,
                              job_ids: Optional[List[str]] = None) -> int:
        """Push a template onto every job, or a chosen subset. Returns the count."""
        targets = (self.jobs if job_ids is None
                   else [j for j in self.jobs if j.job_id in set(job_ids)])
        for job in targets:
            job.apply_template(template)
        if targets:
            self.mark_dirty()
        return len(targets)

    # -- serialization --

    def to_dict(self) -> dict:
        return {
            "schema_version": models.SCHEMA_VERSION,
            "kind": "reefshape_batch",
            "created": self.created,
            "saved": datetime.datetime.now().isoformat(timespec="seconds"),
            "parallelism": self.parallelism,
            "stop_on_error": self.stop_on_error,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, data: dict, path: Optional[str] = None) -> "Batch":
        batch = cls()
        batch.path = path
        batch.created = data.get("created", batch.created)
        # Clamp rather than trust: a hand-edited file with parallelism 64 would
        # thrash the machine into uselessness.
        batch.parallelism = max(1, min(8, int(data.get("parallelism", 1) or 1)))
        batch.stop_on_error = bool(data.get("stop_on_error", False))
        batch.jobs = [Job.from_dict(d) for d in data.get("jobs", [])]
        batch._dirty = False
        return batch

    def save(self, path: Optional[str] = None) -> str:
        target = path or self.path
        if not target:
            raise BatchFileError("No path given to save the batch to.")
        if not target.lower().endswith(BATCH_EXTENSION):
            target += BATCH_EXTENSION
        _atomic_write_json(target, self.to_dict())
        self.path = target
        self._dirty = False
        return target

    @classmethod
    def load(cls, path: str) -> "Batch":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            raise BatchFileError("Could not read {}: {}".format(path, exc))
        except ValueError as exc:
            raise BatchFileError(
                "{} is not a valid batch file: {}".format(path, exc))

        if not isinstance(data, dict) or "jobs" not in data:
            raise BatchFileError(
                "{} does not look like a ReefShape batch file.".format(path))

        version = data.get("schema_version", 0)
        if version > models.SCHEMA_VERSION:
            # Load anyway -- unknown fields are dropped by Job.from_dict, so
            # the worst case is losing settings this version doesn't know
            # about. Refusing outright would be less useful than a warning,
            # but the caller needs to be able to tell the user.
            raise NewerSchemaWarning(data, path, version)

        return cls.from_dict(data, path)


class NewerSchemaWarning(Exception):
    """A batch file written by a newer ReefShape Batch than this one.

    Carries the parsed data so the caller can offer "open anyway" after
    warning that unrecognized settings will be dropped on save.
    """

    def __init__(self, data: dict, path: str, version: int):
        super().__init__(
            "{} was written by a newer version of ReefShape Batch "
            "(file format {}, this version understands {}). Opening it may "
            "discard settings this version does not recognize."
            .format(os.path.basename(path), version, models.SCHEMA_VERSION))
        self.data = data
        self.path = path
        self.version = version

    def load_anyway(self) -> Batch:
        return Batch.from_dict(self.data, self.path)


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON via a temp file and replace, so a crash can't truncate.

    A .rsbatch can represent a lot of setup work, and the moment we are most
    likely to be writing it is also the moment the user is most likely to be
    force-quitting a stuck run.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def _templates_path() -> str:
    return str(config_dir() / "templates.json")


def load_templates() -> List[Template]:
    """All saved templates, always including a built-in Default.

    The Default is synthesized rather than written to disk on first run, so a
    user who deletes templates.json gets a working app rather than an empty
    template list.
    """
    templates: List[Template] = []
    try:
        with open(_templates_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("templates", []):
            templates.append(Template.from_dict(entry))
    except (OSError, ValueError, AttributeError):
        pass

    if not any(t.name == "Default" for t in templates):
        templates.insert(0, Template(name="Default"))
    return templates


def save_templates(templates: List[Template]) -> None:
    _atomic_write_json(_templates_path(), {
        "schema_version": models.SCHEMA_VERSION,
        "templates": [t.to_dict() for t in templates],
    })


def upsert_template(template: Template) -> List[Template]:
    """Add or replace a template by name, returning the full list."""
    templates = load_templates()
    for i, existing in enumerate(templates):
        if existing.name == template.name:
            templates[i] = template
            break
    else:
        templates.append(template)
    save_templates(templates)
    return templates


def delete_template(name: str) -> List[Template]:
    """Remove a template. Default is protected -- it is the fallback."""
    if name == "Default":
        return load_templates()
    templates = [t for t in load_templates() if t.name != name]
    save_templates(templates)
    return templates


def get_template(name: str) -> Template:
    for template in load_templates():
        if template.name == name:
            return template
    return Template(name="Default")


def template_from_job(job: Job, name: str) -> Template:
    """Capture a job's settings as a reusable template.

    "Set one plot up the way you want it, then make that the template" is a
    much more natural way to build one than filling in an empty settings form.
    """
    return Template(
        name=name,
        processing=copy.deepcopy(job.processing),
        export=copy.deepcopy(job.export),
        georef=copy.deepcopy(job.georef),
        resources=copy.deepcopy(job.resources),
    )


# --------------------------------------------------------------------------
# App settings
# --------------------------------------------------------------------------

# Kept in the same JSON config as the Metashape path override. Deliberately
# not QSettings: `store` must stay importable without Qt.

def get_setting(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set_setting(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)


def recent_batches() -> List[str]:
    """Recently opened .rsbatch paths, newest first, pruned of deleted files."""
    paths = get_setting("recent_batches", []) or []
    return [p for p in paths if isinstance(p, str) and os.path.isfile(p)]


def push_recent_batch(path: str, limit: int = 10) -> None:
    path = os.path.abspath(path)
    paths = [p for p in recent_batches()
             if os.path.normcase(p) != os.path.normcase(path)]
    paths.insert(0, path)
    set_setting("recent_batches", paths[:limit])
