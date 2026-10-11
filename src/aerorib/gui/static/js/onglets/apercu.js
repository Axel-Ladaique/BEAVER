// Aperçu instantané : coupe à hauteur réglable, vue de dessus, chiffres estimés, 3D (three.js à la demande).
// Un seul panneau, posé à côté des réglages (onglet Réglages) ou seul (onglet Aperçu).

import { appel, ErreurApi, seg } from "../api.js";
import { debounce, el, message, nombre, duree, svg, vider } from "../dom.js";

const RELANCE_MS = 1000; // préparation en cours : nouvel essai
const CURSEUR_MS = 120;
const DEFAUTS = {
  connectivity: "contour non unique",
  narrow_slit: "fente trop fine",
  skin: "peau entamée",
  material_width: "matière trop fine",
};

let ctx = null;
let racine = null;
const ui = {};
const etat = {
  projet: null,
  config: null, // dernière configuration proposée (dict du projet)
  z: null, // null = milieu de la pièce (choisi par le serveur)
  seq: 0,
  relance: null,
  resultat: null,
  zmin: 0,
  zmax: 1,
  configVue3d: null,
  visible: false, // panneau affiché (onglet Réglages ou Aperçu)
  aJour: true, // la dernière demande a reçu sa réponse définitive
};
let vue3d = null;

// --- requêtes -------------------------------------------------------------------------------------------
async function demander() {
  if (!etat.projet || etat.config === null) return;
  etat.aJour = false;
  if (!etat.visible) return; // onglet quitté : demandé de nouveau à l'affichage (placer)
  const s = ++etat.seq;
  clearTimeout(etat.relance);
  const nom = etat.projet;
  const corps = { config: etat.config };
  if (etat.z !== null) corps.z = etat.z;
  ui.etat.textContent = "Calcul de l'aperçu…";
  let r;
  try {
    r = await appel("POST", `/api/projets/${seg(nom)}/apercu`, corps);
  } catch (e) {
    if (s !== etat.seq) return;
    ui.etat.textContent = "";
    if (e instanceof ErreurApi && e.statut === 422 && /^aperçu impossible/i.test(e.message)) {
      // réglages valides, mais la géométrie de l'aperçu a échoué : aucun champ en cause
      etat.aJour = true;
      ctx.emettre("validation", { ok: true });
      vider(ui.erreur).append(message(e.message));
    } else if (e instanceof ErreurApi && e.statut === 422) {
      etat.aJour = true;
      ctx.emettre("validation", { erreur: e.message });
      vider(ui.erreur).append(message("Réglages invalides : voir les champs signalés dans le formulaire.", "attention"));
    } else {
      ui.etat.textContent = "";
      vider(ui.erreur).append(message(`Aperçu impossible : ${e.message}`));
    }
    return;
  }
  if (s !== etat.seq) return; // réponse périmée : une demande plus récente est partie
  ctx.emettre("validation", { ok: true });
  vider(ui.erreur);
  if (r.statut === 202) {
    ui.etat.textContent = (r.donnees && r.donnees.message) || "Préparation de la pièce…";
    etat.relance = setTimeout(() => {
      etat.relance = null;
      if (s === etat.seq) demander();
    }, RELANCE_MS);
    return;
  }
  etat.resultat = r.donnees;
  etat.aJour = true;
  ui.etat.textContent = "";
  rendre();
  if (vue3d) ui.note3d.hidden = JSON.stringify(etat.config) === etat.configVue3d;
}

const demanderVite = debounce(demander, CURSEUR_MS);

// --- rendu ---------------------------------------------------------------------------------------------
function rendre() {
  const r = etat.resultat;
  if (!r) return;
  const aile = r.planform.aile; // [xmin, zmin, xmax, zmax]
  etat.zmin = aile[1];
  etat.zmax = aile[3];
  ui.curseur.min = String(etat.zmin);
  ui.curseur.max = String(etat.zmax);
  ui.curseur.step = String(Math.max((etat.zmax - etat.zmin) / 500, 0.01));
  ui.curseur.value = String(r.section.z);
  ui.curseur.disabled = false;
  ui.valeurZ.textContent = `z = ${nombre(r.section.z, 1)} mm`;
  vider(ui.coupe).append(dessinCoupe(r.section));
  vider(ui.dessus).append(dessinDessus(r.planform, r.section.z));
  vider(ui.chiffres).append(...chiffres(r.estimates));
  vider(ui.avertissements);
  if (r.avertissements && r.avertissements.length) {
    ui.avertissements.append(el("div", { classe: "message attention", role: "status" }, [
      el("strong", { texte: "Avertissements" }),
      el("ul", { classe: "liste-messages" }, r.avertissements.map((a) => el("li", { texte: a }))),
    ]));
  }
}

function chemin(poly, versEcran) {
  const anneau = (pts) => pts.map(([a, b], i) => {
    const [x, y] = versEcran(a, b);
    return `${i ? "L" : "M"}${x.toFixed(3)},${y.toFixed(3)}`;
  }).join("") + "Z";
  return [anneau(poly.exterieur), ...(poly.trous || []).map(anneau)].join("");
}

function boite(points, marge, rapportMin) {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of points) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  if (!Number.isFinite(x0)) return null;
  let w = x1 - x0 || 1;
  let h = y1 - y0 || 1;
  const m = Math.max(w, h) * marge;
  x0 -= m; y0 -= m; w += 2 * m; h += 2 * m;
  if (h < w * rapportMin) { // bande trop plate : on agrandit la hauteur (même échelle sur les deux axes)
    const d = w * rapportMin - h;
    y0 -= d / 2; h += d;
  }
  return { x0, y0, w, h };
}

let numeroClip = 0;
const TRAIT = { "vector-effect": "non-scaling-stroke" };

function dessinCoupe(sec) {
  const versEcran = (x, y) => [x, -y]; // y vers le haut
  const pts = [];
  for (const p of [...sec.peau, ...sec.polygones]) for (const q of p.exterieur) pts.push([q[0], -q[1]]);
  const b = boite(pts, 0.03, 0.22);
  if (!b) return el("p", { classe: "vide", texte: `Aucune matière à z = ${nombre(sec.z, 1)} mm.` });
  const racineSvg = svg("svg", {
    viewBox: `${b.x0} ${b.y0} ${b.w} ${b.h}`, classe: "vue-svg vue-coupe", role: "img",
    "aria-label": `Coupe approximative à z = ${nombre(sec.z, 1)} mm`,
  });
  racineSvg.append(svg("title", { texte: `Coupe approximative à z = ${nombre(sec.z, 1)} mm (sans booléens 3D)` }));
  for (const p of sec.polygones) racineSvg.append(svg("path", { d: chemin(p, versEcran), classe: "matiere", "stroke-width": 1, ...TRAIT }));
  for (const p of sec.peau) racineSvg.append(svg("path", { d: chemin(p, versEcran), classe: "peau", "stroke-width": 1, ...TRAIT }));
  // empreintes des plaques (outils de coupe) limitées à la peau, sinon elles débordent de la section
  const clipId = `clip-coupe-${++numeroClip}`;
  racineSvg.append(svg("defs", {}, [svg("clipPath", { id: clipId },
    sec.peau.map((p) => svg("path", { d: chemin(p, versEcran), "clip-rule": "evenodd" })))]));
  const plaques = svg("g", { "clip-path": `url(#${clipId})` });
  for (const f of sec.empreintes) {
    plaques.append(svg("path", {
      d: chemin(f.polygone, versEcran), classe: f.famille > 0 ? "plaque-a" : "plaque-b", "stroke-width": 1, ...TRAIT,
    }));
  }
  racineSvg.append(plaques);
  if (sec.bande && sec.bande.length > 1) {
    const d = sec.bande.map(([x, y], i) => `${i ? "L" : "M"}${x},${-y}`).join("");
    racineSvg.append(svg("path", { d, classe: "bande", "stroke-width": 1.5, ...TRAIT }, [
      svg("title", { texte: `Bande centrale (séparation haut/bas, ${nombre(sec.largeur_bande, 2)} mm)` }),
    ]));
  }
  for (const t of sec.trous || []) {
    racineSvg.append(svg("circle", { cx: t.x, cy: -t.y, r: t.r, classe: "trou", "stroke-width": 1.5, ...TRAIT }, [
      svg("title", { texte: `${t.nom} (${t.type}, Ø ${nombre(2 * t.r, 2)} mm)` }),
    ]));
  }
  const rMin = b.w * 0.015;
  for (const d of sec.defauts || []) {
    const nomDefaut = DEFAUTS[d.check] || d.check;
    const bulle = svg("title", { texte: `${nomDefaut} : ${d.message}` });
    let cx = (b.x0 + b.w / 2);
    let cy = (b.y0 + b.h / 2);
    let r = b.w / 2;
    if (d.bounds) {
      const [x0, y0, x1, y1] = d.bounds;
      cx = (x0 + x1) / 2;
      cy = -(y0 + y1) / 2;
      r = Math.max(Math.hypot(x1 - x0, y1 - y0) / 2 * 1.15, rMin);
    }
    racineSvg.append(svg("g", { classe: "defaut-groupe" }, [
      bulle,
      svg("circle", { cx, cy, r, classe: "defaut", "stroke-width": 2.5, ...TRAIT }),
      svg("circle", { cx, cy, r, classe: "defaut-zone" }),
    ]));
  }
  return racineSvg;
}

export function dessinDessus(plan, zCourant) {
  // envergure (z) horizontale, corde (x) verticale
  const [xmin, zmin, xmax, zmax] = plan.aile;
  const b = boite([[zmin, -xmax], [zmax, -xmin]], 0.03, 0.18);
  const s = svg("svg", {
    viewBox: `${b.x0} ${b.y0} ${b.w} ${b.h}`, classe: "vue-svg vue-dessus", role: "img",
    "aria-label": "Vue de dessus du motif (envergure horizontale, corde verticale) ; cliquer pour choisir la hauteur de coupe",
  });
  s.append(svg("title", { texte: "Vue de dessus : cliquer pour placer la coupe à cette hauteur" }));
  s.append(svg("rect", { x: zmin, y: -xmax, width: zmax - zmin, height: xmax - xmin, classe: "contour-aile", "stroke-width": 1, ...TRAIT }));
  for (const [x0, z0, x1, z1, couche, famille] of plan.segments) {
    s.append(svg("line", {
      x1: z0, y1: -x0, x2: z1, y2: -x1, classe: famille > 0 ? "reseau-a" : "reseau-b",
      "stroke-width": couche === 0 ? 1.5 : 1, "stroke-opacity": couche === 0 ? 1 : 0.6, ...TRAIT,
    }));
  }
  for (const t of plan.trous || []) {
    // trou vu de dessus : étendue z × diamètre (x ± r) ; gaine (trou + manchon) en pointillés
    const titre = `${t.type} « ${t.nom} » : x = ${nombre(t.x, 1)} mm, Ø ${nombre(2 * t.r, 1)} mm, `
      + `z = ${nombre(t.z0, 1)} – ${nombre(t.z1, 1)} mm`;
    const g = svg("g", { classe: t.type === "longeron" ? "trou-dessus longeron" : "trou-dessus passage" }, [svg("title", { texte: titre })]);
    if (t.gaine > t.r) {
      g.append(svg("rect", {
        x: t.z0, y: -(t.x + t.gaine), width: t.z1 - t.z0, height: 2 * t.gaine, classe: "gaine", "stroke-width": 1, ...TRAIT,
      }));
    }
    g.append(svg("rect", {
      x: t.z0, y: -(t.x + t.r), width: t.z1 - t.z0, height: 2 * t.r,
      classe: t.type === "passage rebouché" ? "trou rebouche" : "trou", "stroke-width": 1.5, ...TRAIT,
    }));
    s.append(g);
  }
  for (const c of plan.coupes || []) {
    s.append(svg("line", { x1: c, y1: -xmax - b.h, x2: c, y2: -xmin + b.h, classe: "coupe-troncon", "stroke-width": 1.5, ...TRAIT }, [
      svg("title", { texte: `Coupe de tronçon à z = ${nombre(c, 1)} mm` }),
    ]));
  }
  s.append(svg("line", { x1: zCourant, y1: -xmax - b.h, x2: zCourant, y2: -xmin + b.h, classe: "niveau-z", "stroke-width": 1.5, ...TRAIT }, [
    svg("title", { texte: `Coupe affichée : z = ${nombre(zCourant, 1)} mm` }),
  ]));
  s.addEventListener("click", (ev) => {
    const m = s.getScreenCTM();
    if (!m) return;
    const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    choisirZ(Math.min(Math.max(p.x, zmin), zmax));
  });
  return s;
}

function tuile(valeur, etiquette, titre) {
  return el("div", { classe: "chiffre", title: titre || null }, [
    el("div", { classe: "nombre-grand", texte: valeur }),
    el("div", { classe: "etiquette", texte: etiquette }),
  ]);
}

function chiffres(e) {
  const out = [
    tuile(String(e.plaques), "plaques (total)"),
    ...(e.plaques_par_couche || []).map((c) => tuile(String(c.plaques), `plaques — couche ${c.indice + 1} (${c.nom})`)),
    tuile(e.troncons ? String(e.troncons.length) : "—", e.troncons
      ? `tronçon(s) : ${e.troncons.map((h) => `${nombre(h, 1)} mm`).join(" · ")}`
      : "tronçons : découpe impossible (voir avertissements)"),
    tuile(String(e.couches), "couches"),
    tuile(`${nombre(e.trajet_m, 1)} m`, "trajet estimé", "moyenne sur un échantillon de couches"),
    tuile(`${nombre(e.masse_g, 1)} g`, "masse estimée", "trajet × largeur de ligne × hauteur de couche × densité"),
    tuile(duree(e.duree_s), "durée de génération estimée", `préparation mesurée : ${duree(e.preparation_s)}`),
  ];
  return out;
}

function choisirZ(z) {
  etat.z = z;
  ui.curseur.value = String(z);
  ui.valeurZ.textContent = `z = ${nombre(z, 1)} mm`;
  demanderVite();
}

// --- 3D -----------------------------------------------------------------------------------------------
async function stl(nom, quoi) {
  for (let i = 0; i < 300; i++) {
    try {
      return (await appel("GET", `/api/projets/${seg(nom)}/apercu/${quoi}.stl`, undefined, { brut: true })).donnees;
    } catch (e) {
      if (!(e instanceof ErreurApi) || e.statut !== 409) throw e;
      ui.etat3d.textContent = "Préparation de la pièce…";
      await new Promise((ok) => setTimeout(ok, RELANCE_MS));
      if (nom !== etat.projet) throw new Error("projet changé");
    }
  }
  throw new Error("préparation trop longue");
}

async function ouvrir3d() {
  ui.bouton3d.disabled = true;
  ui.etat3d.textContent = "Chargement de three.js…";
  vider(ui.erreur3d);
  let THREE, OrbitControls, STLLoader;
  try {
    [THREE, { OrbitControls }, { STLLoader }] = await Promise.all([
      import("three"),
      import("three/addons/controls/OrbitControls.js"),
      import("three/addons/loaders/STLLoader.js"),
    ]);
  } catch {
    ui.etat3d.textContent = "";
    ui.erreur3d.append(message(
      "Vue 3D indisponible : three.js n'a pas pu être chargé depuis cdn.jsdelivr.net (connexion Internet requise). " +
      "Le reste de l'interface fonctionne normalement."));
    ui.bouton3d.disabled = false;
    return;
  }
  if (!vue3d) vue3d = creerScene(THREE, OrbitControls);
  vue3d.STLLoader = STLLoader;
  ui.bouton3d.textContent = "Actualiser la 3D";
  ui.boite3d.hidden = false;
  await charger3d();
  ui.bouton3d.disabled = false;
}

function creerScene(THREE, OrbitControls) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  ui.boite3d.append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100000);
  camera.up.set(0, 0, 1); // z = envergure (verticale d'impression)
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.4));
  const soleil = new THREE.DirectionalLight(0xffffff, 1.6);
  soleil.position.set(1, -2, 3);
  scene.add(soleil);
  const controls = new OrbitControls(camera, renderer.domElement);
  const rendre3d = () => renderer.render(scene, camera);
  controls.addEventListener("change", rendre3d);
  const taille = () => {
    const w = ui.boite3d.clientWidth || 600;
    const h = ui.boite3d.clientHeight || 420;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    rendre3d();
  };
  new ResizeObserver(taille).observe(ui.boite3d);
  return { THREE, renderer, scene, camera, controls, rendre3d, taille, maillages: [] };
}

async function charger3d() {
  const v = vue3d;
  const { THREE } = v;
  const nom = etat.projet;
  for (const m of v.maillages) {
    v.scene.remove(m);
    m.geometry.dispose();
    m.material.dispose();
  }
  v.maillages = [];
  const chargeur = new v.STLLoader();
  try {
    ui.etat3d.textContent = "Maillage de la peau…";
    const peau = chargeur.parse(await stl(nom, "peau"));
    const mPeau = new THREE.Mesh(peau, new THREE.MeshStandardMaterial({
      color: 0x9fb3c8, transparent: true, opacity: 0.22, depthWrite: false, side: THREE.DoubleSide,
    }));
    v.scene.add(mPeau);
    v.maillages.push(mPeau);
    peau.computeBoundingSphere();
    const { center, radius } = peau.boundingSphere;
    v.controls.target.copy(center);
    v.camera.position.set(center.x + radius * 1.4, center.y - radius * 2.2, center.z + radius * 0.9);
    v.camera.near = radius / 100;
    v.camera.far = radius * 50;
    v.camera.updateProjectionMatrix();
    v.controls.update();
    v.taille();
    ui.etat3d.textContent = "Maillage des plaques…";
    etat.configVue3d = JSON.stringify(etat.config);
    try {
      const plaques = chargeur.parse(await stl(nom, "plaques"));
      const mPlaques = new THREE.Mesh(plaques, new THREE.MeshStandardMaterial({ color: 0x1f6feb, side: THREE.DoubleSide }));
      v.scene.add(mPlaques);
      v.maillages.push(mPlaques);
    } catch (e) {
      ui.erreur3d.append(message(`Plaques non affichées : ${e.message}`, "attention"));
    }
    ui.etat3d.textContent = "Peau transparente, plaques des nervures en bleu (sans découpe). Glisser pour tourner, molette pour zoomer.";
    ui.note3d.hidden = true;
    v.rendre3d();
  } catch (e) {
    ui.etat3d.textContent = "";
    ui.erreur3d.append(message(`Vue 3D impossible : ${e.message}`));
  }
}

function fermer3d() {
  if (!vue3d) return;
  for (const m of vue3d.maillages) {
    vue3d.scene.remove(m);
    m.geometry.dispose();
    m.material.dispose();
  }
  vue3d.maillages = [];
  vue3d.rendre3d();
  ui.boite3d.hidden = true;
  ui.bouton3d.textContent = "Afficher la vue 3D";
  ui.etat3d.textContent = "";
  ui.note3d.hidden = true;
}

// --- montage ---------------------------------------------------------------------------------------------
function construire() {
  ui.etat = el("div", { classe: "etat-apercu", role: "status", "aria-live": "polite" });
  ui.erreur = el("div");
  ui.curseur = el("input", { type: "range", min: "0", max: "1", step: "0.01", disabled: true, id: "curseur-z" });
  ui.curseur.addEventListener("input", () => choisirZ(Number(ui.curseur.value)));
  ui.valeurZ = el("output", { for: "curseur-z", texte: "z = —" });
  ui.coupe = el("div", {}, [el("p", { classe: "vide", texte: "Ouvrez un projet pour voir l'aperçu." })]);
  ui.dessus = el("div");
  ui.chiffres = el("div", { classe: "chiffres" });
  ui.avertissements = el("div");
  ui.bouton3d = el("button", { type: "button", texte: "Afficher la vue 3D", onclick: ouvrir3d });
  ui.etat3d = el("span", { classe: "etat-apercu", role: "status" });
  ui.erreur3d = el("div");
  ui.note3d = el("span", { classe: "aide", texte: "Réglages modifiés depuis l'affichage : cliquez sur « Actualiser la 3D ».", hidden: true });
  ui.boite3d = el("div", { classe: "vue-3d", hidden: true, role: "img", "aria-label": "Vue 3D approximative de la pièce" });

  racine = el("div", { classe: "apercu" }, [
    el("div", { classe: "carte" }, [
      el("h2", { texte: "Aperçu instantané" }),
      ui.etat,
      ui.erreur,
      ui.avertissements,
      el("div", { classe: "apercu-vues" }, [
        el("div", {}, [
          el("h3", { texte: "Coupe à hauteur réglable (approximation)" }),
          el("div", { classe: "curseur-z" }, [el("label", { for: "curseur-z", texte: "Hauteur" }), ui.curseur, ui.valeurZ]),
          ui.coupe,
          el("div", { classe: "legende" }, [
            el("span", {}, [el("span", { classe: "pastille", style: "background: var(--matiere)" }), "matière"]),
            el("span", {}, [el("span", { classe: "pastille", style: "background: var(--reseau-a)" }), "nervures réseau +"]),
            el("span", {}, [el("span", { classe: "pastille", style: "background: var(--reseau-b)" }), "nervures réseau −"]),
            el("span", {}, [el("span", { classe: "pastille", style: "border-top: 2px dashed var(--bande)" }), "bande centrale"]),
            el("span", {}, [el("span", { classe: "pastille", style: "border: 2px solid var(--trou)" }), "trous"]),
            el("span", {}, [el("span", { classe: "pastille", style: "border: 2px solid var(--erreur)" }), "défaut (survoler)"]),
          ]),
        ]),
        el("div", {}, [
          el("h3", { texte: "Vue de dessus (envergure →, corde ↑)" }),
          ui.dessus,
          el("div", { classe: "legende" }, [
            el("span", {}, [el("span", { classe: "pastille", style: "background: var(--reseau-a)" }), "réseau +angle"]),
            el("span", {}, [el("span", { classe: "pastille", style: "background: var(--reseau-b)" }), "réseau −angle"]),
            el("span", {}, [el("span", { classe: "pastille pastille-trou" }), "longeron / passage (gaine en pointillés)"]),
            el("span", {}, [el("span", { classe: "pastille", style: "border-top: 2px dashed var(--texte)" }), "coupe de tronçon"]),
            el("span", {}, [el("span", { classe: "pastille", style: "border-top: 2px dashed var(--attention)" }), "coupe affichée"]),
          ]),
        ]),
      ]),
      el("h3", { texte: "Chiffres estimés" }),
      ui.chiffres,
      el("h3", { texte: "3D approximative" }),
      el("div", { classe: "actions" }, [ui.bouton3d, ui.note3d]),
      ui.etat3d,
      ui.erreur3d,
      ui.boite3d,
    ]),
  ]);
}

export function init(panneau, contexte) {
  ctx = contexte;
  construire();
  panneau.append(racine);
  ctx.sur("projet", (nom) => {
    etat.projet = nom;
    etat.config = null;
    etat.z = null;
    etat.resultat = null;
    etat.seq += 1;
    clearTimeout(etat.relance);
    fermer3d();
    vider(ui.erreur);
    vider(ui.chiffres);
    vider(ui.dessus);
    vider(ui.avertissements);
    vider(ui.coupe).append(el("p", { classe: "vide", texte: nom ? "Chargement…" : "Ouvrez un projet pour voir l'aperçu." }));
    ui.curseur.disabled = true;
  });
  // configuration proposée par les réglages (déjà « débouncée » côté réglages)
  ctx.sur("config-proposee", (config) => {
    etat.config = config;
    demander();
  });
  ctx.sur("reconnexion", () => {
    if (!etat.aJour) demander();
  });
}

export function afficher() {
  if (!etat.projet) {
    ui.etat.textContent = "Aucun projet ouvert : choisissez un projet dans l'onglet Projets.";
  }
}

/** Pose le panneau dans `conteneur` ; compact = à côté des réglages. */
export function placer(conteneur, { compact }) {
  if (!conteneur) return;
  if (racine.parentNode !== conteneur) conteneur.append(racine);
  racine.classList.toggle("apercu-plein", !compact);
  if (vue3d) vue3d.taille();
  etat.visible = true;
  if (!etat.aJour) demander();
}

/** Panneau caché (autre onglet) : plus de requêtes ni de relance de préparation. */
export function masquer() {
  etat.visible = false;
  if (!etat.aJour || etat.relance) {
    etat.seq += 1; // une réponse en vol sera ignorée ; la demande repartira à l'affichage
    clearTimeout(etat.relance);
    etat.relance = null;
    etat.aJour = false;
  }
}
