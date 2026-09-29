from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import queue
import threading
import time
from typing import Callable

from Camera_turret.turret_control.model import AppConfig
from Camera_turret.turret_control.protocol import (
    OFF_COMMAND, STOP_COMMAND, CanFrame, control_frame, decode_sc60r_tpdo,
    decode_slcan_line, servo_frame,
)
from Camera_turret.turret_control.trajectories import Point, Trajectory, reference_point, trajectory_duration


LogFn = Callable[[str], None]
PointFn = Callable[[Point], None]
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
MAX_CONTROL_HZ = 200.0
HOLD_REFRESH_HZ = 50.0
SERIAL_CAPACITY_FRACTION = 0.80
UART_BITS_PER_BYTE = 10.0


def safe_update_hz(baudrate: int, requested_hz: float) -> float:
    """Cadence sûre pour deux commandes SLCAN par cycle, avec 20 % de marge."""
    frame_bytes = len(servo_frame(1, 0))
    serial_limit = (
        baudrate / UART_BITS_PER_BYTE * SERIAL_CAPACITY_FRACTION / (2 * frame_bytes)
    )
    return max(1.0, min(float(requested_hz), MAX_CONTROL_HZ, serial_limit))


def servo_command_due(
    last_sent: tuple[int, float] | None,
    counts: int,
    now: float,
    refresh_hz: float = HOLD_REFRESH_HZ,
) -> bool:
    """Envoie immédiatement un changement, sinon un rappel à cadence réduite."""
    if refresh_hz <= 0:
        raise ValueError("La cadence de maintien doit être positive")
    if last_sent is None or counts != last_sent[0]:
        return True
    return now - last_sent[1] >= 1.0 / refresh_hz - 1e-6


@dataclass
class AxisTelemetry:
    values: dict[str, int | float] = field(default_factory=dict)
    updated_at: float | None = None

    def update(self, values: dict[str, int | float], now: float) -> None:
        self.values.update(values)
        self.updated_at = now


@dataclass
class CsvTarget:
    file: object
    writer: csv.DictWriter


class SlcanTransport:
    def __init__(self, port: str, baudrate: int, simulation: bool, log: LogFn):
        self.port = port
        self.baudrate = baudrate
        self.simulation = simulation
        self.log = log
        self._serial = None
        self._lock = threading.Lock()
        self._receive_buffer = b""

    def open(self) -> None:
        if self.simulation:
            self.log("Mode simulation connecté (aucune trame envoyée au matériel).")
            return
        if not self.port:
            raise ValueError("Sélectionnez un port COM")
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial absent : exécutez pip install -r requirements.txt") from exc
        self._serial = serial.Serial(
            self.port, self.baudrate, bytesize=8, parity="N", stopbits=1,
            timeout=0, write_timeout=0.25,
        )
        self.log(f"Connecté à {self.port} à {self.baudrate} bauds.")

    def send(self, frame: bytes) -> None:
        if self.simulation:
            return
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                raise RuntimeError("Port série non connecté")
            self._serial.write(frame)

    def read_frames(self) -> list[CanFrame]:
        if self.simulation:
            return []
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                return []
            waiting = self._serial.in_waiting
            if waiting:
                self._receive_buffer += self._serial.read(waiting)
            parts = self._receive_buffer.split(b"\r")
            self._receive_buffer = parts.pop()
        frames = []
        for part in parts:
            frame = decode_slcan_line(part)
            if frame is not None:
                frames.append(frame)
        return frames

    def out_waiting(self) -> int:
        if self.simulation:
            return 0
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                return 0
            return int(self._serial.out_waiting)

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                self._serial.close()
                self._serial = None


class TurretController:
    def __init__(self, config: AppConfig, log: LogFn, on_point: PointFn):
        config.validate()
        self.config = config
        self.log = log
        self.on_point = on_point
        self.transport = SlcanTransport(config.port, config.baudrate, config.simulation, log)
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._control_hz = safe_update_hz(config.baudrate, config.update_hz)
        self._last_tick_at: float | None = None
        self._last_servo_sent: dict[int, tuple[int, float]] = {}
        self._state = "stop"
        self._point = Point(0.0, 0.0)
        self._reference_point = self._point
        self._reference_initialized = config.simulation
        self._trajectory: Trajectory | None = None
        self._trajectory_start = 0.0
        self._last_error = ""
        self._telemetry = {
            config.azimuth.node_id: AxisTelemetry(),
            config.elevation.node_id: AxisTelemetry(),
        }
        self._csv_target: CsvTarget | None = None
        self._csv_lock = threading.Lock()
        self._csv_queue: queue.Queue[tuple[str, CsvTarget | None, object | None]] = queue.Queue()
        self._logger_quit = threading.Event()
        self._logger_thread: threading.Thread | None = None
        self._log_started = 0.0
        self._log_stop_at: float | None = None
        self._test_name = ""

    @property
    def point(self) -> Point:
        with self._lock:
            return self._point

    def connect(self) -> None:
        self.transport.open()
        self._logger_thread = threading.Thread(
            target=self._run_logger, name="turret-csv", daemon=True,
        )
        self._logger_thread.start()
        self._thread = threading.Thread(target=self._run, name="turret-control", daemon=True)
        self._thread.start()
        if self._control_hz < self.config.update_hz:
            self.log(
                f"Cadence demandée {self.config.update_hz:.1f} Hz limitée à "
                f"{self._control_hz:.1f} Hz pour la liaison série."
            )

    def set_reference(self, point: Point) -> None:
        """Déclare la position actuelle sans envoyer de commande de mouvement."""
        self.config.azimuth.degrees_to_counts(point.az)
        self.config.elevation.degrees_to_counts(point.el)
        with self._lock:
            self._point = point
            self._reference_point = point
            self._reference_initialized = True
        self.on_point(point)
        self.log(f"Référence actuelle définie : AZ={point.az:.3f}°, EL={point.el:.3f}° (aucun mouvement).")

    def start_trajectory(self, trajectory: Trajectory) -> None:
        if not self._reference_initialized:
            raise RuntimeError("Définissez d'abord la position mécanique actuelle de la tourelle")
        duration = trajectory_duration(trajectory)
        for i in range(360):
            point, _ = trajectory.sample(duration * i / 359)
            self.config.azimuth.degrees_to_counts(point.az)
            self.config.elevation.degrees_to_counts(point.el)
        with self._lock:
            self._trajectory = trajectory
            self._trajectory_start = time.perf_counter()
            self._state = "trajectory"
        self.log("Trajectoire démarrée.")

    def start_test(self, trajectory: Trajectory, test_name: str) -> Path:
        self.start_trajectory(trajectory)
        try:
            path = self._open_csv(test_name)
        except Exception:
            self.stop()
            raise
        self.log(f"Essai « {test_name} » : journal {path}")
        return path

    def hold(self, point: Point) -> None:
        if not self._reference_initialized:
            raise RuntimeError("Définissez d'abord la position mécanique actuelle de la tourelle")
        self.config.azimuth.degrees_to_counts(point.az)
        self.config.elevation.degrees_to_counts(point.el)
        with self._lock:
            self._point = point
            self._reference_point = point
            self._trajectory = None
            self._state = "hold"
        self.log(f"Maintien AZ={point.az:.3f}°, EL={point.el:.3f}°.")

    def stop(self) -> None:
        with self._lock:
            self._trajectory = None
            self._state = "stop"
        self._close_csv()
        self.log("STOP demandé sur les deux axes.")

    def emergency_off(self) -> None:
        with self._lock:
            self._trajectory = None
            self._state = "off"
        self._close_csv()
        self.log("ARRÊT D'URGENCE logiciel : étages de puissance désactivés.")

    def disconnect(self) -> None:
        self.stop()
        try:
            for _ in range(3):
                self.transport.send(control_frame(self.config.azimuth.node_id, STOP_COMMAND))
                self.transport.send(control_frame(self.config.elevation.node_id, STOP_COMMAND))
        except Exception as exc:
            self.log(f"Avertissement pendant l'arrêt : {exc}")
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._logger_quit.set()
        self._csv_queue.put(("wake", None, None))
        if self._logger_thread and self._logger_thread.is_alive():
            self._logger_thread.join(timeout=1.0)
        self.transport.close()
        self.log("Déconnecté.")

    def _open_csv(self, test_name: str) -> Path:
        self._close_csv()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in test_name).strip("_") or "essai"
        path = LOG_DIR / f"test_{safe}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        fields = [
            "timestamp_iso", "elapsed_s", "test_name", "state",
            "reference_az_deg", "reference_el_deg",
            "command_az_deg", "command_el_deg", "command_az_counts", "command_el_counts",
            "actual_az_counts", "actual_el_counts", "error_az_counts", "error_el_counts",
            "az_speed_hz", "el_speed_hz", "az_phase_a_a", "az_phase_b_a",
            "el_phase_a_a", "el_phase_b_a", "az_bus_voltage_v", "el_bus_voltage_v",
            "az_fault_bits", "el_fault_bits", "az_operation_mode", "el_operation_mode",
            "az_commutation_mode", "el_commutation_mode", "az_telemetry_age_ms", "el_telemetry_age_ms",
            "requested_update_hz", "scheduled_update_hz", "control_interval_ms",
            "serial_out_waiting_bytes", "az_command_sent", "el_command_sent",
            "command_policy",
        ]
        file = path.open("w", newline="", encoding="utf-8-sig")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        file.flush()
        with self._csv_lock:
            self._csv_target = CsvTarget(file, writer)
            self._log_started = time.perf_counter()
            self._log_stop_at = None
            self._test_name = test_name
        return path

    def _close_csv(self) -> None:
        with self._csv_lock:
            target = self._csv_target
            self._csv_target = None
            self._log_stop_at = None
        if target is not None:
            closed = threading.Event()
            self._csv_queue.put(("close", target, closed))
            if self._logger_thread is not None and self._logger_thread.is_alive():
                closed.wait(timeout=1.0)
            elif not target.file.closed:
                target.file.close()

    def _write_csv(
        self, now: float, state: str, point: Point, reference: Point,
        control_interval_s: float | None, az_command_sent: bool, el_command_sent: bool,
    ) -> None:
        with self._csv_lock:
            target = self._csv_target
            log_started = self._log_started
            test_name = self._test_name
        if target is None:
            return
        az_count = self.config.azimuth.degrees_to_counts(point.az)
        el_count = self.config.elevation.degrees_to_counts(point.el)
        az = self._telemetry[self.config.azimuth.node_id]
        el = self._telemetry[self.config.elevation.node_id]
        av, ev = az.values, el.values
        actual_az, actual_el = av.get("work_zone_count"), ev.get("work_zone_count")

        def age(item: AxisTelemetry) -> float | str:
            return "" if item.updated_at is None else round((now - item.updated_at) * 1000, 3)

        row = {
            "timestamp_iso": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_s": round(now - log_started, 6), "test_name": test_name, "state": state,
            "reference_az_deg": reference.az, "reference_el_deg": reference.el,
            "command_az_deg": point.az, "command_el_deg": point.el,
            "command_az_counts": az_count, "command_el_counts": el_count,
            "actual_az_counts": actual_az if actual_az is not None else "",
            "actual_el_counts": actual_el if actual_el is not None else "",
            "error_az_counts": az_count - int(actual_az) if actual_az is not None else "",
            "error_el_counts": el_count - int(actual_el) if actual_el is not None else "",
            "az_speed_hz": av.get("electrical_speed_hz", ""), "el_speed_hz": ev.get("electrical_speed_hz", ""),
            "az_phase_a_a": av.get("phase_a_current_a", ""), "az_phase_b_a": av.get("phase_b_current_a", ""),
            "el_phase_a_a": ev.get("phase_a_current_a", ""), "el_phase_b_a": ev.get("phase_b_current_a", ""),
            "az_bus_voltage_v": av.get("bus_voltage_v", ""), "el_bus_voltage_v": ev.get("bus_voltage_v", ""),
            "az_fault_bits": av.get("fault_bits", ""), "el_fault_bits": ev.get("fault_bits", ""),
            "az_operation_mode": av.get("operation_mode", ""), "el_operation_mode": ev.get("operation_mode", ""),
            "az_commutation_mode": av.get("commutation_mode", ""), "el_commutation_mode": ev.get("commutation_mode", ""),
            "az_telemetry_age_ms": age(az), "el_telemetry_age_ms": age(el),
            "requested_update_hz": self.config.update_hz,
            "scheduled_update_hz": self._control_hz,
            "control_interval_ms": (
                "" if control_interval_s is None else round(control_interval_s * 1000.0, 6)
            ),
            "serial_out_waiting_bytes": self.transport.out_waiting(),
            "az_command_sent": int(az_command_sent),
            "el_command_sent": int(el_command_sent),
            "command_policy": "refresh_50_hz",
        }
        self._csv_queue.put(("row", target, row))

    def _run_logger(self) -> None:
        open_targets: dict[int, CsvTarget] = {}
        last_flush = time.perf_counter()
        while not self._logger_quit.is_set() or not self._csv_queue.empty():
            try:
                action, target, payload = self._csv_queue.get(timeout=0.05)
            except queue.Empty:
                action, target, payload = "idle", None, None
            try:
                if action == "row" and target is not None:
                    target.writer.writerow(payload)
                    open_targets[id(target)] = target
                elif action == "close" and target is not None:
                    target.file.flush()
                    target.file.close()
                    open_targets.pop(id(target), None)
                    if isinstance(payload, threading.Event):
                        payload.set()
                now = time.perf_counter()
                if now - last_flush >= 0.25:
                    for active in open_targets.values():
                        active.file.flush()
                    last_flush = now
            except Exception as exc:
                self.log(f"ERREUR journal CSV : {exc}")
                if action == "close" and isinstance(payload, threading.Event):
                    payload.set()
        for target in open_targets.values():
            try:
                target.file.flush()
                target.file.close()
            except Exception:
                pass

    def _run(self) -> None:
        period = 1.0 / self._control_hz
        deadline = time.perf_counter()
        while not self._quit.is_set():
            try:
                self._tick()
                self._last_error = ""
            except Exception as exc:
                message = str(exc)
                if message != self._last_error:
                    self.log(f"ERREUR communication : {message}")
                    self._last_error = message
                with self._lock:
                    self._state = "stop"
            deadline += period
            now = time.perf_counter()
            if deadline <= now:
                # Ne jamais rattraper un retard par une salve de commandes.
                deadline = now + period
            while not self._quit.is_set():
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                if remaining > 0.0004:
                    time.sleep(remaining - 0.0003)

    def _tick(self) -> None:
        now = time.perf_counter()
        control_interval_s = None if self._last_tick_at is None else now - self._last_tick_at
        self._last_tick_at = now
        for frame in self.transport.read_frames():
            update = decode_sc60r_tpdo(frame)
            if update is not None and update.node_id in self._telemetry:
                self._telemetry[update.node_id].update(update.values, now)

        with self._lock:
            state = self._state
            point = self._point
            trajectory = self._trajectory
            started = self._trajectory_start
            reference = self._reference_point

        if state == "trajectory" and trajectory is not None:
            elapsed = now - started
            feedback_sampler = getattr(trajectory, "sample_with_feedback", None)
            if callable(feedback_sampler):
                az_telemetry = self._telemetry[self.config.azimuth.node_id]
                measured_count = az_telemetry.values.get("work_zone_count")
                telemetry_fresh = (
                    az_telemetry.updated_at is not None
                    and now - az_telemetry.updated_at <= 0.25
                )
                measured_az = None
                if measured_count is not None and telemetry_fresh:
                    measured_az = self.config.azimuth.counts_to_degrees(measured_count)
                point, finished = feedback_sampler(elapsed, measured_az)
                current_reference = getattr(trajectory, "current_reference", None)
                reference = current_reference() if callable(current_reference) else point
            else:
                point, finished = trajectory.sample(elapsed)
                reference = reference_point(trajectory, elapsed)
            with self._lock:
                self._point = point
                self._reference_point = reference
                if finished:
                    aborted_reason = getattr(trajectory, "aborted_reason", None)
                    if aborted_reason:
                        self.log(f"Essai adaptatif interrompu : {aborted_reason}")
                    self._trajectory = None
                    self._state = "hold"
                    if self._csv_target is not None:
                        self._log_stop_at = now + 1.0
            self.on_point(point)
            state = "hold" if finished else "trajectory"

        az_node = self.config.azimuth.node_id
        el_node = self.config.elevation.node_id
        az_command_sent = False
        el_command_sent = False
        if state in ("trajectory", "hold"):
            az_counts = self.config.azimuth.degrees_to_counts(point.az)
            el_counts = self.config.elevation.degrees_to_counts(point.el)
            if servo_command_due(self._last_servo_sent.get(az_node), az_counts, now):
                self.transport.send(servo_frame(az_node, az_counts))
                self._last_servo_sent[az_node] = (az_counts, now)
                az_command_sent = True
            if servo_command_due(self._last_servo_sent.get(el_node), el_counts, now):
                self.transport.send(servo_frame(el_node, el_counts))
                self._last_servo_sent[el_node] = (el_counts, now)
                el_command_sent = True
        elif state == "off":
            self.transport.send(control_frame(az_node, OFF_COMMAND))
            self.transport.send(control_frame(el_node, OFF_COMMAND))
        else:
            self.transport.send(control_frame(az_node, STOP_COMMAND))
            self.transport.send(control_frame(el_node, STOP_COMMAND))

        self._write_csv(
            now, state, point, reference, control_interval_s,
            az_command_sent, el_command_sent,
        )
        if self._log_stop_at is not None and now >= self._log_stop_at:
            self._close_csv()
            self.log("Journal d'essai terminé (1 s de stabilisation enregistrée).")
