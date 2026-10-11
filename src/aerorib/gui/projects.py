"""Stockage des projets de l'interface : un dossier par projet sous la racine (piece.step + config.yaml)."""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from aerorib.classify import ClassifyError, classify
from aerorib.config import Config, ConfigError, load_config
from aerorib.io_step import StepReadError, read_step
from aerorib.material_profiles import ProfileError, find_profiles, load_profile

MAX_STEP_BYTES = 200 * 1024 * 1024
STEP_NAME = "piece.step"
CONFIG_NAME = "config.yaml"
_L = "A-Za-z0-9\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff"  # ASCII + lettres latines accentuées
_NAME_OK = re.compile(rf"^[{_L}][{_L} _.\-]{{0,63}}$")
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[^\s'\"():,\\/]+[\\/])+([^\s'\"():,\\/]+)")


def scrub_paths(message: str) -> str:
    """Ne garde que les noms de fichiers : aucun chemin absolu dans les messages destinés à l'écran."""
    return _PATH_RE.sub(r"\1", message)
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ProjectError(Exception):
    """Erreur de projet : `status` HTTP et message français."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def sanitize_name(nom: str) -> str:
    nom = nom or ""
    if (
        nom.strip() != nom
        or not _NAME_OK.match(nom)
        or nom.endswith((".", " "))
        or nom.split(".")[0].strip().lower() in _WIN_RESERVED
    ):
        raise ProjectError(
            422,
            "nom de projet invalide : 1 à 64 caractères (lettres, chiffres, espace, « - », « _ », « . »), "
            "sans espace en début ou fin, sans séparateur de chemin ni point initial",
        )
    return nom


def _flatten(data: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """Feuilles d'un dictionnaire imbriqué (les listes et dictionnaires vides sont des feuilles)."""
    if isinstance(data, dict) and data:
        out: dict[tuple[str, ...], Any] = {}
        for k, v in data.items():
            out.update(_flatten(v, prefix + (str(k),)))
        return out
    return {prefix: data}


def _is_set(data: Any, loc: tuple[str, ...]) -> bool:
    """`loc` est-il posé dans `data` (une liste posée remplace celle de la couche inférieure en bloc) ?"""
    for key in loc:
        if isinstance(data, list):
            return True
        if not isinstance(data, dict) or key not in data:
            return False
        data = data[key]
    return True


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, nom: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(nom, threading.Lock())

    # --- chemins -----------------------------------------------------------------------------
    def _dir(self, nom: str) -> Path:
        nom = sanitize_name(nom)
        root = self.root.resolve()
        d = (root / nom).resolve()
        if d.parent != root:
            raise ProjectError(422, "nom de projet invalide : hors du dossier des projets")
        return d

    def _existing(self, nom: str) -> Path:
        d = self._dir(nom)
        if not d.is_dir():
            raise ProjectError(404, f"projet introuvable : {nom}")
        return d

    def step_path(self, nom: str) -> Path:
        return self._existing(nom) / STEP_NAME

    def config_path(self, nom: str) -> Path:
        return self._existing(nom) / CONFIG_NAME

    # --- liste / création --------------------------------------------------------------------
    @staticmethod
    def _summary(d: Path) -> dict:
        step = d / STEP_NAME
        ref = step if step.exists() else d
        essais = d / "essais"
        nb = sum(1 for p in essais.iterdir() if p.is_dir()) if essais.is_dir() else 0
        return {"nom": d.name, "cree": datetime.fromtimestamp(ref.stat().st_mtime).isoformat(timespec="seconds"),
                "nb_essais": nb}

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        dirs = [d for d in self.root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and (d / STEP_NAME).is_file()]
        return sorted((self._summary(d) for d in dirs), key=lambda s: s["nom"].lower())

    def create(self, nom: str, source: bytes | BinaryIO, filename: str, *, material: str | None = None) -> dict:
        nom = sanitize_name(nom)
        if Path(filename or "").suffix.lower() not in (".step", ".stp"):
            raise ProjectError(422, "seuls les fichiers .step et .stp sont acceptés")
        d = self._dir(nom)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            d.mkdir()
        except FileExistsError:
            raise ProjectError(409, f"le projet « {nom} » existe déjà") from None
        try:
            stream = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
            total = 0
            with (d / STEP_NAME).open("wb") as out:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_STEP_BYTES:
                        raise ProjectError(413, "fichier trop volumineux (max 200 Mo)")
                    out.write(chunk)
            if total == 0:
                raise ProjectError(422, "fichier vide")
            try:
                read_step(d / STEP_NAME)
            except StepReadError as exc:
                raise ProjectError(422, str(exc).replace(str(d / STEP_NAME), filename)) from exc
            text = f"material: {material}\n" if material else "{}\n"
            (d / CONFIG_NAME).write_text(text, encoding="utf-8")
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        return self._summary(d)

    def get(self, nom: str) -> dict:
        d = self._existing(nom)
        if not (d / STEP_NAME).is_file():
            raise ProjectError(404, f"projet introuvable : {nom}")
        return {**self._summary(d), "piece": STEP_NAME, "taille_step": (d / STEP_NAME).stat().st_size}

    # --- inspection --------------------------------------------------------------------------
    def inspect(self, nom: str) -> dict:
        d = self._existing(nom)
        cfg = self._load(d)
        try:
            model = classify(read_step(d / STEP_NAME), cfg)
        except (StepReadError, ClassifyError, FileNotFoundError) as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc
        corps = []
        for cb in model.bodies:
            s = cb.spar
            corps.append({
                "nom": cb.body.display,
                "role": cb.role,
                "regle": cb.matched_by,
                "note": cb.body.note,
                "longeron": None if s is None else {
                    "diametre": s.diameter, "axe": list(s.axis_dir), "longueur": s.length},
            })
        diam = [c["longeron"]["diametre"] for c in corps if c["longeron"]]
        return {"corps": corps, "diametre_longeron": diam[0] if diam else None}

    # --- configuration -----------------------------------------------------------------------
    @staticmethod
    def _load(d: Path) -> Config:
        try:
            return load_config(d / CONFIG_NAME)
        except ConfigError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc

    def read_config(self, nom: str) -> dict:
        d = self._existing(nom)
        path = d / CONFIG_NAME
        try:
            texte = path.read_text(encoding="utf-8") if path.exists() else "{}\n"
        except UnicodeDecodeError:
            raise ProjectError(422, f"{CONFIG_NAME} : la configuration doit être encodée en UTF-8") from None
        except OSError as exc:
            raise ProjectError(422, f"{CONFIG_NAME} illisible : {exc.strerror or exc}") from exc
        try:
            donnees = yaml.safe_load(texte) or {}
        except yaml.YAMLError:
            donnees = None
        return {"texte": texte, "donnees": donnees}

    def write_config(self, nom: str, data: dict | str) -> dict:
        """Valide avec load_config puis remplace atomiquement ; en cas d'erreur, rien n'est écrit."""
        d = self._existing(nom)
        if isinstance(data, str):
            texte = data
        elif isinstance(data, dict):
            texte = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        else:
            raise ProjectError(422, "la configuration doit être un dictionnaire ou un texte YAML")
        with self._lock(d.name):
            fd, tmp_name = tempfile.mkstemp(dir=d, prefix=".config.", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                    fh.write(texte)
                    fh.flush()
                    os.fsync(fh.fileno())
                try:
                    load_config(tmp)
                except ConfigError as exc:
                    raise ProjectError(422, scrub_paths(str(exc).replace(str(tmp), CONFIG_NAME))) from exc
                os.replace(tmp, d / CONFIG_NAME)
            finally:
                tmp.unlink(missing_ok=True)
        return self.read_config(nom)

    def effective_config(self, nom: str) -> dict:
        d = self._existing(nom)
        cfg = self._load(d)
        try:
            project = yaml.safe_load((d / CONFIG_NAME).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ProjectError(422, f"{CONFIG_NAME} illisible : {scrub_paths(str(exc))}") from exc
        try:
            profile, _ = load_profile(cfg.material)
        except ProfileError as exc:
            raise ProjectError(422, str(exc)) from exc
        valeurs = cfg.model_dump(mode="json")
        provenance = []
        for loc in _flatten(valeurs):
            if _is_set(project, loc):
                origine = "projet"
            elif _is_set(profile, loc):
                origine = "profil"
            else:
                origine = "défaut"
            provenance.append({"chemin": list(loc), "origine": origine})
        return {"valeurs": valeurs, "provenance": provenance}

    @staticmethod
    def schema() -> dict:
        profils = [{"nom": n, "origine": p.origin} for n, p in sorted(find_profiles().items())]
        return {"schema": Config.model_json_schema(), "matieres": [p["nom"] for p in profils], "profils": profils}
