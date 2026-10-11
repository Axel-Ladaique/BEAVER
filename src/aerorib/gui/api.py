"""Routes /api de l'interface (le jeton est exigé par TokenMiddleware)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aerorib import __version__
from aerorib.gui.projects import ProjectError, ProjectStore


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

    return router


def project_error_handler(_request: Request, exc: ProjectError) -> JSONResponse:
    return JSONResponse({"erreur": exc.message}, status_code=exc.status)


def build_fallback_router() -> APIRouter:
    """Routes /api inconnues : 404 en français (à inclure APRÈS tous les autres routeurs)."""
    router = APIRouter(prefix="/api")

    @router.api_route("/{chemin:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def inconnue(chemin: str) -> JSONResponse:
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


def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    textes = {404: "route inconnue", 405: "méthode non autorisée"}
    detail = exc.detail if isinstance(exc.detail, str) else ""
    return JSONResponse({"erreur": textes.get(exc.status_code, detail or "erreur")}, status_code=exc.status_code)
