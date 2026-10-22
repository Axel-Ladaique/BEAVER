"""API d'aperçu : adapte `beaver.preview` au HTTP (préparation en arrière-plan, vues JSON, maillages STL)."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from beaver import preview
from beaver.config import Config
from beaver.gui.projects import ProjectError, ProjectStore, scrub_paths

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="beaver-apercu")
_RESULTS = 8  # résultats de vue gardés (plaques, plan, estimations)
# Le fil de l'exécuteur est attendu à la sortie de l'interpréteur : après Ctrl+C, le serveur finit la
# préparation en cours avant de quitter (annoncé par `run_gui`).
_EN_COURS = 0
_EN_COURS_LOCK = threading.Lock()
_SUIVIS: set[Suivi] = set()  # préparations démarrées, pas encore terminées (annulées toutes à l'arrêt du serveur)


class Suivi:
    """Étape et durée d'une préparation, et demande d'arrêt (prise en compte entre deux étapes)."""

    def __init__(self) -> None:
        self.etape = "en attente"
        self.debut: float | None = None
        self.annule = threading.Event()
        self.explicite = False  # arrêt demandé par l'utilisateur (sinon : remplacée par une demande plus récente)

    def __call__(self, etape: str) -> None:
        if self.annule.is_set():
            raise preview.PreparationAnnulee(etape)
        self.etape = etape

    def annuler(self, explicite: bool = False) -> None:
        self.explicite = self.explicite or explicite
        self.annule.set()

    def etat(self) -> dict:
        ecoule = 0.0 if self.debut is None else time.monotonic() - self.debut
        if self.annule.is_set():
            message = "arrêt de la préparation (fin de l'étape en cours)…"
        elif self.debut is None:
            message = "préparation de la pièce : en attente…"
        else:
            message = f"préparation de la pièce : {self.etape}…"
        return {"preparation": True, "message": message, "etape": self.etape, "ecoule_s": round(ecoule, 1)}


def _preparer(step, cfg, suivi: Suivi | None = None):
    global _EN_COURS
    suivi = suivi if suivi is not None else Suivi()
    suivi.debut = time.monotonic()
    with _EN_COURS_LOCK:
        _EN_COURS += 1
        _SUIVIS.add(suivi)
    try:
        suivi("début")  # annulée avant d'avoir démarré
        with preview.suivi(suivi):
            return preview.prepare_status(step, cfg)
    finally:
        with _EN_COURS_LOCK:
            _EN_COURS -= 1
            _SUIVIS.discard(suivi)


def preparation_en_cours() -> bool:
    with _EN_COURS_LOCK:
        return _EN_COURS > 0


def annuler_tout() -> None:
    """Arrêt du serveur : les préparations en cours s'arrêtent à la fin de leur étape."""
    with _EN_COURS_LOCK:
        for suivi in _SUIVIS:
            suivi.annuler()


def _suivi(fut: Future) -> Suivi:
    return getattr(fut, "suivi", None) or Suivi()


def _impossible(exc: BaseException) -> ProjectError:
    texte = str(exc) or exc.__class__.__name__
    return ProjectError(422, scrub_paths(f"aperçu impossible : {texte}"))


class Apercu:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, str], Future] = {}  # en cours ou en échec non signalé
        self._latest: dict[str, tuple[str, str]] = {}  # dernier travail soumis, par projet
        self._last: dict[str, Config] = {}  # dernière configuration d'aperçu reçue, par projet
        self._results: OrderedDict[tuple, dict] = OrderedDict()

    # --- préparation non bloquante ---------------------------------------------------------------
    def _ready(self, nom: str, step, cfg: Config, *, wait: bool):
        """(prep, cache_hit) si prête, son `Suivi` si en cours ; lève ProjectError si la préparation a échoué.

        `_jobs` ne garde que les travaux en cours ou en échec non encore signalé : le cache de `preview`
        est l'unique dépôt des préparations réussies (un travail réussi est retiré dès qu'il est servi).
        """
        key = (str(step), preview.prepare_key(cfg))
        while True:
            with self._lock:
                fut = self._jobs.get(key)
                if fut is not None and not fut.done() and _suivi(fut).annule.is_set():
                    fut = None  # redemandée pendant son arrêt : nouvelle préparation, en file derrière l'arrêt
                if fut is None:
                    prep = preview.cached(step, cfg)
                    if prep is not None:
                        return prep, True
                    self._cancel_previous(nom, key)
                    suivi = Suivi()
                    fut = self._jobs[key] = _EXECUTOR.submit(_preparer, step, cfg, suivi)
                    fut.suivi = suivi
                    self._latest[nom] = key
            if not fut.done() and not wait:
                return _suivi(fut)
            deja_fini = fut.done()
            try:
                prep, hit = fut.result()
            except CancelledError:
                with self._lock:
                    if self._jobs.get(key) is fut:
                        del self._jobs[key]
                continue  # remplacé par une demande plus récente : on soumet à nouveau
            except preview.PreparationAnnulee:
                with self._lock:
                    if self._jobs.get(key) is fut:
                        del self._jobs[key]
                if _suivi(fut).explicite and not deja_fini:
                    raise ProjectError(409, "aperçu arrêté") from None
                continue  # arrêt déjà relevé, ou remplacée puis redemandée : on soumet à nouveau
            except Exception as exc:
                self._forget(key, fut)
                raise _impossible(exc) from exc
            self._forget(key, fut)
            return prep, hit

    def _forget(self, key, fut: Future) -> None:
        with self._lock:
            if self._jobs.get(key) is fut:
                del self._jobs[key]
            for nom, k in list(self._latest.items()):
                if k == key:
                    del self._latest[nom]

    def _cancel_previous(self, nom: str, key) -> None:
        """Annule le travail précédent du projet (verrou `_lock` tenu) : retiré s'il est encore en file, arrêté à la
        fin de son étape s'il a démarré (il ne bloque plus l'exécuteur pendant toute sa préparation)."""
        for k in [k for k, f in self._jobs.items() if f.done() and k != key]:  # abandonnés : jamais relevés
            del self._jobs[k]
        old = self._latest.get(nom)
        if old is not None and old != key:
            fut = self._jobs.get(old)
            if fut is not None and (fut.done() or fut.cancel()):  # terminé non relevé, ou jamais démarré
                del self._jobs[old]
            elif fut is not None:
                _suivi(fut).annuler()

    def arreter(self, nom: str) -> bool:
        """Arrêt demandé par l'utilisateur (bouton, projet fermé) : la préparation en cours du projet s'arrête à la
        fin de son étape ; True s'il y en avait une."""
        with self._lock:
            key = self._latest.get(nom)
            fut = self._jobs.get(key) if key is not None else None
            if fut is None or fut.done():
                return False
            if fut.cancel():
                del self._jobs[key]
                del self._latest[nom]
                return True
            _suivi(fut).annuler(explicite=True)
            return True

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
            state = self._ready(nom, step, cfg, wait=wait)
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        if isinstance(state, Suivi):
            return JSONResponse(state.etat(), status_code=202)
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
        # décisions automatiques de découpe : informations, distinctes des avertissements
        out["notices"] = list(views["planform"].notices)
        return out

    def stl(self, nom: str, which: str):
        step = self.store.step_path(nom)
        with self._lock:
            cfg = self._last.get(nom)
        if cfg is None:
            cfg = self.store.load_config(nom)
        try:
            state = self._ready(nom, step, cfg, wait=False)
        except ProjectError:
            raise
        except Exception as exc:
            raise _impossible(exc) from exc
        if isinstance(state, Suivi):
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

    @router.delete("/projets/{nom}/apercu")
    def arreter(nom: str):
        api.store.require(nom)
        return {"arrete": api.arreter(nom)}

    @router.get("/projets/{nom}/apercu/plaques.stl")
    def plaques_stl(nom: str):
        return api.stl(nom, "plaques")

    @router.get("/projets/{nom}/apercu/peau.stl")
    def peau_stl(nom: str):
        return api.stl(nom, "peau")
