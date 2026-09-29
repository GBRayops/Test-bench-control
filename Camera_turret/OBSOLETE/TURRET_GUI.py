from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from turret_control.calibration import (
    load_servosila_calibration,
    select_import_zero,
    work_zone_degrees,
)
from turret_control.controller import TurretController
from turret_control.model import AppConfig, AxisConfig, load_config, save_config
from turret_control.trajectories import (
    AzimuthCompensatedTrajectory,
    ButterflyTrajectory,
    DiagnosticStepTrajectory,
    DirectDiagnosticTrajectory,
    EllipseTrajectory,
    LineTrajectory,
    Point,
    SequenceTrajectory,
    SineTrajectory,
    SmoothSweepTrajectory,
    StepSweepTrajectory,
    preview_points,
)


CONFIG_PATH = Path(__file__).resolve().parent.parent / "turret_config.json"


class PreviewWidget(QWidget):
    """Qt replacement for the Tkinter Canvas used for AZ/EL preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preview: list[Point] = []
        self.current_point = Point(0, 0)
        self.setMinimumSize(300, 250)

    def set_preview(self, points: list[Point]) -> None:
        self.preview = points
        self.update()

    def set_current_point(self, point: Point) -> None:
        self.current_point = point
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#101820"))

        w = max(self.width(), 10)
        h = max(self.height(), 10)

        points = self.preview or [self.current_point]
        xs = [p.az for p in points] + [self.current_point.az]
        ys = [p.el for p in points] + [self.current_point.el]

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        padx = max((xmax - xmin) * 0.1, 1.0)
        pady = max((ymax - ymin) * 0.1, 1.0)

        xmin -= padx
        xmax += padx
        ymin -= pady
        ymax += pady

        def xy(p: Point) -> QPointF:
            x = 30 + (p.az - xmin) / (xmax - xmin) * (w - 60)
            y = h - 30 - (p.el - ymin) / (ymax - ymin) * (h - 60)
            return QPointF(x, y)

        axis_pen = QPen(QColor("#607080"), 1)
        painter.setPen(axis_pen)
        painter.drawLine(30, h - 30, w - 20, h - 30)
        painter.drawLine(30, 20, 30, h - 30)

        painter.setPen(QPen(Qt.white))
        painter.drawText(w - 55, h - 15, "AZ (°)")
        painter.drawText(35, 15, "EL (°)")

        if len(points) > 1:
            pen = QPen(QColor("#38bdf8"), 2)
            painter.setPen(pen)

            prev = xy(points[0])
            for point in points[1:]:
                cur = xy(point)
                painter.drawLine(prev, cur)
                prev = cur

        x, y = xy(self.current_point).x(), xy(self.current_point).y()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#fb923c")))
        painter.drawEllipse(QPointF(x, y), 5, 5)

        painter.setPen(QPen(Qt.white))
        painter.drawText(
            QPointF(x + 8, y - 10),
            f"{self.current_point.az:.2f}°, {self.current_point.el:.2f}°",
        )


class TurretApp(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Tourelle 2 axes — Servosila SC-60R")
        self.resize(1120, 760)
        self.setMinimumSize(980, 680)

        self.config_data = load_config(CONFIG_PATH)
        self.controller: TurretController | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        # Tkinter's StringVar/BooleanVar are replaced by a dictionary of
        # Qt widgets. _get() and _set() provide a similar interface.
        self.vars: dict[str, QWidget] = {}

        self._build()
        self._load_vars(self.config_data)
        self._refresh_ports()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_events)
        self.timer.start(40)

    # ------------------------------------------------------------------
    # Small variable helpers
    # ------------------------------------------------------------------

    def _add_line_edit(self, name: str, value: object = "") -> QLineEdit:
        widget = QLineEdit(str(value))
        self.vars[name] = widget
        return widget

    def _add_check_box(self, name: str, value: bool = False) -> QCheckBox:
        widget = QCheckBox()
        widget.setChecked(value)
        self.vars[name] = widget
        return widget

    def _get(self, name: str):
        widget = self.vars[name]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        return widget.text()

    def _set(self, name: str, value: object) -> None:
        widget = self.vars[name]
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(str(value))
        else:
            widget.setText(str(value))

    # ------------------------------------------------------------------
    # GUI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        self.setLayout(root)

        root_layout = QGridLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setColumnStretch(1, 1)
        root_layout.setRowStretch(1, 1)

        # Connection ----------------------------------------------------
        connection = QGroupBox("Connexion")
        connection_layout = QHBoxLayout(connection)

        self._add_line_edit("port", "")
        self._add_check_box("simulation", True)
        self._add_line_edit("update_hz", "200")

        connection_layout.addWidget(QLabel("Port COM"))
        self.port_box = QComboBox()
        self.port_box.setEditable(True)
        self.port_box.setMinimumWidth(130)
        connection_layout.addWidget(self.port_box)

        refresh_button = QPushButton("Actualiser")
        refresh_button.clicked.connect(self._refresh_ports)
        connection_layout.addWidget(refresh_button)

        simulation_box = self.vars["simulation"]
        simulation_box.setText("Simulation")
        connection_layout.addWidget(simulation_box)

        connection_layout.addWidget(QLabel("Fréquence (Hz)"))
        connection_layout.addWidget(self.vars["update_hz"])

        self.connect_button = QPushButton("Connecter")
        self.connect_button.clicked.connect(self._toggle_connection)
        connection_layout.addWidget(self.connect_button)

        self.status_var = QLabel("Déconnecté")
        connection_layout.addWidget(self.status_var, 1)

        stop_button = QPushButton("STOP")
        stop_button.clicked.connect(self._stop)
        connection_layout.addWidget(stop_button)

        off_button = QPushButton("ARRÊT PUISSANCE")
        off_button.clicked.connect(self._off)
        connection_layout.addWidget(off_button)

        root_layout.addWidget(connection, 0, 0, 1, 2)

        # Left side -----------------------------------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        axis_book = QTabWidget()
        az_page = QWidget()
        el_page = QWidget()

        axis_book.addTab(az_page, "Azimut")
        axis_book.addTab(el_page, "Élévation")

        self._axis_frame(az_page, "Calibration et axe", "az")
        self._axis_frame(el_page, "Calibration et axe", "el")

        left_layout.addWidget(axis_book)

        motion_book = QTabWidget()

        command = QWidget()
        traj = QWidget()
        tests = QWidget()

        motion_book.addTab(command, "Position / ligne")
        motion_book.addTab(traj, "Cercle / ellipse")
        motion_book.addTab(tests, "Essais vidéo")

        self._build_command_tab(command)
        self._build_trajectory_tab(traj)
        self._build_test_tab(tests)

        left_layout.addWidget(motion_book)

        safety = QHBoxLayout()
        safety_stop = QPushButton("STOP")
        safety_stop.clicked.connect(self._stop)
        safety.addWidget(safety_stop)

        safety_off = QPushButton("ARRÊT PUISSANCE")
        safety_off.clicked.connect(self._off)
        safety.addWidget(safety_off)

        left_layout.addLayout(safety)

        root_layout.addWidget(left, 1, 0)

        # Right side ----------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        plot_group = QGroupBox("Prévisualisation AZ / EL")
        plot_layout = QVBoxLayout(plot_group)
        self.canvas = PreviewWidget()
        plot_layout.addWidget(self.canvas)

        right_layout.addWidget(plot_group, 3)

        log_group = QGroupBox("Journal")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(150)
        log_layout.addWidget(self.log_text)

        right_layout.addWidget(log_group, 1)

        root_layout.addWidget(right, 1, 1)

    def _build_command_tab(self, parent: QWidget) -> None:
        layout = QGridLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        for key, default in (
            ("target_az", "0"),
            ("target_el", "0"),
            ("line_duration", "3"),
        ):
            self._add_line_edit(key, default)

        self._entry(parent, layout, "Azimut cible (°)", "target_az", 0)
        self._entry(parent, layout, "Élévation cible (°)", "target_el", 1)
        self._entry(parent, layout, "Durée ligne (s)", "line_duration", 2)

        button = QPushButton("Aller en ligne droite")
        button.clicked.connect(self._start_line)
        layout.addWidget(button, 3, 0, 1, 2)

        button = QPushButton("Maintenir la position")
        button.clicked.connect(self._hold)
        layout.addWidget(button, 4, 0, 1, 2)

        button = QPushButton("Définir comme position actuelle (sans bouger)")
        button.clicked.connect(self._set_reference)
        layout.addWidget(button, 5, 0, 1, 2)

        button = QPushButton("Calculer les zéros au milieu des Work Zones")
        button.clicked.connect(self._set_midpoint_zeros)
        layout.addWidget(button, 6, 0, 1, 2)

        layout.setColumnStretch(1, 1)

    def _build_trajectory_tab(self, parent: QWidget) -> None:
        layout = QGridLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        defaults = {
            "center_az": "0",
            "center_el": "0",
            "radius_az": "10",
            "radius_el": "10",
            "approach_duration": "3",
            "frequency": "0.1",
            "laps": "1",
            "phase": "0",
        }

        for key, value in defaults.items():
            self._add_line_edit(key, value)

        start = QPushButton("▶ DÉMARRER LE CERCLE / L'ELLIPSE")
        start.clicked.connect(self._start_ellipse)
        layout.addWidget(start, 0, 0, 1, 2)

        labels = [
            ("Centre AZ (°)", "center_az"),
            ("Centre EL (°)", "center_el"),
            ("Rayon AZ (°)", "radius_az"),
            ("Rayon EL (°)", "radius_el"),
            ("Durée d'approche (s)", "approach_duration"),
            ("Fréquence (Hz)", "frequency"),
            ("Nombre de tours", "laps"),
            ("Phase initiale (°)", "phase"),
        ]

        for row, (label, key) in enumerate(labels, start=1):
            self._entry(parent, layout, label, key, row)

        layout.setColumnStretch(1, 1)

    def _build_test_tab(self, parent: QWidget) -> None:
        layout = QGridLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        test_defaults = {
            "test_center_az": "0",
            "test_center_el": "0",
            "test_amplitude_az": "5",
            "test_amplitude_el": "0",
            "test_frequency": "0.1",
            "test_smooth_frequency": "0.0357",
            "test_step_transition": "3",
            "test_step_hold": "3",
            "test_cycles": "3",
            "test_approach": "5",
        }

        for key, value in test_defaults.items():
            self._add_line_edit(key, value)

        self._add_check_box("test_compensate_az", False)

        test_type = QComboBox()
        test_type.addItems([
            "Diagnostic direct Servoscope",
            "Diagnostic 0 / -AZ",
            "Paliers AZ doux",
            "Balayage AZ doux",
            "Balayage AZ",
            "Balayage EL",
            "Diagonale",
            "Cercle",
            "Papillon X",
        ])
        self.vars["test_type"] = test_type

        start = QPushButton("▶ DÉMARRER LE TEST + CSV")
        start.clicked.connect(self._start_video_test)
        layout.addWidget(start, 0, 0, 1, 2)

        note = QLabel(
            "Lancez la vidéo avant le test. Le mode direct change immédiatement "
            "de cible, sans interpolation, et ignore la durée d'approche."
        )
        note.setWordWrap(True)
        layout.addWidget(note, 1, 0, 1, 2)

        layout.addWidget(QLabel("Trajectoire"), 2, 0)
        layout.addWidget(test_type, 2, 1)

        test_labels = [
            ("Centre AZ (°)", "test_center_az"),
            ("Centre EL (°)", "test_center_el"),
            ("Amplitude AZ (°)", "test_amplitude_az"),
            ("Amplitude EL (°)", "test_amplitude_el"),
            ("Fréquence standard (Hz)", "test_frequency"),
            ("Fréquence douce (Hz)", "test_smooth_frequency"),
            ("Transition palier (s)", "test_step_transition"),
            ("Maintien palier (s)", "test_step_hold"),
            ("Cycles", "test_cycles"),
            ("Approche (s)", "test_approach"),
        ]

        for row, (label, key) in enumerate(test_labels, start=3):
            self._entry(parent, layout, label, key, row)

        compensate = self.vars["test_compensate_az"]
        compensate.setText("Compensation dynamique AZ identifiée")
        layout.addWidget(compensate, 13, 0, 1, 2)

        note2 = QLabel("Avance 0,438 s · sans dérivée · limite ±1,5°")
        note2.setWordWrap(True)
        layout.addWidget(note2, 14, 0, 1, 2)

        layout.setColumnStretch(1, 1)

    def _entry(
        self,
        parent: QWidget,
        layout: QGridLayout,
        label: str,
        key: str,
        row: int,
    ) -> None:
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(self.vars[key], row, 1)

    def _axis_frame(self, parent: QWidget, title: str, prefix: str) -> None:
        outer = QVBoxLayout(parent)
        frame = QGroupBox(title)
        frame_layout = QGridLayout(frame)

        defaults = {
            f"{prefix}_node": "1" if prefix == "az" else "2",
            f"{prefix}_calibration": "",
            f"{prefix}_calibration_label": "Aucun fichier importé",
            f"{prefix}_cpr": "16384",
            f"{prefix}_ratio": "1",
            f"{prefix}_direction": "1",
            f"{prefix}_zero": "0",
            f"{prefix}_min_counts": "",
            f"{prefix}_max_counts": "",
            f"{prefix}_min": "-180" if prefix == "az" else "-90",
            f"{prefix}_max": "180" if prefix == "az" else "90",
        }

        for key, value in defaults.items():
            self._add_line_edit(key, value)

        labels = [
            ("Node ID", "node"),
            ("Comptes / tour encodeur", "cpr"),
            ("Rapport de réduction", "ratio"),
            ("Direction (+1/-1)", "direction"),
            ("Zéro 0° (comptes)", "zero"),
            ("Work Zone min (comptes)", "min_counts"),
            ("Work Zone max (comptes)", "max_counts"),
            ("Limite min (°)", "min"),
            ("Limite max (°)", "max"),
        ]

        for row, (label, suffix) in enumerate(labels):
            self._entry(frame, frame_layout, label, f"{prefix}_{suffix}", row)

        import_button = QPushButton("Importer calibration Servosila…")
        import_button.clicked.connect(lambda _checked=False, p=prefix: self._import_calibration(p))
        frame_layout.addWidget(import_button, len(labels), 0, 1, 2)

        calibration_label = QLabel()
        calibration_label.setStyleSheet("color: #555555;")
        calibration_label.setWordWrap(True)
        self.vars[f"{prefix}_calibration_label"] = calibration_label
        frame_layout.addWidget(calibration_label, len(labels) + 1, 0, 1, 2)

        frame_layout.setColumnStretch(1, 1)
        outer.addWidget(frame)
        outer.addStretch()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_vars(self, cfg: AppConfig) -> None:
        self._set("port", cfg.port)
        self._set("simulation", cfg.simulation)
        self._set("update_hz", str(min(cfg.update_hz, 200.0)))

        for prefix, axis in (("az", cfg.azimuth), ("el", cfg.elevation)):
            label = (
                Path(axis.calibration_file).name
                if axis.calibration_file
                else "Aucun fichier importé"
            )
            self._set(f"{prefix}_calibration_label", label)

            values = {
                "node": axis.node_id,
                "calibration": axis.calibration_file,
                "cpr": axis.counts_per_rev,
                "ratio": axis.gear_ratio,
                "direction": axis.direction,
                "zero": axis.zero_offset_counts,
                "min_counts": "" if axis.min_counts is None else axis.min_counts,
                "max_counts": "" if axis.max_counts is None else axis.max_counts,
                "min": axis.min_deg,
                "max": axis.max_deg,
            }

            for suffix, value in values.items():
                self._set(f"{prefix}_{suffix}", value)

    def _read_config(self) -> AppConfig:
        def axis(prefix: str) -> AxisConfig:
            min_counts_text = str(self._get(f"{prefix}_min_counts")).strip()
            max_counts_text = str(self._get(f"{prefix}_max_counts")).strip()

            return AxisConfig(
                node_id=int(self._get(f"{prefix}_node")),
                calibration_file=str(self._get(f"{prefix}_calibration")),
                counts_per_rev=int(self._get(f"{prefix}_cpr")),
                gear_ratio=float(self._get(f"{prefix}_ratio")),
                direction=int(self._get(f"{prefix}_direction")),
                zero_offset_counts=int(self._get(f"{prefix}_zero")),
                min_counts=int(min_counts_text) if min_counts_text else None,
                max_counts=int(max_counts_text) if max_counts_text else None,
                min_deg=float(self._get(f"{prefix}_min")),
                max_deg=float(self._get(f"{prefix}_max")),
            )

        cfg = AppConfig(
            port=str(self.port_box.currentText()),
            simulation=bool(self._get("simulation")),
            update_hz=float(self._get("update_hz")),
            azimuth=axis("az"),
            elevation=axis("el"),
        )
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _import_calibration(self, prefix: str) -> None:
        if self.controller:
            QMessageBox.critical(
                self,
                "Import impossible",
                "Déconnectez le contrôleur avant de modifier sa calibration",
            )
            return

        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir une calibration Servosila",
            "",
            "Configuration Servosila (*.csv);;Tous les fichiers (*)",
        )

        if not selected:
            return

        try:
            summary = load_servosila_calibration(selected).summary()

            self._set(f"{prefix}_node", summary.node_id)
            self._set(f"{prefix}_cpr", summary.counts_per_rev)
            self._set(f"{prefix}_ratio", summary.gear_ratio)

            current_min_counts = str(self._get(f"{prefix}_min_counts")).strip()
            current_max_counts = str(self._get(f"{prefix}_max_counts")).strip()
            current_zero = int(self._get(f"{prefix}_zero"))
            current_direction = int(self._get(f"{prefix}_direction"))

            has_explicit_work_zone = bool(
                current_min_counts or current_max_counts
            )

            safe_zero = select_import_zero(
                summary.zero_offset_counts,
                current_zero,
                has_explicit_work_zone,
            )
            self._set(f"{prefix}_zero", safe_zero)

            safe_min_counts = (
                int(current_min_counts)
                if current_min_counts
                else summary.min_counts
            )
            safe_max_counts = (
                int(current_max_counts)
                if current_max_counts
                else summary.max_counts
            )

            if safe_min_counts >= safe_max_counts:
                raise ValueError("La Work Zone en comptes est invalide")

            self._set(f"{prefix}_min_counts", safe_min_counts)
            self._set(f"{prefix}_max_counts", safe_max_counts)

            active_min_deg, active_max_deg = work_zone_degrees(
                safe_min_counts,
                safe_max_counts,
                safe_zero,
                summary.counts_per_rev,
                summary.gear_ratio,
                current_direction,
            )

            current_min = float(self._get(f"{prefix}_min"))
            current_max = float(self._get(f"{prefix}_max"))

            safe_min = max(current_min, active_min_deg)
            safe_max = min(current_max, active_max_deg)

            if safe_min >= safe_max:
                raise ValueError(
                    "Les limites actuelles ne recoupent pas la Work Zone importée"
                )

            self._set(f"{prefix}_min", f"{safe_min:.9g}")
            self._set(f"{prefix}_max", f"{safe_max:.9g}")
            self._set(f"{prefix}_calibration", str(summary.path))
            self._set(f"{prefix}_calibration_label", summary.path.name)

            details = [
                f"{summary.parameter_count} paramètres validés",
                f"Node ID : {summary.node_id}",
                f"Encodeur : {summary.encoder_type}, "
                f"{summary.counts_per_rev} comptes/tour",
                f"Réduction : {summary.gear_ratio:g}:1",
                f"Zéro mécanique conservé : {safe_zero} comptes",
                f"Work Zone active : {active_min_deg:.3f}° à {active_max_deg:.3f}°",
                f"Garde en comptes : {safe_min_counts} à {safe_max_counts}",
                f"Limites logicielles conservées : "
                f"{safe_min:.3f}° à {safe_max:.3f}°",
            ]

            if summary.motor_poles is not None:
                details.append(f"Moteur : {summary.motor_poles} pôles")

            if summary.max_current_a is not None:
                details.append(
                    f"Courant continu configuré : {summary.max_current_a:g} A"
                )

            if (
                safe_min_counts < summary.min_counts
                or safe_max_counts > summary.max_counts
            ):
                details.append(
                    "ATTENTION : la garde personnalisée dépasse la Work Zone "
                    "du fichier; vérifiez les limites enregistrées dans le SC-60R."
                )

            QMessageBox.information(
                self,
                "Calibration importée",
                "\n".join(details),
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Calibration incompatible",
                str(exc),
            )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except ImportError:
            ports = []

        current = self._get("port")
        self.port_box.blockSignals(True)
        self.port_box.clear()
        self.port_box.addItems(ports)

        if current:
            self.port_box.setCurrentText(str(current))
        elif ports:
            self.port_box.setCurrentIndex(0)
            self._set("port", ports[0])

        self.port_box.blockSignals(False)

    def _toggle_connection(self) -> None:
        if self.controller:
            self.controller.disconnect()
            self.controller = None
            self.connect_button.setText("Connecter")
            self.status_var.setText("Déconnecté")
            return

        try:
            self._set("port", self.port_box.currentText())
            cfg = self._read_config()
            save_config(cfg, CONFIG_PATH)

            ctl = TurretController(
                cfg,
                self._thread_log,
                self._thread_point,
            )
            ctl.connect()

            self.controller = ctl
            self.connect_button.setText("Déconnecter")
            self.status_var.setText(
                "SIMULATION"
                if cfg.simulation
                else f"Connecté — {cfg.port}"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Connexion impossible",
                str(exc),
            )

    def _require_controller(self) -> TurretController:
        if not self.controller:
            raise RuntimeError("Connectez d'abord le contrôleur")
        return self.controller

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def _start_line(self) -> None:
        try:
            ctl = self._require_controller()

            traj = LineTrajectory(
                ctl.point,
                Point(
                    float(self._get("target_az")),
                    float(self._get("target_el")),
                ),
                float(self._get("line_duration")),
            )

            self.canvas.set_preview(preview_points(traj))
            ctl.start_trajectory(traj)

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Trajectoire invalide",
                str(exc),
            )

    def _ellipse(self) -> EllipseTrajectory:
        return EllipseTrajectory(
            Point(
                float(self._get("center_az")),
                float(self._get("center_el")),
            ),
            float(self._get("radius_az")),
            float(self._get("radius_el")),
            float(self._get("frequency")),
            float(self._get("laps")),
            float(self._get("phase")),
        )

    def _start_ellipse(self) -> None:
        try:
            ctl = self._require_controller()
            ellipse = self._ellipse()

            ellipse_start, _ = ellipse.sample(0.0)

            approach = LineTrajectory(
                ctl.point,
                ellipse_start,
                float(self._get("approach_duration")),
            )

            traj = SequenceTrajectory(approach, ellipse)

            self.canvas.set_preview(preview_points(traj))
            ctl.start_trajectory(traj)

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Trajectoire invalide",
                str(exc),
            )

    def _start_video_test(self) -> None:
        try:
            ctl = self._require_controller()
            name = str(self._get("test_type"))

            center = Point(
                float(self._get("test_center_az")),
                float(self._get("test_center_el")),
            )

            amp_az = abs(float(self._get("test_amplitude_az")))
            amp_el = abs(float(self._get("test_amplitude_el")))
            frequency = float(self._get("test_frequency"))
            cycles = float(self._get("test_cycles"))

            direct_servoscope = name == "Diagnostic direct Servoscope"

            if direct_servoscope:
                repetitions = int(cycles)
                if repetitions != cycles:
                    raise ValueError(
                        "Le nombre de cycles doit être entier pour le diagnostic"
                    )

                motion = DirectDiagnosticTrajectory(
                    center,
                    amp_az,
                    float(self._get("test_step_hold")),
                    repetitions,
                )

            elif name == "Diagnostic 0 / -AZ":
                repetitions = int(cycles)
                if repetitions != cycles:
                    raise ValueError(
                        "Le nombre de cycles doit être entier pour le diagnostic"
                    )

                motion = DiagnosticStepTrajectory(
                    center,
                    amp_az,
                    float(self._get("test_step_transition")),
                    float(self._get("test_step_hold")),
                    repetitions,
                )

            elif name == "Paliers AZ doux":
                repetitions = int(cycles)
                if repetitions != cycles:
                    raise ValueError(
                        "Le nombre de cycles doit être entier pour les paliers"
                    )

                motion = StepSweepTrajectory(
                    center,
                    amp_az,
                    float(self._get("test_step_transition")),
                    float(self._get("test_step_hold")),
                    repetitions,
                )

            elif name == "Balayage AZ doux":
                motion = SmoothSweepTrajectory(
                    center,
                    amp_az,
                    float(self._get("test_smooth_frequency")),
                    cycles,
                )

            elif name == "Balayage AZ":
                motion = SineTrajectory(
                    center, amp_az, 0.0, frequency, cycles
                )

            elif name == "Balayage EL":
                motion = SineTrajectory(
                    center, 0.0, amp_el, frequency, cycles
                )

            elif name == "Diagonale":
                motion = SineTrajectory(
                    center, amp_az, amp_el, frequency, cycles
                )

            elif name == "Cercle":
                motion = EllipseTrajectory(
                    center, amp_az, amp_el, frequency, cycles
                )

            else:
                motion = ButterflyTrajectory(
                    center, amp_az, amp_el, frequency, cycles
                )

            compensated = (
                bool(self._get("test_compensate_az"))
                and amp_az > 0
                and name not in (
                    "Diagnostic direct Servoscope",
                    "Diagnostic 0 / -AZ",
                    "Balayage AZ doux",
                    "Paliers AZ doux",
                )
            )

            if compensated:
                motion = AzimuthCompensatedTrajectory(
                    motion,
                    center.az,
                )

            if direct_servoscope:
                trajectory = motion
            else:
                start, _ = motion.sample(0.0)
                trajectory = SequenceTrajectory(
                    LineTrajectory(
                        ctl.point,
                        start,
                        float(self._get("test_approach")),
                    ),
                    motion,
                )

            self.canvas.set_preview(preview_points(trajectory, 600))

            log_name = (
                f"{name} AZ compensé"
                if compensated
                else name
            )

            path = ctl.start_test(trajectory, log_name)

            self.status_var.setText(f"ESSAI — {log_name}")
            self._thread_log(f"CSV créé : {path}")

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Essai invalide",
                str(exc),
            )

    def _hold(self) -> None:
        try:
            self._require_controller().hold(
                Point(
                    float(self._get("target_az")),
                    float(self._get("target_el")),
                )
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Commande invalide",
                str(exc),
            )

    def _set_reference(self) -> None:
        try:
            self._require_controller().set_reference(
                Point(
                    float(self._get("target_az")),
                    float(self._get("target_el")),
                )
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Référence invalide",
                str(exc),
            )

    def _set_midpoint_zeros(self) -> None:
        if self.controller:
            QMessageBox.critical(
                self,
                "Configuration active",
                "Déconnectez d'abord le contrôleur. "
                "Les paramètres d'axe sont chargés lors de la connexion.",
            )
            return

        try:
            zeros: dict[str, int] = {}

            for prefix, name in (
                ("az", "Azimut"),
                ("el", "Élévation"),
            ):
                minimum = int(
                    str(self._get(f"{prefix}_min_counts")).strip()
                )
                maximum = int(
                    str(self._get(f"{prefix}_max_counts")).strip()
                )

                if minimum >= maximum:
                    raise ValueError(
                        f"Work Zone {name} invalide"
                    )

                zero = round((minimum + maximum) / 2)
                self._set(f"{prefix}_zero", zero)
                zeros[name] = zero

            QMessageBox.information(
                self,
                "Zéros calculés",
                (
                    f"Azimut : {zeros['Azimut']} comptes\n"
                    f"Élévation : {zeros['Élévation']} comptes\n\n"
                    "Ces valeurs ne seront utilisées qu'après la prochaine "
                    "connexion. Vérifiez que la pose mécanique centrale doit "
                    "bien correspondre à (0°, 0°)."
                ),
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Calcul impossible",
                str(exc),
            )

    # ------------------------------------------------------------------
    # Stop / emergency
    # ------------------------------------------------------------------

    def _stop(self) -> None:
        if self.controller:
            self.controller.stop()

    def _off(self) -> None:
        if self.controller:
            self.controller.emergency_off()

    # ------------------------------------------------------------------
    # Thread -> GUI event queue
    # ------------------------------------------------------------------

    def _thread_log(self, text: str) -> None:
        self.events.put(("log", text))

    def _thread_point(self, point: Point) -> None:
        self.events.put(("point", point))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()

                if kind == "log":
                    self.log_text.appendPlainText(str(value))

                elif kind == "point":
                    self.canvas.set_current_point(value)

        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.timer.stop()

        if self.controller:
            self.controller.disconnect()
            self.controller = None

        event.accept()


def main_turret() -> None:
    app = QApplication([])

    # Optional: a compact native Qt style.
    app.setStyle("Fusion")

    window = TurretApp()
    window.show()

    app.exec()


if __name__ == "__main__":
    main_turret()
