from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Union


def _smootherstep7(u: float) -> float:
    """Profil 0→1 dont vitesse, accélération et jerk sont nuls aux extrémités."""
    return 35 * u**4 - 84 * u**5 + 70 * u**6 - 20 * u**7


@dataclass(frozen=True)
class Point:
    az: float
    el: float


@dataclass
class LineTrajectory:
    start: Point
    end: Point
    duration: float

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        if self.duration <= 0:
            raise ValueError("La durée doit être positive")
        u = min(max(elapsed / self.duration, 0.0), 1.0)
        # Profil d'ordre 7 : vitesse, accélération et jerk nuls aux extrémités.
        s = _smootherstep7(u)
        point = Point(
            self.start.az + (self.end.az - self.start.az) * s,
            self.start.el + (self.end.el - self.start.el) * s,
        )
        return point, u >= 1.0


@dataclass
class EllipseTrajectory:
    center: Point
    radius_az: float
    radius_el: float
    frequency_hz: float
    laps: float = 1.0
    phase_deg: float = 0.0

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        if self.frequency_hz <= 0 or self.laps <= 0:
            raise ValueError("La fréquence et le nombre de tours doivent être positifs")
        max_time = self.laps / self.frequency_hz
        t = min(max(elapsed, 0.0), max_time)
        angle = 2 * math.pi * self.frequency_hz * t + math.radians(self.phase_deg)
        point = Point(
            self.center.az + self.radius_az * math.cos(angle),
            self.center.el + self.radius_el * math.sin(angle),
        )
        return point, elapsed >= max_time


@dataclass
class SineTrajectory:
    """Balayage sinusoïdal d'un axe, ou des deux axes en phase."""

    center: Point
    amplitude_az: float
    amplitude_el: float
    frequency_hz: float
    cycles: float = 1.0

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        if self.frequency_hz <= 0 or self.cycles <= 0:
            raise ValueError("La fréquence et le nombre de cycles doivent être positifs")
        max_time = self.cycles / self.frequency_hz
        t = min(max(elapsed, 0.0), max_time)
        value = math.sin(2 * math.pi * self.frequency_hz * t)
        return Point(
            self.center.az + self.amplitude_az * value,
            self.center.el + self.amplitude_el * value,
        ), elapsed >= max_time


@dataclass
class SmoothSweepTrajectory:
    """Aller-retour d'ordre 7, sans vitesse, accélération ni jerk aux extrémités."""

    center: Point
    amplitude_az: float
    frequency_hz: float
    cycles: float = 1.0

    @property
    def duration(self) -> float:
        if self.frequency_hz <= 0 or self.cycles <= 0:
            raise ValueError("La fréquence et le nombre de cycles doivent être positifs")
        return self.cycles / self.frequency_hz

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        phase = (t * self.frequency_hz) % 1.0
        if phase < 0.5:
            u = phase * 2.0
            normalized = -1.0 + 2.0 * _smootherstep7(u)
        else:
            u = (phase - 0.5) * 2.0
            normalized = 1.0 - 2.0 * _smootherstep7(u)
        return Point(
            self.center.az + self.amplitude_az * normalized,
            self.center.el,
        ), elapsed >= duration


@dataclass
class StepSweepTrajectory:
    """Paliers AZ répétés, reliés par des transitions d'ordre 7."""

    center: Point
    amplitude_az: float
    transition_s: float = 3.0
    hold_s: float = 3.0
    repetitions: int = 3

    # Une répétition commence et finit à -amplitude.
    _targets = (-0.5, 0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0)

    @property
    def duration(self) -> float:
        if self.transition_s <= 0 or self.hold_s <= 0:
            raise ValueError("Les durées de transition et de maintien doivent être positives")
        if self.repetitions <= 0 or int(self.repetitions) != self.repetitions:
            raise ValueError("Le nombre de répétitions doit être un entier positif")
        return self.hold_s + len(self._targets) * self.repetitions * (
            self.transition_s + self.hold_s
        )

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        start_az = self.center.az - self.amplitude_az
        if t < self.hold_s:
            return Point(start_az, self.center.el), elapsed >= duration

        block_s = self.transition_s + self.hold_s
        movement_count = len(self._targets) * self.repetitions
        local = t - self.hold_s
        movement = min(int(local / block_s), movement_count - 1)
        within = local - movement * block_s
        target_index = movement % len(self._targets)
        start_normalized = -1.0 if target_index == 0 else self._targets[target_index - 1]
        end_normalized = self._targets[target_index]
        if within < self.transition_s:
            u = within / self.transition_s
            normalized = start_normalized + (
                end_normalized - start_normalized
            ) * _smootherstep7(u)
        else:
            normalized = end_normalized
        return Point(
            self.center.az + self.amplitude_az * normalized,
            self.center.el,
        ), elapsed >= duration


@dataclass
class DiagnosticStepTrajectory:
    """Diagnostic répété entre le centre AZ et centre-amplitude."""

    center: Point
    amplitude_az: float
    transition_s: float = 3.0
    hold_s: float = 5.0
    repetitions: int = 5

    @property
    def duration(self) -> float:
        if self.transition_s <= 0 or self.hold_s <= 0:
            raise ValueError("Les durées de transition et de maintien doivent être positives")
        if self.repetitions <= 0 or int(self.repetitions) != self.repetitions:
            raise ValueError("Le nombre de répétitions doit être un entier positif")
        # Maintien initial au centre, puis deux transitions et deux maintiens par cycle.
        return self.hold_s + 2 * self.repetitions * (self.transition_s + self.hold_s)

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        if t < self.hold_s:
            return self.center, elapsed >= duration

        block_s = self.transition_s + self.hold_s
        movement_count = 2 * self.repetitions
        local = t - self.hold_s
        movement = min(int(local / block_s), movement_count - 1)
        within = local - movement * block_s
        start_normalized = 0.0 if movement % 2 == 0 else -1.0
        end_normalized = -1.0 if movement % 2 == 0 else 0.0
        if within < self.transition_s:
            u = within / self.transition_s
            normalized = start_normalized + (
                end_normalized - start_normalized
            ) * _smootherstep7(u)
        else:
            normalized = end_normalized
        return Point(
            self.center.az + self.amplitude_az * normalized,
            self.center.el,
        ), elapsed >= duration


@dataclass
class DirectDiagnosticTrajectory:
    """Diagnostic type Servoscope : une cible directe, puis aucune interpolation."""

    center: Point
    amplitude_az: float
    hold_s: float = 3.0
    repetitions: int = 3

    @property
    def duration(self) -> float:
        if self.hold_s <= 0:
            raise ValueError("La durée de maintien doit être positive")
        if self.repetitions <= 0 or int(self.repetitions) != self.repetitions:
            raise ValueError("Le nombre de répétitions doit être un entier positif")
        # Maintien initial au centre, puis -AZ et 0 pour chaque cycle.
        return self.hold_s * (1 + 2 * self.repetitions)

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        block = min(int(t / self.hold_s), 2 * self.repetitions)
        at_negative_target = block > 0 and block % 2 == 1
        return Point(
            self.center.az - self.amplitude_az if at_negative_target else self.center.az,
            self.center.el,
        ), elapsed >= duration


@dataclass
class ButterflyTrajectory:
    """Deux symboles infini successifs, tournés de +45° puis -45°."""

    center: Point
    amplitude_az: float
    amplitude_el: float
    frequency_hz: float
    cycles_per_orientation: float = 1.0

    @property
    def duration(self) -> float:
        if self.frequency_hz <= 0 or self.cycles_per_orientation <= 0:
            raise ValueError("La fréquence et le nombre de cycles doivent être positifs")
        return 2 * self.cycles_per_orientation / self.frequency_hz

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        half = duration / 2
        local_t = t if t <= half else t - half
        angle = 2 * math.pi * self.frequency_hz * local_t
        u = math.sin(angle)
        v = 0.5 * math.sin(2 * angle)
        # La seconde moitié est le même « infini » réfléchi : les deux
        # orientations forment le papillon en X et se rejoignent au centre.
        sign = 1.0 if t <= half else -1.0
        root2 = math.sqrt(2.0)
        x = (u - sign * v) / root2
        y = (sign * u + v) / root2
        return Point(
            self.center.az + self.amplitude_az * x,
            self.center.el + self.amplitude_el * y,
        ), elapsed >= duration


@dataclass
class AzimuthCompensatedTrajectory:
    """Avance temporelle AZ bornée, appliquée à une trajectoire de test."""

    base: EllipseTrajectory | SineTrajectory | ButterflyTrajectory
    center_az: float
    delay_s: float = 0.438
    time_constant_s: float = 0.0
    gain: float = 1.0
    max_correction_deg: float = 1.5
    fade_s: float = 2.0

    @property
    def duration(self) -> float:
        return trajectory_duration(self.base)

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        if self.delay_s < 0 or self.time_constant_s < 0:
            raise ValueError("Le délai et la constante de temps AZ doivent être positifs")
        if self.gain <= 0 or self.max_correction_deg <= 0 or self.fade_s < 0:
            raise ValueError("Les paramètres de compensation AZ sont invalides")
        duration = self.duration
        t = min(max(elapsed, 0.0), duration)
        raw, _ = self.base.sample(t)
        future_t = min(t + self.delay_s, duration)
        future, _ = self.base.sample(future_t)

        velocity_az = 0.0
        if self.time_constant_s > 0:
            # Le chemin dérivé reste disponible pour des essais explicites,
            # mais il est entièrement contourné par le réglage sûr par défaut.
            h = min(0.005, max(duration / 10_000.0, 0.0001))
            t1, t2 = max(0.0, future_t - h), min(duration, future_t + h)
            before, _ = self.base.sample(t1)
            after, _ = self.base.sample(t2)
            velocity_az = 0.0 if t2 == t1 else (after.az - before.az) / (t2 - t1)
        compensated_az = self.center_az + (
            future.az - self.center_az + self.time_constant_s * velocity_az
        ) / self.gain
        correction = max(
            -self.max_correction_deg,
            min(self.max_correction_deg, compensated_az - raw.az),
        )

        # Rampe lissée au début et à la fin : aucune discontinuité avec
        # l'approche progressive ni avec le maintien final. La rampe de fin
        # atteint zéro avant que l'échantillonnage futur touche la fin de la
        # trajectoire, ce qui évite un changement brutal de pente.
        if self.fade_s > 0:
            edge = min(t, max(duration - self.delay_s - t, 0.0))
            u = min(max(edge / self.fade_s, 0.0), 1.0)
            # Quintique « smootherstep » : vitesse et accélération de la
            # correction sont nulles aux deux extrémités de la rampe.
            blend = u**3 * (10.0 - 15.0 * u + 6.0 * u**2)
        else:
            blend = 1.0
        return Point(raw.az + blend * correction, raw.el), elapsed >= duration


@dataclass
class SequenceTrajectory:
    first: LineTrajectory
    second: (
        EllipseTrajectory | SineTrajectory | SmoothSweepTrajectory
        | StepSweepTrajectory | DiagnosticStepTrajectory | DirectDiagnosticTrajectory
        | ButterflyTrajectory | AzimuthCompensatedTrajectory
    )

    @property
    def duration(self) -> float:
        return self.first.duration + trajectory_duration(self.second)

    def sample(self, elapsed: float) -> tuple[Point, bool]:
        if elapsed < self.first.duration:
            point, _ = self.first.sample(elapsed)
            return point, False
        return self.second.sample(elapsed - self.first.duration)


Trajectory = Union[
    LineTrajectory,
    EllipseTrajectory,
    SineTrajectory,
    SmoothSweepTrajectory,
    StepSweepTrajectory,
    DiagnosticStepTrajectory,
    DirectDiagnosticTrajectory,
    ButterflyTrajectory,
    AzimuthCompensatedTrajectory,
    SequenceTrajectory,
]


def trajectory_duration(trajectory: Trajectory) -> float:
    if isinstance(trajectory, LineTrajectory):
        return trajectory.duration
    if isinstance(trajectory, EllipseTrajectory):
        return trajectory.laps / trajectory.frequency_hz
    if isinstance(trajectory, SineTrajectory):
        return trajectory.cycles / trajectory.frequency_hz
    if isinstance(trajectory, SmoothSweepTrajectory):
        return trajectory.duration
    if isinstance(trajectory, StepSweepTrajectory):
        return trajectory.duration
    if isinstance(trajectory, DiagnosticStepTrajectory):
        return trajectory.duration
    if isinstance(trajectory, DirectDiagnosticTrajectory):
        return trajectory.duration
    if isinstance(trajectory, ButterflyTrajectory):
        return trajectory.duration
    return trajectory.duration


def reference_point(trajectory: Trajectory, elapsed: float) -> Point:
    """Consigne géométrique avant compensation, destinée au journal CSV."""
    if isinstance(trajectory, AzimuthCompensatedTrajectory):
        return trajectory.base.sample(elapsed)[0]
    if isinstance(trajectory, SequenceTrajectory):
        if elapsed < trajectory.first.duration:
            return trajectory.first.sample(elapsed)[0]
        return reference_point(trajectory.second, elapsed - trajectory.first.duration)
    return trajectory.sample(elapsed)[0]


def preview_points(trajectory: Trajectory, count: int = 300) -> list[Point]:
    duration = trajectory_duration(trajectory)
    return [trajectory.sample(duration * i / max(1, count - 1))[0] for i in range(count)]
