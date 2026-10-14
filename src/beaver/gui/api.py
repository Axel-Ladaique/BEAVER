"""Routes /api de l'interface (le jeton est exigé par TokenMiddleware)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.exceptions import HTTPException as StarletteHTTPException

from beaver import __version__
from beaver.gui import apercu
from beaver.gui.projects import ProjectError, ProjectStore, scrub_paths


def build_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api")
    store = ProjectStore(root)

    @router.get("/sante")
    def sante() -> dict:
        return {"ok": True, "version": __version__}

    @router.get("/schema")
    def schema() -> dict:
        return store.schema()

    @router.get("/projets")
    def liste() -> list[dict]:
        return store.list()

    @router.post("/projets", status_code=201)
    def creer(nom: str = Form(...), fichier: UploadFile = File(...)) -> dict:
        return store.create(nom, fichier.file, fichier.filename or "")

    @router.get("/projets/{nom}")
    def lire(nom: str) -> dict:
        return store.get(nom)

    @router.get("/projets/{nom}/corps")
    def corps(nom: str) -> dict:
        return store.inspect(nom)

    @router.get("/projets/{nom}/config")
    def lire_config(nom: str) -> dict:
        return store.read_config(nom)

    @router.put("/projets/{nom}/config")
    def ecrire_config(nom: str, body: dict) -> dict:
        if "texte" in body:
            return store.write_config(nom, body["texte"])
        if "donnees" in body:
            return store.write_config(nom, body["donnees"])
        raise ProjectError(422, "corps attendu : {\"texte\": ...} ou {\"donnees\": {...}}")

    @router.get("/projets/{nom}/config/effective")
    def config_effective(nom: str) -> dict:
        return store.effective_config(nom)

    apercu.register(router, store)
    return router


def project_error_handler(_request: Request, exc: ProjectError) -> JSONResponse:
    return JSONResponse({"erreur": exc.message}, status_code=exc.status)


def build_fallback_router(known: Sequence[APIRouter] = ()) -> APIRouter:
    """Routes /api inconnues : 404 en français (à inclure APRÈS tous les autres routeurs) ; 405 si le chemin
    existe dans l'un des routeurs `known` mais pas pour cette méthode."""
    router = APIRouter(prefix="/api")

    @router.api_route("/{chemin:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    def inconnue(chemin: str, request: Request) -> JSONResponse:
        for r in known:
            for route in r.routes:
                if isinstance(route, APIRoute) and route.matches(request.scope)[0] == Match.PARTIAL:
                    return JSONResponse({"erreur": "méthode non autorisée"}, status_code=405)
        return JSONResponse({"erreur": "route inconnue"}, status_code=404)

    return router


_CHAMP = {"nom": "nom", "fichier": "fichier"}


def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = [str(x) for x in first.get("loc", ())]
    kind = first.get("type", "")
    if loc == ["body"] and kind != "missing":
        msg = "le corps doit être un objet JSON"
    elif kind.startswith("json_") or kind == "model_attributes_type":
        msg = "corps JSON invalide" if kind.startswith("json_") else "corps de requête invalide"
    else:
        champ = loc[-1] if loc else "corps"
        champ = _CHAMP.get(champ, champ)
        msg = f"champ « {champ} » {'manquant' if kind == 'missing' else 'invalide'}"
    return JSONResponse({"erreur": f"requête invalide : {msg}"}, status_code=422)


def internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Dernier recours : 500 JSON en français, sans trace ni chemin. Starlette relance ensuite l'exception,
    que uvicorn journalise avec sa trace dans le terminal du serveur."""
    detail = scrub_paths(str(exc)).strip()
    message = f"erreur interne : {type(exc).__name__}" + (f" : {detail}" if detail else "")
    return JSONResponse({"erreur": message}, status_code=500)


def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    textes = {404: "route inconnue", 405: "méthode non autorisée"}
    detail = exc.detail if isinstance(exc.detail, str) else ""
    return JSONResponse({"erreur": textes.get(exc.status_code, detail or "erreur")}, status_code=exc.status_code)
