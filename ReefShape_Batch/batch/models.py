"""
Job and settings data model for ReefShape Batch.

These objects are the contract between the GUI and the headless workers. The
GUI builds them, `store.py` writes them to a .rsbatch file, and `run_job.py`
receives one as JSON inside a Metashape process. Nothing here imports Qt or
Metashape -- that is what lets the same definitions be used on both sides of
the process boundary.

Enum-ish values (target type, mesh quality) are carried as **strings**, not as
Metashape constants, for the same reason: the GUI process cannot import
Metashape to name `Metashape.CircularTarget12bit`. The worker maps the string
back to the real constant. `TARGET_TYPES` and `MESH_QUALITIES` below are the
authoritative lists, mirrored from `ui_components.GeoreferenceGroupBox` and
`FullWorkflowDlg.comboMeshQuality` so batch jobs offer exactly the same choices
as the in-Metashape dialogs.

Serialization tolerates both unknown keys (a newer file read by older code)
and missing keys (an older file read by newer code, which falls back to the
field default). Batches get saved and re-run across seasons; a schema addition
must not strand last year's file.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1

# Job kinds.
NEW_PLOT = "new_plot"      # never photographed: create the .psx from scratch
REPHOTO = "rephoto"        # revisit: align to a reference chunk in an existing .psx

# Marker target types, mirroring ui_components.GeoreferenceGroupBox.targetTypes.
# Values are the bare Metashape constant names; the worker resolves them with
# getattr(Metashape, name).
TARGET_TYPES = [
    ("Circular Target 12 Bit", "CircularTarget12bit"),
    ("Circular Target 14 Bit", "CircularTarget14bit"),
    ("Circular Target 16 Bit", "CircularTarget16bit"),
    ("Circular Target 20 Bit", "CircularTarget20bit"),
    ("Circular Target", "CircularTarget"),
    ("Cross Target", "CrossTarget"),
]
DEFAULT_TARGET_TYPE = "CircularTarget12bit"

# Mesh quality, mirroring FullWorkflowDlg.comboMeshQuality. The workflow
# computes depth-map downscale as 2 ** combo_index, so the mapping below is
# that same progression made explicit -- keep them in sync.
MESH_QUALITIES = [
    ("Ultra High", 1),
    ("High", 2),
    ("Medium", 4),
    ("Low", 8),
    ("Lowest", 16),
]
DEFAULT_MESH_QUALITY = "Medium"

# The workflow's default CRS: WGS84 with EGM96 geoid heights.
WGS84_EGM96_WKT = (
    'COMPD_CS["WGS 84 + EGM96 height",GEOGCS["WGS 84",DATUM["World Geodetic System 1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],TOWGS84[0,0,0,0,0,0,0],'
    'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.01745329251994328,AUTHORITY["EPSG","9102"]],AUTHORITY["EPSG","4326"]],'
    'VERT_CS["EGM96 height",VERT_DATUM["EGM96 geoid",2005,AUTHORITY["EPSG","5171"]],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","5773"]]]'
)
LOCAL_CS_WKT = (
    'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]]]'
)
BUILTIN_CRS = [
    ("WGS84 + EGM96", WGS84_EGM96_WKT),
    ("Local Coordinates", LOCAL_CS_WKT),
]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".tif", ".tiff")


def mesh_downscale(quality_name: str) -> int:
    """Depth-map downscale factor for a mesh quality label.

    Falls back to Medium for an unrecognized label rather than raising: a
    typo in a hand-edited .rsbatch should degrade to the default, not abort a
    twelve-hour batch on job seven.
    """
    for name, downscale in MESH_QUALITIES:
        if name == quality_name:
            return downscale
    return dict(MESH_QUALITIES)[DEFAULT_MESH_QUALITY]


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------

def _from_dict(cls, data: Optional[Dict[str, Any]]):
    """Build a dataclass from a dict, ignoring unknown keys.

    Unknown keys are dropped (forward compatibility) and absent keys keep
    their declared default (backward compatibility). See module docstring.
    """
    if not isinstance(data, dict):
        return cls()
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------
# Settings groups
# --------------------------------------------------------------------------

@dataclass
class GeorefSettings:
    """Marker detection, scaling and georeferencing.

    Mirrors the Georeferencing panel. `enabled` is the batch equivalent of the
    panel's "Do you want to input georeferencing information now?" dropdown
    (`autoDetectMarkers`): when False the workflow neither detects markers nor
    imports reference data.

    The column indices are 1-based to match what the user sees in a
    spreadsheet, exactly as the spinboxes in the dialog present them.
    """
    enabled: bool = False
    target_type: str = DEFAULT_TARGET_TYPE
    scalebar_path: str = ""
    georef_path: str = ""

    col_label: int = 1
    col_x: int = 3          # longitude
    col_y: int = 2          # latitude
    col_z: int = 4          # depth
    col_x_accuracy: int = 5
    col_y_accuracy: int = 5
    col_z_accuracy: int = 6
    skip_rows: int = 2

    # Corner markers in cyclic order (NW, NE, SE, SW by default) -- the order
    # decides the boundary polygon's winding, so a non-cyclic list produces a
    # self-intersecting boundary.
    corner_markers: List[int] = field(default_factory=lambda: [1, 2, 3, 4])

    def ref_formatting(self) -> List[int]:
        """The 8-element list the workflow's referenceModel() expects."""
        return [self.col_label, self.col_x, self.col_y, self.col_z,
                self.col_x_accuracy, self.col_y_accuracy, self.col_z_accuracy,
                self.skip_rows]


@dataclass
class ProcessingSettings:
    """Alignment, mesh and raster-resolution settings (the General panel)."""
    crs_wkt: str = WGS84_EGM96_WKT
    crs_label: str = "WGS84 + EGM96"
    generic_preselection: bool = True
    mesh_quality: str = DEFAULT_MESH_QUALITY
    vertex_colors: bool = False
    use_default_resolution: bool = False
    ortho_resolution: float = 0.0005

    def effective_ortho_resolution(self) -> float:
        """0 tells Metashape to choose, matching the dialog's checkbox."""
        return 0.0 if self.use_default_resolution else self.ortho_resolution


@dataclass
class ExportSettings:
    """Which data products to write, and where (the Export panel).

    `output_dir` empty means "alongside the project file", matching the
    dialog's "Defaults to project location".

    `taglab_allow_uncropped` replaces an interactive question. Script 01 stops
    and asks when TagLab outputs are requested but no boundary polygon exists;
    a batch has nobody to ask, so the answer becomes a setting. True keeps the
    batch moving and records a warning; False fails the job so a plot never
    silently produces uncropped TagLab products.
    """
    output_dir: str = ""
    report: bool = True
    gis_outputs: bool = True
    taglab_outputs: bool = True
    taglab_allow_uncropped: bool = True


@dataclass
class ResourceSettings:
    """Per-process hardware limits, applied by the worker before processing.

    gpu_mask 0 means "all GPUs" (the worker leaves Metashape's default alone).
    Setting a mask matters when running jobs in parallel across multiple real
    GPUs; it is not useful for pinning work away from an integrated GPU, which
    Metashape already handles.
    """
    gpu_mask: int = 0
    cpu_enable: bool = True


@dataclass
class Template:
    """A named bundle of settings applied across many jobs.

    Filling in identical processing and export settings for twenty plots by
    hand is the single biggest time sink in setting up a season's batch, so
    templates are the default way to build one: pick a template, then override
    per job only where a plot differs.

    Georeferencing is deliberately part of the template -- a survey normally
    reuses one scalebar file and one column layout across every plot, even
    though the georef CSV itself differs per plot.
    """
    name: str = "Default"
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    georef: GeorefSettings = field(default_factory=GeorefSettings)
    resources: ResourceSettings = field(default_factory=ResourceSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Template":
        data = data or {}
        return cls(
            name=data.get("name", "Default"),
            processing=_from_dict(ProcessingSettings, data.get("processing")),
            export=_from_dict(ExportSettings, data.get("export")),
            georef=_from_dict(GeorefSettings, data.get("georef")),
            resources=_from_dict(ResourceSettings, data.get("resources")),
        )


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

# Job lifecycle. PENDING -> RUNNING -> {DONE, FAILED, CANCELLED}. SKIPPED is
# for jobs the user disabled. Persisted with the batch so a GUI crash mid-run
# doesn't lose track of what already completed -- important when a single job
# can represent twelve hours of processing.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
SKIPPED = "skipped"

TERMINAL_STATUSES = (DONE, FAILED, CANCELLED, SKIPPED)


@dataclass
class Job:
    """One plot to process.

    A `new_plot` job creates the .psx, adds photos, and runs the full workflow.
    A `rephoto` job opens an existing .psx, adds the new timepoint's photos to
    a new chunk, aligns that chunk to a reference chunk, and then runs the same
    full workflow -- the two-step sequence you would otherwise perform by hand
    with the Align Timepoints and Full ReefShape Workflow menu items.
    """
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = NEW_PLOT
    enabled: bool = True

    # Display name for the job table. Empty means "derive from the project".
    label: str = ""

    project_path: str = ""            # .psx to create (new) or open (rephoto)
    photo_folders: List[str] = field(default_factory=list)

    # Empty means "derive from the first photo's EXIF capture date", the same
    # YYYYMMDD convention AddPhotosGroupBox applies in the dialog.
    chunk_name: str = ""

    # -- rephoto only --
    reference_chunk: str = ""                 # chunk label to align against
    damaged_markers: List[str] = field(default_factory=list)

    # Settings, seeded from a template then editable per job.
    template_name: str = "Default"
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    georef: GeorefSettings = field(default_factory=GeorefSettings)
    resources: ResourceSettings = field(default_factory=ResourceSettings)

    # -- runtime state (persisted so a crashed GUI can resume the batch) --
    status: str = PENDING
    progress: float = 0.0
    current_step: str = ""
    message: str = ""
    warnings: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    log_path: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    # -- derived --

    def display_label(self) -> str:
        if self.label:
            return self.label
        if self.project_path:
            return os.path.splitext(os.path.basename(self.project_path))[0]
        return "(unnamed)"

    def project_name(self) -> str:
        """Project name without extension, as used in every export filename."""
        return os.path.splitext(os.path.basename(self.project_path))[0]

    def project_folder(self) -> str:
        return os.path.dirname(self.project_path)

    def effective_output_dir(self) -> str:
        """Where data products go -- the project folder unless overridden."""
        return self.export.output_dir or self.project_folder()

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        import time
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def reset_for_run(self) -> None:
        """Clear the previous attempt's outcome, keeping the settings.

        Progress restarts at zero, but the *work* does not: the underlying
        workflow guards every stage on a "does this already exist?" check and
        saves after each one, so a retried job skips straight past whatever
        completed before it died.
        """
        self.status = PENDING
        self.progress = 0.0
        self.current_step = ""
        self.message = ""
        self.warnings = []
        self.outputs = []
        self.started_at = 0.0
        self.finished_at = 0.0

    def apply_template(self, template: Template, keep_paths: bool = True) -> None:
        """Overwrite this job's settings from `template`.

        With `keep_paths` (the default) the per-plot georef CSV is preserved,
        since that file is necessarily different for every plot while
        everything around it -- scalebar file, column layout, corner order --
        is shared. Without it the template wins outright.
        """
        import copy
        georef_path = self.georef.georef_path
        self.processing = copy.deepcopy(template.processing)
        self.export = copy.deepcopy(template.export)
        self.georef = copy.deepcopy(template.georef)
        self.resources = copy.deepcopy(template.resources)
        self.template_name = template.name
        if keep_paths and georef_path:
            self.georef.georef_path = georef_path

    # -- serialization --

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Job":
        data = data or {}
        known = {f.name for f in fields(cls)}
        nested = {"processing", "export", "georef", "resources"}
        plain = {k: v for k, v in data.items()
                 if k in known and k not in nested}
        job = cls(**plain)
        job.processing = _from_dict(ProcessingSettings, data.get("processing"))
        job.export = _from_dict(ExportSettings, data.get("export"))
        job.georef = _from_dict(GeorefSettings, data.get("georef"))
        job.resources = _from_dict(ResourceSettings, data.get("resources"))
        return job


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

ERROR = "error"      # will not run
WARNING = "warning"  # will run, but the user should know


@dataclass
class Issue:
    severity: str
    job_id: str
    message: str


def count_images(folder: str) -> int:
    """Number of files in `folder` the workflow would treat as photos.

    Matches AddPhotosGroupBox.getPhotoFolder: top level only, no recursion.
    """
    try:
        return sum(1 for name in os.listdir(folder)
                   if name.lower().endswith(IMAGE_EXTENSIONS))
    except OSError:
        return 0


def validate_job(job: Job) -> List[Issue]:
    """Check a job for problems, cheaply, before anything is launched.

    Everything here is a filesystem stat or a field check -- deliberately no
    Metashape involvement, so the whole queue can be validated as the user
    types. The payoff is catching a typo'd path now instead of eleven hours
    into an overnight run.
    """
    issues: List[Issue] = []

    def err(msg):
        issues.append(Issue(ERROR, job.job_id, msg))

    def warn(msg):
        issues.append(Issue(WARNING, job.job_id, msg))

    # -- project path --
    if not job.project_path:
        err("No project file set.")
    elif not job.project_path.lower().endswith(".psx"):
        err("Project file must be a .psx file.")
    elif job.kind == REPHOTO:
        if not os.path.isfile(job.project_path):
            err("Project file does not exist: {}".format(job.project_path))
    else:
        parent = job.project_folder()
        if parent and not os.path.isdir(parent):
            err("Folder for the new project does not exist: {}".format(parent))
        elif os.path.exists(job.project_path):
            # Not fatal: the workflow is resumable by design, so pointing at
            # an existing project is how you finish an interrupted run.
            warn("Project already exists; it will be opened and continued "
                 "rather than recreated.")

    # -- photos --
    if not job.photo_folders:
        err("No photo folder selected.")
    else:
        total = 0
        for folder in job.photo_folders:
            if not os.path.isdir(folder):
                err("Photo folder does not exist: {}".format(folder))
                continue
            n = count_images(folder)
            if n == 0:
                warn("No images found in {}".format(folder))
            total += n
        if total and total < 20:
            warn("Only {} images found -- alignment is unlikely to "
                 "succeed with so few photos.".format(total))

    # -- rephoto specifics --
    if job.kind == REPHOTO and not job.reference_chunk:
        err("No reference chunk selected for re-photography.")

    # -- georeferencing --
    if job.georef.enabled:
        if not job.georef.scalebar_path:
            err("Georeferencing is enabled but no scalebar file was selected.")
        elif not os.path.isfile(job.georef.scalebar_path):
            err("Scalebar file does not exist: {}".format(job.georef.scalebar_path))
        if not job.georef.georef_path:
            err("Georeferencing is enabled but no georeferencing file was selected.")
        elif not os.path.isfile(job.georef.georef_path):
            err("Georeferencing file does not exist: {}".format(job.georef.georef_path))
        if len(set(job.georef.corner_markers)) != 4:
            err("Corner markers must be four distinct target numbers.")
    elif job.kind == NEW_PLOT:
        # The workflow deliberately stops after mesh building so the user can
        # reference, level and scale by hand. That is a sensible interactive
        # behaviour and a trap in a batch, so say so plainly up front.
        warn("No georeferencing information: this job will stop after building "
             "the mesh so you can reference the model manually, and will not "
             "produce an orthomosaic, DEM or any exports.")

    # -- exports --
    if job.export.output_dir and not os.path.isdir(job.export.output_dir):
        err("Output folder does not exist: {}".format(job.export.output_dir))
    if not (job.export.report or job.export.gis_outputs or job.export.taglab_outputs):
        warn("No outputs selected: the project will be processed but nothing "
             "will be exported.")
    if (job.export.taglab_outputs and not job.export.taglab_allow_uncropped
            and not job.georef.enabled and job.kind == NEW_PLOT):
        warn("TagLab outputs require a boundary polygon, which needs "
             "georeferencing. This job will fail at the export step.")

    if job.processing.use_default_resolution is False and \
            job.processing.ortho_resolution <= 0:
        err("Custom orthomosaic resolution must be greater than zero.")

    return issues


def validate_batch(jobs: List[Job]) -> List[Issue]:
    """Validate every enabled job, plus batch-level conflicts.

    The cross-job check matters more than it looks: two jobs writing the same
    .psx concurrently corrupts the project, and two jobs writing the same
    chunk name into one project silently produce a duplicate.
    """
    issues: List[Issue] = []
    seen_targets: Dict[str, str] = {}

    for job in jobs:
        if not job.enabled:
            continue
        issues.extend(validate_job(job))

        if job.project_path:
            key = os.path.normcase(os.path.abspath(job.project_path))
            if job.chunk_name:
                key += "::" + job.chunk_name
            if key in seen_targets:
                issues.append(Issue(
                    ERROR, job.job_id,
                    "Another job in this batch targets the same project and "
                    "chunk ({}). Running both would corrupt the project."
                    .format(job.display_label())))
            else:
                seen_targets[key] = job.job_id

    return issues


def jobs_sharing_projects(jobs: List[Job]) -> Dict[str, List[str]]:
    """Map each project path to the ids of enabled jobs targeting it.

    The scheduler uses this to serialize jobs that touch the same .psx even
    when parallelism is set higher: Metashape locks a project while it has it
    open, so two workers on one file is a failure, not a race worth taking.
    """
    out: Dict[str, List[str]] = {}
    for job in jobs:
        if not job.enabled or not job.project_path:
            continue
        key = os.path.normcase(os.path.abspath(job.project_path))
        out.setdefault(key, []).append(job.job_id)
    return out
