"""Logique pure du formulaire (modules ES) exécutée sous Node, si Node est installé."""

import json
import shutil
import subprocess

import pytest

from aerorib.gui.server import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node absent : tests JS ignorés")


def _node(script: str):
    code = f"""
const js = (m) => import({json.dumps((STATIC_DIR / "js").as_uri())} + "/" + m);
const out = await (async () => {{ {script} }})();
console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_suppression_de_couche_reindexe_les_erreurs():
    out = _node("""
const { reindexer } = await js("formulaire.js");
const m = new Map([["rib_layers.0.spacing", "a"], ["rib_layers.1.angle", "b"], ["rib_layers.2.spacing", "c"],
                   ["rib_layers.10.depth", "d"], ["slit_width", "e"], ["rib_layers", "f"]]);
return Object.fromEntries(reindexer(m, "rib_layers", 1));
""")
    assert out == {"rib_layers.0.spacing": "a", "rib_layers.1.spacing": "c", "rib_layers.9.depth": "d",
                   "slit_width": "e", "rib_layers": "f"}


def test_poser_retirer_minimal_et_est_pose():
    out = _node("""
const { estPose, poser, retirer } = await js("chemins.js");
const d = {};
poser(d, ["split", "gap"], 0.8);
poser(d, ["material_settings", "layer_height"], 0.3);
const avant = JSON.parse(JSON.stringify(d));
retirer(d, ["split", "gap"]);
return { avant, apres: d, liste: estPose({ rib_layers: [{}] }, ["rib_layers", 0, "spacing"]),
         absent: estPose({ split: {} }, ["split", "gap"]) };
""")
    assert out["avant"] == {"split": {"gap": 0.8}, "material_settings": {"layer_height": 0.3}}
    assert out["apres"] == {"material_settings": {"layer_height": 0.3}}  # parent vide élagué
    assert out["liste"] is True and out["absent"] is False


def test_erreurs_par_champ():
    out = _node(r"""
const { erreursParChamp } = await js("erreurs.js");
const f = (t) => { const r = erreursParChamp(t); return [Object.fromEntries(r.parChamp), r.general]; };
return [
  f("configuration invalide :\n2 validation errors for Config\nslit_width\n  Input should be a valid number [type=float_parsing]\n    For further information visit x\nrib_layers.0.spacing\n  Input should be greater than 0 [type=greater_than]\n(profil matière utilisé : pla_aero.yaml)"),
  f("profil invalide : champ « split.gap » : valeur invalide (x) ; champ « foo » : champ inconnu"),
  f("configuration invalide :\n1 validation error for Config\n  Value error, fente trop fine [type=value_error]"),
];
""")
    (c1, g1), (c2, g2), (c3, g3) = out
    assert c1 == {"slit_width": "nombre attendu", "rib_layers.0.spacing": "doit être > 0"}
    assert g1 == ["(profil matière utilisé : pla_aero.yaml)"]
    assert c2 == {"split.gap": "valeur invalide (x)", "foo": "champ inconnu"} and g2 == []
    assert c3 == {} and "fente trop fine" in g3[0]


def test_vue_de_dessus_dessine_les_trous():
    """Vue de dessus : un rectangle (étendue z × diamètre) par trou, gaine du longeron, titre en textContent."""
    out = _node(r"""
class N {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.enfants = []; this.textContent = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  append(...e) { this.enfants.push(...e); }
  addEventListener() {}
}
globalThis.Node = N;
globalThis.window = { location: { search: "?token=t" } };
globalThis.sessionStorage = { setItem() {}, getItem() { return ""; } };
globalThis.document = { createElementNS: (ns, t) => new N(t), createElement: (t) => new N(t),
                        createTextNode: (t) => Object.assign(new N("#text"), { textContent: t }) };
const { dessinDessus } = await js("onglets/apercu.js");
const plan = { aile: [0, 0, 100, 80], segments: [], coupes: [], trous: [
  { nom: "Longeron", type: "longeron", x: 30, r: 3.1, gaine: 4.0, z0: 0, z1: 80 },
  { nom: "P<1>", type: "passage relié", x: 70, r: 1.2, gaine: 1.6, z0: 10, z1: 60 },
] };
const s = dessinDessus(plan, 40);
const groupes = s.enfants.filter((n) => n.tag === "g");
return groupes.map((g) => ({
  classe: g.attrs.class,
  titre: g.enfants.find((n) => n.tag === "title").textContent,
  rects: g.enfants.filter((n) => n.tag === "rect").map((r) => [r.attrs.class, +r.attrs.x, +r.attrs.y, +r.attrs.width, +r.attrs.height]),
}));
""")
    lon, pas = out
    assert lon["classe"] == "trou-dessus longeron" and "Longeron" in lon["titre"] and "z = 0" in lon["titre"]
    assert lon["rects"] == [["gaine", 0, -34, 80, 8], ["trou", 0, -33.1, 80, 6.2]]
    assert pas["classe"] == "trou-dessus passage" and "P<1>" in pas["titre"]
    assert pas["rects"][-1][0] == "trou" and pas["rects"][-1][1:] == [10, -71.2, 50, 2.4]


def test_json_canonique_pour_l_indicateur_de_modification():
    out = _node("""
const { canonique } = await js("chemins.js");
return [
  canonique({ b: 1, a: { d: [ { y: 2, x: 1 } ], c: null } }) === canonique({ a: { c: null, d: [ { x: 1, y: 2 } ] }, b: 1 }),
  canonique({ a: 1 }) === canonique({ a: 1, b: undefined }),
  canonique({ a: [1, 2] }) === canonique({ a: [2, 1] }),
  canonique({ a: 1 }) === canonique({ a: "1" }),
];
""")
    assert out == [True, True, False, False]
