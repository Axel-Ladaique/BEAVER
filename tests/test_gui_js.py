"""Logique pure du formulaire (modules ES) exécutée sous Node, si Node est installé."""

import json
import shutil
import subprocess

import pytest

from beaver.gui.server import STATIC_DIR

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
    """Vue de dessus : un rectangle (étendue z × diamètre) par trou, manchon du longeron, titre en textContent."""
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
  { nom: "Longeron", type: "longeron", x: 30, r: 3.1, manchon: 4.0, z0: 0, z1: 80 },
  { nom: "P<1>", type: "passage relié", x: 70, r: 1.2, manchon: 1.6, z0: 10, z1: 60 },
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
    assert lon["rects"] == [["manchon", 0, -34, 80, 8], ["trou", 0, -33.1, 80, 6.2]]
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


_DOM = r"""
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
"""


def test_vue_de_dessus_dessine_les_baies():
    """Baie de servo : rampes (claires) + ouverture + boîte utile, titre en textContent (nom non interprété)."""
    out = _node(_DOM + r"""
const { dessinDessus } = await js("onglets/apercu.js");
const plan = { aile: [0, 0, 100, 120], segments: [], coupes: [], trous: [
  { nom: "SBAR_CABLE_1", type: "gaine", x: 30, r: 3, manchon: 3.5, z0: 0, z1: 50 },
], baies: [
  { nom: "SERVO<1>", cote: "extrados", x0: 22, x1: 42, z0: 40, z1: 75, ouverture: [30, 85], profondeur: 7,
    renfort: true, renfort_partiel: false },
] };
const s = dessinDessus(plan, 40);
const g = s.enfants.filter((n) => n.tag === "g");
const baie = g.find((n) => n.attrs.class === "baie-dessus");
return {
  gaine: g.find((n) => n.enfants.some((e) => e.tag === "title" && e.textContent.startsWith("gaine"))).attrs.class,
  titre: baie.enfants.find((n) => n.tag === "title").textContent,
  rects: baie.enfants.filter((n) => n.tag === "rect").map((r) => [r.attrs.class, +r.attrs.x, +r.attrs.y, +r.attrs.width, +r.attrs.height]),
};
""")
    assert out["gaine"] == "trou-dessus longeron"
    assert out["titre"].startswith("baie de servo « SERVO<1> » (extrados)") and "renfort" in out["titre"]
    assert "30" in out["titre"] and "85" in out["titre"]
    assert out["rects"] == [
        ["baie-rampe", 30, -42, 10, 20], ["baie-rampe", 75, -42, 10, 20],
        ["baie-ouverture", 40, -42, 35, 20], ["baie-boite", 40, -42, 35, 20],
    ]


def test_orientation_minimale_avec_emplanture():
    out = _node("""
const { lireOrientation, valeurOrientation } = await js("formulaire.js");
return [
  valeurOrientation(null, "auto"), valeurOrientation(null, "fin"), valeurOrientation([1, 0, 0], "auto"),
  valeurOrientation([1, 0, 0], "debut"),
  lireOrientation("auto"), lireOrientation({ root: "fin" }), lireOrientation({ axis: [0, 1, 0], root: "debut" }),
];
""")
    assert out == [
        "auto", {"root": "fin"}, {"axis": [1, 0, 0]}, {"axis": [1, 0, 0], "root": "debut"},
        {"axe": None, "racine": "auto"}, {"axe": None, "racine": "fin"}, {"axe": [0, 1, 0], "racine": "debut"},
    ]


def test_libelles_servo_gaines_emplanture():
    out = _node("""
const { aide, libelle, CHOIX } = await js("libelles.js");
const { PLAN } = await js("schema.js");
const chemins = [["servo"], ["servo", "strip"], ["servo", "min_floor_gap"], ["servo", "reinforce"],
  ["servo", "reinforce_offset"], ["servo", "slit_width"], ["naming", "servo"], ["naming", "cable_markers"],
  ["orientation", "root"], ["spars", "cable", "sleeve"], ["roles"]];
return { libelles: chemins.map(libelle), aideRoles: aide(["roles"]), aideCable: aide(["spars", "cable", "sleeve"]),
         choix: [CHOIX.servo, CHOIX.debut, CHOIX.fin], plan: PLAN.some((b) => b.cle === "servo") };
""")
    lib = out["libelles"]
    assert lib == [
        "Baies de servo", "Matière entre nervures et baie (mm)", "Fond ↔ peau opposée min. (mm)",
        "Renfort (paroi doublée)", "Épaisseur de la paroi doublée (mm)", "Largeur de la fente de renfort (mm)",
        "Baie de servo", "Marqueurs de gaine de câble", "Emplanture", "Manchon (mm)", "Rôles imposés (repli)",
    ]
    assert "parent" in out["aideRoles"] and "préfixe" in out["aideRoles"]
    assert "1,2" in out["aideCable"]
    assert out["choix"] == ["baie de servo", "début (coordonnée min.)", "fin (coordonnée max.)"] and out["plan"]


_PLAN_DECOUPE = r"""
const plan = { aile: [-170, 0, 230, 120], segments: [], coupes: [60], trous: [], corps: [],
  morceaux: [
    { nom: "T1-A", corps: "Aile", z0: 0, z1: 60, x0: -170, x1: 23, gauche: null, droite: [23, 23], centre: [-73, 30],
      largeur: 193, profondeur: 51, rotation: 0, tient: true, plateau: 1 },
    { nom: "T1-B", corps: "Aile", z0: 0, z1: 60, x0: 23, x1: 230, gauche: [23, 23], droite: null, centre: [126, 30],
      largeur: 207, profondeur: 44, rotation: -37.5, tient: true, plateau: 2 },
    { nom: "T2", corps: "Aile", z0: 60, z1: 120, x0: -170, x1: 230, gauche: null, droite: null, centre: [30, 90],
      largeur: 300, profondeur: 50, rotation: 0, tient: false, plateau: 3 },
  ],
  plans_corde: [{ troncon: "T1", corps: "Aile", x0: 23, z0: 0, x1: 23, z1: 60, inclinaison: 0, decalage: -7, evite: ["SBAR<8>"] }],
  sbar_transversales: [{ nom: "SBAR_T", type: "transversale", gaine: false, x0: -5, z0: 90, x1: 105, z1: 90, d: 4, r: 2.15, angle: 90, fente: true }],
  notices: ["découpe automatique : coupe dans la corde en 2 morceaux"],
};
"""


def test_vue_de_dessus_dessine_la_decoupe():
    """Coupes en Z en tirets, coupe dans la corde en trait plein, SBAR transversale en bande, morceaux nommés."""
    out = _node(_DOM + _PLAN_DECOUPE + r"""
const { dessinDessus } = await js("onglets/apercu.js");
const s = dessinDessus(plan, 40);
const titre = (n) => (n.enfants.find((e) => e.tag === "title") || {}).textContent;
const lignes = s.enfants.filter((n) => n.tag === "line");
const sbar = s.enfants.find((n) => n.attrs.class === "sbar-dessus");
return {
  zones: s.enfants.filter((n) => n.attrs.class === "morceau-dessus").map((n) => [n.attrs.points, n.attrs.fill, titre(n)]),
  coupeZ: lignes.filter((n) => n.attrs.class === "coupe-troncon").map((n) => [+n.attrs.x1, titre(n)]),
  corde: lignes.filter((n) => n.attrs.class === "coupe-corde").map((n) => [+n.attrs.x1, +n.attrs.y1, +n.attrs.x2, +n.attrs.y2, titre(n)]),
  sbar: [titre(sbar), sbar.enfants.filter((n) => n.tag === "line").map((n) => [n.attrs.class, +n.attrs["stroke-width"]])],
  textes: s.enfants.filter((n) => n.tag === "text").map((n) => [n.textContent, +n.attrs.x, +n.attrs.y]),
};
""")
    a, b, t2 = out["zones"]
    assert a[0] == "0,170 60,170 60,-23 0,-23" and a[1] != b[1] != t2[1]
    assert "Morceau T1-B" in b[2] and "tourné de 38°" in b[2] and "plateau 2" in b[2]
    assert "ne tient pas" in t2[2]
    assert out["coupeZ"] == [[60, "Coupe en Z (tronçon) à z = 60,0 mm"]]
    ((z0, y0, z1, y1, titre),) = out["corde"]
    assert (z0, y0, z1, y1) == (0, -23, 60, -23) and "Coupe dans la corde (Aile, T1)" in titre and "SBAR<8>" in titre
    assert out["sbar"][0].startswith("SBAR transversale « SBAR_T » : Ø 4,0 mm") and "fente" in out["sbar"][0]
    assert out["sbar"][1] == [["sbar-bande", 4.3], ["sbar-axe", 1]]
    assert out["textes"] == [["T1-A", 30, 73], ["T1-B", 30, -126], ["T2", 90, -30]]


def test_morceau_de_point_couleurs_et_plans_3d():
    out = _node(_DOM + _PLAN_DECOUPE + r"""
const { morceauDe, couleursMorceaux, quadsDeCoupe, PALETTE } = await js("decoupe.js");
const pts = [[0, 30], [100, 30], [23.2, 59.8], [0, 90], [500, 30], [0, 200]];
const cols = couleursMorceaux(new Float32Array([0, 1, 30, 100, -1, 30]), plan.morceaux);
return { ids: pts.map(([x, z]) => morceauDe(x, z, plan.morceaux)), cols: Array.from(cols).map((c) => Math.round(c * 255)),
         palette: PALETTE.slice(0, 2), quads: quadsDeCoupe(plan, -10, 20) };
""")
    assert out["ids"] == [0, 1, 0, 2, -1, -1]  # sur la face de coupe : premier morceau (tolérance)
    hexa = lambda h: [int(h[i:i + 2], 16) for i in (1, 3, 5)]  # noqa: E731
    assert out["cols"] == hexa(out["palette"][0]) + hexa(out["palette"][1])
    z_cut, corde = out["quads"]
    assert [p[2] for p in z_cut] == [60] * 4 and z_cut[0][0] < -170 and z_cut[1][0] > 230
    assert corde == [[23, -10, 0], [23, 20, 0], [23, 20, 60], [23, -10, 60]]


def test_panneau_decoupe_et_tableau_de_l_essai():
    out = _node(_DOM + _PLAN_DECOUPE + r"""
const { panneauDecoupe, tableauMorceauxEssai, encadreNotices } = await js("decoupe.js");
const texte = (n) => n.textContent + (n.enfants || []).map(texte).join("");
const lignes = (t) => t.enfants[1].enfants.map((tr) => tr.enfants.map(texte));
const [resume, table] = panneauDecoupe(plan);
const essai = tableauMorceauxEssai([
  { nom: "WING_1", liste_morceaux: [{ nom: "T1", z0: 0, z1: 40, largeur: 100, profondeur: 12, marge: 134.5, rotation: 0, tient: true, plateau: 1, ok: true }] },
  { nom: "WING_2", liste_morceaux: [{ nom: "T1", z0: 0, z1: 40, largeur: 100, profondeur: 12, rotation: 12, tient: true, plateau: 2, ok: false }] },
]);
const boite = encadreNotices(plan.notices);
return { resume: resume.textContent, apercu: lignes(table), essai: lignes(essai), vide: tableauMorceauxEssai([{ nom: "x" }]),
         notice: [boite.attrs.class, texte(boite)], sansNotice: encadreNotices([]),
         indispo: texte(panneauDecoupe({ morceaux: [] })[0]) };
""")
    assert out["resume"] == "3 morceaux, un par plateau : 1 coupe(s) en Z, 1 coupe(s) dans la corde."
    assert out["apercu"][1] == ["T1-B", "0,0 – 60,0", "23,0 – 230,0", "207 × 44", "38°", "2"]
    assert out["apercu"][2][3] == "300 × 50 (ne tient pas)" and out["apercu"][0][4] == "—"
    assert out["essai"] == [["WING_1 : T1", "0,0 – 40,0", "100 × 12", "134,5", "—", "1", "✔"],
                            ["WING_2 : T1", "0,0 – 40,0", "100 × 12", "—", "12°", "2", "✘"]]  # marge absente : —
    assert out["vide"] is None and out["sansNotice"] is None
    assert out["notice"][0] == "message notice" and "coupe dans la corde en 2 morceaux" in out["notice"][1]
    assert "indisponible" in out["indispo"]


def test_libelles_de_la_decoupe():
    out = _node("""
const { libelle, aide, libelleChoix } = await js("libelles.js");
return [libelle(["segment", "chord"]), libelle(["segment"]), libelleChoix(["segment", "chord"], "auto"),
        libelleChoix(["segment", "chord"], "off"), libelleChoix(["passages", "mode"], "off"), aide(["segment", "chord"])];
""")
    assert out[:2] == ["Coupe dans la corde", "Découpe (volume d'impression)"]
    assert out[2].startswith("auto — coupe dans la corde") and out[3].startswith("off — jamais")
    assert out[4] == "off — aucun" and "T1-A" in out[5]


def test_libelles_et_choix_des_evidements():
    out = _node("""
const { aide, libelle, libelleChoix } = await js("libelles.js");
const { PLAN } = await js("schema.js");
const champs = ["mode", "bridge_width", "pitch", "keep_ratio", "keep_min", "ramp_factor"].map((k) => ["ribs_hollow", k]);
return { section: libelle(["ribs_hollow"]), libelles: champs.map(libelle), aides: champs.map(aide),
         choix: ["off", "croisements", "pas"].map((v) => libelleChoix(["ribs_hollow", "mode"], v)),
         couche: [libelle(["rib_layers", 1, "hollow"]), aide(["rib_layers", 1, "hollow"])],
         plan: PLAN.findIndex((b) => b.cle === "ribs_hollow") - PLAN.findIndex((b) => b.cle === "rib_layers") };
""")
    assert out["section"] == "Évidements"
    assert out["libelles"] == [
        "Évidements des nervures", "Largeur des ponts (mm)", "Pas des ponts (mm)",
        "Hauteur gardée (part de la demi-épaisseur)", "Hauteur gardée min. (mm)", "Facteur de pente des rampes",
    ]
    assert all(out["aides"]), out["aides"]  # une aide courte sur chaque champ
    assert "hauteur gardée sous la peau dans les zones évidées (part de la demi-épaisseur" in out["aides"][3]
    assert out["choix"] == ["désactivés", "ponts aux croisements", "ponts à pas régulier"]
    assert out["couche"][0] == "Évidements (surcharge de la couche)" and '{"mode": "off"}' in out["couche"][1]
    assert out["plan"] == 1  # section juste après les couches de nervures


def test_chiffres_estimes_montrent_le_gain_des_evidements():
    out = _node(_DOM + r"""
const { texteEvidements } = await js("onglets/apercu.js");
const e = { masse_g: 51.3, trajet_m: 763.4,
            evidements: { resume: "ponts aux croisements", masse_sans_g: 61.1, trajet_sans_m: 909, gain_masse: 0.1604, gain_trajet: 0.16 } };
return [texteEvidements(e), texteEvidements({ masse_g: 61.1, evidements: null })];
""")
    assert out[0].replace(" ", " ").replace("\xa0", " ") == "évidements : −16 % de masse (51,3 g au lieu de 61,1 g)"
    assert out[1] is None
