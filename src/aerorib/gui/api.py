"""Routes /api de l'interface (le jeton est exigé par TokenMiddleware)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from aerorib import __version__
from aerorib.gui import projects
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
    def creer(request: Request, nom: str = Form(...), fichier: UploadFile = File(...)) -> dict:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > projects.MAX_STEP_BYTES + 1024 * 1024:
            raise ProjectError(413, "fichier trop volumineux (max 200 Mo)")
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
