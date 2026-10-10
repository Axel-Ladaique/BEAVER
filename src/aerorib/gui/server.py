"""Serveur local de l'interface AeroRib (127.0.0.1 uniquement, jeton par lancement)."""

from __future__ import annotations

import errno
import json
import os
import secrets
import socket
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import typer
import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import HTTPConnection

from aerorib.gui.api import build_router, project_error_handler
from aerorib.gui.projects import ProjectError

HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "static"


class TokenMiddleware:
    """Middleware ASGI pur : exige le jeton sur toute route /api/*."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope["path"].startswith("/api/"):
            conn = HTTPConnection(scope)
            fourni = conn.headers.get("X-AeroRib-Token") or conn.query_params.get("token") or ""
            if not secrets.compare_digest(fourni.encode(), self.token.encode()):
                if scope["type"] == "http":
                    corps = json.dumps({"erreur": "jeton manquant ou invalide"}).encode()
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(corps)).encode())]})
                    await send({"type": "http.response.body", "body": corps})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def create_app(root: Path, token: str, routers: Sequence[APIRouter] | None = None) -> FastAPI:
    app = FastAPI(title="AeroRib", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = Path(root)
    app.state.token = token
    app.add_middleware(TokenMiddleware, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_exception_handler(ProjectError, project_error_handler)
    app.include_router(build_router(Path(root)))
    for router in routers or ():
        app.include_router(router)
    # le montage statique est ajouté EN DERNIER : il ne doit masquer aucune route /api
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def build_uvicorn_config(app: FastAPI) -> uvicorn.Config:
    return uvicorn.Config(app, host=HOST, log_level="warning")


def bind_socket(port: int = 0) -> socket.socket:
    """Ouvre le socket d'écoute unique sur 127.0.0.1 (port 0 = choisi par l'OS)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            raise OSError(f"port {port} déjà utilisé") from None
        raise OSError(f"impossible d'ouvrir le port {port} : {exc.strerror or exc}") from None
    sock.listen(128)
    return sock


def _open_when_started(server: uvicorn.Server, url: str) -> None:
    while not server.started and not server.should_exit:
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)


def run_gui(root: Path, port: int = 0, open_browser: bool = True) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        sock = bind_socket(port)
    except OSError as exc:
        typer.echo(f"erreur : {exc}")
        raise typer.Exit(1)
    real_port = sock.getsockname()[1]
    token = secrets.token_urlsafe(24)
    server = uvicorn.Server(build_uvicorn_config(create_app(root, token)))
    url = f"http://{HOST}:{real_port}/?token={token}"
    print(f"AeroRib : interface disponible sur {url}")
    print("Ctrl+C pour arrêter.")
    if open_browser:
        threading.Thread(target=_open_when_started, args=(server, url), daemon=True).start()
    server.run(sockets=[sock])
