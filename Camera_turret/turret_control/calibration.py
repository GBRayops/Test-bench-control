from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CalibrationParameter:
    section: str
    name: str
    value: str
    unit: str
    data_type: str
    index: str
    subindex: str


@dataclass(frozen=True)
class CalibrationSummary:
    path: Path
    parameter_count: int
    node_id: int
    encoder_type: str
    counts_per_rev: int
    gear_ratio: float
    zero_offset_counts: int
    min_counts: int
    max_counts: int
    min_deg: float
    max_deg: float
    motor_poles: int | None
    max_current_a: float | None
    phase_resistance_ohm: float | None
    phase_inductance_h: float | None


def select_import_zero(
    imported_zero: int,
    current_zero: int,
    has_explicit_work_zone: bool,
) -> int:
    """Conserve un zéro mécanique déjà établi lors d'une mise à jour."""
    if has_explicit_work_zone or current_zero != 0:
        return current_zero
    return imported_zero


def work_zone_degrees(
    min_counts: int,
    max_counts: int,
    zero_offset_counts: int,
    counts_per_rev: int,
    gear_ratio: float,
    direction: int = 1,
) -> tuple[float, float]:
    """Convertit une Work Zone en limites mécaniques ordonnées."""
    if min_counts >= max_counts:
        raise ValueError("Limites Work Zone invalides")
    if counts_per_rev <= 0 or gear_ratio <= 0:
        raise ValueError("Résolution d'encodeur ou rapport de réduction invalide")
    if direction not in (-1, 1):
        raise ValueError("La direction doit valoir -1 ou 1")
    scale = counts_per_rev * gear_ratio / 360.0
    endpoints = (
        (min_counts - zero_offset_counts) / (direction * scale),
        (max_counts - zero_offset_counts) / (direction * scale),
    )
    return min(endpoints), max(endpoints)


ENCODER_SECTIONS = {
    1: ("Quadrature", "Peripheral: Quadrature Encoder"),
    2: ("SSI/BISS-C", "Peripheral: SSI/BISS-C Encoder"),
    3: ("SPI", "Peripheral: SPI Encoder"),
    4: ("PWM", "Peripheral: PWM Encoder"),
    5: ("RS485", "Peripheral: RS485 Encoder"),
    6: ("Dual", "Peripheral: Dual Encoder"),
}


class ServosilaCalibration:
    def __init__(self, path: Path, parameters: list[CalibrationParameter]):
        self.path = path
        self.parameters = parameters
        self._by_key = {
            (self._normal(p.section), self._normal(p.name)): p for p in parameters
        }

    @staticmethod
    def _normal(text: str) -> str:
        return text.lstrip("#").strip().casefold()

    def get(self, section: str, name: str) -> CalibrationParameter:
        key = (self._normal(section), self._normal(name))
        try:
            return self._by_key[key]
        except KeyError as exc:
            raise ValueError(f"Paramètre absent : {section} / {name}") from exc

    def number(self, section: str, name: str) -> float:
        raw = self.get(section, name).value.strip().replace(",", ".")
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"Valeur numérique invalide pour {section} / {name}: {raw}") from exc

    def optional_number(self, section: str, name: str) -> float | None:
        try:
            return self.number(section, name)
        except ValueError:
            return None

    def summary(self) -> CalibrationSummary:
        node_id = int(self.number("Networking", "CANopen: Node ID"))
        servo_encoder_code = int(self.number("Datasheet", "Servo Encoder"))
        if servo_encoder_code in ENCODER_SECTIONS:
            encoder_type, encoder_section = ENCODER_SECTIONS[servo_encoder_code]
        elif servo_encoder_code == 0:
            motor_encoder_code = int(self.number("Datasheet", "Motor Encoder"))
            if motor_encoder_code not in ENCODER_SECTIONS:
                raise ValueError(
                    "Aucun encodeur de position pris en charge : "
                    f"Servo Encoder={servo_encoder_code}, Motor Encoder={motor_encoder_code}"
                )
            base_type, encoder_section = ENCODER_SECTIONS[motor_encoder_code]
            encoder_type = f"Motor {base_type}"
        else:
            raise ValueError(
                f"Type d'encodeur servo non pris en charge : {servo_encoder_code}"
            )
        counts_per_rev = int(self.number(encoder_section, "counts per revolution"))
        gear_ratio = self.number("Datasheet", "Rotary Gearbox: Reduction Ratio")
        zero = int(self.number("Work Zone", "Work Zone: zero offset"))
        negative = int(self.number("Work Zone", "Work Zone: Limit in Negative Direction"))
        positive = int(self.number("Work Zone", "Work Zone: Limit in Positive Direction"))
        if not 1 <= node_id <= 126:
            raise ValueError(f"Node ID invalide dans le fichier : {node_id}")
        if counts_per_rev <= 0 or gear_ratio <= 0:
            raise ValueError("Résolution d'encodeur ou rapport de réduction invalide")
        min_deg, max_deg = work_zone_degrees(
            negative,
            positive,
            zero,
            counts_per_rev,
            gear_ratio,
        )
        poles = self.optional_number("Datasheet", "Poles Number (Rotor Poles)")
        return CalibrationSummary(
            path=self.path,
            parameter_count=len(self.parameters),
            node_id=node_id,
            encoder_type=encoder_type,
            counts_per_rev=counts_per_rev,
            gear_ratio=gear_ratio,
            zero_offset_counts=zero,
            min_counts=negative,
            max_counts=positive,
            min_deg=min_deg,
            max_deg=max_deg,
            motor_poles=int(poles) if poles is not None else None,
            max_current_a=self.optional_number(
                "Datasheet", "Maximum Limit on Continuous Current (Line-to-Line)"
            ),
            phase_resistance_ohm=self.optional_number(
                "Datasheet", "Phase Resistance (Line-to-Line)"
            ),
            phase_inductance_h=self.optional_number(
                "Datasheet", "Phase Inductance (Line-to-Line)"
            ),
        )


def load_servosila_calibration(path: str | Path) -> ServosilaCalibration:
    source = Path(path)
    parameters: list[CalibrationParameter] = []
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter=";")
            for line_number, row in enumerate(reader, start=1):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if len(row) != 7:
                    raise ValueError(
                        f"Ligne {line_number}: 7 champs attendus, {len(row)} trouvés"
                    )
                parameters.append(CalibrationParameter(*(cell.strip() for cell in row)))
    except UnicodeDecodeError as exc:
        raise ValueError("Le fichier n'est pas un CSV Servosila UTF-8 valide") from exc
    if not parameters:
        raise ValueError("Le fichier de calibration est vide")
    calibration = ServosilaCalibration(source, parameters)
    calibration.summary()  # Validation complète avant de retourner le fichier.
    return calibration
