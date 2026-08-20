"""
Create Boundary from Markers
Will Greene, Perry Institute for Marine Science

The manual counterpart to the automatic boundary the full workflow builds. That
one takes the convex hull of every georeferenced marker, which is right almost
always and needs no input. This is for the rest: a concave plot the hull would
over-cover, a marker that should not be a vertex, or a boundary drawn from
targets that were never georeferenced.

Opens with the hull already selected, so the common case is to glance at the
plan view and click Create. Clicking markers only matters when you want
something the hull would not produce.

Ordering is the whole difficulty with a hand-drawn boundary -- with four
markers, 16 of the 24 possible orderings cross over themselves -- so the plan
view draws the polygon as it is built and says plainly when edges cross.
"""

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets

from modules.reefshape_core import (
    corner_markers,
    create_shape_from_markers,
    find_outer_boundary,
    hull_order,
    project_markers,
)


class PlanView(QtWidgets.QWidget):
    """Top-down view of the markers, with the boundary drawn as it is built.

    Drawn with QPainter rather than a plotting library so this script keeps
    its current dependency footprint -- Metashape and Qt, nothing to install.

    Its job is to make a bad ordering visible while it is being made, instead
    of after the boundary has been created and opened in the ortho view.
    """

    markerClicked = QtCore.Signal(object)

    MARGIN = 22
    DOT = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 230)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._points = []        # [(x, y, marker), ...] in shape CRS
        self._order = []         # markers, in boundary order
        self._crossings = set()  # indices of edges that cross another edge
        self._screen = {}        # marker -> QPointF

    def setData(self, points, order, crossings):
        self._points = points
        self._order = order
        self._crossings = crossings
        self.update()

    # -- geometry --

    def _transform(self):
        """Scale factors mapping CRS coordinates onto the widget.

        Both axes share one scale so the plot is not stretched -- a squashed
        view would misrepresent which ordering looks sensible. Y is flipped
        because CRS northing increases upward and screen Y increases downward.
        """
        if not self._points:
            return None
        xs = [p[0] for p in self._points]
        ys = [p[1] for p in self._points]
        span_x = max(xs) - min(xs) or 1e-12
        span_y = max(ys) - min(ys) or 1e-12
        usable_w = max(1, self.width() - 2 * self.MARGIN)
        usable_h = max(1, self.height() - 2 * self.MARGIN)
        scale = min(usable_w / span_x, usable_h / span_y)
        offset_x = self.MARGIN + (usable_w - span_x * scale) / 2.0
        offset_y = self.MARGIN + (usable_h - span_y * scale) / 2.0
        return min(xs), min(ys), scale, offset_x, offset_y

    def _project(self):
        transform = self._transform()
        self._screen = {}
        if transform is None:
            return
        min_x, min_y, scale, off_x, off_y = transform
        for x, y, marker in self._points:
            self._screen[marker] = QtCore.QPointF(
                off_x + (x - min_x) * scale,
                self.height() - (off_y + (y - min_y) * scale))

    # -- painting --

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        palette = self.palette()

        painter.fillRect(self.rect(), palette.base())
        self._project()
        if not self._screen:
            painter.setPen(palette.color(QtGui.QPalette.Mid))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter,
                             "No placed markers in this chunk")
            return

        ordered = [m for m in self._order if m in self._screen]

        if len(ordered) >= 2:
            polygon = QtGui.QPolygonF([self._screen[m] for m in ordered])
            if len(ordered) >= 3:
                fill = QtGui.QColor(55, 138, 221, 38)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(fill)
                painter.drawPolygon(polygon)

            for i in range(len(ordered)):
                if i == len(ordered) - 1 and len(ordered) < 3:
                    break
                start = self._screen[ordered[i]]
                end = self._screen[ordered[(i + 1) % len(ordered)]]
                crossing = i in self._crossings
                pen = QtGui.QPen(QtGui.QColor(200, 60, 60) if crossing
                                 else QtGui.QColor(55, 138, 221))
                pen.setWidthF(2.4 if crossing else 1.8)
                painter.setPen(pen)
                painter.drawLine(start, end)

        index_of = {m: i for i, m in enumerate(ordered)}
        for _x, _y, marker in self._points:
            centre = self._screen[marker]
            selected = marker in index_of
            painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.Mid), 1.4))
            painter.setBrush(QtGui.QColor(55, 138, 221) if selected
                             else palette.base())
            painter.drawEllipse(centre, self.DOT, self.DOT)

            painter.setPen(palette.color(QtGui.QPalette.Text))
            painter.drawText(
                QtCore.QRectF(centre.x() - 60, centre.y() - self.DOT - 17,
                              120, 14),
                QtCore.Qt.AlignCenter, marker.label)

            if selected:
                painter.setPen(QtGui.QColor(255, 255, 255))
                font = painter.font()
                font.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
                painter.setFont(font)
                painter.drawText(
                    QtCore.QRectF(centre.x() - self.DOT, centre.y() - self.DOT,
                                  self.DOT * 2, self.DOT * 2),
                    QtCore.Qt.AlignCenter, str(index_of[marker] + 1))
                painter.setFont(QtWidgets.QApplication.font())

    def mousePressEvent(self, event):
        """Emit the nearest marker, if the click landed near one."""
        if not self._screen:
            return
        position = event.pos()
        nearest, best = None, None
        for marker, point in self._screen.items():
            dx = point.x() - position.x()
            dy = point.y() - position.y()
            distance = (dx * dx + dy * dy) ** 0.5
            if best is None or distance < best:
                nearest, best = marker, distance
        # Generous radius: the dots are small, and a near miss meaning nothing
        # is more annoying than an occasional wrong pick the user can undo.
        if best is not None and best <= self.DOT * 3:
            self.markerClicked.emit(nearest)


def segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        value = ((b[0] - a[0]) * (c[1] - a[1])
                 - (b[1] - a[1]) * (c[0] - a[0]))
        return (value > 1e-18) - (value < -1e-18)
    return (orient(p1, p2, p3) != orient(p1, p2, p4)
            and orient(p3, p4, p1) != orient(p3, p4, p2))


def find_crossings(points):
    """Indices of edges that cross a non-adjacent edge."""
    crossings = set()
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or (i == 0 and j == n - 1):
                continue
            if segments_cross(points[i], points[(i + 1) % n],
                              points[j], points[(j + 1) % n]):
                crossings.add(i)
                crossings.add(j)
    return crossings


def polygon_area(points):
    """Shoelace area, unsigned."""
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total / 2.0)


class CreateBoundaryDlg(QtWidgets.QDialog):

    def __init__(self, parent):
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Create Boundary from Markers")
        self.resize(880, 560)

        self.doc = Metashape.app.document
        self.chunk = self.doc.chunk
        self.order = []

        # Every placed marker is offered, not just the georeferenced ones: a
        # hand-drawn boundary is exactly the case where you might want a
        # target the automatic path ignores.
        self.projected = project_markers(self.chunk, self.chunk.markers) \
            if self.chunk else []
        self.by_marker = {m: (x, y) for x, y, m in self.projected}

        # Metres per CRS unit, for reporting an area the user can sanity-check.
        self.crs_to_m = self._estimate_scale()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Click markers in the order they go around the plot. Click a "
            "placed marker again to remove it."))

        panes = QtWidgets.QHBoxLayout()
        panes.addWidget(self._build_marker_list(), 0)
        panes.addWidget(self._build_plan_view(), 1)
        panes.addWidget(self._build_order_list(), 0)
        layout.addLayout(panes, 1)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addLayout(self._build_buttons())

        self._prefill()

    # -- construction --

    def _build_marker_list(self):
        box = QtWidgets.QGroupBox("Markers in chunk")
        box.setFixedWidth(190)
        inner = QtWidgets.QVBoxLayout(box)
        self.marker_table = QtWidgets.QTableWidget(0, 2)
        self.marker_table.setHorizontalHeaderLabels(["Marker", "Order"])
        self.marker_table.verticalHeader().setVisible(False)
        self.marker_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self.marker_table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        header = self.marker_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.marker_table.cellClicked.connect(self._on_table_clicked)
        inner.addWidget(self.marker_table)
        return box

    def _build_plan_view(self):
        box = QtWidgets.QGroupBox("Plan view")
        inner = QtWidgets.QVBoxLayout(box)
        self.plan = PlanView()
        self.plan.markerClicked.connect(self._toggle_marker)
        inner.addWidget(self.plan)
        return box

    def _build_order_list(self):
        box = QtWidgets.QGroupBox("Boundary order")
        box.setFixedWidth(190)
        inner = QtWidgets.QVBoxLayout(box)
        self.order_list = QtWidgets.QListWidget()
        inner.addWidget(self.order_list)

        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Up", lambda: self._move(-1)),
                           ("Down", lambda: self._move(1)),
                           ("Remove", self._remove_selected)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        inner.addLayout(row)
        return box

    def _build_buttons(self):
        row = QtWidgets.QHBoxLayout()
        for text, slot, tip in (
            ("Auto-order", self._auto_order,
             "Reorder the selected markers into a convex hull -- the same "
             "boundary the full workflow builds automatically."),
            ("Reverse", self._reverse, "Flip the direction of travel."),
            ("Clear", self._clear, "Start again with nothing selected."),
        ):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)

        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.create_button = QtWidgets.QPushButton("Create boundary")
        self.create_button.setDefault(True)
        self.create_button.clicked.connect(self._create)
        row.addWidget(cancel)
        row.addWidget(self.create_button)
        return row

    # -- state --

    def _estimate_scale(self):
        """Metres per CRS unit on each axis, from the camera positions.

        Returns (scale_x, scale_y) or None. Used only to report an area the
        user can sanity-check against the plot they swam -- it does not affect
        the boundary itself.

        Two things this has to get right, both of which it got wrong first
        time round. `camera.center` is in chunk-*internal* units, not metres,
        so it needs `chunk.transform.scale` applied; without that the reported
        area came out 6x too large. And the axes need separate scales: in a
        geographic CRS a degree of longitude and a degree of latitude are
        different distances (about 99 km and 111 km at this latitude), so
        using one scale for both skews the area by ~10%.
        """
        if not self.chunk or not self.chunk.transform:
            return None
        internal_to_m = self.chunk.transform.scale
        if not internal_to_m:
            return None

        local, crs = [], []
        transform = self.chunk.transform.matrix
        shape_crs = self.chunk.shapes.crs if self.chunk.shapes else self.chunk.crs
        for camera in self.chunk.cameras:
            if camera.transform is None or camera.center is None:
                continue
            try:
                projected = shape_crs.project(transform.mulp(camera.center))
            except Exception:
                continue
            local.append((camera.center.x, camera.center.y))
            crs.append((projected.x, projected.y))
        if len(local) < 2:
            return None

        scales = []
        for axis in (0, 1):
            span_local = (max(p[axis] for p in local)
                          - min(p[axis] for p in local)) * internal_to_m
            span_crs = max(p[axis] for p in crs) - min(p[axis] for p in crs)
            if abs(span_crs) < 1e-15 or abs(span_local) < 1e-9:
                return None
            scales.append(abs(span_local / span_crs))
        return scales[0], scales[1]

    def _prefill(self):
        """Open with the hull of the georeferenced markers already selected.

        Makes the common case a glance and a click; manual selection is then
        only for the boundaries the hull would not produce.
        """
        georeferenced = corner_markers(self.chunk) if self.chunk else []
        if len(georeferenced) >= 3:
            self.order = hull_order(self.chunk, georeferenced)
        self._refresh()

    def _toggle_marker(self, marker):
        if marker in self.order:
            self.order.remove(marker)
        else:
            self.order.append(marker)
        self._refresh()

    def _on_table_clicked(self, row, _column):
        item = self.marker_table.item(row, 0)
        if item is not None:
            self._toggle_marker(item.data(QtCore.Qt.UserRole))

    def _move(self, delta):
        row = self.order_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self.order)):
            return
        self.order[row], self.order[target] = self.order[target], self.order[row]
        self._refresh()
        self.order_list.setCurrentRow(target)

    def _remove_selected(self):
        row = self.order_list.currentRow()
        if 0 <= row < len(self.order):
            del self.order[row]
            self._refresh()

    def _auto_order(self):
        if len(self.order) >= 3:
            self.order = hull_order(self.chunk, self.order)
        self._refresh()

    def _reverse(self):
        self.order.reverse()
        self._refresh()

    def _clear(self):
        self.order = []
        self._refresh()

    # -- display --

    def _refresh(self):
        points = [self.by_marker[m] for m in self.order if m in self.by_marker]
        crossings = find_crossings(points) if len(points) >= 4 else set()

        self.marker_table.blockSignals(True)
        self.marker_table.setRowCount(len(self.projected))
        for row, (_x, _y, marker) in enumerate(self.projected):
            name = QtWidgets.QTableWidgetItem(marker.label)
            name.setData(QtCore.Qt.UserRole, marker)
            self.marker_table.setItem(row, 0, name)
            position = (str(self.order.index(marker) + 1)
                        if marker in self.order else "")
            self.marker_table.setItem(row, 1,
                                      QtWidgets.QTableWidgetItem(position))
        self.marker_table.blockSignals(False)

        current = self.order_list.currentRow()
        self.order_list.clear()
        for i, marker in enumerate(self.order):
            self.order_list.addItem("{}   {}".format(i + 1, marker.label))
        if 0 <= current < self.order_list.count():
            self.order_list.setCurrentRow(current)

        self.plan.setData(self.projected, self.order, crossings)

        if len(self.order) < 3:
            self.status.setText(
                "Select at least 3 markers ({} so far).".format(len(self.order)))
            self.status.setStyleSheet("color: palette(mid);")
            self.create_button.setEnabled(False)
        elif crossings:
            self.status.setText(
                "The boundary crosses itself. Reorder the markers, or use "
                "Auto-order.")
            self.status.setStyleSheet("color: #b8860b;")
            self.create_button.setEnabled(False)
        else:
            area = polygon_area(points)
            if self.crs_to_m:
                scale_x, scale_y = self.crs_to_m
                area_text = "{:.1f} m2".format(area * scale_x * scale_y)
            else:
                area_text = "{:.3g} CRS units2".format(area)
            self.status.setText("{} vertices, {}.".format(
                len(self.order), area_text))
            self.status.setStyleSheet("color: #1a7f37;")
            self.create_button.setEnabled(True)

    # -- create --

    def _create(self):
        existing = find_outer_boundary(self.chunk)
        if existing is not None:
            reply = QtWidgets.QMessageBox.question(
                self, "Replace existing boundary",
                "This chunk already has an outer boundary ({}).\n\n"
                "Replace it with the new one?".format(
                    existing.label or "unnamed"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            # Demote rather than delete: the polygon may have been drawn by
            # hand and is not ours to throw away.
            existing.boundary_type = Metashape.Shape.BoundaryType.NoBoundary
            existing.label = (existing.label or "Boundary") + " (replaced)"

        if not create_shape_from_markers(self.chunk, self.order):
            Metashape.app.messageBox("Could not create the boundary polygon.")
            return

        Metashape.app.update()
        self.doc.save()
        print("Boundary created from {} markers: {}".format(
            len(self.order), ", ".join(m.label for m in self.order)))
        self.accept()


def run_script():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()
    doc = Metashape.app.document
    if not doc.chunk or len(doc.chunk.markers) == 0:
        Metashape.app.messageBox(
            "This chunk has no markers to build a boundary from.")
        return
    try:
        dlg = CreateBoundaryDlg(parent)
        dlg.exec()
    except Exception as err:
        Metashape.app.messageBox("Create Boundary error:\n\n{}".format(err))


label = "ReefShape/Tools/Create Boundary from Markers"
Metashape.app.removeMenuItem(label)
Metashape.app.removeMenuItem("ReefShape/Tools/Create Boundary")
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
