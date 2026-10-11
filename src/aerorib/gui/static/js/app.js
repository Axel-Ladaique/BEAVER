// Point d'entrée : onglets (clavier, #ancre), projet courant, bus d'événements entre onglets.

import { api, surReconnexion } from "./api.js";
import { el, vider } from "./dom.js";
import * as projets from "./onglets/projets.js";
import * as reglages from "./onglets/reglages.js";
import * as apercu from "./onglets/apercu.js";
import * as generer from "./onglets/generer.js";
import * as historique from "./onglets/historique.js";
import * as profils from "./onglets/profils.js";

const MODULES = { projets, reglages, apercu, generer, historique, profils };
const ORDRE = Object.keys(MODULES);
const CLE_PROJET = "aerorib-projet";

// --- état partagé et événements ----------------------------------------------------------------------
const ecouteurs = new Map();

export const etat = {
  projet: null, // nom du projet ouvert
  onglet: null,
};

export function sur(evenement, fn) {
  if (!ecouteurs.has(evenement)) ecouteurs.set(evenement, new Set());
  ecouteurs.get(evenement).add(fn);
}

export function emettre(evenement, donnees) {
  for (const fn of ecouteurs.get(evenement) || []) {
    try {
      fn(donnees);
    } catch (e) {
      console.error(e);
    }
  }
}

export function ouvrirProjet(nom) {
  if (etat.projet === nom) return;
  if (etat.projet && reglages.estModifie() &&
      !window.confirm("Les réglages modifiés n'ont pas été enregistrés. Changer de projet quand même ?")) {
    return;
  }
  etat.projet = nom;
  try {
    if (nom) sessionStorage.setItem(CLE_PROJET, nom);
    else sessionStorage.removeItem(CLE_PROJET);
  } catch {
    /* sans stockage : le projet n'est pas retenu au rechargement */
  }
  const p = document.getElementById("projet-courant");
  vider(p);
  if (nom) p.append("Projet : ", el("strong", { texte: nom }));
  else p.append("Aucun projet ouvert");
  emettre("projet", nom);
}

/** Message affiché dans un onglet qui exige un projet ouvert. */
export function sansProjet(conteneur) {
  vider(conteneur).append(
    el("div", { classe: "carte" }, [
      el("p", { classe: "vide", texte: "Aucun projet ouvert." }),
      el("button", { type: "button", texte: "Choisir un projet", onclick: () => allerA("projets") }),
    ]),
  );
}

// --- onglets -------------------------------------------------------------------------------------------
function boutons() {
  return [...document.querySelectorAll('.onglets [role="tab"]')];
}

export function allerA(nom, { focus = false } = {}) {
  if (!MODULES[nom]) nom = "projets";
  etat.onglet = nom;
  for (const b of boutons()) {
    const actif = b.dataset.onglet === nom;
    b.setAttribute("aria-selected", String(actif));
    b.tabIndex = actif ? 0 : -1;
    if (actif && focus) b.focus();
  }
  for (const n of ORDRE) document.getElementById(`panneau-${n}`).hidden = n !== nom;
  if (window.location.hash !== `#${nom}`) history.replaceState(null, "", `${window.location.search}#${nom}`);
  // l'aperçu est un seul panneau, posé à côté des réglages ou seul dans son onglet
  if (nom === "reglages") apercu.placer(reglages.emplacementApercu(), { compact: true });
  if (nom === "apercu") apercu.placer(document.getElementById("panneau-apercu"), { compact: false });
  if (nom !== "reglages" && nom !== "apercu") apercu.masquer();
  MODULES[nom].afficher?.();
}

function clavierOnglets(ev) {
  const liste = boutons();
  const i = liste.indexOf(document.activeElement);
  if (i < 0) return;
  let j = null;
  if (ev.key === "ArrowRight") j = (i + 1) % liste.length;
  else if (ev.key === "ArrowLeft") j = (i - 1 + liste.length) % liste.length;
  else if (ev.key === "Home") j = 0;
  else if (ev.key === "End") j = liste.length - 1;
  if (j === null) return;
  ev.preventDefault();
  allerA(liste[j].dataset.onglet, { focus: true });
}

async function demarrer() {
  const ctx = { etat, sur, emettre, ouvrirProjet, allerA, sansProjet };
  for (const n of ORDRE) MODULES[n].init(document.getElementById(`panneau-${n}`), ctx);
  const nav = document.querySelector(".onglets");
  nav.addEventListener("click", (ev) => {
    const b = ev.target.closest('[role="tab"]');
    if (b) allerA(b.dataset.onglet);
  });
  nav.addEventListener("keydown", clavierOnglets);
  window.addEventListener("hashchange", () => allerA(window.location.hash.slice(1)));
  window.addEventListener("beforeunload", (ev) => {
    if (reglages.estModifie() || profils.estModifie()) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });
  surReconnexion(() => emettre("reconnexion"));

  let memo = null;
  try {
    memo = sessionStorage.getItem(CLE_PROJET);
  } catch {
    memo = null;
  }
  if (memo) {
    try {
      const liste = await api.get("/api/projets");
      if (liste.some((p) => p.nom === memo)) ouvrirProjet(memo);
    } catch {
      /* bandeau déjà affiché par api.js */
    }
  }
  allerA(window.location.hash.slice(1) || (etat.projet ? "reglages" : "projets"));
}

demarrer();
