"""Configuration AeroRib : défauts → preset matière → YAML → overrides (spec §10)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aerorib.material_profiles import ProfileError, load_profile, profile_names

Role = Literal["skin", "spar", "servo", "split", "norib", "cut", "ignore"]


SLIT_MARGIN = 1.1  # une fente doit dépasser de 10 % le seuil de fermeture du slicer


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterialPreset(_Strict):
    # valeurs de repli (PLA Aero) ; les profils matière (aerorib/profiles/*.yaml) les remplacent
    line_width: float = Field(default=0.42, gt=0)  # mm
    layer_height: float = Field(default=0.2, gt=0)  # mm
    density: float = Field(default=0.8, gt=0)  # g/cm³ effective du filament imprimé (estimation, à recaler par pesée)
    # mm ; en dessous, le slicer referme la fente (Bambu Studio / Orca : slice_closing_radius 0,049)
    min_slit_width: float = Field(default=0.10, gt=0)
    max_overhang_deg: float = Field(default=45, gt=0, le=90)  # inclinaison max des murs internes par rapport à Z


class PrinterSpec(_Strict):
    volume: tuple[float, float, float]  # X, Y, Z utiles en mm


PRINTERS: dict[str, PrinterSpec] = {
    "p1s": PrinterSpec(volume=(256, 256, 256)),
}


class Naming(_Strict):
    skin: list[str] = ["SKIN", "Wing", "Fuse", "Aile", "Peau"]
    spar: list[str] = ["SPAR", "SBAR", "Longeron"]
    split: list[str] = ["SPLIT"]
    norib: list[str] = ["NORIB"]
    cut: list[str] = ["CUT"]
    servo: list[str] = ["SERVO"]  # baie de servo (boîte = espace utile + jeu)
    # un longeron dont le nom propre contient l'un de ces marqueurs est une gaine de câble
    cable_markers: list[str] = ["CABLE"]

    @field_validator("skin", "spar", "split", "norib", "cut", "servo", "cable_markers")
    @classmethod
    def _no_blank_prefix(cls, v: list[str]) -> list[str]:
        if any(not p.strip() for p in v):
            raise ValueError("un préfixe de nommage ne peut pas être vide")
        return v


class OrientationAxis(_Strict):
    """Orientation explicite : axe d'envergure imposé (facultatif) et/ou choix de l'emplanture.

    `root` : `auto` (bout des gaines de câble, sinon plus grande section), `debut` (coordonnée minimale
    le long de l'axe d'envergure, repère d'origine) ou `fin` (coordonnée maximale).
    """

    axis: tuple[float, float, float] | None = None
    root: Literal["auto", "debut", "fin"] = "auto"

    @field_validator("axis")
    @classmethod
    def _non_zero(cls, v: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
        if v is not None and sum(c * c for c in v) < 1e-12:
            raise ValueError("axe d'orientation nul")
        return v


class SplitSettings(_Strict):
    gap: float = Field(default=0.6, gt=0)  # largeur de la bande de séparation (mm), ≥ largeur de ligne
    min_rib_depth: float = Field(default=1.0, ge=0)  # nervure plus courte → non coupée (bords d'attaque/fuite)
    grid: tuple[int, int] = (101, 22)  # résolution (nx, nz) de la surface médiane

    @field_validator("grid")
    @classmethod
    def _grid_min(cls, v: tuple[int, int]) -> tuple[int, int]:
        if min(v) < 2:
            raise ValueError("split.grid : au moins 2 points par axe (nx, nz)")
        return v


class RibLayer(_Strict):
    name: str = "principal"
    pattern: Literal["diamond"] = "diamond"
    angle: float | None = Field(default=None, gt=0, lt=90)  # None → min(45, max_overhang_deg du preset)
    spacing: float = Field(default=30.0, gt=0)
    # full : jusqu'à la bande de séparation ; d (mm) : seulement à moins de d de la peau (mesuré selon Y)
    depth: Literal["full"] | float = "full"
    phase: float = 0.0  # décalage du motif (mm, le long de X)
    samples: int = Field(default=16, ge=4)

    @field_validator("depth", mode="before")
    @classmethod
    def _full_or_number(cls, v: object) -> object:
        if v == "full" or (isinstance(v, (int, float)) and not isinstance(v, bool)):
            return v
        raise ValueError(f"rib_layers.depth : 'full' ou une profondeur en mm (reçu {v!r})")

    @field_validator("depth")
    @classmethod
    def _positive_depth(cls, v: object) -> object:
        if v != "full" and not v > 0:  # type: ignore[operator]
            raise ValueError(f"depth : profondeur de nervure > 0 mm attendue (ou full), reçu {v}")
        return v


CABLE_KEY = "cable"  # spars.cable : réglages communs des gaines de câble
REINFORCE_LINES = 1.2  # paroi doublée d'une baie de servo par défaut, en largeurs de ligne
CABLE_SLEEVE_LINES = 1.2  # manchon d'une gaine, en largeurs de ligne (0 ou 1 ligne : matière trop fine)


class BoreSettings(_Strict):
    clearance: float = Field(default=0.15, ge=0)  # jeu radial autour du longeron
    # strictement > 0 : le manchon est le seul pont entre les deux côtés du trou sur la plaque opposée à la fente
    # ≥ largeur de ligne du preset (vérifié par Config)
    sleeve: float = Field(default=0.8, gt=0)  # matière gardée autour du trou, sans nervure
    strip: float = Field(default=2.0, gt=0)  # largeur de la bande sans nervure jusqu'à la peau
    slit_side: Literal["top", "bottom"] = "top"  # top = extrados (+Y)
    slit_width: float = Field(default=0.15, gt=0)  # largeur de la fente trou → peau (≥ 1,1 × min_slit_width)
    # attache côté opposé : fente borgne du trou jusqu'à `anchor_gap` de la peau opposée (≥ largeur de ligne)
    anchor: bool = True
    anchor_gap: float = Field(default=1.0, gt=0)


class PassageSettings(_Strict):
    # fill : rebouche les alésages qui ne logent pas un longeron ; link : les relie à la peau par une fente ;
    # off : n'y touche pas (le validateur signale les trous isolés)
    mode: Literal["fill", "link", "off"] = "fill"
    sleeve: float = Field(default=0.8, gt=0)  # cf. BoreSettings : ≥ largeur de ligne
    strip: float = Field(default=1.0, gt=0)
    slit_side: Literal["top", "bottom"] = "top"
    slit_width: float = Field(default=0.15, gt=0)
    max_tilt_deg: float = Field(default=5.0, gt=0, lt=90)


class ServoSettings(_Strict):
    """Baies de servo (corps SERVO…) : poche ouverte sur une peau, rampes aux deux bouts."""

    strip: float = Field(default=2.0, gt=0)  # mm : bande sans nervure autour de la poche
    min_floor_gap: float = Field(default=1.0, gt=0)  # mm : écart minimal entre le fond de la baie et la peau opposée
    # renfort : fente parallèle aux parois, au fond et aux rampes de la poche, débouchant sur la peau aux deux bords
    # de l'ouverture ⇒ paroi doublée
    reinforce: bool = True
    # mm : épaisseur de la paroi doublée, de la paroi de la poche au bord proche de la fente (≥ largeur de ligne) ;
    # None = 1,2 × largeur de ligne du preset
    reinforce_offset: float | None = Field(default=None, gt=0)
    # mm : fente verticale, vue telle quelle en tranche (≥ 1,1 × material_settings.min_slit_width du preset) ; le
    # slit_width global des nervures (0,1) n'y suffit pas : il n'est assez large qu'incliné
    slit_width: float = Field(default=0.15, gt=0)


class OutputSettings(_Strict):
    dir: str = "out"
    formats: list[Literal["step", "stl", "3mf"]] = ["step", "stl"]


class SegmentSettings(_Strict):
    # auto : coupe en tronçons perpendiculaires à l'envergure quand la pièce dépasse la hauteur utile
    mode: Literal["auto", "off"] = "auto"
    margin: float = Field(default=5.0, ge=0)  # mm retirés de la hauteur d'impression (jupe, purge)
    cuts_at: list[float] = Field(default_factory=list)  # altitudes de coupe forcées (mm, repère d'impression)
    # force la hauteur utile (sinon : hauteur du gabarit Bambu − marge ; sans gabarit : imprimante − marge)
    max_height: float | None = Field(default=None, gt=0)


class BambuSettings(_Strict):
    # gabarit de projet Bambu Studio (.3mf) ; None = aucun (le profil matière fournit le gabarit embarqué)
    template: str | None = None
    open: bool = False  # ouvre le projet dans Bambu Studio après génération


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
    servo: ServoSettings = Field(default_factory=ServoSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    segment: SegmentSettings = Field(default_factory=SegmentSettings)
    bambu: BambuSettings = Field(default_factory=BambuSettings)

    @field_validator("material")
    @classmethod
    def _known_material(cls, v: str) -> str:
        names = profile_names()
        if v not in names:
            raise ValueError(f"matière inconnue '{v}' (profils disponibles : {', '.join(names)})")
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
                self.spar_settings(name, cable=name == CABLE_KEY)
            except ValidationError as exc:
                raise ValueError(f"spars '{name}' invalide : {exc}") from exc
        return self

    def _spar_entries(self) -> list[tuple[str, BoreSettings]]:
        """Réglages à contrôler : (libellé, réglages) de chaque entrée `spars` ; gaine incluse."""
        names = list(self.spars) or ["default"]
        return [(f"spars '{n}'", self.spar_settings(n, cable=n == CABLE_KEY)) for n in names]

    @model_validator(mode="after")
    def _printable_widths(self) -> "Config":
        lw = self.preset.line_width
        sleeves = [(w, st.sleeve) for w, st in self._spar_entries()]
        sleeves.append(("passages", self.passages.sleeve))
        for where, sleeve in sleeves:
            if sleeve < lw:
                raise ValueError(
                    f"{where} : manchon (sleeve) {sleeve} mm < largeur de ligne {lw} mm — "
                    "la matière autour du trou serait plus fine qu'une ligne"
                )
        for where, s in self._spar_entries():
            if s.anchor and s.anchor_gap < lw:
                raise ValueError(
                    f"{where} : anchor_gap {s.anchor_gap} mm < largeur de ligne {lw} mm — "
                    "le pont entre l'attache et la peau ne serait pas imprimable"
                )
        need = SLIT_MARGIN * self.preset.min_slit_width
        for layer in self.rib_layers:
            seen = self.slit_width / math.cos(math.radians(self.rib_angle(layer)))
            if seen < need - 1e-9:
                raise ValueError(
                    f"rib_layers '{layer.name}' : fente de {self.slit_width} mm vue {seen:.3f} mm en tranche "
                    f"< {need:.3f} mm (1,1 × min_slit_width) — le slicer la refermerait"
                )
        mouth = self.validation.max_mouth_width
        for layer in self.rib_layers:
            seen = self.slit_width / math.cos(math.radians(self.rib_angle(layer)))
            if seen >= mouth:
                raise ValueError(
                    f"rib_layers '{layer.name}' : fente (slit_width) {self.slit_width} mm vue {seen:.3f} mm en tranche "
                    f"à {self.rib_angle(layer)}° ≥ ouverture max validation.max_mouth_width {mouth} mm — chaque fente "
                    "entamerait la peau, même plate ; réduire slit_width (ou augmenter validation.max_mouth_width)"
                )
        slits = [(w, st.slit_width) for w, st in self._spar_entries()]
        slits.append(("passages", self.passages.slit_width))
        if self.servo.reinforce:
            slits.append(("servo", self.servo.slit_width))
        for where, width in slits:
            if width < need - 1e-9:
                raise ValueError(
                    f"{where} : fente (slit_width) {width} mm < {need:.3f} mm (1,1 × min_slit_width) — "
                    "le slicer la refermerait"
                )
        if self.servo.reinforce_offset is not None and self.servo.reinforce_offset < lw - 1e-9:
            raise ValueError(
                f"servo : reinforce_offset {self.servo.reinforce_offset} mm < largeur de ligne {lw} mm — "
                "la paroi entre la poche et la fente de renfort ne serait pas imprimable"
            )
        for layer in self.rib_layers:
            if layer.depth != "full" and layer.depth < 2 * lw - 1e-9:
                raise ValueError(
                    f"rib_layers '{layer.name}' : profondeur {layer.depth} mm < 2 × largeur de ligne ({2 * lw:.2f} mm) — "
                    "les deux murs de la nervure de peau se confondraient"
                )
        if self.split.gap < lw:
            raise ValueError(
                f"split.gap {self.split.gap} mm < largeur de ligne {lw} mm — "
                "la bande de séparation ne serait pas imprimable"
            )
        return self

    @property
    def preset(self) -> MaterialPreset:
        return MaterialPreset.model_validate(self.material_settings)

    @property
    def printer_spec(self) -> PrinterSpec:
        return PRINTERS[self.printer]

    def rib_angle(self, layer: RibLayer) -> float:
        return layer.angle if layer.angle is not None else min(45.0, self.preset.max_overhang_deg)

    def reinforce_offset(self) -> float:
        """Épaisseur de la paroi doublée (paroi de poche → bord proche de la fente de renfort, mm) : réglée, sinon
        1,2 × largeur de ligne du preset."""
        off = self.servo.reinforce_offset
        return off if off is not None else REINFORCE_LINES * self.preset.line_width

    def spar_settings(self, name: str, cable: bool = False) -> BoreSettings:
        """Réglages d'un longeron. Précédence : défauts du code < `spars.default` < (gaine) `spars.cable`
        < `spars.<nom>`. Gaine : manchon minimal imprimable (1,2 × largeur de ligne ; un manchon de 0 ou d'une
        seule ligne est refusé par la validation : « matière plus fine qu'une largeur de ligne »), sans attache,
        bande = largeur de ligne du preset."""
        base = self.spars.get("default", {})
        layer: dict[str, Any] = {}
        if cable:
            lw = self.preset.line_width
            layer = {"sleeve": CABLE_SLEEVE_LINES * lw, "anchor": False, "strip": lw, **self.spars.get(CABLE_KEY, {})}
        override = next(
            (v for k, v in self.spars.items() if k not in ("default", CABLE_KEY) and k.lower() == name.lower()), {}
        )
        return BoreSettings.model_validate({**base, **layer, **override})


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


def check_template_path(data: dict[str, Any], base: Path) -> None:
    """Un gabarit relatif d'un YAML projet s'entend relativement au dossier de ce YAML."""
    bambu = data.get("bambu")
    if isinstance(bambu, dict) and isinstance(bambu.get("template"), str):
        try:
            resolved = str((base / Path(bambu["template"]).expanduser()).resolve())
        except (ValueError, OSError, RuntimeError) as exc:
            raise ConfigError("configuration invalide : chemin de gabarit invalide (bambu.template)") from exc
        data["bambu"] = {**bambu, "template": resolved}


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    """Défauts du code → profil de la matière → YAML projet → overrides (spec §10)."""
    project: dict[str, Any] = {}
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
        project = loaded
        check_template_path(project, Path(path).resolve().parent)
    over = overrides or {}
    material = over.get("material", project.get("material", "pla_aero"))
    try:
        profile, profile_files = load_profile(material) if isinstance(material, str) else ({}, ())
    except ProfileError as exc:
        raise ConfigError(str(exc)) from exc
    data = _deep_merge(_deep_merge(profile, project), over)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        # la clé `material` ne dit rien sur l'origine de l'erreur : elle ne compte pas comme « projet »
        overriding = [d for d in ({k: v for k, v in d.items() if k != "material"} for d in (project, over)) if d]
        locs = [e["loc"] for e in exc.errors()]
        from_profile = bool(profile_files) and all(
            loc and not any(_set_in(d, loc) for d in overriding) for loc in locs
        )
        if from_profile or (profile_files and not overriding):
            head = "profil matière invalide"
        else:
            head = "configuration invalide"
        used = f"\n(profil matière utilisé : {', '.join(str(f) for f in profile_files)})" if profile_files else ""
        raise ConfigError(f"{head} :\n{exc}{used}") from exc


def _set_in(data: Any, loc: tuple) -> bool:
    """Le champ `loc` est-il posé dans `data` (une liste posée remplace celle du profil en bloc) ?"""
    for key in loc:
        if isinstance(data, list):
            return True
        if not isinstance(data, dict) or key not in data:
            return False
        data = data[key]
    return True


def material_label(material: str) -> str:
    head, _, tail = material.partition("_")
    return f"{head.upper()} {tail.capitalize()}".strip()


def resolve_template(cfg: Config) -> Path:
    """Chemin du gabarit Bambu à utiliser ; ConfigError clair si absent."""
    template = cfg.bambu.template
    if template is None:
        raise ConfigError(
            f"pas de gabarit Bambu pour {cfg.material} : enregistrer un projet {material_label(cfg.material)} "
            "dans Bambu Studio et renseigner bambu.template"
        )
    if not Path(template).is_file():
        raise ConfigError(f"gabarit Bambu introuvable : {template} (bambu.template)")
    return Path(template)
