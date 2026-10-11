// Générer : enregistre les réglages modifiés, lance la génération complète, suit la progression (SSE).

import { api, ErreurApi, seg, urlAvecJeton } from "../api.js";
import { badgeStatut, el, duree, message, nombre, vider } from "../dom.js";
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

async function reprendre(job) {
  if (suivi.fini || suivi.job !== job) return;
  try {
    const st = await api.get(`/api/generations/${seg(job.id)}`);
    if (st.statut !== "en cours") terminer(job);
    else ecouter(job);
  } catch (e) {
    if (e instanceof ErreurApi && e.statut === 404) terminer(job); // serveur relancé : voir l'historique
    else setTimeout(() => reprendre(job), RECONNEXION_MS);
  }
}

async function terminer(job) {
  if (suivi.fini && suivi.job === job) return;
  suivi.fini = true;
  fermerFlux();
  enCours(false);
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

/** Liens et boutons d'un essai terminé (partagés avec l'historique). */
export function actionsEssai(projet, essai, resume, surErreur) {
  const base = `/api/projets/${seg(projet)}/essais/${seg(essai)}`;
  const fichiers = resume.fichiers || [];
  const lien = (nom, texte) => el("a", {
    classe: "bouton petit", href: urlAvecJeton(`${base}/fichiers/${seg(nom)}`), target: "_blank", rel: "noopener", texte,
  });
  const out = [];
  if (resume.rapport && fichiers.includes(resume.rapport)) out.push(lien(resume.rapport, "Rapport"));
  for (const f of fichiers.filter((x) => /\.stl$/i.test(x))) out.push(lien(f, `STL (${f})`));
  for (const f of fichiers.filter((x) => /\.3mf$/i.test(x))) out.push(lien(f, `3MF (${f})`));
  for (const f of fichiers.filter((x) => /\.(step|stp)$/i.test(x))) out.push(lien(f, `STEP (${f})`));
  const action = (texte, route, principal = false) => el("button", {
    type: "button", classe: `petit${principal ? " principal" : ""}`, texte,
    onclick: async (ev) => {
      const b = ev.currentTarget;
      b.disabled = true;
      try {
        await api.post(`${base}/${route}`);
      } catch (e) {
        surErreur(e.message);
      } finally {
        b.disabled = false;
      }
    },
  });
  if (fichiers.some((x) => /\.3mf$/i.test(x))) out.push(action("Ouvrir dans Bambu", "ouvrir-bambu", true));
  out.push(action("Ouvrir le dossier", "ouvrir-dossier"));
  return out;
}

function afficherResume(projet, essai, r) {
  vider(ui.resume);
  const erreurs = el("div");
  const carte = el("div", { classe: "carte" }, [
    el("h3", {}, [`Essai ${essai} `, badgeStatut(r.statut)]),
  ]);
  if (r.message) carte.append(el("p", { texte: r.message }));
  if (r.nervures !== undefined) {
    carte.append(el("div", { classe: "chiffres" }, [
      ["nervures (plaques)", r.nervures], ["tronçons", r.troncons], ["masse", r.masse !== undefined ? `${nombre(r.masse, 1)} g` : "—"],
      ["trajet", r.trajet !== undefined ? `${nombre(r.trajet, 1)} m` : "—"], ["durée", duree(r.duree)],
      ["orientation", r.orientation || "—"],
    ].map(([e, v]) => el("div", { classe: "chiffre" }, [
      el("div", { classe: "nombre-grand", texte: String(v ?? "—") }), el("div", { classe: "etiquette", texte: e }),
    ]))));
  } else if (r.duree !== undefined) carte.append(el("p", { texte: `Durée : ${duree(r.duree)}` }));
  if (r.erreurs && r.erreurs.length) {
    carte.append(el("div", { classe: "message erreur" }, [
      el("strong", { texte: r.statut === "non imprimable" ? "Non imprimable en vase mode" : "Erreurs" }),
      el("ul", { classe: "liste-messages" }, r.erreurs.map((x) => el("li", { texte: x }))),
    ]));
  }
  if (r.avertissements && r.avertissements.length) {
    carte.append(el("div", { classe: "message attention" }, [
      el("strong", { texte: "Avertissements" }),
      el("ul", { classe: "liste-messages" }, r.avertissements.map((x) => el("li", { texte: x }))),
    ]));
  }
  if (r.corps && r.corps.length) {
    carte.append(el("table", {}, [
      el("thead", {}, [el("tr", {}, ["Corps", "Imprimable", "Nervures", "Tronçons", "Couches", "Hauteur", "Masse", "Trajet", "Trous"]
        .map((t) => el("th", { texte: t })))]),
      el("tbody", {}, r.corps.map((c) => el("tr", {}, [
        el("td", { texte: c.nom }), el("td", { texte: c.ok ? "oui" : "non" }),
        el("td", { classe: "nombre", texte: String(c.nervures) }), el("td", { classe: "nombre", texte: String(c.troncons) }),
        el("td", { classe: "nombre", texte: String(c.couches) }), el("td", { classe: "nombre", texte: `${nombre(c.hauteur, 1)} mm` }),
        el("td", { classe: "nombre", texte: `${nombre(c.masse, 1)} g` }), el("td", { classe: "nombre", texte: `${nombre(c.trajet, 1)} m` }),
        el("td", { texte: c.trous || "" }),
      ]))),
    ]));
  }
  carte.append(el("div", { classe: "actions liens" }, actionsEssai(projet, essai, r, (m) => vider(erreurs).append(message(m)))), erreurs);
  ui.resume.append(carte);
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
        "Comme « aerorib run --3mf » : STEP, STL et projet Bambu (3MF) dans un nouvel essai du projet. " +
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
