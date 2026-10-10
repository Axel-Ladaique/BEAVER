"""Profils matière : fichiers YAML (embarqués ou de l'utilisateur) portant les paramètres géométriques.

Recherche, fichier par fichier, dans l'ordre : dossier de la variable d'environnement AERORIB_PROFILES,
puis ./profiles/ (répertoire courant), puis les profils embarqués. Le premier trouvé gagne.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

ENV_VAR = "AERORIB_PROFILES"
# clés de config que le profil peut porter (le reste — naming, roles, output… — relève du projet)
PROFILE_KEYS = frozenset(
    {"material_settings", "slit_width", "split", "rib_layers", "spars", "passages", "bambu"}
)


class ProfileError(ValueError):
    """Profil matière introuvable ou illisible (message en français)."""


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    path: Path
    origin: str  # "embarqué" | "utilisateur"


def embedded_dir() -> Path:
    return Path(str(resources.files("aerorib") / "profiles"))


def _search_dirs() -> list[tuple[Path, str]]:
    dirs: list[tuple[Path, str]] = []
    env = os.environ.get(ENV_VAR)
    if env:
        dirs.append((Path(env), "utilisateur"))
    dirs.append((Path.cwd() / "profiles", "utilisateur"))
    dirs.append((embedded_dir(), "embarqué"))
    return dirs


def find_profiles() -> dict[str, ProfileInfo]:
    found: dict[str, ProfileInfo] = {}
    for directory, origin in _search_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            found.setdefault(path.stem, ProfileInfo(path.stem, path, origin))
    return found


def profile_names() -> list[str]:
    return sorted(find_profiles())


def load_profile(name: str) -> tuple[dict[str, Any], Path]:
    """Données du profil (gabarit Bambu relatif résolu en chemin absolu) et fichier d'origine."""
    profiles = find_profiles()
    if name not in profiles:
        raise ProfileError(f"matière inconnue '{name}' (profils disponibles : {', '.join(sorted(profiles))})")
    path = profiles[name].path
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ProfileError(f"profil matière illisible : {path} ({exc.strerror or exc})") from exc
    except UnicodeDecodeError as exc:
        raise ProfileError(f"profil matière {path} : le fichier doit être encodé en UTF-8") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"profil matière {path} : YAML mal formé ({exc})") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profil matière {path} : doit être un dictionnaire YAML")
    unknown = set(data) - PROFILE_KEYS
    if unknown:
        raise ProfileError(
            f"profil matière {path} : clé(s) non autorisée(s) {', '.join(sorted(unknown))} "
            f"(autorisées : {', '.join(sorted(PROFILE_KEYS))})"
        )
    bambu = data.get("bambu")
    if isinstance(bambu, dict) and isinstance(bambu.get("template"), str):
        data["bambu"] = {**bambu, "template": str((path.parent / bambu["template"]).resolve())}
    return data, path


def copy_profile(name: str, dest_dir: Path) -> Path:
    """Copie le profil embarqué `name` dans dest_dir pour édition ; refuse d'écraser un fichier existant."""
    source = embedded_dir() / f"{name}.yaml"
    if not source.is_file():
        embedded = sorted(p.stem for p in embedded_dir().glob("*.yaml"))
        raise ProfileError(f"pas de profil embarqué '{name}' (embarqués : {', '.join(embedded)})")
    target = Path(dest_dir) / source.name
    if target.exists():
        raise ProfileError(f"{target} existe déjà : supprimez-le ou éditez-le directement")
    text = source.read_text(encoding="utf-8")
    # le gabarit embarqué est relatif au dossier du paquet : le rendre absolu pour la copie
    text = text.replace("../templates/", (source.parent.parent / "templates").as_posix() + "/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
