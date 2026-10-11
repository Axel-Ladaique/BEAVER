"""Serveur local de l'interface AeroRib (127.0.0.1 uniquement, jeton par lancement)."""

from __future__ import annotations

import errno
import json
import mimetypes
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

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from aerorib.gui import profils, projects, travaux
from aerorib.gui.api import (
    build_fallback_router,
    build_router,
    http_error_handler,
    project_error_handler,
    validation_error_handler,
)
from aerorib.gui.projects import ProjectError, ProjectStore

HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "static"
# corps maximal d'un import (STEP 200 Mo + champs multipart) et d'un PUT de configuration ; lus à l'appel
MAX_UPLOAD_BODY = projects.MAX_STEP_BYTES + 1024 * 1024
MAX_CONFIG_BODY = 1024 * 1024
MAX_SMALL_BODY = 64 * 1024


def _types_mime() -> None:
    """Types MIME des fichiers statiques forcés : sous Windows, le registre peut associer .js à text/plain,
    ce que les navigateurs refusent pour un module ES."""
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("text/html", ".html")


_types_mime()


class StatiqueRevalide(StaticFiles):
    """Fichiers de l'interface toujours revalidés (ETag) : pas de module JS périmé après une mise à jour."""

    def file_response(self, *args, **kwargs):
        reponse = super().file_response(*args, **kwargs)
        reponse.headers["Cache-Control"] = "no-cache"
        return reponse


class BodyTooLarge(BaseException):  # pas Exception : FastAPI le convertirait en 400
    pass


class BodyLimitMiddleware:
    """Plafonne le corps RÉEL des requêtes (octets reçus, Content-Length non fiable) avant tout stockage."""

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _limit(scope) -> tuple[int, str] | None:
        path, method = scope["path"].rstrip("/"), scope["method"]
        if method == "POST" and path == "/api/projets":
            return MAX_UPLOAD_BODY, "fichier trop volumineux (max 200 Mo)"
        if method == "POST" and path.startswith("/api/projets/") and path.endswith("/apercu"):
            return MAX_CONFIG_BODY, "configuration trop volumineuse (max 1 Mo)"
        if method == "PUT" and path.startswith("/api/projets/") and path.endswith("/config"):
            return MAX_CONFIG_BODY, "configuration trop volumineuse (max 1 Mo)"
        if method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
            # générations, annulation, ouvertures : pas de corps attendu
            return MAX_SMALL_BODY, "corps de requête trop volumineux (max 64 Ko)"
        return None

    async def __call__(self, scope, receive, send):
        rule = self._limit(scope) if scope["type"] == "http" else None
        if rule is None:
            await self.app(scope, receive, send)
            return
        limit, message = rule
        started = False

        async def refuse():
            corps = json.dumps({"erreur": message}).encode()
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(corps)).encode()),
                                    (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": corps})

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:  # voie rapide
            await refuse()
            return
        total = 0

        async def counting_receive():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:
                    raise BodyTooLarge
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await refuse()


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


def create_app(
    root: Path, token: str, routers: Sequence[APIRouter] | None = None, profiles_dir: Path | None = None
) -> FastAPI:
    _types_mime()
    app = FastAPI(title="AeroRib", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = Path(root)
    app.state.token = token
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TokenMiddleware, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_exception_handler(ProjectError, project_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    principal = build_router(Path(root))
    app.include_router(principal)
    app.state.travaux = travaux.JobManager(ProjectStore(Path(root)))
    generations = travaux.build_router(app.state.travaux.store, app.state.travaux)
    app.include_router(generations)
    app.router.add_event_handler("shutdown", app.state.travaux.shutdown)
    app.state.profils = profils.ProfileStore(profiles_dir)  # dossier résolu une fois, ici
    profils_router = profils.build_router(app.state.profils)
    app.include_router(profils_router)
    for router in routers or ():
        app.include_router(router)
    app.include_router(build_fallback_router([principal, generations, profils_router, *(routers or ())]))
    # le montage statique est ajouté EN DERNIER : il ne doit masquer aucune route /api
    app.mount("/", StatiqueRevalide(directory=STATIC_DIR, html=True), name="static")
    return app


def build_uvicorn_config(app: FastAPI) -> uvicorn.Config:
    # un flux SSE ouvert ne doit pas bloquer l'arrêt (Ctrl+C) : le travail en cours est annulé ensuite
    return uvicorn.Config(app, host=HOST, log_level="warning", timeout_graceful_shutdown=2)


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
