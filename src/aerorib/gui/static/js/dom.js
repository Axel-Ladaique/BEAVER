// Construction DOM sans innerHTML : tout texte venu du serveur passe par textContent.

const SVG_NS = "http://www.w3.org/2000/svg";

function remplir(noeud, attrs, enfants) {
  for (const [cle, val] of Object.entries(attrs || {})) {
    if (val === undefined || val === null || val === false) continue;
    if (cle === "texte") noeud.textContent = String(val);
    else if (cle === "classe") noeud.setAttribute("class", val);
    else if (cle.startsWith("on") && typeof val === "function") noeud.addEventListener(cle.slice(2), val);
    else if (cle === "dataset") Object.assign(noeud.dataset, val);
    else if (cle in noeud && typeof val !== "string" && !(noeud instanceof SVGElement)) noeud[cle] = val;
    else noeud.setAttribute(cle, val === true ? "" : String(val));
  }
  for (const e of [].concat(enfants ?? [])) {
    if (e === null || e === undefined || e === false) continue;
    noeud.append(e instanceof Node ? e : document.createTextNode(String(e)));
  }
  return noeud;
}

/** el("button", {classe: "x", onclick: f, texte: "OK"}, [enfants…]) */
export function el(tag, attrs = {}, enfants = []) {
  return remplir(document.createElement(tag), attrs, enfants);
}

/** Élément SVG (attributs toujours posés en attributs). */
export function svg(tag, attrs = {}, enfants = []) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [cle, val] of Object.entries(attrs || {})) {
    if (val === undefined || val === null) continue;
    if (cle === "texte") n.textContent = String(val);
    else if (cle === "classe") n.setAttribute("class", val);
    else n.setAttribute(cle, String(val));
  }
  for (const e of [].concat(enfants ?? [])) if (e) n.append(e);
  return n;
}

export function vider(noeud) {
  noeud.replaceChildren();
  return noeud;
}

export function message(texte, genre = "erreur") {
  return el("div", { classe: `message ${genre}`, role: genre === "erreur" ? "alert" : "status", texte });
}

const NF = new Map();
/** Nombre formaté à la française (virgule décimale). */
export function nombre(v, decimales = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (!NF.has(decimales)) {
    NF.set(decimales, new Intl.NumberFormat("fr-FR", { minimumFractionDigits: decimales, maximumFractionDigits: decimales }));
  }
  return NF.get(decimales).format(v);
}

export function duree(s) {
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${nombre(s, s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
}

export function dateFr(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" });
}

/** Classe CSS d'un statut d'essai (« non imprimable » → non-imprimable, « annulé » → annule). */
export function classeStatut(statut) {
  return String(statut || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, "-");
}

export function badgeStatut(statut) {
  return el("span", { classe: `statut ${classeStatut(statut)}`, texte: statut || "—" });
}

/**
 * Gabarit Bambu en lecture seule ({chemin_nom, existe} renvoyé par l'API des profils) :
 * nom du fichier et présence, jamais de chemin complet ni de champ éditable.
 */
export function gabaritLectureSeule(g, { present = " — présent", absent = " — fichier introuvable",
  aucun = "aucun gabarit : le 3MF ne sera pas produit", note = null } = {}) {
  if (!g || !g.chemin_nom) return el("span", { classe: "lecture-seule", texte: aucun });
  return el("span", { classe: "lecture-seule" }, [
    el("code", { texte: g.chemin_nom }),
    g.existe ? present : absent,
    note ? el("span", { classe: "aide", texte: note }) : null,
  ]);
}

/** Rappel retardé : chaque appel repousse l'exécution de `ms`. */
export function debounce(fn, ms) {
  let t = null;
  const f = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  f.annuler = () => clearTimeout(t);
  return f;
}
