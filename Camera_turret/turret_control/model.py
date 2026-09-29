from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


@dataclass
class AxisConfig:
    node_id: int
    calibration_file: str = ""
    counts_per_rev: int = 16384
    gear_ratio: float = 1.0
    direction: int = 1
    zero_offset_counts: int = 0
    min_counts: int | None = None
    max_counts: int | None = None
    min_deg: float = -180.0
    max_deg: float = 180.0

    def degrees_to_counts(self, degrees: float) -> int:
        if not self.min_deg <= degrees <= self.max_deg:
            raise ValueError(
                f"{degrees:.3f}° est hors limites [{self.min_deg:.3f}, {self.max_deg:.3f}]°"
            )
        scale = self.counts_per_rev * self.gear_ratio / 360.0
        counts = round(self.zero_offset_counts + self.direction * degrees * scale)
        if self.min_counts is not None and counts < self.min_counts:
            raise ValueError(
                f"La consigne {degrees:.3f}° correspond à {counts} comptes, sous la Work Zone "
                f"({self.min_counts}). Configurez d'abord « Zéro 0° (comptes) » puis reconnectez."
            )
        if self.max_counts is not None and counts > self.max_counts:
            raise ValueError(
                f"La consigne {degrees:.3f}° correspond à {counts} comptes, au-dessus de la Work Zone "
                f"({self.max_counts}). Configurez d'abord « Zéro 0° (comptes) » puis reconnectez."
            )
        return counts

    def counts_to_degrees(self, counts: int | float) -> float:
        """Convert an absolute encoder count back to the configured axis angle."""
        scale = self.counts_per_rev * self.gear_ratio / 360.0
        return (float(counts) - self.zero_offset_counts) / (self.direction * scale)


@dataclass
class AppConfig:
    port: str = ""
    baudrate: int = 115200
    update_hz: float = 200.0
    simulation: bool = True
    azimuth: AxisConfig = field(default_factory=lambda: AxisConfig(node_id=1))
    elevation: AxisConfig = field(
        default_factory=lambda: AxisConfig(node_id=2, min_deg=-90.0, max_deg=90.0)
    )

    def validate(self, include_elevation: bool = True) -> None:
        if include_elevation and self.azimuth.node_id == self.elevation.node_id:
            raise ValueError("Les deux Node ID doivent être différents")
        if not 1 <= self.update_hz <= 500:
            raise ValueError("La fréquence de mise à jour doit être entre 1 et 500 Hz")
        axes = [("Azimut", self.azimuth)]
        if include_elevation:
            axes.append(("Élévation", self.elevation))
        for name, axis in axes:
            if not 1 <= axis.node_id <= 126:
                raise ValueError(f"Node ID {name} invalide")
            if axis.counts_per_rev <= 0 or axis.gear_ratio <= 0:
                raise ValueError(f"Échelle {name} invalide")
            if axis.direction not in (-1, 1):
                raise ValueError(f"Direction {name} doit valoir -1 ou 1")
            if axis.min_deg >= axis.max_deg:
                raise ValueError(f"Limites {name} invalides")
            if (
                axis.min_counts is not None
                and axis.max_counts is not None
                and axis.min_counts >= axis.max_counts
            ):
                raise ValueError(f"Work Zone en comptes {name} invalide")


def save_config(config: AppConfig, path: Path) -> None:
    path.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["azimuth"] = AxisConfig(**raw.get("azimuth", {"node_id": 1}))
    raw["elevation"] = AxisConfig(**raw.get("elevation", {"node_id": 2}))
    return AppConfig(**raw)
