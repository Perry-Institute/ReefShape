"""
Export Compressed DEM
Will Greene, Perry Institute for Marine Science

Metashape's stock Export DEM writes an uncompressed GeoTIFF. Elevation rasters
compress very well losslessly -- a reef plot DEM typically lands around a third
of its uncompressed size with LZW -- and nothing downstream can tell the
difference, because LZW is lossless and every GIS reads it transparently.

This dialog offers the same options as the stock one and exports with LZW
compression instead. It is otherwise a faithful stand-in: the defaults below
are Metashape's own exportRaster defaults, so leaving everything alone produces
the same raster the built-in tool would, only smaller.

Operates on the active chunk's active DEM.
"""

import os

import Metashape
from PySide2 import QtCore, QtWidgets


class ExportCompressedDemDlg(QtWidgets.QDialog):

    def __init__(self, parent):
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Export Compressed DEM")
        self.setMinimumWidth(520)

        self.doc = Metashape.app.document
        self.chunk = self.doc.chunk
        # Held as state rather than read at export time, so the Select button
        # can offer something other than the chunk's own system.
        self.crs = self.chunk.crs if self.chunk else None

        layout = QtWidgets.QVBoxLayout(self)

        elevation = self.chunk.elevation if self.chunk else None
        summary = QtWidgets.QLabel(
            "DEM resolution {:.5f} m, {} x {} px".format(
                elevation.resolution, elevation.width, elevation.height)
            if elevation else "This chunk has no DEM.")
        summary.setStyleSheet("color: palette(mid);")
        layout.addWidget(summary)

        # ---- Coordinate system ----
        crs_row = QtWidgets.QHBoxLayout()
        crs_row.addWidget(QtWidgets.QLabel("Coordinate system:"))
        self.crs_label = QtWidgets.QLabel(self.crs.name if self.crs else "None")
        self.crs_label.setWordWrap(True)
        crs_row.addWidget(self.crs_label, 1)
        self.btn_crs = QtWidgets.QPushButton("Select...")
        self.btn_crs.clicked.connect(self.select_crs)
        crs_row.addWidget(self.btn_crs)
        layout.addLayout(crs_row)

        # ---- Resolution and no-data ----
        res_row = QtWidgets.QHBoxLayout()
        res_row.addWidget(QtWidgets.QLabel("Resolution (m):"))
        self.spin_res = QtWidgets.QDoubleSpinBox()
        self.spin_res.setDecimals(6)
        self.spin_res.setRange(0.0, 10000.0)
        self.spin_res.setSingleStep(0.001)
        # Prefilled with the DEM's own resolution, as the stock dialog does.
        # 0 would tell Metashape to choose, which for an existing DEM arrives
        # at the same number by a longer route.
        self.spin_res.setValue(elevation.resolution if elevation else 0.0)
        res_row.addWidget(self.spin_res)
        res_row.addStretch(1)

        res_row.addWidget(QtWidgets.QLabel("No-data value:"))
        self.edit_nodata = QtWidgets.QLineEdit("-32767")
        self.edit_nodata.setFixedWidth(90)
        self.edit_nodata.setToolTip(
            "Value written where the DEM has no data. Metashape's default is "
            "-32767; the ReefShape workflow uses -5 for its own exports.")
        res_row.addWidget(self.edit_nodata)
        layout.addLayout(res_row)

        # ---- Blocks ----
        block_row = QtWidgets.QHBoxLayout()
        self.check_blocks = QtWidgets.QCheckBox("Split in blocks")
        self.check_blocks.setToolTip(
            "Write the DEM as a grid of separate files. Rarely needed now that "
            "BigTIFF removes the 4 GB limit.")
        self.check_blocks.toggled.connect(self._on_blocks_toggled)
        block_row.addWidget(self.check_blocks)
        self.spin_block_w = QtWidgets.QSpinBox()
        self.spin_block_h = QtWidgets.QSpinBox()
        for spin in (self.spin_block_w, self.spin_block_h):
            spin.setRange(1, 1000000)
            spin.setValue(10000)
            spin.setEnabled(False)
        block_row.addWidget(QtWidgets.QLabel("width:"))
        block_row.addWidget(self.spin_block_w)
        block_row.addWidget(QtWidgets.QLabel("height:"))
        block_row.addWidget(self.spin_block_h)
        block_row.addStretch(1)
        layout.addLayout(block_row)

        # ---- Options, mirroring the stock dialog's checkboxes ----
        options = QtWidgets.QGroupBox("Options")
        grid = QtWidgets.QGridLayout(options)
        self.check_clip = QtWidgets.QCheckBox("Clip to boundary shapes")
        self.check_clip.setChecked(True)
        self.check_alpha = QtWidgets.QCheckBox("Write alpha channel")
        self.check_alpha.setChecked(True)
        self.check_world = QtWidgets.QCheckBox("Write world file")
        self.check_kml = QtWidgets.QCheckBox("Write KML file")
        self.check_scheme = QtWidgets.QCheckBox("Write tile scheme")
        self.check_white = QtWidgets.QCheckBox("White background")
        self.check_white.setChecked(True)
        for i, box in enumerate((self.check_clip, self.check_alpha,
                                 self.check_world, self.check_kml,
                                 self.check_scheme, self.check_white)):
            grid.addWidget(box, i // 2, i % 2)
        layout.addWidget(options)

        # ---- GeoTIFF settings ----
        tiff = QtWidgets.QGroupBox("GeoTIFF")
        tiff_layout = QtWidgets.QGridLayout(tiff)
        note = QtWidgets.QLabel(
            "Compression is LZW -- lossless, and the reason this tool exists. "
            "The stock Export DEM writes the same raster uncompressed.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        tiff_layout.addWidget(note, 0, 0, 1, 2)

        self.check_big = QtWidgets.QCheckBox("BigTIFF")
        self.check_big.setChecked(True)
        self.check_big.setToolTip(
            "Required beyond 4 GB. Harmless below it, and reef plot DEMs get "
            "there easily.")
        self.check_tiled = QtWidgets.QCheckBox("Tiled")
        self.check_overviews = QtWidgets.QCheckBox("Generate overviews")
        self.check_overviews.setChecked(True)
        self.check_overviews.setToolTip(
            "Embedded pyramids. Makes the DEM redraw quickly when zoomed out "
            "in GIS software, at a small cost in file size.")
        tiff_layout.addWidget(self.check_big, 1, 0)
        tiff_layout.addWidget(self.check_tiled, 1, 1)
        tiff_layout.addWidget(self.check_overviews, 2, 0)
        layout.addWidget(tiff)

        # ---- Buttons ----
        buttons = QtWidgets.QDialogButtonBox()
        self.btn_export = buttons.addButton(
            "Export...", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.btn_export.clicked.connect(self.export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if elevation is None:
            self.btn_export.setEnabled(False)

    def _on_blocks_toggled(self, enabled):
        self.spin_block_w.setEnabled(enabled)
        self.spin_block_h.setEnabled(enabled)

    def select_crs(self):
        crs = Metashape.app.getCoordinateSystem("Select Coordinate System",
                                                self.crs)
        if crs:
            self.crs = crs
            self.crs_label.setText(crs.name)

    def export(self):
        if self.chunk is None or self.chunk.elevation is None:
            Metashape.app.messageBox("This chunk has no DEM to export.")
            return

        try:
            nodata = float(self.edit_nodata.text())
        except ValueError:
            Metashape.app.messageBox(
                "No-data value must be a number. Metashape's default is -32767.")
            return

        suggested = os.path.join(
            os.path.dirname(self.doc.path or ""),
            "{}_DEM.tif".format(self.chunk.label or "chunk"))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export compressed DEM", suggested, "GeoTIFF (*.tif)")
        if not path:
            return

        compression = Metashape.ImageCompression()
        compression.tiff_compression = (
            Metashape.ImageCompression.TiffCompressionLZW)
        compression.tiff_big = self.check_big.isChecked()
        compression.tiff_tiled = self.check_tiled.isChecked()
        compression.tiff_overviews = self.check_overviews.isChecked()

        projection = Metashape.OrthoProjection()
        projection.crs = self.crs

        self.setEnabled(False)
        try:
            self.chunk.exportRaster(
                path=path,
                source_data=Metashape.ElevationData,
                projection=projection,
                resolution=self.spin_res.value(),
                nodata_value=nodata,
                image_compression=compression,
                split_in_blocks=self.check_blocks.isChecked(),
                block_width=self.spin_block_w.value(),
                block_height=self.spin_block_h.value(),
                clip_to_boundary=self.check_clip.isChecked(),
                save_alpha=self.check_alpha.isChecked(),
                save_world=self.check_world.isChecked(),
                save_kml=self.check_kml.isChecked(),
                save_scheme=self.check_scheme.isChecked(),
                white_background=self.check_white.isChecked(),
                title="DEM",
                description="Generated by Agisoft Metashape with ReefShape",
            )
        except Exception as err:
            Metashape.app.messageBox("Unable to export DEM:\n\n{}".format(err))
            return
        finally:
            self.setEnabled(True)

        try:
            size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
            size_note = "\n\nFile size: {:.1f} MB".format(size_mb)
        except OSError:
            # Split-in-blocks writes several files under generated names, so
            # the path we handed in may not itself exist.
            size_note = ""

        print("Compressed DEM exported to {}".format(path))
        Metashape.app.messageBox(
            "DEM exported with LZW compression.{}".format(size_note))
        self.accept()


def run_script():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()
    try:
        dlg = ExportCompressedDemDlg(parent)
        dlg.exec()
    except Exception as err:
        Metashape.app.messageBox(
            "Export Compressed DEM error:\n\n{}".format(err))


label = "ReefShape/Tools/Export Compressed DEM"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
