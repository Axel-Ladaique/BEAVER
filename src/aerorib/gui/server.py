"""Serveur local de l'interface AeroRib (127.0.0.1 uniquement, jeton par lancement)."""

from __future__ import annotations

import secrets
import socket
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from aerorib import __version__

HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(root: Path, token: str) -> FastAPI:
    app = FastAPI(title="AeroRib", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = Path(root)
    app.state.token = token

    @app.middleware("http")
    async def verifier_jeton(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            fourni = request.headers.get("X-AeroRib-Token") or request.query_params.get("token") or ""
            if not secrets.compare_digest(fourni.encode(), token.encode()):
                return JSONResponse({"erreur": "jeton manquant ou invalide"}, status_code=401)
        return await call_next(request)

    @app.get("/api/sante")
    def sante() -> dict:
        return {"ok": True, "version": __version__}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def build_uvicorn_config(app: FastAPI, port: int) -> uvicorn.Config:
    return uvicorn.Config(app, host=HOST, port=port, log_level="warning")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def run_gui(root: Path, port: int = 0, open_browser: bool = True) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    port = port or _free_port()
    token = secrets.token_urlsafe(24)
    app = create_app(root, token)
    url = f"http://{HOST}:{port}/?token={token}"
    print(f"AeroRib : interface disponible sur {url}")
    print("Ctrl+C pour arrêter.")
    if open_browser:
        webbrowser.open(url)
    cfg = build_uvicorn_config(app, port)
    uvicorn.run(cfg.app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)
