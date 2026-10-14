// Générer : enregistre les réglages modifiés, lance la génération complète, suit la progression (SSE).

import { api, ErreurApi, seg, surReconnexion, urlAvecJeton } from "../api.js";
import { duree, el, message, vider } from "../dom.js";
import { carteEssai } from "../essai.js";
import { enregistrerSiModifie } from "./reglages.js";

const FINALS = new Set(["fin", "erreur", "annule"]);
const RECONNEXION_MS = 1500;

let ctx = null;
const ui = {};
const suivi = {
  job: null, // {id, projet, essai}
  source: null,
  fini: true,
  debut: 0,
  minuteur: null,
  attenteReseau: false, // flux coupé par une perte de réseau : reprise à la reconnexion
};

function fermerFlux() {
  if (suivi.source) {
    suivi.source.close();
    suivi.source = null;
  }
}

function enCours(actif) {
  ui.lancer.disabled = actif || !ctx.etat.projet;
  ui.annuler.hidden = !actif;
  ui.barre.hidden = !actif;
  clearInterval(suivi.minuteur);
  if (actif) {
    suivi.minuteur = setInterval(() => {
      ui.chrono.textContent = `écoulé : ${duree((Date.now() - suivi.debut) / 1000)}`;
    }, 1000);
  }
}

function journal(texte) {
  ui.journal.append(`${texte}\n`);
  ui.journal.scrollTop = ui.journal.scrollHeight;
}

function ecouter(job) {
  fermerFlux();
  suivi.job = job;
  suivi.fini = false;
  vider(ui.journal);
  const es = new EventSource(urlAvecJeton(`/api/generations/${seg(job.id)}/flux`));
  suivi.source = es;
  es.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.type === "progression") {
      journal(msg.message);
      return;
    }
    if (FINALS.has(msg.type)) {
      // premier message final : on ferme, sinon le navigateur se reconnecte et rejoue tout le flux
      es.close();
      if (suivi.source === es) suivi.source = null;
      if (msg.message) journal(msg.type === "annule" ? `■ ${msg.message}` : `✘ ${msg.message}`);
      terminer(job);
    }
  };
  es.onerror = () => {
    if (suivi.source !== es || suivi.fini) return;
    // flux coupé avant le message final : nouvelle connexion propre (le serveur rejoue tout le journal)
    es.close();
    suivi.source = null;
    setTimeout(() => reprendre(job), RECONNEXION_MS);
  };
}

/**
 * Flux coupé avant le message final : relit le statut, une seule fois.
 * Réseau coupé → on attend la reconnexion signalée par api.js (bandeau + /api/sante), sans minuteur propre ;
 * toute autre réponse HTTP (404 serveur relancé, 401 jeton périmé…) arrête le suivi.
 */
async function reprendre(job) {
  if (suivi.fini || suivi.job !== job) return;
  suivi.attenteReseau = false;
  try {
    const st = await api.get(`/api/generations/${seg(job.id)}`);
    if (suivi.fini || suivi.job !== job) return;
    if (st.statut !== "en cours") terminer(job);
    else ecouter(job);
  } catch (e) {
    if (suivi.fini || suivi.job !== job) return;
    if (e instanceof ErreurApi && e.statut === 0) {
      suivi.attenteReseau = true; // repris par surReconnexion
      ui.etat.textContent = "Connexion perdue : suivi repris au retour du serveur.";
    } else if (e instanceof ErreurApi && e.statut === 404) {
      terminer(job); // serveur relancé : la génération n'est plus suivie, voir l'historique
    } else abandonner(e.message);
  }
}

function abandonner(raison) {
  suivi.fini = true;
  suivi.attenteReseau = false;
  fermerFlux();
  enCours(false);
  ui.etat.textContent = "";
  ui.erreur.append(message(`Suivi de la génération interrompu : ${raison}. Consultez l'onglet Historique.`));
}

async function terminer(job) {
  if (suivi.fini && suivi.job === job) return;
  suivi.fini = true;
  suivi.attenteReseau = false;
  fermerFlux();
  enCours(false);
  ui.etat.textContent = `Essai ${job.essai} terminé.`;
  ctx.emettre("generation-finie", job);
  let detail;
  try {
    detail = await api.get(`/api/projets/${seg(job.projet)}/essais/${seg(job.essai)}`);
  } catch (e) {
    vider(ui.resume).append(message(`Résumé indisponible : ${e.message}`));
    return;
  }
  afficherResume(job.projet, job.essai, detail.resume);
}

function afficherResume(projet, essai, r) {
  vider(ui.resume).append(carteEssai(projet, essai, r));
}

async function lancer() {
  const nom = ctx.etat.projet;
  if (!nom) return;
  vider(ui.erreur);
  vider(ui.resume);
  ui.lancer.disabled = true;
  ui.etat.textContent = "Enregistrement des réglages…";
  const ok = await enregistrerSiModifie();
  if (!ok) {
    ui.etat.textContent = "";
    ui.erreur.append(message("Réglages invalides ou non enregistrés : corrigez-les dans l'onglet Réglages."));
    ui.lancer.disabled = false;
    return;
  }
  ui.etat.textContent = "Démarrage…";
  let job;
  try {
    job = await api.post(`/api/projets/${seg(nom)}/generations`);
  } catch (e) {
    ui.etat.textContent = "";
    ui.erreur.append(message(e.message));
    ui.lancer.disabled = false;
    return;
  }
  ui.etat.textContent = `Génération en cours — essai ${job.essai}`;
  suivi.debut = Date.now();
  enCours(true);
  ecouter(job);
}

async function annuler() {
  if (!suivi.job || suivi.fini) return;
  ui.annuler.disabled = true;
  try {
    await api.post(`/api/generations/${seg(suivi.job.id)}/annuler`);
  } catch (e) {
    ui.erreur.append(message(e.message));
  } finally {
    ui.annuler.disabled = false;
  }
}

/** Rattache l'onglet à une génération déjà en cours (rechargement de la page). */
async function rattacher(nom) {
  if (!nom || (suivi.job && !suivi.fini)) return;
  try {
    const essais = await api.get(`/api/projets/${seg(nom)}/essais`);
    const vivant = essais.find((e) => e.statut === "en cours" && e.id);
    if (vivant && nom === ctx.etat.projet && (!suivi.job || suivi.fini)) {
      ui.etat.textContent = `Génération en cours — essai ${vivant.essai}`;
      suivi.debut = Date.now();
      enCours(true);
      ecouter({ id: vivant.id, projet: nom, essai: vivant.essai });
    }
  } catch {
    /* rien à rattacher */
  }
}

export function init(panneau, contexte) {
  ctx = contexte;
  surReconnexion(() => {
    if (suivi.attenteReseau && suivi.job && !suivi.fini) reprendre(suivi.job);
  });
  ui.lancer = el("button", { type: "button", classe: "principal", texte: "Générer", onclick: lancer, disabled: true });
  ui.annuler = el("button", { type: "button", classe: "danger", texte: "Annuler la génération", onclick: annuler, hidden: true });
  ui.etat = el("span", { classe: "etat-apercu", role: "status", "aria-live": "polite" });
  ui.chrono = el("span", { classe: "aide" });
  ui.barre = el("div", { classe: "progression", hidden: true, role: "progressbar", "aria-label": "Génération en cours" }, [el("div")]);
  ui.erreur = el("div");
  ui.journal = el("pre", { classe: "journal", "aria-label": "Journal de la génération", tabindex: 0 });
  ui.resume = el("div");
  ui.vide = el("div");
  ui.contenu = el("div", {}, [
    el("div", { classe: "carte" }, [
      el("h2", { texte: "Génération complète" }),
      el("p", { classe: "aide", texte:
        "Comme « beaver run --3mf » : STEP, STL et projet Bambu (3MF) dans un nouvel essai du projet. " +
        "Les réglages modifiés sont enregistrés d'abord." }),
      el("div", { classe: "actions" }, [ui.lancer, ui.annuler, ui.etat, ui.chrono]),
      ui.barre,
      ui.erreur,
      ui.journal,
    ]),
    ui.resume,
  ]);
  panneau.append(ui.vide, ui.contenu);
  ctx.sur("projet", (nom) => {
    if (suivi.job && !suivi.fini && suivi.job.projet !== nom) {
      // la génération continue côté serveur ; on cesse seulement de la suivre ici
      fermerFlux();
      suivi.fini = true;
      enCours(false);
    }
    ui.contenu.hidden = !nom;
    if (nom) vider(ui.vide);
    else ctx.sansProjet(ui.vide);
    ui.lancer.disabled = !nom || Boolean(suivi.job && !suivi.fini);
    vider(ui.resume);
    vider(ui.erreur);
    ui.etat.textContent = "";
    ui.chrono.textContent = "";
    vider(ui.journal);
    rattacher(nom);
  });
  ui.contenu.hidden = true;
  ctx.sansProjet(ui.vide);
}

export function afficher() {
  rattacher(ctx.etat.projet);
}
