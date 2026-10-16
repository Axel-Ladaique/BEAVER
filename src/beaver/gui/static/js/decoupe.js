// Découpe pour le volume d'impression : couleurs des morceaux, appartenance d'un point à un morceau (vue 3D),
// plans de coupe en 3D, panneau « Découpe » de l'aperçu et tableau des morceaux d'un essai.

import { el, nombre } from "./dom.js";

/** Couleurs distinctes des morceaux (vue de dessus, liste, 3D), lisibles en clair comme en sombre. */
export const PALETTE = ["#1f6feb", "#d97706", "#16a34a", "#9333ea", "#db2777", "#0891b2", "#65a30d", "#dc2626"];

export function couleurMorceau(i) {
  return PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

const TOL = 0.5; // mm : un sommet sur une face de coupe ou au bout d'un tronçon appartient au morceau

/** x d'un plan de coupe [x(z0), x(z1)] à la hauteur z. */
function xPlan(plan, z0, z1, z) {
  if (z1 === z0) return plan[0];
  return plan[0] + (z - z0) * (plan[1] - plan[0]) / (z1 - z0);
}

/** Indice du premier morceau qui contient le point (x, z) vu de dessus (repère orienté), -1 sinon. */
export function morceauDe(x, z, morceaux) {
  for (let i = 0; i < morceaux.length; i++) {
    const m = morceaux[i];
    if (z < m.z0 - TOL || z > m.z1 + TOL) continue;
    if (Number.isFinite(m.x0) && (x < m.x0 - TOL || x > m.x1 + TOL)) continue;
    if (m.gauche && x < xPlan(m.gauche, m.z0, m.z1, z) - TOL) continue;
    if (m.droite && x > xPlan(m.droite, m.z0, m.z1, z) + TOL) continue;
    return i;
  }
  return -1;
}

function rgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** Couleur (r, g, b dans [0, 1]) de chaque sommet (positions x, y, z à plat) selon son morceau ; gris hors morceau. */
export function couleursMorceaux(positions, morceaux) {
  const out = new Float32Array(positions.length);
  const couleurs = morceaux.map((_, i) => rgb(couleurMorceau(i)));
  const gris = [0.62, 0.66, 0.7];
  for (let k = 0; k < positions.length; k += 3) {
    const i = morceauDe(positions[k], positions[k + 2], morceaux);
    const c = i >= 0 ? couleurs[i] : gris;
    out[k] = c[0]; out[k + 1] = c[1]; out[k + 2] = c[2];
  }
  return out;
}

/**
 * Plans de coupe en 3D (quadrilatères [[x, y, z] × 4]) : coupes en Z sur toute la corde de l'aile, coupes dans la
 * corde (contiennent l'axe Y) sur leur tronçon ; `ymin`, `ymax` : épaisseur de la peau.
 */
export function quadsDeCoupe(plan, ymin, ymax) {
  const [xmin, , xmax] = plan.aile;
  const m = (xmax - xmin) * 0.03;
  const out = [];
  for (const z of plan.coupes || []) {
    out.push([[xmin - m, ymin, z], [xmax + m, ymin, z], [xmax + m, ymax, z], [xmin - m, ymax, z]]);
  }
  for (const p of plan.plans_corde || []) {
    out.push([[p.x0, ymin, p.z0], [p.x0, ymax, p.z0], [p.x1, ymax, p.z1], [p.x1, ymin, p.z1]]);
  }
  return out;
}

/** Nom affiché d'un morceau : préfixé du corps quand le fichier en a plusieurs. */
export function nomMorceau(m, plusieursCorps) {
  return plusieursCorps && m.corps ? `${m.corps} : ${m.nom}` : m.nom;
}

function rotationTexte(deg) {
  return deg ? `${nombre(Math.abs(deg), 0)}°` : "—";
}

/** Encadré des décisions automatiques de découpe (informations, distinct des avertissements). */
export function encadreNotices(notices, titre = "Découpe automatique") {
  if (!notices || !notices.length) return null;
  return el("div", { classe: "message notice", role: "status" }, [
    el("strong", { texte: titre }),
    // « Wing_1 : découpe automatique : … » → « Wing_1 : … » (le titre de l'encadré le dit déjà)
    el("ul", { classe: "liste-messages" }, notices.map((n) => el("li", {
      texte: String(n).replace(/^(.*? : )?découpe automatique : /, (_, corps) => corps || ""),
    }))),
  ]);
}

/** Panneau « Découpe » de l'aperçu : morceaux (nom, étendue, emprise sur le plateau, rotation, plateau). */
export function panneauDecoupe(plan) {
  const morceaux = plan.morceaux || [];
  if (!morceaux.length) {
    return [el("p", { classe: "vide", texte: "Plan de découpe indisponible : voir les avertissements." })];
  }
  const plusieurs = new Set(morceaux.map((m) => m.corps)).size > 1;
  const resume = morceaux.length === 1
    ? "Pièce imprimée d'un seul morceau."
    : `${morceaux.length} morceaux, un par plateau : ${(plan.coupes || []).length} coupe(s) en Z, `
      + `${(plan.plans_corde || []).length} coupe(s) dans la corde.`;
  const table = el("table", { classe: "table-morceaux" }, [
    el("thead", {}, [el("tr", {}, ["Morceau", "z (mm)", "Corde x (mm)", "Emprise sur le plateau (mm)", "Rotation", "Plateau"]
      .map((t, i) => el("th", { classe: i ? "nombre" : null, texte: t })))]),
    el("tbody", {}, morceaux.map((m, i) => el("tr", { classe: m.tient ? null : "ne-tient-pas" }, [
      el("td", {}, [el("span", { classe: "pastille-morceau", style: `background: ${couleurMorceau(i)}` }), nomMorceau(m, plusieurs)]),
      el("td", { classe: "nombre", texte: `${nombre(m.z0, 1)} – ${nombre(m.z1, 1)}` }),
      el("td", { classe: "nombre", texte: `${nombre(m.x0, 1)} – ${nombre(m.x1, 1)}` }),
      el("td", { classe: "nombre", texte: `${nombre(m.largeur, 0)} × ${nombre(m.profondeur, 0)}${m.tient ? "" : " (ne tient pas)"}` }),
      el("td", { classe: "nombre", texte: rotationTexte(m.rotation) }),
      el("td", { classe: "nombre", texte: String(m.plateau) }),
    ]))),
  ]);
  return [el("p", { classe: "aide", texte: resume }), table];
}

/** Tableau des morceaux imprimés d'un essai (résumé : `corps[].liste_morceaux`), null s'il n'y en a pas. */
export function tableauMorceauxEssai(corps) {
  const lignes = [];
  const plusieurs = (corps || []).length > 1;
  for (const c of corps || []) for (const m of c.liste_morceaux || []) lignes.push({ ...m, corps: c.nom });
  if (!lignes.length) return null;
  return el("table", { classe: "table-morceaux" }, [
    el("thead", {}, [el("tr", {}, ["Morceau", "z (mm)", "Emprise (mm)", "Marge (mm)", "Rotation", "Plateau", "Vase mode"]
      .map((t, i) => el("th", { classe: i === 0 ? null : i === 6 ? "centre" : "nombre", texte: t })))]),
    el("tbody", {}, lignes.map((m) => el("tr", {}, [
      el("td", { texte: nomMorceau(m, plusieurs) }),
      el("td", { classe: "nombre", texte: m.z0 === null ? "—" : `${nombre(m.z0, 1)} – ${nombre(m.z1, 1)}` }),
      el("td", { classe: "nombre", texte: m.largeur === null ? "—"
        : `${nombre(m.largeur, 0)} × ${nombre(m.profondeur, 0)}${m.tient === false ? " (ne tient pas)" : ""}` }),
      el("td", { classe: "nombre", texte: nombre(m.marge, 1) }),
      el("td", { classe: "nombre", texte: rotationTexte(m.rotation) }),
      el("td", { classe: "nombre", texte: String(m.plateau) }),
      el("td", { classe: `centre ${m.ok ? "ok-oui" : "ok-non"}`, texte: m.ok ? "✔" : "✘" }),
    ]))),
  ]);
}
