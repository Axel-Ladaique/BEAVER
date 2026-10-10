"""Configuration AeroRib : défauts → preset matière → YAML → overrides (spec §10)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Role = Literal["skin", "spar", "split", "norib", "cut", "ignore"]


SLIT_MARGIN = 1.1  # une fente doit dépasser de 10 % le seuil de fermeture du slicer


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterialPreset(_Strict):
    line_width: float = Field(gt=0)  # mm
    layer_height: float = Field(gt=0)  # mm
    density: float = Field(gt=0)  # g/cm³ effective du filament imprimé (estimation, à recaler par pesée)
    # mm ; en dessous, le slicer referme la fente (Bambu Studio / Orca : slice_closing_radius 0,049)
    min_slit_width: float = Field(gt=0)
    max_overhang_deg: float = Field(gt=0, le=90)  # inclinaison max des murs internes par rapport à Z


class PrinterSpec(_Strict):
    volume: tuple[float, float, float]  # X, Y, Z utiles en mm


MATERIALS: dict[str, MaterialPreset] = {
    "pla_aero": MaterialPreset(
        line_width=0.45, layer_height=0.2, density=0.8, min_slit_width=0.10, max_overhang_deg=45
    ),
    "asa_aero": MaterialPreset(
        line_width=0.45, layer_height=0.2, density=0.7, min_slit_width=0.10, max_overhang_deg=40
    ),
}

PRINTERS: dict[str, PrinterSpec] = {
    "p1s": PrinterSpec(volume=(256, 256, 256)),
}


class Naming(_Strict):
    skin: list[str] = ["SKIN", "Wing", "Fuse", "Aile", "Peau"]
    spar: list[str] = ["SPAR", "SBAR", "Longeron"]
    split: list[str] = ["SPLIT"]
    norib: list[str] = ["NORIB"]
    cut: list[str] = ["CUT"]

    @field_validator("skin", "spar", "split", "norib", "cut")
    @classmethod
    def _no_blank_prefix(cls, v: list[str]) -> list[str]:
        if any(not p.strip() for p in v):
            raise ValueError("un préfixe de nommage ne peut pas être vide")
        return v


class OrientationAxis(_Strict):
    axis: tuple[float, float, float]

    @field_validator("axis")
    @classmethod
    def _non_zero(cls, v: tuple[float, float, float]) -> tuple[float, float, float]:
        if sum(c * c for c in v) < 1e-12:
            raise ValueError("axe d'orientation nul")
        return v


class SplitSettings(_Strict):
    gap: float = Field(default=0.6, gt=0)  # largeur de la bande de séparation (mm), ≥ largeur de ligne
    min_rib_depth: float = Field(default=1.0, ge=0)  # nervure plus courte → non coupée (bords d'attaque/fuite)
    grid: tuple[int, int] = (101, 22)  # résolution (nx, nz) de la surface médiane


class RibLayer(_Strict):
    name: str = "principal"
    pattern: Literal["diamond"] = "diamond"
    angle: float | None = Field(default=None, gt=0, lt=90)  # None → min(45, max_overhang_deg du preset)
    spacing: float = Field(default=30.0, gt=0)
    depth: Literal["full"] | float = "full"
    phase: float = 0.0
    samples: int = Field(default=16, ge=4)

    @field_validator("depth")
    @classmethod
    def _full_only(cls, v: object) -> object:
        if v != "full":
            raise ValueError("profondeur partielle des nervures prévue en phase P3 (utiliser depth: full)")
        return v


class BoreSettings(_Strict):
    clearance: float = Field(default=0.15, ge=0)  # jeu radial autour du longeron
    # strictement > 0 : le manchon est le seul pont entre les deux côtés du trou sur la plaque opposée à la fente
    # ≥ largeur de ligne du preset (vérifié par Config)
    sleeve: float = Field(default=0.7, gt=0)  # matière gardée autour du trou, sans nervure
    strip: float = Field(default=2.0, gt=0)  # largeur de la bande sans nervure jusqu'à la peau
    slit_side: Literal["top", "bottom"] = "top"  # top = extrados (+Y)
    slit_width: float = Field(default=0.15, gt=0)  # largeur de la fente trou → peau (≥ 1,1 × min_slit_width)


class PassageSettings(_Strict):
    mode: Literal["auto", "off"] = "auto"
    sleeve: float = Field(default=0.7, gt=0)  # cf. BoreSettings : ≥ largeur de ligne
    strip: float = Field(default=1.0, gt=0)
    slit_side: Literal["top", "bottom"] = "top"
    slit_width: float = Field(default=0.15, gt=0)
    max_tilt_deg: float = Field(default=5.0, gt=0, lt=90)


class OutputSettings(_Strict):
    dir: str = "out"
    formats: list[Literal["step", "stl"]] = ["step", "stl"]


class ValidationSettings(_Strict):
    tessellation_tol: float = Field(default=0.02, gt=0)  # mm, précision du maillage de validation
    skin_tolerance: float = Field(default=0.05, gt=0)  # mm, écart toléré entre peau d'origine et résultat
    # ouverture max tolérée dans la peau (bouche de fente), mesurée réellement : la longueur
    # manquante est corrigée de 2·skin_tolerance avant comparaison
    max_mouth_width: float = Field(default=0.5, gt=0)  # mm


class Config(_Strict):
    material: str = "pla_aero"
    material_settings: dict[str, float] = Field(default_factory=dict)
    printer: str = "p1s"
    slit_width: float = Field(default=0.1, gt=0)
    naming: Naming = Field(default_factory=Naming)
    roles: dict[str, Role] = Field(default_factory=dict)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    orientation: Literal["auto"] | OrientationAxis = "auto"
    split: SplitSettings = Field(default_factory=SplitSettings)
    rib_layers: list[RibLayer] = Field(default_factory=lambda: [RibLayer()])
    spars: dict[str, dict[str, Any]] = Field(default_factory=dict)
    passages: PassageSettings = Field(default_factory=PassageSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)

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

    @model_validator(mode="after")
    def _valid_preset(self) -> "Config":
        try:
            self.preset
        except ValidationError as exc:
            raise ValueError(f"réglage matière invalide : {exc}") from exc
        return self

    @model_validator(mode="after")
    def _valid_p1(self) -> "Config":
        limit = self.preset.max_overhang_deg
        for layer in self.rib_layers:
            if self.rib_angle(layer) > limit:
                raise ValueError(
                    f"rib_layers '{layer.name}' : angle {self.rib_angle(layer)}° > surplomb max {limit}° du preset"
                )
        for name in self.spars:
            try:
                self.spar_settings(name)
            except ValidationError as exc:
                raise ValueError(f"spars '{name}' invalide : {exc}") from exc
        return self

    @model_validator(mode="after")
    def _printable_widths(self) -> "Config":
        lw = self.preset.line_width
        sleeves = [(f"spars '{n}'", self.spar_settings(n).sleeve) for n in self.spars or {"default": {}}]
        sleeves.append(("passages", self.passages.sleeve))
        for where, sleeve in sleeves:
            if sleeve < lw:
                raise ValueError(
                    f"{where} : manchon (sleeve) {sleeve} mm < largeur de ligne {lw} mm — "
                    "la matière autour du trou serait plus fine qu'une ligne"
                )
        need = SLIT_MARGIN * self.preset.min_slit_width
        for layer in self.rib_layers:
            seen = self.slit_width / math.cos(math.radians(self.rib_angle(layer)))
            if seen < need - 1e-9:
                raise ValueError(
                    f"rib_layers '{layer.name}' : fente de {self.slit_width} mm vue {seen:.3f} mm en tranche "
                    f"< {need:.3f} mm (1,1 × min_slit_width) — le slicer la refermerait"
                )
        slits = [(f"spars '{n}'", self.spar_settings(n).slit_width) for n in self.spars or {"default": {}}]
        slits.append(("passages", self.passages.slit_width))
        for where, width in slits:
            if width < need - 1e-9:
                raise ValueError(
                    f"{where} : fente (slit_width) {width} mm < {need:.3f} mm (1,1 × min_slit_width) — "
                    "le slicer la refermerait"
                )
        if self.split.gap < lw:
            raise ValueError(
                f"split.gap {self.split.gap} mm < largeur de ligne {lw} mm — "
                "la bande de séparation ne serait pas imprimable"
            )
        return self

    @property
    def preset(self) -> MaterialPreset:
        return MaterialPreset.model_validate({**MATERIALS[self.material].model_dump(), **self.material_settings})

    @property
    def printer_spec(self) -> PrinterSpec:
        return PRINTERS[self.printer]

    def rib_angle(self, layer: RibLayer) -> float:
        return layer.angle if layer.angle is not None else min(45.0, self.preset.max_overhang_deg)

    def spar_settings(self, name: str) -> BoreSettings:
        base = self.spars.get("default", {})
        override = next(
            (v for k, v in self.spars.items() if k != "default" and k.lower() == name.lower()), {}
        )
        return BoreSettings.model_validate({**base, **override})


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
        try:
            loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise ConfigError(f"configuration introuvable / illisible : {path} ({exc.strerror or exc})") from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{path} : la configuration doit être encodée en UTF-8") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} : YAML mal formé ({exc})") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} : la config doit être un dictionnaire YAML")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"configuration invalide :\n{exc}") from exc
