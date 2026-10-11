"""API d'aperçu : adapte `aerorib.preview` au HTTP (préparation en arrière-plan, vues JSON, maillages STL)."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from aerorib import preview
from aerorib.config import Config
from aerorib.gui.projects import ProjectError, ProjectStore, scrub_paths

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aerorib-apercu")
_RESULTS = 8  # résultats de vue gardés (plaques, plan, estimations)


def _impossible(exc: BaseException) -> ProjectError:
    texte = str(exc) or exc.__class__.__name__
    return ProjectError(422, scrub_paths(f"aperçu impossible : {texte}"))


class Apercu:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, str], Future] = {}
        self._last: dict[str, Config] = {}  # dernière configuration d'aperçu reçue, par projet
        self._results: OrderedDict[tuple, dict] = OrderedDict()

    # --- préparation non bloquante ---------------------------------------------------------------
    def _ready(self, step, cfg: Config, *, wait: bool):
        """(prep, cache_hit) si prête, None si en cours ; lève ProjectError si la préparation a échoué."""
        key = (preview._file_sha1(step), preview.prepare_key(cfg))
        with self._lock:
            fut = self._jobs.get(key)
            if fut is not None and fut.done():
                if fut.exception() is not None:
                    del self._jobs[key]  # l'échec n'est pas gardé : le prochain appel réessaie
                    raise _impossible(fut.exception())
                with preview._lock:
                    in_cache = key in preview._cache
                if not in_cache:  # évincée du cache : on recalcule
                    fut = None
            if fut is None:
                fut = self._jobs[key] = _EXECUTOR.submit(preview.prepare_status, step, cfg)
        if not fut.done():
            if not wait:
                return None
            try:
                fut.result()
            except Exception as exc:
                with self._lock:
                    self._jobs.pop(key, None)
                raise _impossible(exc) from exc
        prep, hit = fut.result()
        with self._lock:
            first = getattr(fut, "_servi", False) is False
            fut._servi = True  # type: ignore[attr-defined]
        return prep, (hit if first else True)

    def _cfg(self, nom: str, proposed) -> Config:
        return self.store.config_from_dict(nom, proposed)

    # --- vues -------------------------------------------------------------------------------------
    def _views(self, prep, hit: bool, cfg: Config) -> dict:
        key = (prep.key, json.dumps(cfg.model_dump(mode="json"), sort_keys=True, default=str))
        with self._lock:
            got = self._results.get(key)
            if got is not None:
                self._results.move_to_end(key)
                return got
        try:
            plates = preview.plates_2d(prep, cfg)
            plan = preview.planform(prep, plates, cfg)
            est = preview.estimates(prep, plates, cfg, cached=hit)
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        got = {"plates": plates, "planform": plan, "estimates": est, "mesh": None}
        with self._lock:
            self._results[key] = got
            while len(self._results) > _RESULTS:
                self._results.popitem(last=False)
        return got

    def post(self, nom: str, body: dict, attendre_q: bool):
        step = self.store.step_path(nom)
        if "config" not in body:
            raise ProjectError(422, "requête invalide : champ « config » manquant")
        cfg = self._cfg(nom, body["config"])
        z = body.get("z")
        if z is not None and (isinstance(z, bool) or not isinstance(z, (int, float)) or z != z):
            raise ProjectError(422, "requête invalide : champ « z » invalide")
        wait = bool(body.get("attendre")) or attendre_q
        with self._lock:
            self._last[nom] = cfg
        try:
            state = self._ready(step, cfg, wait=wait)
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        if state is None:
            return JSONResponse({"preparation": True, "message": "préparation de la pièce…"}, status_code=202)
        prep, hit = state
        views = self._views(prep, hit, cfg)
        try:
            zz = (prep.zrange[0] + prep.zrange[1]) / 2 if z is None else float(z)
            sec = preview.section(prep, views["plates"], cfg, zz)
            out = {"preparation": False, "section": sec.to_dict(), "planform": views["planform"].to_dict(),
                   "estimates": views["estimates"]}
        except Exception as exc:
            raise _impossible(exc) from exc
        warns: list[str] = []
        for w in [*views["planform"].warnings, *views["estimates"].get("avertissements", [])]:
            if w not in warns:
                warns.append(w)
        out["avertissements"] = warns
        return out

    def stl(self, nom: str, which: str):
        step = self.store.step_path(nom)
        with self._lock:
            cfg = self._last.get(nom)
        if cfg is None:
            cfg = self.store._load(self.store.require(nom))
        try:
            state = self._ready(step, cfg, wait=False)
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        if state is None:
            return JSONResponse({"erreur": "aperçu en préparation"}, status_code=409)
        prep, hit = state
        try:
            if which == "peau":
                data = preview.skin_mesh(prep)
            else:
                views = self._views(prep, hit, cfg)
                if views["mesh"] is None:
                    views["mesh"] = preview.plates_mesh(prep, views["plates"])
                data = views["mesh"]
        except preview.PreviewError as exc:
            raise ProjectError(422, scrub_paths(str(exc))) from exc
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        return Response(data, media_type="model/stl")


def register(router: APIRouter, store: ProjectStore) -> None:
    api = Apercu(store)

    @router.post("/projets/{nom}/apercu")
    def apercu(nom: str, body: dict, attendre: bool = Query(False)):
        return api.post(nom, body, attendre)

    @router.get("/projets/{nom}/apercu/plaques.stl")
    def plaques_stl(nom: str):
        return api.stl(nom, "plaques")

    @router.get("/projets/{nom}/apercu/peau.stl")
    def peau_stl(nom: str):
        return api.stl(nom, "peau")
