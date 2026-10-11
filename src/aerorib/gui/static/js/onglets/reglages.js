// Réglages du projet : formulaire (schéma + configuration effective), validation par l'aperçu, enregistrement.

import { api, ErreurApi, seg } from "../api.js";
import { debounce, el, message, vider } from "../dom.js";
import { cleChemin, erreursParChamp, estPose, Formulaire, lire, Schema } from "../formulaire.js";

const DEBOUNCE_MS = 400;
// sorties forcées par la génération (dossier de l'essai, STEP + STL + 3MF, pas d'ouverture automatique)
const MASQUES = new Set(["output", "bambu.open"]);

let ctx = null;
let panneau = null;
const ui = {};
let formulaire = null;
let schemaCache = null;
const profils = new Map(); // nom de matière → réponse GET /api/profils/{nom} (ou null si illisible)
const corpsParProjet = new Map();
const etat = {
  projet: null,
  effective: null, // {valeurs, provenance}
  provenance: new Map(),
  matiereEnregistree: null,
  texte: "",
  modifie: false,
  chargement: 0,
};

export function estModifie() {
  return etat.modifie;
}

export function emplacementApercu() {
  return ui.colonneApercu;
}

async function schema() {
  if (!schemaCache) schemaCache = await api.get("/api/schema");
  return schemaCache;
}

async function profil(nom) {
  if (!nom) return null;
  if (!profils.has(nom)) {
    try {
      profils.set(nom, await api.get(`/api/profils/${seg(nom)}`));
    } catch {
      profils.set(nom, null);
    }
  }
  return profils.get(nom);
}

function matiereCourante() {
  const m = formulaire ? lire(formulaire.modele, ["material"]) : undefined;
  return typeof m === "string" ? m : etat.matiereEnregistree;
}

/** Valeur héritée (profil puis défaut) d'un champ non posé dans le projet. */
function herite(chemin) {
  const mat = matiereCourante();
  const p = profils.get(mat);
  if (p && estPose(p.valeurs, chemin)) {
    const v = lire(p.valeurs, chemin);
    if (v !== undefined) return { valeur: v, origine: "profil" };
  }
  const eff = etat.effective ? etat.effective.valeurs : {};
  const o = etat.provenance.get(cleChemin(chemin));
  if (mat === etat.matiereEnregistree && o && o !== "projet") return { valeur: lire(eff, chemin), origine: o };
  const d = formulaire.schema.defaut(chemin);
  if (d !== undefined) return { valeur: d, origine: "défaut" };
  return { valeur: lire(eff, chemin), origine: "défaut" };
}

function marquer(modifie) {
  etat.modifie = modifie;
  ui.sale.hidden = !modifie;
  ui.enregistrer.disabled = !modifie;
  ui.annuler.disabled = !modifie;
}

function gabarit(valeur) {
  const p = profils.get(matiereCourante());
  const g = p && p.gabarit;
  if (g && g.chemin_nom) {
    return el("span", { classe: "lecture-seule" }, [
      el("code", { texte: g.chemin_nom }),
      g.existe ? " (présent, fourni par le profil)" : " — fichier absent : le 3MF ne sera pas produit",
    ]);
  }
  if (valeur) return el("span", { classe: "lecture-seule", texte: "gabarit défini dans config.yaml (voir le texte)" });
  return el("span", { classe: "lecture-seule", texte: "aucun : le 3MF ne sera pas produit" });
}

const proposer = debounce(() => {
  if (formulaire.aDesErreursLocales()) return;
  ctx.emettre("config-proposee", formulaire.donnees());
}, DEBOUNCE_MS);

async function changement() {
  marquer(true);
  const mat = matiereCourante();
  if (!profils.has(mat)) {
    await profil(mat);
    formulaire.rendre();
  }
  proposer();
}

async function charger() {
  const nom = etat.projet;
  const n = ++etat.chargement;
  vider(ui.erreur);
  if (!nom) return;
  ui.etat.textContent = "Chargement des réglages…";
  try {
    const [sch, cfg, eff] = await Promise.all([
      schema(),
      api.get(`/api/projets/${seg(nom)}/config`),
      api.get(`/api/projets/${seg(nom)}/config/effective`),
    ]);
    if (n !== etat.chargement) return;
    if (!formulaire) {
      formulaire = new Formulaire({
        schema: new Schema(sch.schema), mode: "projet", masques: MASQUES,
        speciaux: { "bambu.template": gabarit }, surChangement: changement,
      });
      ui.zoneFormulaire.append(formulaire.element);
    }
    etat.effective = eff;
    etat.provenance = new Map(eff.provenance.map((p) => [cleChemin(p.chemin), p.origine]));
    etat.matiereEnregistree = eff.valeurs.material;
    etat.texte = cfg.texte;
    ui.yaml.value = cfg.texte;
    profils.clear();
    await profil(etat.matiereEnregistree);
    if (n !== etat.chargement) return;
    if (cfg.donnees === null || typeof cfg.donnees !== "object" || Array.isArray(cfg.donnees)) {
      ui.erreur.append(message("config.yaml n'est pas un dictionnaire YAML lisible : corrigez-le dans l'éditeur de texte ci-dessous."));
    }
    const projet = cfg.donnees && typeof cfg.donnees === "object" && !Array.isArray(cfg.donnees) ? cfg.donnees : {};
    formulaire.chargerProjet({ projet, herite, choix: { material: sch.matieres, corps: corpsParProjet.get(nom) || [] } });
    formulaire.effacerErreurs();
    marquer(false);
    ui.etat.textContent = "";
    ctx.emettre("config-proposee", formulaire.donnees());
    chargerCorps(nom);
  } catch (e) {
    if (n !== etat.chargement) return;
    ui.etat.textContent = "";
    ui.erreur.append(message(`Réglages illisibles : ${e.message}`));
  }
}

async function chargerCorps(nom) {
  if (corpsParProjet.has(nom)) return;
  try {
    const r = await api.get(`/api/projets/${seg(nom)}/corps`);
    const noms = r.corps.map((c) => String(c.nom).split("/").pop());
    corpsParProjet.set(nom, noms);
    if (nom === etat.projet && formulaire && !formulaire.element.contains(document.activeElement)) {
      formulaire.choix.corps = noms;
      formulaire.rendre();
    }
  } catch {
    /* noms des corps facultatifs (suggestions des rôles) */
  }
}

/** Enregistre la configuration du formulaire ; renvoie true si c'est fait (ou rien à faire). */
export async function enregistrerSiModifie() {
  if (!etat.modifie) return true;
  return enregistrer();
}

async function enregistrer() {
  if (!formulaire || !etat.projet) return false;
  if (formulaire.aDesErreursLocales()) {
    vider(ui.erreur).append(message("Corrigez les champs en erreur avant d'enregistrer."));
    return false;
  }
  ui.enregistrer.disabled = true;
  vider(ui.erreur);
  try {
    await api.put(`/api/projets/${seg(etat.projet)}/config`, { donnees: formulaire.donnees() });
  } catch (e) {
    ui.enregistrer.disabled = false;
    if (e instanceof ErreurApi && e.statut === 422) {
      const { parChamp, general } = erreursParChamp(e.message);
      formulaire.afficherErreurs(parChamp, general);
      ui.erreur.append(message("Enregistrement refusé : configuration invalide (voir les champs en rouge)."));
    } else {
      ui.erreur.append(message(`Enregistrement impossible : ${e.message}`));
    }
    return false;
  }
  await charger();
  ui.etat.textContent = "Réglages enregistrés dans config.yaml.";
  return true;
}

async function enregistrerTexte() {
  vider(ui.erreurYaml);
  if (etat.modifie && !window.confirm("Les modifications du formulaire seront perdues. Continuer ?")) return;
  try {
    await api.put(`/api/projets/${seg(etat.projet)}/config`, { texte: ui.yaml.value });
  } catch (e) {
    ui.erreurYaml.append(message(e.message));
    return;
  }
  await charger();
  ui.etat.textContent = "config.yaml enregistré.";
}

function construire() {
  ui.etat = el("div", { classe: "etat-apercu", role: "status", "aria-live": "polite" });
  ui.erreur = el("div");
  ui.zoneFormulaire = el("div");
  ui.sale = el("span", { classe: "indicateur-sale", texte: "● modifications non enregistrées", hidden: true });
  ui.enregistrer = el("button", { type: "button", classe: "principal", texte: "Enregistrer", disabled: true, onclick: () => enregistrer() });
  ui.annuler = el("button", {
    type: "button", texte: "Annuler les modifications", disabled: true,
    onclick: () => {
      if (window.confirm("Revenir aux réglages enregistrés ?")) charger();
    },
  });
  ui.yaml = el("textarea", { rows: 14, spellcheck: false, "aria-label": "Texte de config.yaml" });
  ui.erreurYaml = el("div");
  ui.colonneApercu = el("div", { classe: "colonne-apercu" });
  ui.colonneForm = el("div", { classe: "colonne-reglages" }, [
    el("div", { classe: "carte" }, [
      el("h2", { texte: "Réglages du projet" }),
      el("p", { classe: "aide", texte:
        "Valeurs grisées : héritées du profil matière ou du défaut. « surcharger » les fixe dans le projet ; " +
        "« revenir à l'hérité » les retire de config.yaml. L'aperçu valide chaque modification." }),
      ui.etat,
      ui.erreur,
      ui.zoneFormulaire,
      el("div", { classe: "barre-enregistrer" }, [ui.enregistrer, ui.annuler, ui.sale]),
    ]),
    el("details", { classe: "carte" }, [
      el("summary", { texte: "Avancé : texte de config.yaml" }),
      el("p", { classe: "aide", texte: "Pour les réglages absents du formulaire (axe d'orientation, réglages par longeron…). Validé avant écriture." }),
      ui.yaml,
      ui.erreurYaml,
      el("div", { classe: "actions" }, [el("button", { type: "button", texte: "Enregistrer le texte", onclick: enregistrerTexte })]),
    ]),
  ]);
  return el("div", { classe: "disposition-reglages" }, [ui.colonneForm, ui.colonneApercu]);
}

export function init(p, contexte) {
  ctx = contexte;
  panneau = p;
  ui.disposition = construire();
  ui.vide = el("div");
  panneau.append(ui.vide, ui.disposition);
  ctx.sur("projet", (nom) => {
    etat.projet = nom;
    proposer.annuler();
    marquer(false);
    ui.disposition.hidden = !nom;
    if (nom) {
      vider(ui.vide);
      charger();
    } else ctx.sansProjet(ui.vide);
  });
  ctx.sur("validation", (r) => {
    if (!formulaire) return;
    if (r.ok) formulaire.afficherErreurs(new Map(), []);
    else {
      const { parChamp, general } = erreursParChamp(r.erreur);
      formulaire.afficherErreurs(parChamp, general);
    }
  });
  ctx.sur("profil-enregistre", async (nomProfil) => {
    if (!etat.projet) return;
    if (!etat.modifie) {
      charger();
      return;
    }
    profils.delete(nomProfil); // valeurs héritées changées : on les relit sans perdre les modifications
    await profil(nomProfil);
    formulaire?.rendre();
  });
  if (!etat.projet) {
    ui.disposition.hidden = true;
    ctx.sansProjet(ui.vide);
  }
}

export function afficher() {
  if (etat.projet && formulaire && !etat.modifie && etat.effective === null) charger();
}

