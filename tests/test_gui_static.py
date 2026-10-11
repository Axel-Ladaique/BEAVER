"""Interface statique : la page, ses modules et l'import map sont servis et cohérents."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
from fastapi.testclient import TestClient

from aerorib.gui.server import STATIC_DIR, create_app

TOKEN = "jeton-test"
THREE = "https://cdn.jsdelivr.net/npm/three@0.160.0/"
JS_TYPES = ("text/javascript", "application/javascript")
# import/export statiques : `import x from "…"`, `import "…"`, `export … from "…"` (pas les import() dynamiques)
_IMPORT_RE = re.compile(
    r"""^\s*(?:import\s*['"]([^'"]+)['"]|(?:import|export)\b[^'"();=]*?\bfrom\s*['"]([^'"]+)['"])""", re.M)


def _imports(code: str) -> list[str]:
    return [a or b for a, b in _IMPORT_RE.findall(code)]


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []
        self.importmap: str | None = None
        self.inline_modules: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href"):
            if a.get(key):
                self.refs.append(a[key])
        if tag == "script" and not a.get("src"):
            self._in = a.get("type") or "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = None

    def handle_data(self, data):
        if self._in == "importmap":
            self.importmap = (self.importmap or "") + data
        elif self._in == "module":
            self.inline_modules.append(data)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path, TOKEN), base_url="http://127.0.0.1")


def _page(client) -> tuple[str, _Page]:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    p = _Page()
    p.feed(r.text)
    return r.text, p


def _local(url: str) -> bool:
    return not urlparse(url).scheme and not url.startswith(("//", "#", "data:"))


def test_index_servi_en_francais(client):
    html, page = _page(client)
    assert '<html lang="fr">' in html
    assert "js/app.js" in page.refs and "style.css" in page.refs


def test_import_map_fige_three_0_160_0(client):
    _, page = _page(client)
    assert page.importmap, "import map absente"
    imports = json.loads(page.importmap)["imports"]
    assert imports["three"] == THREE + "build/three.module.js"
    assert imports["three/addons/"] == THREE + "examples/jsm/"


def _crawl(client, page: _Page) -> dict[str, str]:
    """Tous les fichiers locaux référencés (HTML puis imports statiques des modules) → contenu."""
    todo = [urljoin("/", r) for r in page.refs if _local(r)]
    for code in page.inline_modules:
        todo += [urljoin("/", s) for s in _imports(code) if s.startswith((".", "/"))]
    seen: dict[str, str] = {}
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        r = client.get(url)
        assert r.status_code == 200, f"{url} : {r.status_code}"
        ctype = r.headers["content-type"].split(";")[0].strip()
        if url.endswith(".js"):
            assert ctype in JS_TYPES, f"{url} servi en {ctype}"
            for spec in _imports(r.text):
                if spec.startswith((".", "/")):
                    todo.append(urljoin(url, spec))
                else:  # spécifieur nu : seulement ceux de l'import map
                    assert spec == "three" or spec.startswith("three/addons/"), f"{url} importe {spec}"
        elif url.endswith(".css"):
            assert ctype == "text/css", f"{url} servi en {ctype}"
        seen[url] = r.text
    return seen


def test_tous_les_fichiers_references_sont_servis(client):
    _, page = _page(client)
    fichiers = _crawl(client, page)
    attendus = {"/js/api.js", "/js/app.js", "/js/formulaire.js", "/style.css"} | {
        f"/js/onglets/{n}.js" for n in ("projets", "reglages", "apercu", "generer", "historique", "profils")}
    assert attendus <= set(fichiers), attendus - set(fichiers)


def test_tous_les_modules_du_dossier_sont_atteints(client):
    """Aucun module mort : chaque .js du dossier statique est importé depuis la page."""
    _, page = _page(client)
    fichiers = _crawl(client, page)
    sur_disque = {"/" + p.relative_to(STATIC_DIR).as_posix() for p in STATIC_DIR.rglob("*.js")}
    assert sur_disque <= set(fichiers), sur_disque - set(fichiers)


def test_aucune_ressource_externe_hors_three(client):
    html, page = _page(client)
    fichiers = _crawl(client, page)
    for nom, texte in {"/": html, **fichiers}.items():
        for url in re.findall(r"https?://[^\s'\"`)]+", texte):
            if url == "http://www.w3.org/2000/svg":  # espace de noms SVG, pas une ressource chargée
                continue
            assert url.startswith(THREE), f"{nom} référence {url}"


def test_pas_de_innerhtml(client):
    """Les noms (fichiers, corps, profils) viennent du serveur : construction DOM uniquement."""
    _, page = _page(client)
    for nom, texte in _crawl(client, page).items():
        if nom.endswith(".js"):
            assert not re.search(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML|document\.write", texte), nom


def test_three_charge_a_la_demande(client):
    """three.js n'est importé que dynamiquement (vue 3D), jamais statiquement."""
    _, page = _page(client)
    fichiers = _crawl(client, page)
    for nom, texte in fichiers.items():
        if nom.endswith(".js"):
            statiques = [s for s in _imports(texte) if s.startswith("three")]
            assert not statiques, f"{nom} importe three statiquement"
    assert any("import(\"three\")" in t or "import('three')" in t for t in fichiers.values())


def test_page_sans_jeton_dans_le_html(client):
    html = client.get("/", params={"token": TOKEN}).text
    assert TOKEN not in html


def test_fichiers_statiques_sans_jeton(client):
    """Les fichiers statiques ne sont pas protégés (le jeton ne protège que /api)."""
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/api/sante").status_code == 401


def test_extension_js_forcee_en_javascript(monkeypatch, tmp_path):
    """Windows : le registre peut associer .js à text/plain ; le serveur force le bon type."""
    import mimetypes

    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    from aerorib.gui import server

    server._types_mime()
    c = TestClient(create_app(tmp_path, TOKEN), base_url="http://127.0.0.1")
    assert c.get("/js/app.js").headers["content-type"].split(";")[0] in JS_TYPES


def test_fichiers_statiques_revalides(client):
    """Après une mise à jour d'AeroRib, le navigateur ne doit pas garder d'anciens modules en cache."""
    for url in ("/", "/js/app.js", "/style.css"):
        assert client.get(url).headers.get("cache-control") == "no-cache", url
