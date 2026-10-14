// Historique : tableau des essais du projet, détail d'un essai, différences de réglages entre deux essais.

import { api, seg, urlAvecJeton } from "../api.js";
import { badgeStatut, dateFr, duree, el, message, nombre, vider } from "../dom.js";
import { carteEssai } from "../essai.js";

let ctx = null;
const ui = {};
const choisis = []; // au plus deux essais, dans l'ordre de sélection
let ouvert = null; // essai dont le détail est affiché
let chargement = 0;

/** Ordre chronologique des essais, comme `_cle_essai` du serveur : « …-2 » avant « …-10 ». */
function cleEssai(essai) {
  const n = essai.slice(16);
  return `${essai.slice(0, 15)}-${String(/^\d+$/.test(n) ? Number(n) : 1).padStart(4, "0")}`;
}

function valeurTexte(v) {
  if (v === undefined || v === null) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

async function charger() {
  const nom = ctx.etat.projet;
  const n = ++chargement;
  vider(ui.erreur);
  if (!nom) return;
  let essais;
  try {
    essais = await api.get(`/api/projets/${seg(nom)}/essais`);
  } catch (e) {
    if (n === chargement) ui.erreur.append(message(e.message));
    return;
  }
  if (n !== chargement) return;
  for (let i = choisis.length - 1; i >= 0; i--) if (!essais.some((e) => e.essai === choisis[i])) choisis.splice(i, 1);
  majComparer();
  vider(ui.tableau);
  if (!essais.length) {
    ui.tableau.append(el("p", { classe: "vide", texte: "Aucun essai : lancez une génération depuis l'onglet Générer." }));
    return;
  }
  const corps = el("tbody");
  for (const e of essais) {
    const caseChoix = el("input", { type: "checkbox", "aria-label": `Sélectionner l'essai ${e.essai} pour comparaison` });
    caseChoix.checked = choisis.includes(e.essai);
    caseChoix.addEventListener("change", () => {
      const i = choisis.indexOf(e.essai);
      if (caseChoix.checked && i < 0) {
        choisis.push(e.essai);
        if (choisis.length > 2) choisis.shift();
      } else if (!caseChoix.checked && i >= 0) choisis.splice(i, 1);
      for (const c of corps.querySelectorAll("input[type=checkbox]")) c.checked = choisis.includes(c.dataset.essai);
      majComparer();
    });
    caseChoix.dataset.essai = e.essai;
    const base = `/api/projets/${seg(nom)}/essais/${seg(e.essai)}`;
    const alertes = [];
    if (e.nb_erreurs) alertes.push(`${e.nb_erreurs} erreur(s)`);
    if (e.nb_avertissements) alertes.push(`${e.nb_avertissements} avert.`);
    corps.append(el("tr", { classe: ouvert === e.essai ? "selection" : null }, [
      el("td", {}, [caseChoix]),
      el("td", {}, [el("div", { texte: dateFr(e.date) }), el("code", { classe: "aide", texte: e.essai })]),
      el("td", {}, [badgeStatut(e.statut)]),
      el("td", { classe: "nombre", texte: valeurTexte(e.nervures) }),
      el("td", { classe: "nombre", texte: valeurTexte(e.troncons) }),
      el("td", { classe: "nombre", texte: valeurTexte(e.morceaux) }),
      el("td", { classe: "nombre", texte: e.masse !== null && e.masse !== undefined ? `${nombre(e.masse, 1)} g` : "—" }),
      el("td", { classe: "nombre", texte: e.trajet !== null && e.trajet !== undefined ? `${nombre(e.trajet, 1)} m` : "—" }),
      el("td", { classe: "nombre", texte: duree(e.duree) }),
      el("td", { texte: alertes.join(", ") || "—" }),
      el("td", {}, [el("div", { classe: "actions" }, [
        e.rapport ? el("a", {
          classe: "bouton petit", href: urlAvecJeton(`${base}/fichiers/${seg(e.rapport)}`), target: "_blank", rel: "noopener", texte: "Rapport",
        }) : null,
        el("button", { type: "button", classe: "petit", texte: ouvert === e.essai ? "Masquer" : "Détails",
          "aria-expanded": String(ouvert === e.essai),
          onclick: () => {
            ouvert = ouvert === e.essai ? null : e.essai;
            charger();
            detail();
          } }),
      ])]),
    ]));
  }
  ui.tableau.append(el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      el("th", { texte: "Comparer" }), el("th", { texte: "Date" }), el("th", { texte: "Statut" }),
      el("th", { classe: "nombre", texte: "Nervures" }), el("th", { classe: "nombre", texte: "Tronçons" }),
      el("th", { classe: "nombre", texte: "Morceaux" }),
      el("th", { classe: "nombre", texte: "Masse" }), el("th", { classe: "nombre", texte: "Trajet" }),
      el("th", { classe: "nombre", texte: "Durée" }), el("th", { texte: "Alertes" }), el("th", { texte: "Actions" }),
    ])]),
    corps,
  ]));
}

async function detail() {
  vider(ui.detail);
  const nom = ctx.etat.projet;
  const essai = ouvert;
  if (!nom || !essai) return;
  let d;
  try {
    d = await api.get(`/api/projets/${seg(nom)}/essais/${seg(essai)}`);
  } catch (e) {
    ui.detail.append(message(e.message));
    return;
  }
  if (essai !== ouvert) return;
  const carte = carteEssai(nom, essai, d.resume || {});
  if (d.config) {
    const pre = el("pre", { classe: "journal", texte: JSON.stringify(d.config, null, 2) });
    carte.append(el("details", {}, [el("summary", { texte: "Configuration résolue utilisée" }), pre]));
  }
  ui.detail.append(carte);
}

function majComparer() {
  ui.comparer.disabled = choisis.length !== 2;
  ui.choix.textContent = choisis.length ? `Sélection : ${choisis.join(" et ")}` : "Cochez deux essais pour comparer leurs réglages.";
}

async function comparer() {
  vider(ui.diff);
  const nom = ctx.etat.projet;
  if (choisis.length !== 2) return;
  const [a, b] = [...choisis].sort((x, y) => (cleEssai(x) < cleEssai(y) ? -1 : 1));
  let lignes;
  try {
    lignes = await api.get(`/api/projets/${seg(nom)}/essais/diff?a=${seg(a)}&b=${seg(b)}`);
  } catch (e) {
    ui.diff.append(message(e.message));
    return;
  }
  const carte = el("div", { classe: "carte" }, [el("h3", { texte: `Différences de réglages : ${a} → ${b}` })]);
  if (!lignes.length) carte.append(el("p", { classe: "vide", texte: "Réglages identiques." }));
  else {
    carte.append(el("table", {}, [
      el("thead", {}, [el("tr", {}, [el("th", { texte: "Réglage" }), el("th", { texte: a }), el("th", { texte: b })])]),
      el("tbody", {}, lignes.map((l) => el("tr", {}, [
        el("td", {}, [el("code", { texte: l.chemin.join(".") })]),
        el("td", { texte: valeurTexte(l.a) }),
        el("td", { texte: valeurTexte(l.b) }),
      ]))),
    ]));
  }
  ui.diff.append(carte);
}

export function init(panneau, contexte) {
  ctx = contexte;
  ui.erreur = el("div");
  ui.tableau = el("div");
  ui.detail = el("div");
  ui.diff = el("div");
  ui.choix = el("span", { classe: "aide" });
  ui.comparer = el("button", { type: "button", texte: "Comparer les réglages", disabled: true, onclick: comparer });
  ui.vide = el("div");
  ui.contenu = el("div", { hidden: true }, [
    el("div", { classe: "carte" }, [
      el("div", { classe: "couche-entete" }, [
        el("h2", { texte: "Historique des essais" }),
        el("button", { type: "button", classe: "petit", texte: "Actualiser", onclick: () => charger() }),
      ]),
      ui.erreur,
      el("div", { classe: "actions" }, [ui.comparer, ui.choix]),
      ui.tableau,
    ]),
    ui.diff,
    ui.detail,
  ]);
  panneau.append(ui.vide, ui.contenu);
  ctx.sansProjet(ui.vide);
  majComparer();
  ctx.sur("projet", (nom) => {
    choisis.length = 0;
    ouvert = null;
    vider(ui.diff);
    vider(ui.detail);
    vider(ui.tableau);
    ui.contenu.hidden = !nom;
    if (nom) vider(ui.vide);
    else ctx.sansProjet(ui.vide);
    majComparer();
    if (nom && ctx.etat.onglet === "historique") charger();
  });
  ctx.sur("generation-finie", () => {
    if (ctx.etat.onglet === "historique") charger();
  });
}

export function afficher() {
  charger();
  if (ouvert) detail();
}
