"""Configuration AeroRib : défauts → preset matière → YAML → overrides (spec §10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Role = Literal["skin", "spar", "split", "norib", "cut", "ignore"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterialPreset(_Strict):
    line_width: float  # mm
    layer_height: float  # mm
    density: float  # g/cm³ effective du filament imprimé (estimation, à recaler par pesée)
    min_slit_width: float  # mm ; en dessous, le slicer referme la fente
    max_overhang_deg: float  # inclinaison max des murs internes par rapport à Z


class PrinterSpec(_Strict):
    volume: tuple[float, float, float]  # X, Y, Z utiles en mm


MATERIALS: dict[str, MaterialPreset] = {
    "pla_aero": MaterialPreset(
        line_width=0.45, layer_height=0.2, density=0.8, min_slit_width=0.08, max_overhang_deg=45
    ),
    "asa_aero": MaterialPreset(
        line_width=0.45, layer_height=0.2, density=0.7, min_slit_width=0.08, max_overhang_deg=40
    ),
}

PRINTERS: dict[str, PrinterSpec] = {
    "p1s": PrinterSpec(volume=(256, 256, 256)),
}


class Naming(_Strict):
    skin: list[str] = ["SKIN", "Wing", "Fuse"]
    spar: list[str] = ["SPAR", "SBAR", "Longeron"]
    split: list[str] = ["SPLIT"]
    norib: list[str] = ["NORIB"]
    cut: list[str] = ["CUT"]


class ValidationSettings(_Strict):
    tessellation_tol: float = 0.005  # mm, précision du maillage de validation
    skin_tolerance: float = 0.05  # mm, écart toléré entre peau d'origine et résultat
    max_mouth_width: float = 0.5  # mm, plus grande ouverture tolérée dans la peau (bouche de fente)


class Config(_Strict):
    material: str = "pla_aero"
    material_settings: dict[str, float] = Field(default_factory=dict)
    printer: str = "p1s"
    slit_width: float = 0.1
    naming: Naming = Field(default_factory=Naming)
    roles: dict[str, Role] = Field(default_factory=dict)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)

    @field_validator("material")
    @classmethod
    def _known_material(cls, v: str) -> str:
        if v not in MATERIALS:
            raise ValueError(f"matière inconnue '{v}' (connues : {', '.join(sorted(MATERIALS))})")
        return v

    @field_validator("printer")
    @classmethod
    def _known_printer(cls, v: str) -> str:
        if v not in PRINTERS:
            raise ValueError(f"imprimante inconnue '{v}' (connues : {', '.join(sorted(PRINTERS))})")
        return v

    @field_validator("material_settings")
    @classmethod
    def _known_settings(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = set(v) - set(MaterialPreset.model_fields)
        if unknown:
            raise ValueError(f"réglage matière inconnu : {', '.join(sorted(unknown))}")
        return v

    @property
    def preset(self) -> MaterialPreset:
        return MATERIALS[self.material].model_copy(update=self.material_settings)

    @property
    def printer_spec(self) -> PrinterSpec:
        return PRINTERS[self.printer]


class ConfigError(ValueError):
    """Configuration invalide (message en français, prêt à afficher)."""


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    data: dict[str, Any] = {}
    if path is not None:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} : la config doit être un dictionnaire YAML")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"configuration invalide :\n{exc}") from exc
