"""Profils matière : fichiers YAML (embarqués ou de l'utilisateur) portant les paramètres géométriques.

Un profil utilisateur est FUSIONNÉ sur le profil embarqué de même nom (il ne change que ce qu'il contient) ;
sans équivalent embarqué, il est utilisé seul. Priorité croissante : embarqué < ./profiles/ <
dossier de la variable d'environnement BEAVER_PROFILES.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from beaver.env import lire_env

ENV_VAR = "BEAVER_PROFILES"  # repli : AERORIB_PROFILES (ancien nom)
# clés de config que le profil peut porter (le reste — naming, roles, output… — relève du projet)
PROFILE_KEYS = frozenset(
    {"material_settings", "slit_width", "split", "rib_layers", "spars", "passages", "bambu"}
)


class ProfileError(ValueError):
    """Profil matière introuvable ou illisible (message en français)."""


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    path: Path  # fichier de plus haute priorité
    origin: str  # "embarqué" | "utilisateur"
    layers: tuple[Path, ...] = ()  # tous les fichiers fusionnés, du moins au plus prioritaire


def embedded_dir() -> Path:
    return Path(str(resources.files("beaver") / "profiles"))


def _search_dirs() -> list[tuple[Path, str]]:
    """Du moins au plus prioritaire."""
    dirs: list[tuple[Path, str]] = [(embedded_dir(), "embarqué"), (Path.cwd() / "profiles", "utilisateur")]
    env = lire_env("PROFILES")
    if env:
        d = Path(env).expanduser()
        if not d.is_dir():
            raise ProfileError(f"dossier de profils introuvable : {d} (variable {ENV_VAR})")
        dirs.append((d, "utilisateur"))
    return dirs


def _is_profile(path: Path) -> bool:
    """Un .yaml compte comme profil s'il est un dictionnaire (un YAML mal formé compte : il sera signalé)."""
    try:
        return isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
    except yaml.YAMLError:
        return True
    except (OSError, UnicodeDecodeError):
        return True


def find_profiles() -> dict[str, ProfileInfo]:
    layers: dict[str, list[tuple[Path, str]]] = {}
    for directory, origin in _search_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if _is_profile(path):
                layers.setdefault(path.stem, []).append((path, origin))
    return {
        name: ProfileInfo(
            name,
            ls[-1][0],
            "embarqué" if all(o == "embarqué" for _, o in ls) else "utilisateur",
            tuple(p for p, _ in ls),
        )
        for name, ls in layers.items()
    }


def profile_names() -> list[str]:
    return sorted(find_profiles())


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _read_layer(path: Path) -> dict[str, Any]:
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
    if isinstance(bambu, dict) and isinstance(bambu.get("template"), str) and bambu["template"] != "auto":
        data["bambu"] = {**bambu, "template": str((path.parent / bambu["template"]).expanduser().resolve())}
    return data


def load_profile(name: str) -> tuple[dict[str, Any], tuple[Path, ...]]:
    """Données fusionnées du profil (gabarit Bambu résolu en chemin absolu) et fichiers qui le composent."""
    profiles = find_profiles()
    if name not in profiles:
        raise ProfileError(f"matière inconnue '{name}' (profils disponibles : {', '.join(sorted(profiles))})")
    data: dict[str, Any] = {}
    for path in profiles[name].layers:
        data = _deep_merge(data, _read_layer(path))
    return data, profiles[name].layers


_COPY_HEADER = """\
# Copie éditable du profil embarqué. Ce fichier est FUSIONNÉ sur le profil embarqué de même nom :
# seules les clés présentes ici changent (vous pouvez en supprimer). bambu.template n'est pas copié :
# il est hérité du profil embarqué ; ajoutez `bambu: {template: chemin.3mf}` pour en imposer un autre.
"""


def copy_profile(name: str, dest_dir: Path) -> Path:
    """Copie le profil embarqué `name` dans dest_dir pour édition ; refuse d'écraser un fichier existant."""
    source = embedded_dir() / f"{name}.yaml"
    if not source.is_file():
        embedded = sorted(p.stem for p in embedded_dir().glob("*.yaml"))
        raise ProfileError(f"pas de profil embarqué '{name}' (embarqués : {', '.join(embedded)})")
    target = Path(dest_dir) / source.name
    if target.exists():
        raise ProfileError(f"{target} existe déjà : supprimez-le ou éditez-le directement")
    lines = source.read_text(encoding="utf-8").splitlines()
    cut = next((i for i, ln in enumerate(lines) if ln.startswith("bambu:")), len(lines))
    text = _COPY_HEADER + "\n".join(lines[:cut]).rstrip() + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
