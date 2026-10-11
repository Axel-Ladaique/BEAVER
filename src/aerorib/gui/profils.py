"""API des profils matière (onglet « Profils »).

Dossier des profils utilisateur : résolu UNE fois à la création de l'application, comme `material_profiles`
(variable `AERORIB_PROFILES` si définie, sinon `./profiles` du répertoire de lancement) ; injectable pour les
tests. Les valeurs d'un profil = couche embarquée (si elle existe) fusionnée avec `<dossier>/<nom>.yaml`.
Le dossier d'écriture est le seul où l'API écrit ; la lecture suit toutes les couches du cœur.

Lecture : exactement les couches du cœur (`find_profiles`/`load_profile` : embarqué < ./profiles < $AERORIB_PROFILES).
Écriture (`PUT`) : le serveur reçoit les valeurs complètes de l'éditeur, calcule la différence imbriquée avec
la fusion de toutes les couches INFÉRIEURES au fichier écrit et n'écrit QUE cette différence (5 == 5.0) (les listes comme `rib_layers` sont remplacées en bloc).
Sans couche inférieure, tout est écrit. Les commentaires du fichier ne sont pas conservés. Différence vide : le fichier n'est jamais supprimé ; s'il
existe il devient `{}` (mapping vide, profil toujours valide), sinon il reste absent. `bambu.template` est en
lecture seule : exposé par son nom de fichier seul, ignoré dans `donnees`, et la valeur brute de la couche
utilisateur existante est conservée telle quelle (relative, absolue ou absente).

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
    _read_layer,
    copy_profile,
    embedded_dir,
    find_profiles,
)



def _read_umask() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return umask


_MODE = 0o666 & ~_read_umask()  # lu une fois à l'import ; le umask du processus n'est plus touché ensuite
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


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _same(a: Any, b: Any) -> bool:
    """Égalité de valeurs YAML/JSON : 5 et 5.0 sont identiques (un aller-retour JS perd le « .0 »), pas True et 1."""
    if _num(a) and _num(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


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
    return None if _same(base, new) else new


def _with_template_name(data: dict) -> dict:
    """Copie où bambu.template (chemin) est réduit à son nom de fichier (affichage seul)."""
    out = copy.deepcopy(data)
    bambu = out.get("bambu")
    if isinstance(bambu, dict) and isinstance(bambu.get("template"), str):
        bambu["template"] = Path(bambu["template"]).name
    return out


def _without_template(data: dict) -> dict:
    out = copy.deepcopy(data)
    bambu = out.get("bambu")
    if isinstance(bambu, dict):
        bambu.pop("template", None)
        if not bambu:
            del out["bambu"]
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
    """Lecture : exactement les couches du cœur (`find_profiles`/`load_profile`). Écriture : `user_dir` seul."""

    def __init__(self, user_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir is not None else default_user_dir()
        self._lock = threading.Lock()

    def _target(self, nom: str) -> Path:
        return self.user_dir / f"{nom}.yaml"

    @staticmethod
    def _info(nom: str):
        try:
            return find_profiles().get(nom)
        except ProfileError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc

    def _layer_data(self, paths) -> list[tuple[Path, dict]]:
        try:
            return [(p, _read_layer(p)) for p in paths]
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
        info = self._info(nom)
        if info is None:
            raise ProjectError(404, f"profil inconnu : « {nom} »")
        layers = self._layer_data(info.layers)
        merged: dict = {}
        for _, data in layers:
            merged = _deep_merge(merged, data)
        user = [d for p, d in layers if p.parent != embedded_dir()]
        valeurs = _with_template_name(merged)
        provenance = [
            {"chemin": list(loc), "origine": "utilisateur" if any(_is_set(u, loc) for u in user) else "embarqué"}
            for loc in _flatten(valeurs)
        ]
        return {
            "nom": nom,
            "origine": info.origin,
            "embarque": any(p.parent == embedded_dir() for p, _ in layers),
            "valeurs": valeurs,
            "provenance": provenance,
            "gabarit": self._gabarit(merged),
        }

    def list(self) -> list[dict]:
        try:
            noms = sorted(n for n in find_profiles() if NAME_RE.fullmatch(n))
        except ProfileError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc
        out = []
        for nom in noms:
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
        """Écriture atomique (temporaire + fsync + replace, droits 0666 & ~umask), `validate(tmp)` avant remplacement."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.user_dir, prefix=f".{nom}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(texte)
                fh.flush()
                os.fsync(fh.fileno())
            validate(tmp)
            os.chmod(tmp, _MODE)
            os.replace(tmp, self._target(nom))
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _validated(base: dict, usr_layer: dict) -> None:
        try:
            Config.model_validate(_deep_merge(base, usr_layer))
        except ValidationError as exc:
            raise ProjectError(422, scrub_paths(_config_message(exc))) from exc

    def _lower(self, nom: str) -> dict:
        """Fusion de toutes les couches du cœur sauf le fichier écrit."""
        info = self._info(nom)
        target = self._target(nom).resolve()
        paths = [p for p in (info.layers if info else ()) if p.resolve() != target]
        merged: dict = {}
        for _, data in self._layer_data(paths):
            merged = _deep_merge(merged, data)
        return merged

    def put(self, nom: str, donnees: Any) -> dict:
        check_name(nom)
        if not isinstance(donnees, dict):
            raise ProjectError(422, "« donnees » doit être un dictionnaire de valeurs de profil")
        with self._lock:
            lower = self._lower(nom)
            target = self._target(nom)
            raw_template = None  # valeur brute de la couche utilisateur : le gabarit n'est pas éditable ici
            if target.is_file():
                try:
                    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
                except (OSError, UnicodeDecodeError, yaml.YAMLError):
                    raw = {}
                if isinstance(raw, dict) and isinstance(raw.get("bambu"), dict):
                    raw_template = raw["bambu"].get("template")
            diff = _diff(_without_template(lower), _without_template(donnees)) or {}
            if raw_template is not None:
                diff.setdefault("bambu", {})["template"] = raw_template
            texte = yaml.safe_dump(diff, allow_unicode=True, sort_keys=False) if diff else "{}\n"

            def validate(tmp: Path) -> None:
                try:
                    usr_layer = _read_layer(tmp)
                except ProfileError as exc:
                    raise ProjectError(422, _fix(exc, tmp=tmp, nom=nom)) from exc
                self._validated(lower, usr_layer)

            if not diff and not target.is_file():
                self._validated(lower, {})  # rien à écrire : le fichier reste absent
            else:
                self._write(nom, texte, validate)
        return self.get(nom)

    def copy(self, nom: str, vers: Any) -> dict:
        check_name(nom)
        with self._lock:
            if vers is None:
                try:
                    if self._target(nom).exists():
                        raise ProjectError(409, f"{nom}.yaml existe déjà : supprimez-le ou éditez-le directement")
                    copy_profile(nom, self.user_dir)
                except ProfileError as exc:
                    raise ProjectError(404, _fix(exc)) from exc
                return self.get(nom)
            if not isinstance(vers, str):
                raise ProjectError(422, "« vers » doit être un nom de profil")
            check_name(vers)
            source = self.get(nom)  # 404 si la source n'existe pas
            if self._info(vers) is not None or self._target(vers).exists():
                raise ProjectError(409, f"un profil « {vers} » existe déjà")
            valeurs = copy.deepcopy(source["valeurs"])
            valeurs.setdefault("bambu", {})["template"] = None
            entete = f"# Copie du profil « {nom} » : profil autonome (gabarit Bambu à renseigner dans bambu.template).\n"
            texte = entete + yaml.safe_dump(valeurs, allow_unicode=True, sort_keys=False)
            self._write(vers, texte, lambda tmp: self._validated({}, _read_layer(tmp)))
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
