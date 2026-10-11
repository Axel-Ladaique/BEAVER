"""API des profils matière (onglet « Profils »).

Dossier des profils utilisateur : résolu UNE fois à la création de l'application, comme `material_profiles`
(variable `AERORIB_PROFILES` si définie, sinon `./profiles` du répertoire de lancement) ; injectable pour les
tests. Les valeurs d'un profil = couche embarquée (si elle existe) fusionnée avec `<dossier>/<nom>.yaml`.
Le second dossier lu par le cœur (`./profiles` quand `AERORIB_PROFILES` est défini) n'est pas pris en compte ici.

Écriture (`PUT`) : le serveur reçoit les valeurs complètes de l'éditeur, calcule la différence imbriquée avec
l'embarqué de même nom et n'écrit QUE cette différence (les listes comme `rib_layers` sont remplacées en bloc).
Sans profil embarqué du même nom, tout est écrit. Différence vide : le fichier n'est jamais supprimé ; s'il
existe il devient `{}` (mapping vide, profil toujours valide), sinon il reste absent. Le gabarit Bambu est exposé
par son nom de fichier seul ; la valeur identique au gabarit embarqué n'est jamais écrite.

Copie (`POST …/copier`) : sans corps, comme `aerorib profils --copier` (profil embarqué copié tel quel, sans
`bambu.template`, vers `<dossier>/<nom>.yaml`). Avec `{"vers": "nouveau"}`, les valeurs fusionnées actuelles du
profil sont copiées dans un NOUVEAU profil autonome `<dossier>/nouveau.yaml` (gabarit à null) ; le nom visé ne
doit être ni un profil embarqué ni un fichier utilisateur existant (409).
"""

from __future__ import annotations

import copy
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body
from pydantic import ValidationError

from aerorib.config import Config
from aerorib.gui.projects import ProjectError, _flatten, _is_set, scrub_paths
from aerorib.material_profiles import (
    ENV_VAR,
    ProfileError,
    _deep_merge,
    _is_profile,
    _read_layer,
    copy_profile,
    embedded_dir,
)

NAME_RE = re.compile(r"[a-z0-9_]{1,40}")
_MSG = {"missing": "champ manquant", "extra_forbidden": "champ inconnu"}


def default_user_dir() -> Path:
    env = os.environ.get(ENV_VAR)
    return Path(env).expanduser() if env else Path.cwd() / "profiles"


def check_name(nom: str) -> str:
    if not NAME_RE.fullmatch(nom or ""):
        raise ProjectError(
            422, "nom de profil invalide : 1 à 40 caractères parmi minuscules sans accent, chiffres et « _ »"
        )
    return nom


def _diff(base: Any, new: Any) -> Any:
    """Plus petit sous-ensemble imbriqué de `new` qui, fusionné sur `base`, redonne `new` (None = identique)."""
    if isinstance(base, dict) and isinstance(new, dict):
        out = {}
        for k, v in new.items():
            if k not in base:
                out[k] = v
            else:
                d = _diff(base[k], v)
                if d is not None or (v is None and base[k] is not None):
                    out[k] = d
        return out or None
    return None if base == new and type(base) is type(new) else new


def _with_template_name(data: dict) -> dict:
    """Copie où bambu.template (chemin) est réduit à son nom de fichier."""
    out = copy.deepcopy(data)
    bambu = out.get("bambu")
    if isinstance(bambu, dict) and isinstance(bambu.get("template"), str):
        bambu["template"] = Path(bambu["template"]).name
    return out


def _fix(exc: Exception, *, tmp: Path | None = None, nom: str = "") -> str:
    text = str(exc)
    if tmp is not None:
        text = text.replace(str(tmp), f"{nom}.yaml")
    return scrub_paths(text)


def _config_message(exc: ValidationError) -> str:
    lignes = []
    for e in exc.errors():
        champ = ".".join(str(x) for x in e["loc"]) or "profil"
        kind = e["type"]
        if kind in _MSG:
            msg = _MSG[kind]
        elif kind == "value_error":
            msg = str(e["msg"]).removeprefix("Value error, ")
        else:
            msg = f"valeur invalide ({e['msg']})"
        lignes.append(f"champ « {champ} » : {msg}")
    return "profil invalide : " + " ; ".join(lignes)


class ProfileStore:
    def __init__(self, user_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir is not None else default_user_dir()
        self._lock = threading.Lock()

    # --- lecture -----------------------------------------------------------------------------
    def _embedded_path(self, nom: str) -> Path | None:
        p = embedded_dir() / f"{nom}.yaml"
        return p if p.is_file() else None

    def _user_path(self, nom: str) -> Path | None:
        p = self.user_dir / f"{nom}.yaml"
        return p if p.is_file() and _is_profile(p) else None

    def _layers(self, nom: str) -> tuple[dict, dict, bool, bool]:
        """(couche embarquée, couche utilisateur, a un embarqué, a un utilisateur) ; `_read_layer` résout le gabarit."""
        emb, usr = self._embedded_path(nom), self._user_path(nom)
        try:
            return (
                _read_layer(emb) if emb else {},
                _read_layer(usr) if usr else {},
                emb is not None,
                usr is not None,
            )
        except ProfileError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc

    @staticmethod
    def _gabarit(merged: dict) -> dict:
        tpl = (merged.get("bambu") or {}).get("template")
        if not isinstance(tpl, str):
            return {"chemin_nom": None, "existe": False}
        try:
            existe = Path(tpl).is_file()
        except OSError:
            existe = False
        return {"chemin_nom": Path(tpl).name, "existe": existe}

    def get(self, nom: str) -> dict:
        check_name(nom)
        emb, usr, has_emb, has_usr = self._layers(nom)
        if not has_emb and not has_usr:
            raise ProjectError(404, f"profil inconnu : « {nom} »")
        merged = _deep_merge(emb, usr)
        valeurs = _with_template_name(merged)
        provenance = [
            {"chemin": list(loc), "origine": "utilisateur" if _is_set(usr, loc) else "embarqué"}
            for loc in _flatten(valeurs)
        ]
        return {
            "nom": nom,
            "origine": "utilisateur" if has_usr else "embarqué",
            "embarque": has_emb,
            "valeurs": valeurs,
            "provenance": provenance,
            "gabarit": self._gabarit(merged),
        }

    def list(self) -> list[dict]:
        noms = {p.stem for p in embedded_dir().glob("*.yaml")}
        if self.user_dir.is_dir():
            noms |= {p.stem for p in self.user_dir.glob("*.yaml") if NAME_RE.fullmatch(p.stem) and _is_profile(p)}
        out = []
        for nom in sorted(noms):
            try:
                d = self.get(nom)
            except ProjectError as exc:  # profil illisible : listé, avec son erreur
                out.append({"nom": nom, "origine": "utilisateur", "gabarit": {"chemin_nom": None, "existe": False},
                            "existe": False, "erreur": exc.message})
                continue
            out.append({"nom": nom, "origine": d["origine"], "gabarit": d["gabarit"], "existe": d["gabarit"]["existe"]})
        return out

    # --- écriture ----------------------------------------------------------------------------
    def _write(self, nom: str, texte: str, validate) -> None:
        """Écriture atomique (temporaire + fsync + replace, droits suivant le umask), `validate(tmp)` avant remplacement."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.user_dir, prefix=f".{nom}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(texte)
                fh.flush()
                os.fsync(fh.fileno())
            validate(tmp)
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
            os.replace(tmp, self.user_dir / f"{nom}.yaml")
        finally:
            tmp.unlink(missing_ok=True)

    def _validated(self, nom: str, emb_layer: dict, usr_layer: dict) -> None:
        try:
            Config.model_validate(_deep_merge(emb_layer, usr_layer))
        except ValidationError as exc:
            raise ProjectError(422, scrub_paths(_config_message(exc))) from exc

    def put(self, nom: str, donnees: Any) -> dict:
        check_name(nom)
        if not isinstance(donnees, dict):
            raise ProjectError(422, "« donnees » doit être un dictionnaire de valeurs de profil")
        emb_path = self._embedded_path(nom)
        try:
            raw = yaml.safe_load(emb_path.read_text(encoding="utf-8")) if emb_path else {}
        except (OSError, yaml.YAMLError) as exc:  # ne devrait pas arriver (profil du paquet)
            raise ProjectError(500, "profil embarqué illisible") from exc
        base = _with_template_name(raw or {})
        diff = _diff(base, donnees) or {}
        usr_exists = self.user_dir.joinpath(f"{nom}.yaml").is_file()
        texte = yaml.safe_dump(diff, allow_unicode=True, sort_keys=False) if diff else "{}\n"
        emb_layer = _read_layer(emb_path) if emb_path else {}

        def validate(tmp: Path) -> None:
            try:
                usr_layer = _read_layer(tmp)
            except ProfileError as exc:
                raise ProjectError(422, _fix(exc, tmp=tmp, nom=nom)) from exc
            self._validated(nom, emb_layer, usr_layer)

        with self._lock:
            if not diff and not usr_exists:
                self._validated(nom, emb_layer, {})  # rien à écrire : le fichier reste absent
            else:
                self._write(nom, texte, validate)
        return self.get(nom)

    def copy(self, nom: str, vers: Any) -> dict:
        check_name(nom)
        with self._lock:
            if vers is None:
                try:
                    if (self.user_dir / f"{nom}.yaml").exists():
                        raise ProjectError(409, f"{nom}.yaml existe déjà : supprimez-le ou éditez-le directement")
                    copy_profile(nom, self.user_dir)
                except ProfileError as exc:
                    raise ProjectError(404, _fix(exc)) from exc
                return self.get(nom)
            if not isinstance(vers, str):
                raise ProjectError(422, "« vers » doit être un nom de profil")
            check_name(vers)
            self.get(nom)  # 404 si la source n'existe pas
            if self._embedded_path(vers) is not None:
                raise ProjectError(409, f"un profil embarqué porte déjà le nom « {vers} »")
            if (self.user_dir / f"{vers}.yaml").exists():
                raise ProjectError(409, f"{vers}.yaml existe déjà")
            valeurs = _with_template_name(_deep_merge(*self._layers(nom)[:2]))
            valeurs.setdefault("bambu", {})["template"] = None
            entete = f"# Copie du profil « {nom} » : profil autonome (gabarit Bambu à renseigner dans bambu.template).\n"
            texte = entete + yaml.safe_dump(valeurs, allow_unicode=True, sort_keys=False)
            self._write(vers, texte, lambda tmp: self._validated(vers, {}, _read_layer(tmp)))
        return self.get(vers)


def build_router(store: ProfileStore) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/profils")
    def liste() -> list[dict]:
        return store.list()

    @router.get("/profils/{nom}")
    def lire(nom: str) -> dict:
        return store.get(nom)

    @router.put("/profils/{nom}")
    def ecrire(nom: str, body: dict) -> dict:
        if "donnees" not in body:
            raise ProjectError(422, "corps attendu : {\"donnees\": {...}}")
        return store.put(nom, body["donnees"])

    @router.post("/profils/{nom}/copier", status_code=201)
    def copier(nom: str, body: dict | None = Body(None)) -> dict:
        return store.copy(nom, (body or {}).get("vers"))

    return router
