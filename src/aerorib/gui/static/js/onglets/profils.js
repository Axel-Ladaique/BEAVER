// Profils matière (PLA Aero, ASA Aero, copies) : liste, édition (même formulaire que les réglages), copie.

import { api, ErreurApi, seg } from "../api.js";
import { el, message, vider } from "../dom.js";
import { erreursParChamp, Formulaire, Schema } from "../formulaire.js";

const NOM_RE = /^[a-z0-9_]{1,40}$/;

let ctx = null;
const ui = {};
let formulaire = null;
let courant = null; // réponse GET /api/profils/{nom}
let modifie = false;

function gabarit() {
  const g = courant && courant.gabarit;
  if (!g || !g.chemin_nom) return el("span", { classe: "lecture-seule", texte: "aucun gabarit : le 3MF ne sera pas produit" });
  return el("span", { classe: "lecture-seule" }, [
    el("code", { texte: g.chemin_nom }),
    g.existe ? " — présent" : " — fichier introuvable",
    el("span", { classe: "aide", texte: " (lecture seule : à changer dans le fichier YAML du profil)" }),
  ]);
}

function marquer(m) {
  modifie = m;
  ui.enregistrer.disabled = !m;
  ui.sale.hidden = !m;
}

async function lister() {
  vider(ui.erreurListe);
  let liste;
  try {
    liste = await api.get("/api/profils");
  } catch (e) {
    ui.erreurListe.append(message(e.message));
    return;
  }
  vider(ui.liste);
  for (const p of liste) {
    const actif = courant && courant.nom === p.nom;
    ui.liste.append(el("li", {}, [el("button", {
      type: "button", "aria-current": actif ? "true" : null,
      onclick: () => ouvrir(p.nom),
    }, [
      el("span", {}, [el("strong", { texte: p.nom })]),
      el("span", { classe: "aide", texte: p.erreur ? "illisible" : `${p.origine}${p.existe ? "" : " · sans gabarit"}` }),
    ])]));
    if (p.erreur) ui.liste.append(el("li", {}, [message(p.erreur)]));
  }
}

async function ouvrir(nom) {
  if (modifie && courant && courant.nom !== nom &&
      !window.confirm("Les modifications du profil ne sont pas enregistrées. Changer de profil ?")) return;
  vider(ui.erreur);
  vider(ui.etat);
  let p;
  try {
    p = await api.get(`/api/profils/${seg(nom)}`);
  } catch (e) {
    ui.erreur.append(message(e.message));
    return;
  }
  afficherProfil(p);
  lister();
}

async function afficherProfil(p) {
  courant = p;
  if (!formulaire) {
    const sch = await api.get("/api/schema");
    formulaire = new Formulaire({
      schema: new Schema(sch.schema), mode: "profil", speciaux: { "bambu.template": gabarit },
      surChangement: () => marquer(true),
    });
    ui.zoneFormulaire.append(formulaire.element);
  }
  ui.editeur.hidden = false;
  ui.titre.textContent = `Profil « ${p.nom} »`;
  ui.origine.textContent = p.origine === "embarqué"
    ? "Profil embarqué : l'enregistrement crée ./profiles/" + p.nom + ".yaml avec les seules valeurs modifiées."
    : `Profil ${p.origine} : seules les valeurs différentes du profil embarqué sont écrites.`;
  ui.copier.hidden = !(p.origine === "embarqué");
  formulaire.chargerProfil({ valeurs: p.valeurs, provenance: p.provenance });
  formulaire.effacerErreurs();
  marquer(false);
}

async function enregistrer() {
  if (!courant || !formulaire) return;
  vider(ui.erreur);
  if (formulaire.aDesErreursLocales()) {
    ui.erreur.append(message("Corrigez les champs en erreur avant d'enregistrer."));
    return;
  }
  ui.enregistrer.disabled = true;
  try {
    const p = await api.put(`/api/profils/${seg(courant.nom)}`, { donnees: formulaire.donnees() });
    await afficherProfil(p);
    ui.etat.textContent = `Profil « ${p.nom} » enregistré.`;
    ctx.emettre("profil-enregistre", p.nom);
    lister();
  } catch (e) {
    ui.enregistrer.disabled = false;
    if (e instanceof ErreurApi && e.statut === 422) {
      const { parChamp, general } = erreursParChamp(e.message);
      formulaire.afficherErreurs(parChamp, general);
    } else ui.erreur.append(message(e.message));
  }
}

async function copier(vers) {
  if (!courant) return;
  vider(ui.erreur);
  vider(ui.etat);
  if (vers !== undefined && !NOM_RE.test(vers)) {
    ui.erreur.append(message("Nom de profil : 1 à 40 caractères parmi minuscules sans accent, chiffres et « _ »."));
    return;
  }
  try {
    const p = await api.post(`/api/profils/${seg(courant.nom)}/copier`, vers === undefined ? undefined : { vers });
    await afficherProfil(p);
    ui.etat.textContent = vers === undefined
      ? `Copie utilisateur créée : ./profiles/${p.nom}.yaml`
      : `Nouveau profil « ${p.nom} » créé.`;
    ui.nouveauNom.value = "";
    lister();
  } catch (e) {
    ui.erreur.append(message(e.message));
  }
}

export function init(panneau, contexte) {
  ctx = contexte;
  ui.liste = el("ul", { classe: "liste-choix" });
  ui.erreurListe = el("div");
  ui.titre = el("h2");
  ui.origine = el("p", { classe: "aide" });
  ui.etat = el("div", { classe: "etat-apercu", role: "status", "aria-live": "polite" });
  ui.erreur = el("div");
  ui.zoneFormulaire = el("div");
  ui.sale = el("span", { classe: "indicateur-sale", texte: "● modifications non enregistrées", hidden: true });
  ui.enregistrer = el("button", { type: "button", classe: "principal", texte: "Enregistrer le profil", disabled: true, onclick: enregistrer });
  ui.annuler = el("button", { type: "button", texte: "Annuler les modifications", onclick: () => courant && ouvrir(courant.nom) });
  ui.copier = el("button", { type: "button", texte: "Créer la copie utilisateur (./profiles)", onclick: () => copier(undefined) });
  ui.nouveauNom = el("input", { id: "profil-nouveau-nom", type: "text", placeholder: "ex. pla_aero_leger", maxlength: 40 });
  ui.editeur = el("div", { classe: "carte", hidden: true }, [
    ui.titre,
    ui.origine,
    ui.etat,
    ui.erreur,
    ui.zoneFormulaire,
    el("div", { classe: "barre-enregistrer" }, [ui.enregistrer, ui.annuler, ui.sale]),
    el("h3", { texte: "Copier" }),
    el("div", { classe: "actions" }, [ui.copier]),
    el("div", { classe: "form-ligne" }, [
      el("label", { for: "profil-nouveau-nom", texte: "Dupliquer sous le nom" }),
      ui.nouveauNom,
      el("button", { type: "button", texte: "Dupliquer", onclick: () => copier(ui.nouveauNom.value.trim()) }),
    ]),
  ]);
  panneau.append(el("div", { classe: "deux-colonnes" }, [
    el("div", { classe: "carte" }, [
      el("h2", { texte: "Profils matière" }),
      el("p", { classe: "aide", texte: "Embarqués (PLA Aero, ASA Aero) et copies utilisateur dans ./profiles." }),
      ui.erreurListe,
      ui.liste,
    ]),
    ui.editeur,
  ]));
  ctx.sur("reconnexion", lister);
}

export function afficher() {
  lister();
}
