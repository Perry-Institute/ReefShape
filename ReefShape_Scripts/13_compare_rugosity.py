"""
Compare Rugosity
Will Greene, Perry Institute for Marine Science

Fractional-change rugosity raster between two chunks (typically two
timepoints of the same plot). Runs in the "newer" chunk and takes a
"reference" (older) chunk from a dropdown. For each cell:

    delta = (new_rugosity - ref_rugosity) / ref_rugosity

so a cell value of 0.6 means the cell's rugosity increased by 60% from
the reference to the current state, and -0.25 means it decreased by
25%. Result is imported into the active chunk as a labeled DEM
alongside both source rugosity rasters, with optional GeoTIFF + stats
sidecar written to disk.

The reference chunk's rugosity DEM is also copied into the active chunk
as its own labeled DEM so you can view both timepoints and the delta
side by side without re-opening the reference project.

Grid alignment: this script requires that the two rugosity rasters
share the same CRS, dimensions, and geotransform bit-for-bit. That
happens naturally when you run script 11 or 12 on both chunks with the
same cell size and the same boundary polygon (e.g., after using
script 07 to copy the boundary across chunks). If the grids don't
match, the script errors out and points you at the fix instead of
silently reprojecting.

Requires: numpy, rasterio (both auto-installed on first run).
"""

import os
import tempfile

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets

from modules.pip_auto_install import pip_install

pip_install("""numpy==1.26.4
rasterio>=1.4,<2
""")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402


NODATA = np.float32(-9999.0)
RASTER_LABEL_PREFIX_REF = "Rugosity Ref"
RASTER_LABEL_PREFIX_DELTA = "Rugosity Δ (fraction)"


# ---------------------------------------------------------------------------
# Chunk / raster helpers
# ---------------------------------------------------------------------------

def _iter_elevations(chunk):
    """Return [(label_for_dropdown, elevation)] for the chunk's DEMs, in
    the order Metashape reports them. Falls back to a generated label
    when the elevation has no user-set label."""
    out = []
    if not chunk.elevations:
        return out
    for i, elev in enumerate(chunk.elevations):
        label = elev.label or "Elevation {}".format(i + 1)
        out.append((label, elev))
    return out


def _describe_elevation(elev):
    """Return a short summary string for an Elevation. Metashape's
    Elevation exposes width/height on most versions; wrap in getattr
    since older API surfaces have varied."""
    w = getattr(elev, "width", None)
    h = getattr(elev, "height", None)
    res = getattr(elev, "resolution", None)
    parts = []
    if w is not None and h is not None:
        parts.append("{}x{}".format(w, h))
    if res is not None:
        parts.append("res={}".format(res))
    return ", ".join(parts) if parts else "(no size info)"


def _elevation_grid(elev):
    """Return (rasterio_transform, width, height, crs_wkt) for the
    elevation's native grid. The transform maps pixel coords (col, row)
    into the elevation's own projection (usually the chunk's CRS)."""
    left = float(elev.left)
    right = float(elev.right)
    top = float(elev.top)
    bottom = float(elev.bottom)
    width = int(elev.width)
    height = int(elev.height)
    dx = (right - left) / width
    dy = (top - bottom) / height  # positive; from_origin expects positive
    transform = rasterio.transform.from_origin(left, top, dx, dy)
    crs_wkt = elev.crs.wkt if elev.crs else ""
    return transform, width, height, crs_wkt


def _sample_elevation_to_array(elev, transform, width, height, progress=None,
                               progress_prefix="Sampling"):
    """Sample `elev` at the center of every cell of the (width, height)
    grid defined by `transform`. Returns a float32 array of shape
    (height, width) with NODATA sentinels where altitude() returns
    nan/inf/None (nodata cells in the source).

    We use elev.altitude(Vector([x, y])) directly instead of exporting
    and re-reading, because chunk.exportRaster is not grid-preserving —
    it snaps to a slightly different output grid than the source raster,
    losing edge cells and shifting the origin. altitude() reads the
    stored per-cell value directly, and when queried at a cell's exact
    center returns that cell's value with no interpolation ambiguity.
    """
    arr = np.full((height, width), float(NODATA), dtype=np.float32)
    total = width * height
    every = max(1, total // 20)
    n_done = 0
    n_valid = 0
    for r in range(height):
        for c in range(width):
            # Cell center: (col + 0.5, row + 0.5) in pixel coords.
            x, y = transform * (c + 0.5, r + 0.5)
            try:
                z = elev.altitude(Metashape.Vector([x, y]))
            except Exception:
                z = None
            if z is not None and np.isfinite(z):
                arr[r, c] = float(z)
                n_valid += 1
            n_done += 1
            if progress is not None and n_done % every == 0:
                progress.set_status("{} ({} / {} cells, {} with data)".format(
                    progress_prefix, n_done, total, n_valid))
    return arr


def _verify_source_grids_match(new_elev, ref_elev):
    """Raise ValueError with a specific message if the two Elevation
    objects don't share bit-for-bit identical native grids."""
    if str(new_elev.crs) != str(ref_elev.crs):
        raise ValueError(
            "Grid mismatch: CRSes differ.\n"
            "  new: {}\n  ref: {}\n"
            "Re-run scripts 11 or 12 with matching CRS settings.".format(
                new_elev.crs, ref_elev.crs))
    if (int(new_elev.width), int(new_elev.height)) != (
            int(ref_elev.width), int(ref_elev.height)):
        raise ValueError(
            "Grid mismatch: dimensions differ.\n"
            "  new: {}×{} cols×rows\n  ref: {}×{} cols×rows\n"
            "Both rugosity rasters must come from the same boundary "
            "polygon and cell size. Use script 07 to copy the boundary "
            "between chunks before running 11/12.".format(
                new_elev.width, new_elev.height,
                ref_elev.width, ref_elev.height))
    # Bbox: allow a small absolute tolerance to absorb float noise from
    # any internal transforms Metashape may have applied. For a 1 m
    # cell in WGS84 (~9e-6 degrees), 1e-10 degrees is ~1 μm on the
    # ground — well under any conceivable numerical drift for identical
    # inputs, but well over float64 round-trip noise.
    for attr in ("left", "right", "top", "bottom"):
        n = float(getattr(new_elev, attr))
        r = float(getattr(ref_elev, attr))
        if abs(n - r) > 1e-10:
            raise ValueError(
                "Grid mismatch: {} differs.\n"
                "  new: {}\n  ref: {}\n"
                "Rugosity rasters were computed with different boundary "
                "polygons. Use script 07 to copy the boundary between "
                "chunks, then re-run 11/12.".format(attr, n, r))


def _import_raster_to_chunk(chunk, path, label):
    """Import `path` as an Elevation product in `chunk` and rename it to
    `label`. Preserves the previously-active elevation so the newly
    imported raster coexists with (rather than replaces) the chunk's
    real DEM.

    Deliberately does NOT pass crs= to importRaster. Passing crs=chunk.crs
    triggers Metashape to reproject the file into the chunk's CRS even
    when the file's embedded CRS is functionally identical — that
    reprojection can shift the grid origin and drop columns on the
    edges. Letting importRaster use the file's own embedded CRS keeps
    the grid bit-for-bit if the file was already in the chunk's CRS
    (which our exports are)."""
    before_keys = set()
    if chunk.elevations:
        before_keys = {e.key for e in chunk.elevations}
    prior_active = chunk.elevation

    chunk.importRaster(
        path=path,
        raster_type=Metashape.DataSource.ElevationData,
    )

    new_elev = None
    if chunk.elevations:
        for e in chunk.elevations:
            if e.key not in before_keys:
                new_elev = e
                break
    if new_elev is not None:
        new_elev.label = label
        print("      → imported as elevation {} ({})".format(
            label, _describe_elevation(new_elev)))
    else:
        print("      → WARNING: no new elevation entry found post-import")

    if prior_active is not None and chunk.elevation is not prior_active:
        chunk.elevation = prior_active

    return new_elev


def write_geotiff(path, data, transform, crs_wkt):
    """Write a single-band float32 GeoTIFF with our nodata sentinel."""
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs_wkt,
        "transform": transform,
        "nodata": float(NODATA),
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_rugosity_delta(active_chunk, ref_chunk, new_elev, ref_elev,
                           progress=None):
    """Verify the two source rugosity elevations share an identical
    native grid, sample both directly (via elev.altitude() per cell
    center — no exportRaster round-trip), and compute the fractional-
    change raster.

    Returns (delta_temp_path, ref_temp_path, cell_size_m, stats). Both
    files are written using the active-chunk elevation's own native
    grid, so importing them back preserves alignment with the source.
    """
    def _step(text):
        if progress is not None:
            progress.set_status(text)

    tmp_dir = tempfile.mkdtemp(prefix="reefshape_rugosity_compare_")
    new_path = os.path.join(tmp_dir, "new_rugosity.tif")  # for debug only
    ref_path = os.path.join(tmp_dir, "ref_rugosity.tif")
    delta_path = os.path.join(tmp_dir, "rugosity_delta.tif")

    print("  source elevations:")
    print("    new  ({}): {}".format(
        new_elev.label, _describe_elevation(new_elev)))
    print("    ref  ({}): {}".format(
        ref_elev.label, _describe_elevation(ref_elev)))

    _step("Verifying grid alignment…")
    _verify_source_grids_match(new_elev, ref_elev)

    # Use the active-chunk elevation's own grid definition, verbatim.
    # verify_source_grids_match has guaranteed the ref matches, so
    # sampling both at these coords gives cell-for-cell correspondence.
    transform, width, height, crs_wkt = _elevation_grid(new_elev)
    cell_size_m = float(new_elev.resolution) if getattr(
        new_elev, "resolution", None) else abs(float(transform.a))

    print("  sampling grid: {}x{} @ transform={}".format(
        width, height, transform))
    print("  cell size (ground meters, from elev.resolution): {:.4f}".format(
        cell_size_m))

    _step("Sampling active-chunk rugosity ({} cells)…".format(width * height))
    new_arr = _sample_elevation_to_array(
        new_elev, transform, width, height, progress=progress,
        progress_prefix="Sampling active chunk")

    _step("Sampling reference-chunk rugosity ({} cells)…".format(
        width * height))
    ref_arr = _sample_elevation_to_array(
        ref_elev, transform, width, height, progress=progress,
        progress_prefix="Sampling reference chunk")

    _step("Computing fractional change…")
    # Valid = both cells finite, both non-NODATA sentinel, ref > 0
    # (rugosity is ≥ 1 by definition; ≤ 0 means nodata that slipped
    # past our sentinel).
    valid = np.ones_like(new_arr, dtype=bool)
    valid &= new_arr != NODATA
    valid &= ref_arr != NODATA
    valid &= np.isfinite(new_arr) & np.isfinite(ref_arr)
    valid &= ref_arr > 0

    delta = np.full(new_arr.shape, float(NODATA), dtype=np.float32)
    delta[valid] = ((new_arr[valid].astype(np.float64)
                     - ref_arr[valid].astype(np.float64))
                    / ref_arr[valid].astype(np.float64)).astype(np.float32)

    n_valid = int(valid.sum())
    if n_valid > 0:
        vals = delta[valid]
        stats = {
            "n_cells": n_valid,
            "mean": float(np.mean(vals)),
            "median": float(np.median(vals)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "n_increased": int(np.sum(vals > 0)),
            "n_decreased": int(np.sum(vals < 0)),
            "n_unchanged": int(np.sum(vals == 0)),
        }
    else:
        stats = {"n_cells": 0, "mean": 0, "median": 0, "min": 0, "max": 0,
                 "n_increased": 0, "n_decreased": 0, "n_unchanged": 0}

    _step("Writing GeoTIFFs…")
    # Ref array gets written as a stand-alone GeoTIFF so it can be
    # imported into the active chunk and viewed alongside the new
    # rugosity + delta. Same grid → aligns perfectly.
    write_geotiff(ref_path, ref_arr, transform, crs_wkt)
    write_geotiff(delta_path, delta, transform, crs_wkt)
    # Also write the new-arr sample for debugging — unused by the
    # dialog, but useful when diffing against exportRaster's output.
    write_geotiff(new_path, new_arr, transform, crs_wkt)

    return delta_path, ref_path, cell_size_m, stats


# ---------------------------------------------------------------------------
# Text output helpers
# ---------------------------------------------------------------------------

def _format_stats_text(new_chunk_label, ref_chunk_label, cell_size_m, stats,
                       disk_path=None):
    lines = [
        "Rugosity change: {} vs {} (reference)".format(
            new_chunk_label, ref_chunk_label),
        "",
        "Cell size: {:.2f} m".format(cell_size_m),
        "Cells with data: {}".format(stats["n_cells"]),
        "",
        "Delta = (new − ref) / ref, stored as a fraction.",
        "  0.6  → cell increased by 60%",
        "  0.0  → no change",
        " -0.25 → cell decreased by 25%",
        "",
        "Mean:    {:+.3f} ({:+.1f}%)".format(
            stats["mean"], 100 * stats["mean"]),
        "Median:  {:+.3f} ({:+.1f}%)".format(
            stats["median"], 100 * stats["median"]),
        "Min:     {:+.3f} ({:+.1f}%)".format(
            stats["min"], 100 * stats["min"]),
        "Max:     {:+.3f} ({:+.1f}%)".format(
            stats["max"], 100 * stats["max"]),
        "",
        "Cells increased:  {}".format(stats["n_increased"]),
        "Cells decreased:  {}".format(stats["n_decreased"]),
        "Cells unchanged:  {}".format(stats["n_unchanged"]),
    ]
    if disk_path:
        lines.append("")
        lines.append("Saved to: {}".format(disk_path))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Progress dialog
# ---------------------------------------------------------------------------

class _ProgressDialog(QtWidgets.QDialog):
    """Small indeterminate-progress dialog. Compare is fast (I/O bound,
    a few seconds even on large plots) so we don't need per-cell
    counters or an ETA — just a status label and a busy indicator."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setFixedSize(460, 140)
        flags = self.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint
        flags &= ~QtCore.Qt.WindowSystemMenuHint
        self.setWindowFlags(flags)

        self._heading = QtWidgets.QLabel("Comparing rugosity rasters…")
        font = self._heading.font()
        font.setBold(True)
        self._heading.setFont(font)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: palette(mid);")

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.addWidget(self._heading)
        layout.addWidget(self._status)
        layout.addStretch(1)
        layout.addWidget(self._bar)

    def set_status(self, text):
        self._status.setText(text)
        QtWidgets.QApplication.processEvents()


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class CompareRugosityDlg(QtWidgets.QDialog):
    """Reference chunk + DEM pickers, active chunk's DEM picker,
    save-to-disk options, and a run button.

    QSettings-persisted:
      - last-picked reference chunk label
      - last-picked reference DEM label
      - last-picked new DEM label
      - save_to_disk, save_stats, output_dir
    """

    SETTINGS_GROUP = "ReefShape/CompareRugosity"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Rugosity")
        self.setMinimumWidth(520)

        self.doc = Metashape.app.document
        self.active_chunk = self.doc.chunk
        if self.active_chunk is None:
            raise RuntimeError("No active chunk. Open the newer timepoint's "
                               "chunk before running this script.")

        self.settings = QtCore.QSettings("PerryInstitute", "ReefShape")
        self.settings.beginGroup(self.SETTINGS_GROUP)
        self.output_dir = self.settings.value("output_dir", "", type=str)

        intro = QtWidgets.QLabel(
            "Compares rugosity between the active chunk and a reference "
            "chunk. Result is a fractional-change raster imported into "
            "the active chunk: cell value 0.6 = +60% rugosity, "
            "-0.25 = -25% rugosity.")
        intro.setWordWrap(True)

        # --- Reference chunk + DEM row ---
        self.cbxRefChunk = QtWidgets.QComboBox()
        for c in self.doc.chunks:
            if c is self.active_chunk:
                continue
            self.cbxRefChunk.addItem(c.label or "chunk", c)
        if self.cbxRefChunk.count() == 0:
            raise RuntimeError(
                "This project has only one chunk — no reference to "
                "compare against. Add or align a second chunk first.")
        # Restore last-picked chunk if it's still here.
        last_ref_chunk = self.settings.value("ref_chunk_label", "", type=str)
        if last_ref_chunk:
            idx = self.cbxRefChunk.findText(last_ref_chunk)
            if idx >= 0:
                self.cbxRefChunk.setCurrentIndex(idx)
        self.cbxRefChunk.currentIndexChanged.connect(self._onRefChunkChanged)

        self.cbxRefElev = QtWidgets.QComboBox()
        self._populateRefElev()

        # --- Active chunk DEM row ---
        self.cbxNewElev = QtWidgets.QComboBox()
        self._populateNewElev()

        # --- Save-to-disk row ---
        self.checkSaveDisk = QtWidgets.QCheckBox(
            "Also save delta raster to disk (GeoTIFF)")
        self.checkSaveDisk.setChecked(
            self.settings.value("save_to_disk", False, type=bool))
        self.checkSaveDisk.toggled.connect(self._onSaveDiskToggled)

        self.checkSaveStats = QtWidgets.QCheckBox(
            "Also save stats summary as sibling .txt")
        self.checkSaveStats.setChecked(
            self.settings.value("save_stats", False, type=bool))

        self.labelOutDir = QtWidgets.QLabel("Output folder:")
        self.txtOutDir = QtWidgets.QLineEdit(self.output_dir)
        self.txtOutDir.setReadOnly(True)
        self.btnOutDir = QtWidgets.QPushButton("Browse…")
        self.btnOutDir.clicked.connect(self._onPickOutDir)

        # --- OK / Close ---
        self.btnOk = QtWidgets.QPushButton("Compare")
        self.btnClose = QtWidgets.QPushButton("Close")

        # --- Layout ---
        form = QtWidgets.QFormLayout()
        form.addRow("Reference chunk:", self.cbxRefChunk)
        form.addRow("Reference DEM:", self.cbxRefElev)
        form.addRow("New DEM (from active chunk):", self.cbxNewElev)

        dir_layout = QtWidgets.QHBoxLayout()
        dir_layout.addWidget(self.labelOutDir)
        dir_layout.addWidget(self.txtOutDir)
        dir_layout.addWidget(self.btnOutDir)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.btnOk)
        btn_layout.addWidget(self.btnClose)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(intro)
        main_layout.addLayout(form)
        main_layout.addWidget(self.checkSaveDisk)
        main_layout.addWidget(self.checkSaveStats)
        main_layout.addLayout(dir_layout)
        main_layout.addStretch(1)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        self._onSaveDiskToggled(self.checkSaveDisk.isChecked())

        self.btnOk.clicked.connect(self.run)
        self.btnClose.clicked.connect(self.reject)

    # -- dropdown wiring --

    def _populateRefElev(self):
        self.cbxRefElev.clear()
        ref_chunk = self.cbxRefChunk.currentData()
        if ref_chunk is None:
            return
        entries = _iter_elevations(ref_chunk)
        if not entries:
            self.cbxRefElev.addItem("(no DEMs in this chunk)", None)
            return
        for label, elev in entries:
            self.cbxRefElev.addItem(label, elev)
        # Prefer the last-picked label, else the last elevation whose
        # label looks like a rugosity raster, else the first entry.
        last_label = self.settings.value("ref_elev_label", "", type=str)
        if last_label:
            idx = self.cbxRefElev.findText(last_label)
            if idx >= 0:
                self.cbxRefElev.setCurrentIndex(idx)
                return
        for i in range(self.cbxRefElev.count() - 1, -1, -1):
            if "rugosity" in (self.cbxRefElev.itemText(i) or "").lower():
                self.cbxRefElev.setCurrentIndex(i)
                return

    def _populateNewElev(self):
        self.cbxNewElev.clear()
        entries = _iter_elevations(self.active_chunk)
        if not entries:
            self.cbxNewElev.addItem("(no DEMs in active chunk)", None)
            return
        for label, elev in entries:
            self.cbxNewElev.addItem(label, elev)
        last_label = self.settings.value("new_elev_label", "", type=str)
        if last_label:
            idx = self.cbxNewElev.findText(last_label)
            if idx >= 0:
                self.cbxNewElev.setCurrentIndex(idx)
                return
        for i in range(self.cbxNewElev.count() - 1, -1, -1):
            if "rugosity" in (self.cbxNewElev.itemText(i) or "").lower():
                self.cbxNewElev.setCurrentIndex(i)
                return

    def _onRefChunkChanged(self, _idx):
        self._populateRefElev()

    def _onSaveDiskToggled(self, checked):
        self.labelOutDir.setEnabled(checked)
        self.txtOutDir.setEnabled(checked)
        self.btnOutDir.setEnabled(checked)
        self.checkSaveStats.setEnabled(checked)

    def _onPickOutDir(self):
        start = self.output_dir if os.path.isdir(self.output_dir) else \
            os.path.expanduser("~")
        picked = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output folder", start)
        if picked:
            self.output_dir = picked
            self.txtOutDir.setText(picked)
            self.settings.setValue("output_dir", picked)

    # -- run --

    def run(self):
        try:
            self._runImpl()
        except Exception as exc:
            self.setEnabled(True)
            QtWidgets.QMessageBox.critical(
                self, "Compare Rugosity failed", str(exc))
            raise

    def _runImpl(self):
        ref_chunk = self.cbxRefChunk.currentData()
        ref_elev = self.cbxRefElev.currentData()
        new_elev = self.cbxNewElev.currentData()

        if ref_chunk is None:
            raise RuntimeError("No reference chunk selected.")
        if ref_elev is None:
            raise RuntimeError("Reference chunk has no DEMs to compare.")
        if new_elev is None:
            raise RuntimeError("Active chunk has no DEMs to compare.")
        if ref_elev is new_elev:
            raise RuntimeError(
                "Reference and new DEMs are the same object — pick "
                "different rasters or a different reference chunk.")

        save_to_disk = self.checkSaveDisk.isChecked()
        save_stats = save_to_disk and self.checkSaveStats.isChecked()
        if save_to_disk and (not self.output_dir
                             or not os.path.isdir(self.output_dir)):
            raise RuntimeError(
                "Disk export is checked but no valid output folder is "
                "selected. Either uncheck \"Also save delta raster\" or "
                "pick a folder.")

        self.settings.setValue("ref_chunk_label", self.cbxRefChunk.currentText())
        self.settings.setValue("ref_elev_label", self.cbxRefElev.currentText())
        self.settings.setValue("new_elev_label", self.cbxNewElev.currentText())
        self.settings.setValue("save_to_disk", save_to_disk)
        self.settings.setValue("save_stats", self.checkSaveStats.isChecked())

        ref_chunk_label = ref_chunk.label or "reference"
        new_chunk_label = self.active_chunk.label or "chunk"

        self.setEnabled(False)
        progress = _ProgressDialog(self, "Compare Rugosity")
        progress.show()
        QtWidgets.QApplication.processEvents()

        try:
            print("Compare Rugosity:")
            print("  new chunk: {}".format(new_chunk_label))
            print("  new DEM:   {}".format(self.cbxNewElev.currentText()))
            print("  ref chunk: {}".format(ref_chunk_label))
            print("  ref DEM:   {}".format(self.cbxRefElev.currentText()))

            delta_path, ref_path, cell_size_m, stats = compute_rugosity_delta(
                self.active_chunk, ref_chunk, new_elev, ref_elev,
                progress=progress)

            cell_size_cm = int(round(cell_size_m * 100))

            # Import the reference DEM into the active chunk so both
            # timepoints and the delta are viewable side by side.
            progress.set_status("Importing reference DEM into active chunk…")
            ref_label = "{} ({:.2f}m, from: {})".format(
                RASTER_LABEL_PREFIX_REF, cell_size_m, ref_chunk_label)
            imported_ref = _import_raster_to_chunk(
                self.active_chunk, ref_path, ref_label)
            if imported_ref is None:
                print("  WARNING: reference raster imported but no new "
                      "elevation entry found; skipping label rename.")

            # Import the delta.
            progress.set_status("Importing delta DEM into active chunk…")
            delta_label = "{} ({:.2f}m, vs: {})".format(
                RASTER_LABEL_PREFIX_DELTA, cell_size_m, ref_chunk_label)
            imported_delta = _import_raster_to_chunk(
                self.active_chunk, delta_path, delta_label)
            if imported_delta is None:
                print("  WARNING: delta raster imported but no new "
                      "elevation entry found; skipping label rename.")

            # Optional disk copy.
            disk_path = None
            stats_path = None
            if save_to_disk:
                import shutil
                project_name = os.path.basename(self.doc.path or "untitled")
                for ext in (".psx", ".psz", ".files"):
                    if project_name.lower().endswith(ext):
                        project_name = project_name[:-len(ext)]
                        break
                out_basename = "{}_{}_vs_{}_rugosity_delta_{}cm.tif".format(
                    project_name, new_chunk_label, ref_chunk_label,
                    cell_size_cm)
                disk_path = os.path.join(self.output_dir, out_basename)
                shutil.copy2(delta_path, disk_path)
                print("  saved to: {}".format(disk_path))
                if save_stats:
                    stats_path = os.path.splitext(disk_path)[0] + ".txt"
                    body = _format_stats_text(
                        new_chunk_label, ref_chunk_label, cell_size_m,
                        stats, disk_path=disk_path)
                    with open(stats_path, "w", encoding="utf-8") as f:
                        f.write(body)
                    print("  stats: {}".format(stats_path))

            print("  done.")
            print("  cells with data: {}".format(stats["n_cells"]))
            if stats["n_cells"] > 0:
                print("  mean delta: {:+.3f} ({:+.1f}%)".format(
                    stats["mean"], 100 * stats["mean"]))
                print("  median delta: {:+.3f} ({:+.1f}%)".format(
                    stats["median"], 100 * stats["median"]))
                print("  range: {:+.3f} to {:+.3f}".format(
                    stats["min"], stats["max"]))

            summary = _format_stats_text(
                new_chunk_label, ref_chunk_label, cell_size_m, stats,
                disk_path=disk_path)
            if stats_path:
                summary += "\nStats text: {}".format(stats_path)

            progress.close()
            QtWidgets.QMessageBox.information(
                self, "Compare Rugosity", summary)
            self.accept()
        finally:
            progress.close()
            self.setEnabled(True)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_script():
    try:
        app = QtWidgets.QApplication.instance()
        parent = app.activeWindow() if app else None
        dlg = CompareRugosityDlg(parent)
        dlg.exec()
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", str(e))


label = "ReefShape/Tools/Compare Rugosity"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
