# P0 — Fondations : implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** poser le squelette du projet `aerorib`. On sait lire un STEP avec ses noms, classer les corps par rôle (peau, longeron…), et **juger automatiquement** si une pièce est imprimable en vase mode (`aerorib inspect` / `aerorib check`).

**Architecture :** paquet Python `src/aerorib`, un module par responsabilité (`config`, `io_step`, `classify`, `slicing`, `validate`, `report`, `cli`). La géométrie passe par OpenCascade via `build123d`. La validation tessele le solide (`trimesh`), le découpe en couches et analyse chaque couche en 2D (`shapely`). Des pièces synthétiques générées en code (aile NACA bonne ou cassée) servent d'oracle aux tests.

**Tech Stack :** Python ≥ 3.12, `uv`, `build123d` (OCP), `trimesh` (+ `networkx`, `scipy`, `rtree`), `shapely` 2, `pydantic` 2, `PyYAML`, `typer`, `pytest`.

**Spec :** `docs/specs/2026-10-09-aerorib-design.md` (lire §2, §5, §6, §8, §9, §10, §15).

## Global Constraints

- Python `>=3.12`. Doit fonctionner sous **Windows et macOS**.
- Unités internes : **millimètres** (OpenCascade convertit les STEP à la lecture).
- Identifiants de code en anglais ; **messages utilisateur (CLI, rapports, erreurs) en français**.
- Repère d'impression : **Z = axe de construction**.
- `slit_width` par défaut **0.1 mm** ; bande de séparation par défaut **0.6 mm** (spec §2, §10).
- Critère bloquant n°1 : **chaque couche = exactement 1 polygone, sans trou intérieur** (spec §2, §8).
- Ordre de configuration : défauts → preset matière → YAML → options CLI (spec §10). Les surcharges par nom CAO arrivent en P1.
- Préfixes de nommage par défaut (spec §6) : skin `SKIN, Wing, Fuse` ; spar `SPAR, Longeron` ; split `SPLIT` ; norib `NORIB` ; cut `CUT`. Comparaison insensible à la casse, par préfixe, à tous les niveaux du chemin.
- Tests rapides par défaut ; les tests lents portent le marqueur `@pytest.mark.slow`.

## Hors périmètre P0 (reporté, volontairement)

- Vérifications d'angle de surplomb, de fragments de nervure et de distance entre plis : elles ont besoin de connaître les nervures générées → **P1/P3**.
- Vérification du trou de longeron dans la pièce finale → **P1**. Tenue dans le volume imprimante → **P4**.
- Volumes H2S / X2D → **P4** (spec §17).
- Test d'unités en pouces : `build123d.export_step(unit=Unit.IN)` produit un fichier faux (constaté lors des sondages : géométrie mise à l'échelle mais unité déclarée en mm). On le couvrira avec les exports réels Fusion/SolidWorks (Task 11).

## Structure des fichiers

```
pyproject.toml                    dépendances, script `aerorib`, config pytest
.python-version                   3.12
.gitignore
README.md                         installation + usage
.github/workflows/tests.yml       CI Windows + macOS
src/aerorib/__init__.py           __version__
src/aerorib/config.py             Config pydantic, presets matière, imprimantes, load_config
src/aerorib/io_step.py            NamedBody, read_step, write_step
src/aerorib/classify.py           SparInfo, ClassifiedBody, Model, detect_spar, classify
src/aerorib/slicing.py            Layer, to_mesh, layer_heights, slice_layers
src/aerorib/validate.py           Issue, Report, check_* , validate
src/aerorib/report.py             format_text, write_html
src/aerorib/cli.py                typer : --version, inspect, check
src/aerorib/testing/__init__.py
src/aerorib/testing/synthetic.py  naca4, naca_wing, ribbed_wing, broken_wing
tests/test_cli_version.py
tests/test_config.py
tests/test_synthetic.py
tests/test_io_step.py
tests/test_classify.py
tests/test_slicing.py
tests/test_validate_checks.py     tests unitaires shapely (rapides)
tests/test_validate.py            tests sur pièces synthétiques
tests/test_report.py
tests/test_cli_inspect.py
tests/test_cli_check.py
tests/test_real_fixtures.py       sauté tant que tests/fixtures/ est vide
tests/fixtures/README.md
```

---

### Task 1 : Squelette du projet et `aerorib --version`

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/aerorib/__init__.py`, `src/aerorib/cli.py`
- Test: `tests/test_cli_version.py`

**Interfaces:**
- Produces : `aerorib.__version__: str = "0.1.0"` ; `aerorib.cli.app: typer.Typer` (les commandes suivantes s'y ajoutent).

- [ ] **Step 1 : installer uv (une fois par machine)**

macOS : `brew install uv` — Windows : `winget install astral-sh.uv`. Vérifier : `uv --version`.

- [ ] **Step 2 : écrire `pyproject.toml`**

```toml
[project]
name = "aerorib"
version = "0.1.0"
description = "Générateur de structure interne pour impression vase mode"
requires-python = ">=3.12"
dependencies = [
  "build123d>=0.13",
  "numpy>=1.26",
  "trimesh>=4.0",
  "networkx>=3.0",
  "scipy>=1.11",
  "rtree>=1.0",
  "shapely>=2.0",
  "pydantic>=2.6",
  "pyyaml>=6.0",
  "typer>=0.12",
]

[project.scripts]
aerorib = "aerorib.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aerorib"]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: tests lents (slicing Bambu, grosses pièces)"]
```

`.python-version` :
```
3.12
```

`.gitignore` :
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
out/
*.stl
!tests/fixtures/**
```

- [ ] **Step 3 : écrire le test qui échoue**

`tests/test_cli_version.py` :
```python
from typer.testing import CliRunner

from aerorib.cli import app


def test_version_flag_prints_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "aerorib 0.1.0" in result.output
```

- [ ] **Step 4 : `uv sync` puis lancer le test**

Run : `uv sync && uv run pytest tests/test_cli_version.py -v`
Attendu : FAIL (`ModuleNotFoundError: aerorib`).

- [ ] **Step 5 : implémentation minimale**

`src/aerorib/__init__.py` :
```python
"""AeroRib : structure interne pour impression vase mode."""

__version__ = "0.1.0"
```

`src/aerorib/cli.py` :
```python
"""Interface en ligne de commande AeroRib."""

from __future__ import annotations

import typer

from aerorib import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"aerorib {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Affiche la version."
    ),
) -> None:
    """AeroRib : structure interne pour impression vase mode."""
```

- [ ] **Step 6 : relancer**

Run : `uv sync && uv run pytest tests/test_cli_version.py -v` → PASS. Vérifier aussi `uv run aerorib --version`.

- [ ] **Step 7 : commit**

```bash
git add pyproject.toml .python-version .gitignore uv.lock src tests
git commit -m "chore: squelette du projet aerorib"
```

---

### Task 2 : Configuration (`config.py`)

**Files:**
- Create: `src/aerorib/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces :
  - `Role = Literal["skin", "spar", "split", "norib", "cut", "ignore"]`
  - `MaterialPreset(line_width, layer_height, density, min_slit_width, max_overhang_deg)`
  - `PrinterSpec(volume: tuple[float, float, float])`
  - `Naming(skin, spar, split, norib, cut: list[str])`
  - `ValidationSettings(tessellation_tol=0.005, skin_tolerance=0.05, max_mouth_width=0.5)`
  - `Config(material, material_settings, printer, slit_width, naming, roles, validation)` avec propriétés `.preset -> MaterialPreset` et `.printer_spec -> PrinterSpec`
  - `MATERIALS: dict[str, MaterialPreset]`, `PRINTERS: dict[str, PrinterSpec]`
  - `ConfigError(ValueError)`
  - `load_config(path: Path | None = None, overrides: dict | None = None) -> Config`

- [ ] **Step 1 : écrire les tests qui échouent**

`tests/test_config.py` :
```python
from pathlib import Path

import pytest

from aerorib.config import MATERIALS, Config, ConfigError, load_config


def test_defaults():
    cfg = load_config()
    assert cfg.material == "pla_aero"
    assert cfg.printer == "p1s"
    assert cfg.slit_width == 0.1
    assert cfg.naming.spar == ["SPAR", "Longeron"]
    assert cfg.preset == MATERIALS["pla_aero"]
    assert cfg.printer_spec.volume == (256, 256, 256)


def test_yaml_partial_nested_override(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("material: asa_aero\nnaming:\n  spar: [TUBE]\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.material == "asa_aero"
    assert cfg.naming.spar == ["TUBE"]
    assert cfg.naming.skin == ["SKIN", "Wing", "Fuse"]  # défaut conservé


def test_overrides_win_over_yaml(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("slit_width: 0.2\nvalidation:\n  skin_tolerance: 0.1\n", encoding="utf-8")
    cfg = load_config(p, overrides={"slit_width": 0.15, "validation": {"max_mouth_width": 1.0}})
    assert cfg.slit_width == 0.15
    assert cfg.validation.skin_tolerance == 0.1
    assert cfg.validation.max_mouth_width == 1.0


def test_material_settings_patch_preset():
    cfg = load_config(overrides={"material_settings": {"layer_height": 0.25}})
    assert cfg.preset.layer_height == 0.25
    assert cfg.preset.line_width == MATERIALS["pla_aero"].line_width


def test_unknown_material_setting_rejected():
    with pytest.raises(ConfigError, match="inconnu"):
        load_config(overrides={"material_settings": {"nope": 1.0}})


def test_unknown_key_rejected(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("slitwidth: 0.1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_unknown_material_rejected():
    with pytest.raises(ConfigError, match="matière inconnue"):
        load_config(overrides={"material": "pet_carbon"})


def test_unknown_printer_rejected():
    with pytest.raises(ConfigError, match="imprimante inconnue"):
        load_config(overrides={"printer": "x9"})


def test_roles_mapping():
    cfg = load_config(overrides={"roles": {"Body3": "spar"}})
    assert cfg.roles == {"Body3": "spar"}
    with pytest.raises(ConfigError):
        load_config(overrides={"roles": {"Body3": "wing"}})


def test_yaml_not_a_mapping(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="dictionnaire"):
        load_config(p)


def test_config_is_immutable():
    cfg = Config()
    with pytest.raises(Exception):
        cfg.slit_width = 0.3
```

- [ ] **Step 2 : lancer**

Run : `uv run pytest tests/test_config.py -v` → FAIL (module absent).

- [ ] **Step 3 : implémenter `src/aerorib/config.py`**

```python
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
    spar: list[str] = ["SPAR", "Longeron"]
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
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_config.py -v` → PASS.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/config.py tests/test_config.py
git commit -m "feat(config): schéma de configuration, presets matière et imprimantes"
```

---

### Task 3 : Pièces synthétiques de test (`testing/synthetic.py`)

Ces pièces sont l'**oracle** de la validation : une aile NACA 4412 nervurée « à la main » (bonne), et des variantes cassées dont on connaît le défaut.

**Files:**
- Create: `src/aerorib/testing/__init__.py` (vide), `src/aerorib/testing/synthetic.py`
- Test: `tests/test_synthetic.py`

**Interfaces:**
- Produces :
  - `naca4(code: str = "4412", chord: float = 150.0, n: int = 80) -> tuple[list[tuple[float, float]], Callable[[float], float]]` : points du contour (extrados BF→BA puis intrados BA→BF, bord de fuite ouvert) et fonction cambrure `y_c(x)` en mm.
  - `naca_wing(code="4412", chord=150.0, span=100.0) -> tuple[Part, Callable[[float], float]]` : profil dans XY, extrudé de z=0 à z=span.
  - `ribbed_wing(rib_x=(20, 70, 90, 110, 130), spar_x=45.0, spar_d=8.0, slit=0.1, gap=0.6, chord=150.0, span=100.0) -> Part` : pièce **valide** (fentes haut/bas qui s'arrêtent à ±gap/2 de la cambrure, trou de longeron relié à l'extrados par une fente).
  - `broken_wing(kind: Literal["hole", "island", "narrow", "skin"]) -> Part` : `hole` = trou de longeron sans fente (trou isolé) ; `island` = fente traversante (2 îlots) ; `narrow` = fente de 0.05 mm ; `skin` = entaille de 3 mm dans la peau.

- [ ] **Step 1 : tests qui échouent**

`tests/test_synthetic.py` :
```python
import pytest

from aerorib.testing.synthetic import broken_wing, naca4, naca_wing, ribbed_wing


def test_naca4_shape():
    pts, camber = naca4("4412", chord=150.0, n=80)
    xs = [p[0] for p in pts]
    # l'épaisseur est appliquée perpendiculairement à la cambrure : x déborde un peu de [0, corde]
    assert min(xs) == pytest.approx(0.0, abs=0.1)
    assert max(xs) == pytest.approx(150.0, abs=0.1)
    assert camber(0.0) == pytest.approx(0.0, abs=1e-6)
    assert camber(60.0) == pytest.approx(0.04 * 150, rel=2e-3)  # cambrure max 4 % à 40 % de corde


def test_naca_wing_is_valid_solid():
    wing, _ = naca_wing(span=100.0)
    assert wing.is_valid
    bb = wing.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-6)
    assert bb.max.Z == pytest.approx(100.0, abs=1e-6)


def test_ribbed_wing_removes_material():
    wing, _ = naca_wing()
    ribbed = ribbed_wing()
    assert ribbed.is_valid
    assert ribbed.volume < wing.volume


@pytest.mark.parametrize("kind", ["hole", "island", "narrow", "skin"])
def test_broken_variants_are_valid_solids(kind):
    part = broken_wing(kind)
    assert part.is_valid
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_synthetic.py -v` → FAIL.

- [ ] **Step 3 : implémenter `src/aerorib/testing/synthetic.py`**

```python
"""Pièces synthétiques pour les tests : aile NACA bonne et variantes cassées."""

from __future__ import annotations

import math
from typing import Callable, Literal

import numpy as np
from build123d import (
    Align,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Compound,
    Cylinder,
    Line,
    Location,
    Part,
    Spline,
    extrude,
    make_face,
)

Camber = Callable[[float], float]


def naca4(code: str = "4412", chord: float = 150.0, n: int = 80) -> tuple[list[tuple[float, float]], Camber]:
    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
    beta = np.linspace(0.0, math.pi, n)
    x = (1 - np.cos(beta)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    if m > 0:
        yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x**2))
        dyc = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc = np.zeros_like(x)
        dyc = np.zeros_like(x)
    th = np.arctan(dyc)
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    pts = list(zip(xu[::-1], yu[::-1])) + list(zip(xl[1:], yl[1:]))
    pts = [(float(a) * chord, float(b) * chord) for a, b in pts]

    def camber(xx: float) -> float:
        return float(np.interp(xx / chord, x, yc)) * chord

    return pts, camber


def naca_wing(code: str = "4412", chord: float = 150.0, span: float = 100.0) -> tuple[Part, Camber]:
    pts, camber = naca4(code, chord)
    mid = len(pts) // 2
    with BuildPart() as bp:
        with BuildSketch():
            with BuildLine():
                Spline(*pts[: mid + 1])
                Spline(*pts[mid:])
                Line(pts[-1], pts[0])
            make_face()
        extrude(amount=span)
    return bp.part, camber


def _slit(x: float, y_from: float, up: bool, width: float, span: float) -> Box:
    """Plaque verticale d'épaisseur `width` partant de y_from vers le haut (up) ou le bas."""
    align_y = Align.MIN if up else Align.MAX
    return Box(width, 60, span * 2, align=(Align.CENTER, align_y, Align.CENTER)).moved(
        Location((x, y_from, span / 2))
    )


def ribbed_wing(
    rib_x: tuple[float, ...] = (20, 70, 90, 110, 130),
    spar_x: float = 45.0,
    spar_d: float = 8.0,
    slit: float = 0.1,
    gap: float = 0.6,
    chord: float = 150.0,
    span: float = 100.0,
) -> Part:
    wing, camber = naca_wing(chord=chord, span=span)
    tools = []
    for x in rib_x:
        yc = camber(x)
        tools.append(_slit(x, yc + gap / 2, True, slit, span))
        tools.append(_slit(x, yc - gap / 2, False, slit, span))
    ys = camber(spar_x)
    tools.append(Cylinder(spar_d / 2, span * 2).moved(Location((spar_x, ys, span / 2))))
    tools.append(_slit(spar_x, ys, True, slit, span))
    return wing - Compound(tools)


def broken_wing(kind: Literal["hole", "island", "narrow", "skin"]) -> Part:
    wing, camber = naca_wing()
    if kind == "hole":
        return wing - Cylinder(4, 200).moved(Location((45, camber(45), 50)))
    if kind == "island":
        return wing - Box(0.1, 80, 200).moved(Location((100, 0, 50)))
    if kind == "narrow":
        return wing - _slit(100, camber(100) + 0.3, True, 0.05, 100)
    if kind == "skin":
        return wing - _slit(100, camber(100) + 0.3, True, 3.0, 100)
    raise ValueError(f"variante inconnue : {kind}")
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_synthetic.py -v` → PASS.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/testing tests/test_synthetic.py
git commit -m "test: pièces synthétiques NACA (bonne et cassées)"
```

---

### Task 4 : Lecture / écriture STEP avec noms (`io_step.py`)

**Files:**
- Create: `src/aerorib/io_step.py`
- Test: `tests/test_io_step.py`

**Interfaces:**
- Consumes : `aerorib.testing.synthetic.naca_wing`.
- Produces :
  - `@dataclass(frozen=True) NamedBody(path: tuple[str, ...], solid: Solid)` avec propriétés `.name -> str` (dernier élément, `""` si vide) et `.display -> str` (`"/".join(path)` ou `"<sans nom>"`).
  - `read_step(path: str | Path) -> list[NamedBody]` : parcourt l'arbre importé ; les libellés vides sont ignorés ; un nœud sans enfant contenant plusieurs solides produit `nom#1`, `nom#2`… Lève `FileNotFoundError` si le fichier n'existe pas, `StepReadError` si aucun solide.
  - `write_step(path: str | Path, bodies: list[NamedBody], assembly_name: str = "AERORIB") -> Path` : un assemblage plat, chaque solide portant `body.name`.
  - `StepReadError(ValueError)`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_io_step.py` :
```python
from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, Location, export_step

from aerorib.io_step import NamedBody, StepReadError, read_step, write_step


def _spar():
    return Cylinder(7, 200).moved(Location((10, 0, 100)))


def test_roundtrip_flat_assembly(tmp_path: Path):
    skin = Box(100, 20, 200)
    out = write_step(
        tmp_path / "a.step",
        [NamedBody(("SKIN_wing",), skin), NamedBody(("SPAR_main",), _spar())],
    )
    bodies = read_step(out)
    assert [b.path for b in bodies] == [("AERORIB", "SKIN_wing"), ("AERORIB", "SPAR_main")]
    assert [b.name for b in bodies] == ["SKIN_wing", "SPAR_main"]
    assert bodies[0].solid.volume == pytest.approx(100 * 20 * 200, rel=1e-6)


def test_nested_assembly_paths(tmp_path: Path):
    skin = Box(100, 20, 200)
    skin.label = "SKIN"
    spar = _spar()
    spar.label = "SPAR_main"
    left = Compound(children=[skin, spar], label="LEFT")
    root = Compound(children=[left], label="PLANE")
    export_step(root, str(tmp_path / "n.step"))
    paths = [b.path for b in read_step(tmp_path / "n.step")]
    assert paths == [("PLANE", "LEFT", "SKIN"), ("PLANE", "LEFT", "SPAR_main")]


def test_single_solid_file(tmp_path: Path):
    box = Box(10, 10, 10)
    box.label = "Wing_tip"
    export_step(box, str(tmp_path / "s.step"))
    bodies = read_step(tmp_path / "s.step")
    assert len(bodies) == 1
    assert bodies[0].name == "Wing_tip"


def test_display():
    assert NamedBody(("A", "B"), Box(1, 1, 1)).display == "A/B"
    assert NamedBody((), Box(1, 1, 1)).display == "<sans nom>"


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_step(tmp_path / "nope.step")


def test_garbage_file(tmp_path: Path):
    p = tmp_path / "bad.step"
    p.write_text("not a step file", encoding="utf-8")
    with pytest.raises(StepReadError):
        read_step(p)
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_io_step.py -v` → FAIL.

- [ ] **Step 3 : implémenter `src/aerorib/io_step.py`**

```python
"""Lecture/écriture STEP en conservant les noms des corps (spec §6)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from build123d import Compound, Shape, Solid, export_step, import_step


class StepReadError(ValueError):
    """Le fichier STEP est illisible ou ne contient aucun solide."""


@dataclass(frozen=True)
class NamedBody:
    path: tuple[str, ...]
    solid: Solid

    @property
    def name(self) -> str:
        return self.path[-1] if self.path else ""

    @property
    def display(self) -> str:
        return "/".join(self.path) if self.path else "<sans nom>"


def read_step(path: str | Path) -> list[NamedBody]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"fichier introuvable : {path}")
    try:
        root = import_step(str(path))
    except Exception as exc:  # OCP lève des types variés
        raise StepReadError(f"{path} : lecture STEP impossible ({exc})") from exc

    bodies: list[NamedBody] = []

    def walk(shape: Shape, prefix: tuple[str, ...]) -> None:
        label = (shape.label or "").strip()
        here = prefix + ((label,) if label else ())
        children = list(getattr(shape, "children", ()) or ())
        if children:
            for child in children:
                walk(child, here)
            return
        solids = shape.solids()
        for i, solid in enumerate(solids, start=1):
            if len(solids) == 1:
                bodies.append(NamedBody(here, solid))
            else:
                base = here[-1] if here else "corps"
                bodies.append(NamedBody(here[:-1] + (f"{base}#{i}",) if here else (f"{base}#{i}",), solid))

    walk(root, ())
    if not bodies:
        raise StepReadError(f"{path} : aucun solide trouvé")
    return bodies


def write_step(path: str | Path, bodies: list[NamedBody], assembly_name: str = "AERORIB") -> Path:
    path = Path(path)
    children = []
    for body in bodies:
        solid = copy.copy(body.solid)
        solid.label = body.name
        children.append(solid)
    export_step(Compound(children=children, label=assembly_name), str(path))
    return path
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_io_step.py -v` → PASS.

Si `test_garbage_file` échoue parce qu'`import_step` renvoie un compound vide au lieu de lever une exception, c'est le `if not bodies` qui doit lever `StepReadError` : vérifier que c'est bien le cas, sans changer le test.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/io_step.py tests/test_io_step.py
git commit -m "feat(io): lecture/écriture STEP avec noms des corps"
```

---

### Task 5 : Classification par nom et détection des longerons (`classify.py`)

**Files:**
- Create: `src/aerorib/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes : `NamedBody` (Task 4), `Config`, `Role`, `load_config` (Task 2).
- Produces :
  - `@dataclass(frozen=True) SparInfo(diameter: float, axis_point: tuple[float, float, float], axis_dir: tuple[float, float, float], length: float)`. `axis_dir` est normé, avec sa plus grande composante (en valeur absolue) positive ; `axis_point` est le point de l'axe à l'extrémité de projection minimale.
  - `@dataclass(frozen=True) ClassifiedBody(body: NamedBody, role: Role | None, matched_by: str, spar: SparInfo | None = None)`. `matched_by` vaut `"roles:<nom>"`, `"préfixe '<p>' sur '<niveau>'"`, `"seul corps restant"` ou `""`.
  - `@dataclass(frozen=True) Model(bodies: tuple[ClassifiedBody, ...])` avec `.of(role) -> list[ClassifiedBody]` et `.unassigned -> list[ClassifiedBody]`.
  - `detect_spar(solid: Solid) -> SparInfo | None`.
  - `classify(bodies: list[NamedBody], cfg: Config) -> Model`.
  - `ClassifyError(ValueError)`.

Règles (spec §6) :
1. Un niveau du chemin égal (insensible à la casse) à une clé de `cfg.roles` → ce rôle (`ignore` compris).
2. Sinon, du niveau le plus profond (feuille) vers la racine, et pour chaque niveau dans l'ordre `spar, split, norib, cut, skin` : le premier préfixe qui correspond gagne.
3. Si aucune peau n'est trouvée et qu'il reste exactement un corps sans rôle → c'est la peau.
4. Aucune peau → `ClassifyError` qui liste les corps trouvés.
5. Chaque `spar` doit contenir un cylindre, sinon `ClassifyError`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_classify.py` :
```python
import math

import pytest
from build123d import Box, Cylinder, Location

from aerorib.classify import ClassifyError, classify, detect_spar
from aerorib.config import load_config
from aerorib.io_step import NamedBody


def nb(*path, solid=None):
    return NamedBody(tuple(path), solid if solid is not None else Box(10, 10, 10))


def spar_solid():
    return Cylinder(7, 200).moved(Location((10, 5, 100)))


def roles(model):
    return {cb.body.display: cb.role for cb in model.bodies}


def test_prefix_case_insensitive_leaf():
    m = classify([nb("A", "skin_main"), nb("A", "spar_1", solid=spar_solid())], load_config())
    assert roles(m) == {"A/skin_main": "skin", "A/spar_1": "spar"}


def test_parent_level_match():
    m = classify([nb("Wing_L", "Body1")], load_config())
    assert roles(m) == {"Wing_L/Body1": "skin"}
    assert "Wing" in m.bodies[0].matched_by


def test_leaf_beats_parent():
    m = classify([nb("Wing_L", "Body1"), nb("Wing_L", "SPAR_main", solid=spar_solid())], load_config())
    assert roles(m)["Wing_L/SPAR_main"] == "spar"


def test_roles_override_and_ignore():
    cfg = load_config(overrides={"roles": {"body3": "spar", "Junk": "ignore"}})
    m = classify([nb("SKIN"), nb("Body3", solid=spar_solid()), nb("Junk")], cfg)
    assert roles(m) == {"SKIN": "skin", "Body3": "spar", "Junk": "ignore"}
    assert m.bodies[1].matched_by == "roles:body3"


def test_fallback_single_unassigned_is_skin():
    m = classify([nb("Body1"), nb("SPAR_x", solid=spar_solid())], load_config())
    assert roles(m)["Body1"] == "skin"
    assert m.bodies[0].matched_by == "seul corps restant"


def test_no_skin_raises_with_names():
    with pytest.raises(ClassifyError, match="Body1"):
        classify([nb("Body1"), nb("Body2")], load_config())


def test_spar_without_cylinder_raises():
    with pytest.raises(ClassifyError, match="cylindre"):
        classify([nb("SKIN"), nb("SPAR_box")], load_config())


def test_model_queries():
    m = classify([nb("SKIN"), nb("SPAR_a", solid=spar_solid()), nb("Other"), nb("Thing")], load_config())
    assert [c.body.name for c in m.of("spar")] == ["SPAR_a"]
    assert [c.body.name for c in m.unassigned] == ["Other", "Thing"]


def test_detect_spar_along_z():
    info = detect_spar(spar_solid())
    assert info.diameter == pytest.approx(14.0)
    assert info.axis_dir == pytest.approx((0, 0, 1), abs=1e-9)
    assert info.length == pytest.approx(200.0)
    assert info.axis_point == pytest.approx((10, 5, 0), abs=1e-6)


def test_detect_spar_along_x_normalized_sign():
    s = Cylinder(5, 80).moved(Location((0, 0, 0), (0, -90, 0)))
    info = detect_spar(s)
    assert info.diameter == pytest.approx(10.0)
    assert info.axis_dir == pytest.approx((1, 0, 0), abs=1e-9)
    assert info.length == pytest.approx(80.0)


def test_detect_spar_tube_takes_outer_diameter():
    tube = Cylinder(7, 100) - Cylinder(6, 120)
    assert detect_spar(tube).diameter == pytest.approx(14.0)


def test_detect_spar_none_for_box():
    assert detect_spar(Box(5, 5, 5)) is None
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_classify.py -v` → FAIL.

- [ ] **Step 3 : implémenter `src/aerorib/classify.py`**

```python
"""Rôles des corps d'après leurs noms (spec §6) et détection des longerons."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import GeomType, Solid
from OCP.BRepAdaptor import BRepAdaptor_Surface

from aerorib.config import Config, Role
from aerorib.io_step import NamedBody

_PREFIX_ORDER: tuple[Role, ...] = ("spar", "split", "norib", "cut", "skin")


class ClassifyError(ValueError):
    """Impossible d'attribuer les rôles (message en français)."""


@dataclass(frozen=True)
class SparInfo:
    diameter: float
    axis_point: tuple[float, float, float]
    axis_dir: tuple[float, float, float]
    length: float


@dataclass(frozen=True)
class ClassifiedBody:
    body: NamedBody
    role: Role | None
    matched_by: str
    spar: SparInfo | None = None


@dataclass(frozen=True)
class Model:
    bodies: tuple[ClassifiedBody, ...]

    def of(self, role: Role) -> list[ClassifiedBody]:
        return [b for b in self.bodies if b.role == role]

    @property
    def unassigned(self) -> list[ClassifiedBody]:
        return [b for b in self.bodies if b.role is None]


def detect_spar(solid: Solid) -> SparInfo | None:
    best = None
    for face in solid.faces():
        if face.geom_type != GeomType.CYLINDER:
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        if best is None or cyl.Radius() > best.Radius():
            best = cyl
    if best is None:
        return None
    ax = best.Axis()
    d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()], dtype=float)
    d /= np.linalg.norm(d)
    if d[np.argmax(np.abs(d))] < 0:
        d = -d
    origin = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()], dtype=float)
    pts = np.array([[v.X, v.Y, v.Z] for v in solid.vertices()], dtype=float)
    proj = (pts - origin) @ d
    start = origin + d * proj.min()
    clean = lambda a: tuple(float(round(c, 9)) + 0.0 for c in a)  # noqa: E731  (évite -0.0)
    return SparInfo(
        diameter=2 * best.Radius(),
        axis_point=clean(start),
        axis_dir=clean(d),
        length=float(proj.max() - proj.min()),
    )


def _match(body: NamedBody, cfg: Config) -> tuple[Role | None, str]:
    explicit = {k.lower(): (k, v) for k, v in cfg.roles.items()}
    for level in reversed(body.path):
        if level.lower() in explicit:
            key, role = explicit[level.lower()]
            return role, f"roles:{key}"
    for level in reversed(body.path):
        low = level.lower()
        for role in _PREFIX_ORDER:
            for prefix in getattr(cfg.naming, role):
                if low.startswith(prefix.lower()):
                    return role, f"préfixe '{prefix}' sur '{level}'"
    return None, ""


def classify(bodies: list[NamedBody], cfg: Config) -> Model:
    matched = [(b, *_match(b, cfg)) for b in bodies]
    if not any(role == "skin" for _, role, _ in matched):
        free = [i for i, (_, role, _) in enumerate(matched) if role is None]
        if len(free) == 1:
            b, _, _ = matched[free[0]]
            matched[free[0]] = (b, "skin", "seul corps restant")
        else:
            names = ", ".join(b.display for b in bodies)
            raise ClassifyError(
                f"aucune peau trouvée. Corps présents : {names}. "
                f"Nommez la peau avec un préfixe {cfg.naming.skin} ou utilisez 'roles:' dans la config."
            )
    result = []
    for body, role, how in matched:
        spar = None
        if role == "spar":
            spar = detect_spar(body.solid)
            if spar is None:
                raise ClassifyError(f"{body.display} : marqué longeron mais aucun cylindre détecté")
        result.append(ClassifiedBody(body, role, how, spar))
    return Model(tuple(result))
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_classify.py -v` → PASS.

Si `test_detect_spar_along_x_normalized_sign` échoue sur `axis_point` ou le signe, corriger l'implémentation (normalisation du signe) sans affaiblir le test.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/classify.py tests/test_classify.py
git commit -m "feat(classify): rôles par nommage et détection des longerons"
```

---

### Task 6 : Découpe en couches (`slicing.py`)

**Files:**
- Create: `src/aerorib/slicing.py`
- Test: `tests/test_slicing.py`

**Interfaces:**
- Produces :
  - `@dataclass(frozen=True) Layer(z: float, polygons: tuple[Polygon, ...])`.
  - `to_mesh(shape: Shape, tol: float = 0.005) -> trimesh.Trimesh`.
  - `layer_heights(zmin: float, zmax: float, layer_height: float) -> np.ndarray` : milieu de chaque couche, `zmin + h/2, zmin + 3h/2, …` strictement < `zmax`.
  - `slice_layers(shape: Shape, heights: Sequence[float], tol: float = 0.005) -> list[Layer]` : une `Layer` par hauteur (polygones vides si pas d'intersection). Coordonnées XY identiques pour deux formes découpées aux mêmes hauteurs.

- [ ] **Step 1 : tests qui échouent**

`tests/test_slicing.py` :
```python
import numpy as np
import pytest
from build123d import Box, Compound, Cylinder, Location

from aerorib.slicing import layer_heights, slice_layers, to_mesh


def test_layer_heights():
    zs = layer_heights(-15.0, 15.0, 0.2)
    assert len(zs) == 150
    assert zs[0] == pytest.approx(-14.9)
    assert zs[-1] == pytest.approx(14.9)


def test_to_mesh_watertight():
    assert to_mesh(Box(10, 20, 30)).is_watertight


def test_box_single_polygon():
    layers = slice_layers(Box(10, 20, 30), [-10.0, 0.0, 10.0])
    assert [len(l.polygons) for l in layers] == [1, 1, 1]
    assert layers[1].polygons[0].area == pytest.approx(200.0, rel=1e-6)
    assert layers[1].z == 0.0


def test_hole_gives_interior():
    part = Box(10, 20, 30) - Cylinder(2, 40)
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 1
    assert len(layer.polygons[0].interiors) == 1


def test_two_bodies_two_polygons():
    part = Compound([Box(10, 10, 10), Box(10, 10, 10).moved(Location((50, 0, 0)))])
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 2


def test_slit_keeps_single_polygon():
    part = Box(100, 20, 50) - Box(0.1, 15, 60).moved(Location((0, 5, 0)))
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 1
    # fente de 0.1 × 12.5 mm (y de -2.5 à +10, bord de la boîte)
    assert layer.polygons[0].area == pytest.approx(2000 - 1.25, abs=0.05)


def test_outside_height_is_empty():
    (layer,) = slice_layers(Box(10, 10, 10), [100.0])
    assert layer.polygons == ()
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_slicing.py -v` → FAIL.

- [ ] **Step 3 : implémenter `src/aerorib/slicing.py`**

```python
"""Tessellation et découpe en couches horizontales (Z = axe de construction)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from build123d import Shape
from shapely.geometry import Polygon


@dataclass(frozen=True)
class Layer:
    z: float
    polygons: tuple[Polygon, ...]


def to_mesh(shape: Shape, tol: float = 0.005) -> trimesh.Trimesh:
    vertices, triangles = shape.tessellate(tol, 0.1)
    verts = np.array([[v.X, v.Y, v.Z] for v in vertices], dtype=float)
    return trimesh.Trimesh(verts, np.array(triangles, dtype=np.int64), process=True)


def layer_heights(zmin: float, zmax: float, layer_height: float) -> np.ndarray:
    return np.arange(zmin + layer_height / 2, zmax, layer_height)


def slice_layers(shape: Shape, heights: Sequence[float], tol: float = 0.005) -> list[Layer]:
    mesh = to_mesh(shape, tol)
    zs = [float(z) for z in heights]
    sections = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    layers = []
    for z, section in zip(zs, sections):
        polys = tuple(section.polygons_full) if section is not None else ()
        layers.append(Layer(z, polys))
    return layers
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_slicing.py -v` → PASS.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/slicing.py tests/test_slicing.py
git commit -m "feat(slicing): tessellation et découpe en couches"
```

---

### Task 7 : Vérifications vase mode (`validate.py`)

**Files:**
- Create: `src/aerorib/validate.py`
- Test: `tests/test_validate_checks.py` (unitaires, polygones shapely), `tests/test_validate.py` (pièces synthétiques)

**Interfaces:**
- Consumes : `Layer`, `layer_heights`, `slice_layers` (Task 6) ; `Config` (Task 2) ; pièces synthétiques (Task 3).
- Produces :
  - `Severity = Literal["error", "warning"]`
  - `@dataclass(frozen=True) Issue(check: str, severity: Severity, z: float, message: str, bounds: tuple[float, float, float, float] | None = None)`. Valeurs de `check` : `"connectivity"`, `"narrow_slit"`, `"skin"`.
  - `@dataclass(frozen=True) Report(issues: tuple[Issue, ...], stats: dict[str, float], failed_layers: dict[float, tuple[Polygon, ...]])` avec `.ok -> bool` (aucune erreur) et `.errors -> list[Issue]`. `failed_layers` garde au plus `MAX_FAILED_LAYERS = 20` couches en erreur, pour le rapport HTML.
  - `check_connectivity(layer: Layer) -> list[Issue]`
  - `check_narrow_slits(layer: Layer, min_width: float) -> list[Issue]` : une fente plus étroite que `min_width` est détectée par fermeture morphologique (buffer +r puis −r, `r = min_width/2`, jointures en onglet). Les morceaux d'aire > `min_width * 0.5` sont signalés.
  - `check_skin(layer: Layer, original: Layer, max_mouth: float, tol: float) -> list[Issue]` : les portions du contour d'origine absentes du contour du résultat (à `tol` près), plus longues que `max_mouth`, sont signalées.
  - `validate(shape: Shape, cfg: Config, original: Shape | None = None, heights: Sequence[float] | None = None) -> Report`. Par défaut, `heights = layer_heights(bbox.zmin, bbox.zmax, cfg.preset.layer_height)`. Stats : `layers`, `height_mm`, `path_length_m` (somme des longueurs de contour), `mass_g` (`path_length × line_width × layer_height × density`).

- [ ] **Step 1 : tests unitaires qui échouent**

`tests/test_validate_checks.py` :
```python
from shapely.geometry import Point, Polygon, box

from aerorib.slicing import Layer
from aerorib.validate import check_connectivity, check_narrow_slits, check_skin

BASE = box(0, 0, 20, 10)


def notch(x, width, depth=5.0):
    return BASE.difference(box(x - width / 2, 10 - depth, x + width / 2, 10.5))


def test_connectivity_ok():
    assert check_connectivity(Layer(1.0, (notch(10, 0.1),))) == []


def test_connectivity_islands():
    issues = check_connectivity(Layer(1.0, (box(0, 0, 1, 1), box(5, 0, 6, 1))))
    assert [i.check for i in issues] == ["connectivity"]
    assert issues[0].severity == "error"
    assert "2 îlots" in issues[0].message


def test_connectivity_interior_hole():
    poly = BASE.difference(Point(10, 5).buffer(2))
    issues = check_connectivity(Layer(1.0, (poly,)))
    assert len(issues) == 1
    assert "trou isolé" in issues[0].message
    x0, y0, x1, y1 = issues[0].bounds
    assert 7.9 < x0 < 8.1 and 11.9 < x1 < 12.1


def test_connectivity_empty_layer():
    issues = check_connectivity(Layer(1.0, ()))
    assert len(issues) == 1 and "vide" in issues[0].message


def test_narrow_slit_detected():
    issues = check_narrow_slits(Layer(1.0, (notch(10, 0.05),)), min_width=0.08)
    assert len(issues) == 1
    assert issues[0].check == "narrow_slit"
    x0, _, x1, _ = issues[0].bounds
    assert 9.9 < x0 and x1 < 10.1


def test_wide_enough_slit_ok():
    assert check_narrow_slits(Layer(1.0, (notch(10, 0.1),)), min_width=0.08) == []


def test_skin_ok_with_slit_mouth():
    orig = Layer(1.0, (BASE,))
    res = Layer(1.0, (notch(10, 0.1),))
    assert check_skin(res, orig, max_mouth=0.5, tol=0.05) == []


def test_skin_damage_detected():
    orig = Layer(1.0, (BASE,))
    res = Layer(1.0, (notch(10, 3.0),))
    issues = check_skin(res, orig, max_mouth=0.5, tol=0.05)
    assert len(issues) == 1
    assert issues[0].check == "skin"
    assert "3.0 mm" in issues[0].message
```

- [ ] **Step 2 : tests pièces synthétiques qui échouent**

`tests/test_validate.py` :
```python
import pytest

from aerorib.config import load_config
from aerorib.testing.synthetic import broken_wing, naca_wing, ribbed_wing
from aerorib.validate import validate

ZS = [5.0, 50.0, 95.0]
CFG = load_config()


def checks(report):
    return sorted({i.check for i in report.errors})


def test_good_ribbed_wing_passes():
    wing, _ = naca_wing()
    report = validate(ribbed_wing(), CFG, original=wing, heights=ZS)
    assert report.ok, report.issues
    assert report.failed_layers == {}


@pytest.mark.parametrize(
    "kind, expected",
    [("hole", ["connectivity"]), ("island", ["connectivity"]), ("narrow", ["narrow_slit"])],
)
def test_broken_variants_detected(kind, expected):
    report = validate(broken_wing(kind), CFG, heights=ZS)
    assert not report.ok
    assert checks(report) == expected
    assert set(report.failed_layers) == set(ZS)


def test_skin_damage_needs_original():
    wing, _ = naca_wing()
    assert validate(broken_wing("skin"), CFG, heights=ZS).ok
    report = validate(broken_wing("skin"), CFG, original=wing, heights=ZS)
    assert checks(report) == ["skin"]


def test_stats():
    wing, _ = naca_wing()
    report = validate(wing, CFG, heights=ZS)
    assert report.stats["layers"] == 3
    # périmètre mesuré du NACA 4412 corde 150 ≈ 307 mm, 3 couches
    assert report.stats["path_length_m"] == pytest.approx(3 * 0.307, rel=0.02)
    assert report.stats["mass_g"] > 0


def test_default_heights_cover_part():
    wing, _ = naca_wing(span=2.0)
    report = validate(wing, CFG)
    assert report.stats["layers"] == 10  # 2 mm / 0.2 mm
    assert report.stats["height_mm"] == pytest.approx(2.0)
```

- [ ] **Step 3 : lancer** — `uv run pytest tests/test_validate_checks.py tests/test_validate.py -v` → FAIL.

- [ ] **Step 4 : implémenter `src/aerorib/validate.py`**

```python
"""Vérifications d'imprimabilité en vase mode, couche par couche (spec §8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from build123d import Shape
from shapely.geometry import Polygon
from shapely.ops import unary_union

from aerorib.config import Config
from aerorib.slicing import Layer, layer_heights, slice_layers

Severity = Literal["error", "warning"]
MAX_FAILED_LAYERS = 20


@dataclass(frozen=True)
class Issue:
    check: str
    severity: Severity
    z: float
    message: str
    bounds: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class Report:
    issues: tuple[Issue, ...]
    stats: dict[str, float]
    failed_layers: dict[float, tuple[Polygon, ...]] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _parts(geom):
    return list(getattr(geom, "geoms", [geom])) if not geom.is_empty else []


def check_connectivity(layer: Layer) -> list[Issue]:
    if not layer.polygons:
        return [Issue("connectivity", "error", layer.z, "couche vide")]
    issues = []
    if len(layer.polygons) > 1:
        smallest = sorted(layer.polygons, key=lambda p: p.area)[:-1]
        bounds = unary_union(smallest).bounds
        issues.append(
            Issue("connectivity", "error", layer.z, f"{len(layer.polygons)} îlots au lieu d'un seul contour", bounds)
        )
    for poly in layer.polygons:
        for ring in poly.interiors:
            issues.append(
                Issue("connectivity", "error", layer.z, "trou isolé (non relié à la peau)", Polygon(ring).bounds)
            )
    return issues


def check_narrow_slits(layer: Layer, min_width: float) -> list[Issue]:
    r = min_width / 2
    issues = []
    for poly in layer.polygons:
        closed = poly.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
        for part in _parts(closed.difference(poly)):
            if part.area > min_width * 0.5:
                issues.append(
                    Issue("narrow_slit", "error", layer.z, f"fente plus étroite que {min_width} mm", part.bounds)
                )
    return issues


def check_skin(layer: Layer, original: Layer, max_mouth: float, tol: float) -> list[Issue]:
    if not original.polygons:
        return []
    orig_boundary = unary_union([p.boundary for p in original.polygons])
    res_boundary = unary_union([p.boundary for p in layer.polygons]) if layer.polygons else None
    missing = orig_boundary if res_boundary is None else orig_boundary.difference(res_boundary.buffer(tol))
    issues = []
    for piece in _parts(missing):
        if piece.length > max_mouth:
            issues.append(
                Issue("skin", "error", layer.z, f"peau entamée sur {piece.length:.1f} mm", piece.bounds)
            )
    return issues


def validate(
    shape: Shape,
    cfg: Config,
    original: Shape | None = None,
    heights: Sequence[float] | None = None,
) -> Report:
    preset = cfg.preset
    tol = cfg.validation.tessellation_tol
    bb = shape.bounding_box()
    zs = list(heights) if heights is not None else list(layer_heights(bb.min.Z, bb.max.Z, preset.layer_height))
    layers = slice_layers(shape, zs, tol)
    originals = slice_layers(original, zs, tol) if original is not None else None

    issues: list[Issue] = []
    failed: dict[float, tuple[Polygon, ...]] = {}
    path_mm = 0.0
    for i, layer in enumerate(layers):
        found = check_connectivity(layer) + check_narrow_slits(layer, preset.min_slit_width)
        if originals is not None:
            found += check_skin(
                layer, originals[i], cfg.validation.max_mouth_width, cfg.validation.skin_tolerance
            )
        if any(x.severity == "error" for x in found) and len(failed) < MAX_FAILED_LAYERS:
            failed[layer.z] = layer.polygons
        issues.extend(found)
        path_mm += sum(p.length for p in layer.polygons)

    mass_g = path_mm * preset.line_width * preset.layer_height * preset.density / 1000.0
    stats = {
        "layers": float(len(layers)),
        "height_mm": float(bb.max.Z - bb.min.Z),
        "path_length_m": path_mm / 1000.0,
        "mass_g": mass_g,
    }
    return Report(tuple(issues), stats, failed)
```

Note : `Polygon.length` vaut le périmètre total (extérieur + trous), ce qui correspond bien au trajet de buse.

- [ ] **Step 5 : lancer** — `uv run pytest tests/test_validate_checks.py tests/test_validate.py -v` → PASS.

Si `test_good_ribbed_wing_passes` échoue sur `skin` à cause de l'imprécision du maillage, ne pas élargir `max_mouth_width` : vérifier d'abord la longueur signalée (elle doit être ≤ 0.1 mm pour une bouche de fente) et ajuster `skin_tolerance` dans le test via `load_config(overrides=...)` seulement si l'écart vient de la tessellation.

- [ ] **Step 6 : commit**

```bash
git add src/aerorib/validate.py tests/test_validate_checks.py tests/test_validate.py
git commit -m "feat(validate): vérifications vase mode par couche"
```

---

### Task 8 : Rapports texte et HTML (`report.py`)

**Files:**
- Create: `src/aerorib/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes : `Report`, `Issue` (Task 7).
- Produces :
  - `format_text(report: Report, title: str = "") -> str` : première ligne `"✔ imprimable en vase mode"` ou `"✘ NON imprimable en vase mode"` (précédée de `title` s'il est fourni). Puis une ligne par type de vérification en défaut : `"  - <libellé> : N couche(s), z = a – b mm, ex. : <message>"`. Enfin les stats : `"  couches : N · hauteur : H mm · trajet : L m · masse ≈ M g"`.
  - `write_html(report: Report, path: Path, title: str = "AeroRib") -> Path` : page autonome, une figure SVG par couche de `failed_layers` (contour en gris, rectangles rouges sur les `bounds` des problèmes de la couche), plus la liste des problèmes. Pas de dépendance externe.

Libellés : `connectivity` → `"contour non unique"`, `narrow_slit` → `"fente trop fine"`, `skin` → `"peau entamée"`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_report.py` :
```python
from pathlib import Path

from shapely.geometry import box

from aerorib.report import format_text, write_html
from aerorib.validate import Issue, Report

STATS = {"layers": 500.0, "height_mm": 100.0, "path_length_m": 153.2, "mass_g": 11.04}


def failing():
    issues = (
        Issue("connectivity", "error", 0.1, "trou isolé (non relié à la peau)", (8, 3, 12, 7)),
        Issue("connectivity", "error", 0.3, "trou isolé (non relié à la peau)", (8, 3, 12, 7)),
        Issue("narrow_slit", "error", 0.3, "fente plus étroite que 0.08 mm", (9.9, 5, 10.1, 10)),
    )
    layers = {0.1: (box(0, 0, 20, 10),), 0.3: (box(0, 0, 20, 10),)}
    return Report(issues, STATS, layers)


def test_text_ok():
    text = format_text(Report((), STATS, {}), title="aile.step")
    assert text.splitlines()[0] == "aile.step : ✔ imprimable en vase mode"
    assert "couches : 500 · hauteur : 100.0 mm · trajet : 153.2 m · masse ≈ 11.0 g" in text


def test_text_failing_groups_by_check():
    text = format_text(failing())
    lines = text.splitlines()
    assert lines[0] == "✘ NON imprimable en vase mode"
    assert "  - contour non unique : 2 couche(s), z = 0.1 – 0.3 mm, ex. : trou isolé (non relié à la peau)" in lines
    assert "  - fente trop fine : 1 couche(s), z = 0.3 – 0.3 mm, ex. : fente plus étroite que 0.08 mm" in lines


def test_html_contains_svg_per_failed_layer(tmp_path: Path):
    out = write_html(failing(), tmp_path / "r.html", title="aile")
    html = out.read_text(encoding="utf-8")
    assert html.count("<svg") == 2
    assert "z = 0.10 mm" in html and "z = 0.30 mm" in html
    assert "trou isolé" in html
    assert "<title>aile</title>" in html
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_report.py -v` → FAIL.

- [ ] **Step 3 : implémenter `src/aerorib/report.py`**

```python
"""Mise en forme des rapports de validation (terminal et HTML)."""

from __future__ import annotations

import html
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Polygon

from aerorib.validate import Issue, Report

LABELS = {
    "connectivity": "contour non unique",
    "narrow_slit": "fente trop fine",
    "skin": "peau entamée",
}


def format_text(report: Report, title: str = "") -> str:
    head = "✔ imprimable en vase mode" if report.ok else "✘ NON imprimable en vase mode"
    lines = [f"{title} : {head}" if title else head]
    by_check: dict[str, list[Issue]] = defaultdict(list)
    for issue in report.errors:
        by_check[issue.check].append(issue)
    for check, issues in by_check.items():
        zs = sorted({i.z for i in issues})
        lines.append(
            f"  - {LABELS.get(check, check)} : {len(zs)} couche(s), "
            f"z = {zs[0]:.1f} – {zs[-1]:.1f} mm, ex. : {issues[0].message}"
        )
    s = report.stats
    lines.append(
        f"  couches : {int(s['layers'])} · hauteur : {s['height_mm']:.1f} mm · "
        f"trajet : {s['path_length_m']:.1f} m · masse ≈ {s['mass_g']:.1f} g"
    )
    return "\n".join(lines)


def _ring_path(coords) -> str:
    pts = list(coords)
    return "M" + " L".join(f"{x:.3f},{-y:.3f}" for x, y in pts) + " Z"


def _svg(polys: tuple[Polygon, ...], issues: list[Issue]) -> str:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in polys:
        x0, y0, x1, y1 = p.bounds
        xs0.append(x0), ys0.append(y0), xs1.append(x1), ys1.append(y1)
    if not polys:
        return "<svg viewBox='0 0 1 1'></svg>"
    x0, y0, x1, y1 = min(xs0), min(ys0), max(xs1), max(ys1)
    pad = max(x1 - x0, y1 - y0) * 0.03
    vb = f"{x0 - pad:.3f} {-y1 - pad:.3f} {x1 - x0 + 2 * pad:.3f} {y1 - y0 + 2 * pad:.3f}"
    d = " ".join(_ring_path(r.coords) for p in polys for r in (p.exterior, *p.interiors))
    marks = "".join(
        f"<rect x='{b[0]:.3f}' y='{-b[3]:.3f}' width='{max(b[2] - b[0], pad / 4):.3f}' "
        f"height='{max(b[3] - b[1], pad / 4):.3f}' class='bad'/>"
        for i in issues
        if (b := i.bounds) is not None
    )
    return (
        f"<svg viewBox='{vb}' preserveAspectRatio='xMidYMid meet'>"
        f"<path d='{d}' class='part' fill-rule='evenodd'/>{marks}</svg>"
    )


_CSS = """
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --part:#d6d3d1; --stroke:#57534e; --bad:#dc2626; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --part:#44403c; --stroke:#d6d3d1; --bad:#f87171; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:960px; margin:0 auto; }
pre { white-space:pre-wrap; color:var(--muted); }
figure { margin:24px 0; }
svg { width:100%; height:auto; max-height:420px; }
.part { fill:var(--part); stroke:var(--stroke); stroke-width:0.1; vector-effect:non-scaling-stroke; }
.bad { fill:none; stroke:var(--bad); stroke-width:2; vector-effect:non-scaling-stroke; }
"""


def write_html(report: Report, path: Path, title: str = "AeroRib") -> Path:
    path = Path(path)
    figures = []
    for z, polys in sorted(report.failed_layers.items()):
        issues = [i for i in report.errors if i.z == z]
        items = "".join(f"<li>{html.escape(LABELS.get(i.check, i.check))} : {html.escape(i.message)}</li>" for i in issues)
        figures.append(
            f"<figure><figcaption>z = {z:.2f} mm</figcaption>{_svg(polys, issues)}<ul>{items}</ul></figure>"
        )
    body = (
        f"<main><h1>{html.escape(title)}</h1><pre>{html.escape(format_text(report))}</pre>"
        f"{''.join(figures)}</main>"
    )
    path.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_report.py -v` → PASS.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/report.py tests/test_report.py
git commit -m "feat(report): rapports texte et HTML"
```

---

### Task 9 : `aerorib inspect`

**Files:**
- Modify: `src/aerorib/cli.py`
- Test: `tests/test_cli_inspect.py`

**Interfaces:**
- Consumes : `load_config`, `ConfigError` (Task 2) ; `read_step`, `write_step`, `NamedBody`, `StepReadError` (Task 4) ; `classify`, `ClassifyError` (Task 5).
- Produces : commande `aerorib inspect FICHIER [-c CONFIG]`, avec un code de retour 0 si au moins une peau est trouvée et 1 sinon (message `erreur : …` sur la sortie). Format d'une ligne par corps :
  `  <rôle ou '—':<6>  <chemin>   (<matched_by>)` et, pour un longeron, `      Ø <d> mm · axe (<x>, <y>, <z>) · longueur <L> mm`.
  Fonction utilitaire partagée `_load(cfg_path) -> Config` et `_fail(msg) -> NoReturn` dans `cli.py`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_cli_inspect.py` :
```python
from pathlib import Path

from build123d import Box, Cylinder, Location
from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.io_step import NamedBody, write_step

runner = CliRunner()


def step_with(tmp_path: Path, *bodies: NamedBody) -> Path:
    return write_step(tmp_path / "in.step", list(bodies))


def test_inspect_lists_roles_and_spar(tmp_path: Path):
    f = step_with(
        tmp_path,
        NamedBody(("SKIN_wing",), Box(150, 20, 200)),
        NamedBody(("SPAR_main",), Cylinder(7, 200).moved(Location((10, 0, 100)))),
    )
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 0, r.output
    assert "skin    AERORIB/SKIN_wing" in r.output
    assert "spar    AERORIB/SPAR_main" in r.output
    assert "Ø 14.0 mm · axe (0, 0, 1) · longueur 200.0 mm" in r.output


def test_inspect_uses_config_roles(tmp_path: Path):
    f = step_with(
        tmp_path,
        NamedBody(("Body1",), Box(150, 20, 200)),
        NamedBody(("Body2",), Cylinder(7, 200)),
    )
    cfg = tmp_path / "c.yaml"
    cfg.write_text("roles: {Body2: spar}\n", encoding="utf-8")
    r = runner.invoke(app, ["inspect", str(f), "-c", str(cfg)])
    assert r.exit_code == 0, r.output
    assert "spar    AERORIB/Body2" in r.output
    assert "skin    AERORIB/Body1" in r.output


def test_inspect_no_skin_fails(tmp_path: Path):
    f = step_with(tmp_path, NamedBody(("Body1",), Box(1, 1, 1)), NamedBody(("Body2",), Box(1, 1, 1)))
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 1
    assert "erreur : aucune peau trouvée" in r.output


def test_inspect_missing_file(tmp_path: Path):
    r = runner.invoke(app, ["inspect", str(tmp_path / "nope.step")])
    assert r.exit_code == 1
    assert "erreur : fichier introuvable" in r.output
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_cli_inspect.py -v` → FAIL.

- [ ] **Step 3 : ajouter à `src/aerorib/cli.py`** (en conservant le contenu de la Task 1)

Imports à ajouter en haut :
```python
from pathlib import Path
from typing import NoReturn, Optional

from aerorib.classify import ClassifyError, classify
from aerorib.config import Config, ConfigError, load_config
from aerorib.io_step import StepReadError, read_step
```

Fonctions à ajouter à la fin :
```python
def _fail(message: str) -> NoReturn:
    typer.echo(f"erreur : {message}")
    raise typer.Exit(1)


def _load(cfg_path: Optional[Path]) -> Config:
    try:
        return load_config(cfg_path)
    except (ConfigError, OSError) as exc:
        _fail(str(exc))


def _fmt_vec(v) -> str:
    return "(" + ", ".join(f"{c:g}" for c in v) + ")"


@app.command()
def inspect(
    file: Path = typer.Argument(..., help="Fichier STEP à analyser."),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Fichier de configuration YAML."),
) -> None:
    """Affiche les corps trouvés et le rôle attribué à chacun."""
    cfg = _load(config)
    try:
        model = classify(read_step(file), cfg)
    except (FileNotFoundError, StepReadError, ClassifyError) as exc:
        _fail(str(exc))
    typer.echo(f"{file.name} : {len(model.bodies)} corps")
    for cb in model.bodies:
        role = cb.role or "—"
        how = f"   ({cb.matched_by})" if cb.matched_by else ""
        typer.echo(f"  {role:<6}  {cb.body.display}{how}")
        if cb.spar is not None:
            s = cb.spar
            typer.echo(
                f"      Ø {s.diameter:.1f} mm · axe {_fmt_vec(s.axis_dir)} · longueur {s.length:.1f} mm"
            )
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_cli_inspect.py tests/test_cli_version.py -v` → PASS.

- [ ] **Step 5 : commit**

```bash
git add src/aerorib/cli.py tests/test_cli_inspect.py
git commit -m "feat(cli): commande inspect"
```

---

### Task 10 : `aerorib check`

**Files:**
- Modify: `src/aerorib/cli.py`
- Test: `tests/test_cli_check.py`

**Interfaces:**
- Consumes : `_load`, `_fail` (Task 9) ; `validate` (Task 7) ; `format_text`, `write_html` (Task 8) ; `classify` (Task 5) ; `read_step` (Task 4).
- Produces : `aerorib check FICHIER [-c CONFIG] [--original ORIG.step] [--html RAPPORT.html]`. Valide chaque corps `skin` du fichier. Avec `--original`, chaque peau est appariée à la peau d'origine de même nom, ou à l'unique peau si chaque fichier n'en a qu'une (sinon erreur). Code de retour 0 si tout est imprimable, 1 sinon. Avec `--html`, écrit un rapport par peau (`rapport.html`, ou `rapport-<nom>.html` s'il y a plusieurs peaux).

- [ ] **Step 1 : tests qui échouent**

`tests/test_cli_check.py` :
```python
from pathlib import Path

from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.io_step import NamedBody, write_step
from aerorib.testing.synthetic import broken_wing, naca_wing, ribbed_wing

runner = CliRunner()


def save(tmp_path: Path, name: str, part, body_name="SKIN_wing") -> Path:
    return write_step(tmp_path / name, [NamedBody((body_name,), part.solids()[0])])


def coarse_cfg(tmp_path: Path) -> Path:
    # couches de 10 mm pour garder le test rapide
    p = tmp_path / "c.yaml"
    p.write_text("material_settings: {layer_height: 10.0}\n", encoding="utf-8")
    return p


def test_check_good_with_original(tmp_path: Path):
    wing, _ = naca_wing()
    good = save(tmp_path, "good.step", ribbed_wing())
    orig = save(tmp_path, "orig.step", wing)
    r = runner.invoke(app, ["check", str(good), "--original", str(orig), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 0, r.output
    assert "✔ imprimable en vase mode" in r.output


def test_check_broken_fails_and_writes_html(tmp_path: Path):
    bad = save(tmp_path, "bad.step", broken_wing("hole"))
    html = tmp_path / "rapport.html"
    r = runner.invoke(app, ["check", str(bad), "-c", str(coarse_cfg(tmp_path)), "--html", str(html)])
    assert r.exit_code == 1
    assert "✘ NON imprimable en vase mode" in r.output
    assert "contour non unique" in r.output
    assert html.exists() and "<svg" in html.read_text(encoding="utf-8")


def test_check_skin_damage_with_original(tmp_path: Path):
    wing, _ = naca_wing()
    bad = save(tmp_path, "bad.step", broken_wing("skin"))
    orig = save(tmp_path, "orig.step", wing)
    r = runner.invoke(app, ["check", str(bad), "--original", str(orig), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 1
    assert "peau entamée" in r.output


def test_check_original_mismatch(tmp_path: Path):
    wing, _ = naca_wing()
    a = write_step(
        tmp_path / "a.step",
        [NamedBody(("SKIN_a",), wing.solids()[0]), NamedBody(("SKIN_b",), wing.solids()[0])],
    )
    o = write_step(
        tmp_path / "o.step",
        [NamedBody(("SKIN_x",), wing.solids()[0]), NamedBody(("SKIN_y",), wing.solids()[0])],
    )
    r = runner.invoke(app, ["check", str(a), "--original", str(o), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 1
    assert "erreur : pas de peau d'origine pour SKIN_a" in r.output
```

- [ ] **Step 2 : lancer** — `uv run pytest tests/test_cli_check.py -v` → FAIL.

- [ ] **Step 3 : ajouter à `src/aerorib/cli.py`**

Imports supplémentaires :
```python
from aerorib.report import format_text, write_html
from aerorib.validate import validate
```

Commande :
```python
def _skins(file: Path, cfg: Config):
    try:
        return classify(read_step(file), cfg).of("skin")
    except (FileNotFoundError, StepReadError, ClassifyError) as exc:
        _fail(str(exc))


@app.command()
def check(
    file: Path = typer.Argument(..., help="Pièce à vérifier (STEP)."),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Fichier de configuration YAML."),
    original: Optional[Path] = typer.Option(None, "--original", help="STEP d'origine, pour vérifier la peau."),
    html: Optional[Path] = typer.Option(None, "--html", help="Écrit un rapport HTML."),
) -> None:
    """Vérifie qu'une pièce est imprimable en vase mode."""
    cfg = _load(config)
    skins = _skins(file, cfg)
    originals = {}
    if original is not None:
        orig_skins = _skins(original, cfg)
        by_name = {s.body.name: s.body.solid for s in orig_skins}
        for s in skins:
            if s.body.name in by_name:
                originals[s.body.name] = by_name[s.body.name]
            elif len(skins) == 1 and len(orig_skins) == 1:
                originals[s.body.name] = orig_skins[0].body.solid
            else:
                _fail(f"pas de peau d'origine pour {s.body.name}")
    all_ok = True
    for s in skins:
        report = validate(s.body.solid, cfg, original=originals.get(s.body.name))
        all_ok &= report.ok
        typer.echo(format_text(report, title=s.body.display))
        if html is not None:
            target = html if len(skins) == 1 else html.with_name(f"{html.stem}-{s.body.name}{html.suffix}")
            write_html(report, target, title=s.body.display)
            typer.echo(f"  rapport : {target}")
    if not all_ok:
        raise typer.Exit(1)
```

- [ ] **Step 4 : lancer** — `uv run pytest tests/test_cli_check.py -v` → PASS.

- [ ] **Step 5 : test manuel en vraie grandeur (couches de 0,2 mm)**

```bash
uv run python -c "from aerorib.testing.synthetic import ribbed_wing, broken_wing; from aerorib.io_step import NamedBody, write_step; write_step('out/good.step',[NamedBody(('SKIN',), ribbed_wing().solids()[0])]); write_step('out/bad.step',[NamedBody(('SKIN',), broken_wing('hole').solids()[0])])"
uv run aerorib check out/good.step
uv run aerorib check out/bad.step --html out/bad.html
```
(créer `out/` avant ; ne pas utiliser la variante `island` ici, elle contient 2 solides et `solids()[0]` n'en garderait qu'un). Attendu : `good` → ✔, 500 couches, en quelques secondes ; `bad` → ✘, contour non unique (trou isolé), `out/bad.html` lisible dans un navigateur. Noter le temps d'exécution dans le message de commit.

- [ ] **Step 6 : commit**

```bash
git add src/aerorib/cli.py tests/test_cli_check.py
git commit -m "feat(cli): commande check avec rapport HTML"
```

---

### Task 11 : Fixtures réelles, CI et README

**Files:**
- Create: `tests/fixtures/README.md`, `tests/test_real_fixtures.py`, `.github/workflows/tests.yml`, `README.md`

**Interfaces:**
- Consumes : `read_step`, `classify`, `load_config`.
- Produces : un test paramétré sur chaque `tests/fixtures/*.step` / `*.stp`, sauté s'il n'y en a aucun ; la CI Windows + macOS.

- [ ] **Step 1 : `tests/fixtures/README.md`**

```markdown
# Fixtures réelles

Déposer ici des exports STEP **réels** depuis SolidWorks et Fusion 360 :

- la même pièce simple dans les deux logiciels : un tronçon d'aile nommé `SKIN…` et un longeron cylindrique nommé `SPAR…` ;
- nom de fichier : `<logiciel>_<description>.step`, ex. `solidworks_aile_simple.step`, `fusion_aile_simple.step` ;
- au moins un export Fusion en **pouces**, pour vérifier la conversion d'unités (`fusion_aile_simple_inch.step`).

Chaque fichier doit, après classification, contenir au moins une peau et un longeron.
Les fichiers `*_inch.step` doivent avoir un longeron de même diamètre (en mm) que leur jumeau en mm.
```

- [ ] **Step 2 : `tests/test_real_fixtures.py`**

```python
from pathlib import Path

import pytest

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import read_step

FIXTURES = Path(__file__).parent / "fixtures"
FILES = sorted([*FIXTURES.glob("*.step"), *FIXTURES.glob("*.stp")])


@pytest.mark.skipif(not FILES, reason="aucune fixture réelle dans tests/fixtures/")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_real_export_has_skin_and_spar(path: Path):
    model = classify(read_step(path), load_config())
    assert model.of("skin"), "aucune peau détectée"
    assert model.of("spar"), "aucun longeron détecté"


@pytest.mark.parametrize(
    "inch", [p for p in FILES if p.stem.endswith("_inch")], ids=lambda p: p.name
)
def test_inch_export_matches_mm_twin(inch: Path):
    twin = inch.with_name(inch.stem.removesuffix("_inch") + inch.suffix)
    if not twin.exists():
        pytest.skip(f"jumeau en mm absent : {twin.name}")
    cfg = load_config()
    d_in = classify(read_step(inch), cfg).of("spar")[0].spar.diameter
    d_mm = classify(read_step(twin), cfg).of("spar")[0].spar.diameter
    assert d_in == pytest.approx(d_mm, rel=1e-4)
```

- [ ] **Step 3 : lancer** — `uv run pytest tests/test_real_fixtures.py -v` → SKIPPED (normal tant que le dossier est vide).

- [ ] **Step 4 : `.github/workflows/tests.yml`**

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync
      - run: uv run pytest -m "not slow" -v
```

- [ ] **Step 5 : `README.md`**

````markdown
# AeroRib

Transforme une pièce CAO (STEP) en pièce imprimable en **vase mode** sur Bambu Lab :
nervures internes, logements de longerons, découpe en tronçons.
Voir la spec : `docs/specs/2026-10-09-aerorib-design.md`.

## Installation

1. Installer [uv](https://docs.astral.sh/uv/) : `brew install uv` (macOS) ou `winget install astral-sh.uv` (Windows).
2. Dans ce dossier : `uv sync`

## Usage (P0)

```bash
uv run aerorib inspect piece.step              # corps trouvés et rôles
uv run aerorib check piece.step                # imprimable en vase mode ?
uv run aerorib check piece.step --original origine.step --html rapport.html
```

## Nommage dans la CAO

| Préfixe | Rôle |
|---|---|
| `SKIN`, `Wing`, `Fuse` | corps à traiter |
| `SPAR`, `Longeron` | longeron (diamètre et axe détectés) |
| `SPLIT` | surface de séparation manuelle |
| `NORIB` | zone sans nervures |
| `CUT` | plan de découpe forcé |

Insensible à la casse. Repli possible dans la config : `roles: {Body3: spar}`.

## Tests

```bash
uv run pytest            # tous les tests
uv run pytest -m "not slow"
```
````

- [ ] **Step 6 : suite complète**

Run : `uv run pytest -v` → tout PASS (fixtures réelles SKIPPED).

- [ ] **Step 7 : commit**

```bash
git add tests/fixtures/README.md tests/test_real_fixtures.py .github/workflows/tests.yml README.md
git commit -m "chore: fixtures réelles, CI Windows/macOS, README"
```

---

## Couverture de la spec par P0

| Spec | Tâche |
|---|---|
| §4 choix techniques (Python, build123d, trimesh/shapely, pydantic, typer, uv, pytest, CI) | 1, 6, 7, 11 |
| §5 modules `config`, `io_step`, `classify`, `validate`, `cli` | 2, 4, 5, 7, 9, 10 |
| §6 STEP + noms, multi-niveaux, insensible à la casse, repli `roles:`, `inspect` | 4, 5, 9 |
| §8 1 contour/couche, fentes ≥ seuil, peau intacte, stats, rapport texte + HTML, code retour | 7, 8, 10 |
| §8 surplomb, fragments, plis, longeron, volume | reportés (voir « Hors périmètre ») |
| §9 niveaux 1, 2 (pièces synthétiques), 3 (fixtures réelles), CI | 2–11 |
| §10 couches de config (défauts, preset, YAML, overrides) | 2 |
| §13 CLI `inspect`, `check` | 9, 10 |
