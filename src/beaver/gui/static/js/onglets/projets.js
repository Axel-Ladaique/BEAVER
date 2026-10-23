// Projets : liste, création (import STEP), ouverture, carte des corps et de leurs rôles.

import { api, seg } from "../api.js";
import { dateFr, el, message, nombre, vider } from "../dom.js";

const ROLES = {
  skin: "peau", spar: "longeron", split: "séparation", norib: "sans nervure", cut: "découpe", ignore: "ignoré",
};

let ctx = null;
const ui = {};
let seqListe = 0; // listes demandées en rafale (démarrage, ouverture, onglet) : seule la dernière réponse compte

async function lister() {
  const s = ++seqListe;
  vider(ui.erreurListe);
  let liste;
  try {
    liste = await api.get("/api/projets");
  } catch (e) {
    if (s !== seqListe) return;
    ui.erreurListe.append(message(`Liste des projets indisponible : ${e.message}`));
    return;
  }
  if (s !== seqListe) return;
  vider(ui.liste);
  if (!liste.length) {
    ui.liste.append(el("p", { classe: "vide", texte: "Aucun projet : importez un fichier STEP pour commencer." }));
    return;
  }
  const corps = el("tbody");
  for (const p of liste) {
    const courant = p.nom === ctx.etat.projet;
    corps.append(el("tr", { classe: courant ? "selection" : null }, [
      el("td", {}, [el("strong", { texte: p.nom })]),
      el("td", { texte: dateFr(p.cree) }),
      el("td", { classe: "nombre", texte: String(p.nb_essais) }),
      el("td", {}, [
        el("button", {
          type: "button", classe: courant ? "petit" : "petit principal", texte: courant ? "Ouvert" : "Ouvrir",
          "aria-label": `Ouvrir le projet ${p.nom}`, disabled: courant,
          onclick: () => ctx.ouvrirProjet(p.nom),
        }),
      ]),
    ]));
  }
  ui.liste.append(el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      el("th", { texte: "Projet" }), el("th", { texte: "Créé le" }), el("th", { classe: "nombre", texte: "Essais" }), el("th", { texte: "" }),
    ])]),
    corps,
  ]));
}

async function creer(ev) {
  ev.preventDefault();
  vider(ui.erreurCreation);
  const nom = ui.nom.value.trim();
  const fichier = ui.fichier.files[0];
  if (!nom) {
    ui.erreurCreation.append(message("Donnez un nom au projet."));
    ui.nom.focus();
    return;
  }
  if (!fichier) {
    ui.erreurCreation.append(message("Choisissez un fichier STEP (.step ou .stp)."));
    ui.fichier.focus();
    return;
  }
  const fd = new FormData();
  fd.append("nom", nom);
  fd.append("fichier", fichier, fichier.name);
  ui.creer.disabled = true;
  ui.etatCreation.textContent = `Import de ${fichier.name} (${nombre(fichier.size / 1048576, 1)} Mo) et lecture du STEP…`;
  try {
    const p = await api.post("/api/projets", fd);
    ui.nom.value = "";
    ui.fichier.value = "";
    ui.etatCreation.textContent = `Projet « ${p.nom} » créé.`;
    await lister();
    ctx.ouvrirProjet(p.nom);
  } catch (e) {
    ui.etatCreation.textContent = "";
    ui.erreurCreation.append(message(e.message));
  } finally {
    ui.creer.disabled = false;
  }
}

async function montrerCorps(nom) {
  vider(ui.corps);
  if (!nom) {
    ui.carteCorps.hidden = true;
    return;
  }
  ui.carteCorps.hidden = false;
  ui.titreCorps.textContent = `Corps du projet « ${nom} »`;
  ui.corps.append(el("p", { classe: "etat-apercu", texte: "Lecture du STEP et classement des corps…" }));
  let r;
  try {
    r = await api.get(`/api/projets/${seg(nom)}/corps`);
  } catch (e) {
    if (nom !== ctx.etat.projet) return;
    vider(ui.corps).append(message(e.message));
    return;
  }
  if (nom !== ctx.etat.projet) return;
  vider(ui.corps);
  const lignes = r.corps.map((c) => el("tr", {}, [
    el("td", {}, [el("code", { texte: c.nom })]),
    el("td", { texte: ROLES[c.role] || c.role }),
    el("td", { classe: "aide", texte: c.regle || "" }),
    el("td", { texte: c.longeron ? `Ø ${nombre(c.longeron.diametre, 2)} mm, longueur ${nombre(c.longeron.longueur, 1)} mm` : "" }),
    el("td", {}, c.note ? [el("span", { classe: "statut non-imprimable", texte: "⚠" }), " ", c.note] : []),
  ]));
  ui.corps.append(el("table", {}, [
    el("thead", {}, [el("tr", {}, ["Corps", "Rôle", "Règle", "Longeron", "Remarque"].map((t) => el("th", { texte: t })))]),
    el("tbody", {}, lignes),
  ]));
  const peaux = r.corps.filter((c) => c.role === "skin").length;
  if (!peaux) ui.corps.append(message("Aucun corps « peau » reconnu : imposez un rôle dans Réglages → Rôles imposés.", "attention"));
  ui.corps.append(el("div", { classe: "actions" }, [
    el("button", { type: "button", classe: "principal", texte: "Régler et prévisualiser →", onclick: () => ctx.allerA("reglages") }),
  ]));
}

export function init(panneau, contexte) {
  ctx = contexte;
  ui.liste = el("div");
  ui.erreurListe = el("div");
  ui.nom = el("input", { id: "nouveau-nom", type: "text", maxlength: 64, autocomplete: "off", placeholder: "ex. aile-gauche" });
  ui.fichier = el("input", { id: "nouveau-fichier", type: "file", accept: ".step,.stp,.STEP,.STP" });
  ui.creer = el("button", { type: "submit", classe: "principal", texte: "Créer le projet" });
  ui.etatCreation = el("div", { classe: "etat-apercu", role: "status", "aria-live": "polite" });
  ui.erreurCreation = el("div");
  ui.titreCorps = el("h2");
  ui.corps = el("div");
  ui.carteCorps = el("div", { classe: "carte", hidden: true }, [ui.titreCorps, ui.corps]);
  panneau.append(
    el("div", { classe: "deux-colonnes" }, [
      el("div", {}, [
        el("form", { classe: "carte", onsubmit: creer }, [
          el("h2", { texte: "Nouveau projet" }),
          el("div", { classe: "form-ligne" }, [el("label", { for: "nouveau-nom", texte: "Nom" }), ui.nom]),
          el("div", { classe: "form-ligne" }, [el("label", { for: "nouveau-fichier", texte: "Fichier STEP" }), ui.fichier]),
          el("p", { classe: "aide", texte: "Aile exportée de la CAO (STEP ≤ 200 Mo). Le fichier est copié dans le dossier du projet." }),
          el("div", { classe: "actions" }, [ui.creer]),
          ui.etatCreation,
          ui.erreurCreation,
        ]),
      ]),
      el("div", {}, [
        el("div", { classe: "carte" }, [
          el("div", { classe: "couche-entete" }, [
            el("h2", { texte: "Projets" }),
            el("button", { type: "button", classe: "petit", texte: "Actualiser", onclick: lister }),
          ]),
          ui.erreurListe,
          ui.liste,
        ]),
        ui.carteCorps,
      ]),
    ]),
  );
  ctx.sur("projet", (nom) => {
    lister();
    montrerCorps(nom);
  });
  ctx.sur("generation-finie", lister);
  ctx.sur("reconnexion", lister);
  lister();
}

export function afficher() {
  lister();
}
