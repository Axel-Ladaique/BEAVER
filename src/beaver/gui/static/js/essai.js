// Résumé d'un essai (onglets Générer et Historique) : chiffres, messages, tableau des corps, fichiers, actions.

import { api, seg, urlAvecJeton } from "./api.js";
import { encadreNotices, tableauMorceauxEssai } from "./decoupe.js";
import { badgeStatut, duree, el, message, nombre, vider } from "./dom.js";

/**
 * Sorties d'un essai terminé : actions (Bambu, rapport, dossier) puis fichiers à télécharger (3MF, STEP, STL).
 */
export function actionsEssai(projet, essai, resume, surErreur) {
  const base = `/api/projets/${seg(projet)}/essais/${seg(essai)}`;
  const fichiers = resume.fichiers || [];
  const href = (nom) => urlAvecJeton(`${base}/fichiers/${seg(nom)}`);
  const fichier = (nom, type) => el("a", {
    classe: "bouton fichier", href: href(nom), target: "_blank", rel: "noopener", title: `${type} : ${nom}`,
  }, [el("span", { classe: `type-fichier type-${type.toLowerCase()}`, texte: type }), el("span", { classe: "nom-fichier", texte: nom })]);
  const telechargements = [
    ...fichiers.filter((x) => /\.3mf$/i.test(x)).map((f) => fichier(f, "3MF")),
    ...fichiers.filter((x) => /\.(step|stp)$/i.test(x)).map((f) => fichier(f, "STEP")),
    ...fichiers.filter((x) => /\.stl$/i.test(x)).map((f) => fichier(f, "STL")),
  ];
  const action = (texte, route, principal = false) => el("button", {
    type: "button", classe: principal ? "principal" : null, texte,
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
  const actions = [];
  if (fichiers.some((x) => /\.3mf$/i.test(x))) actions.push(action("Ouvrir dans Bambu", "ouvrir-bambu", true));
  if (resume.rapport && fichiers.includes(resume.rapport)) {
    actions.push(el("a", { classe: "bouton", href: href(resume.rapport), target: "_blank", rel: "noopener", texte: "Rapport" }));
  }
  actions.push(action("Ouvrir le dossier", "ouvrir-dossier"));
  return [
    el("div", { classe: "actions" }, actions),
    telechargements.length
      ? el("div", { classe: "sorties-fichiers" }, [el("h4", { texte: "Fichiers" }), ...telechargements]) : null,
  ];
}

/** Carte de résumé d'un essai (chiffres, erreurs, avertissements, corps, liens et actions). */
export function carteEssai(projet, essai, r) {
  const erreurs = el("div");
  const carte = el("div", { classe: "carte" }, [
    el("h3", { classe: "titre-essai" }, [el("span", {}, ["Essai ", el("code", { texte: essai })]), badgeStatut(r.statut)]),
  ]);
  if (r.message) carte.append(el("p", { texte: r.message }));
  if (r.nervures !== undefined) {
    carte.append(el("div", { classe: "chiffres" }, [
      ["nervures (plaques)", r.nervures], ["tronçons", r.troncons], ["morceaux (plateaux)", r.morceaux], ["masse", r.masse !== undefined ? `${nombre(r.masse, 1)} g` : "—"],
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
  const notices = encadreNotices(r.notices);
  if (notices) carte.append(notices);
  if (r.avertissements && r.avertissements.length) {
    carte.append(el("div", { classe: "message attention" }, [
      el("strong", { texte: "Avertissements" }),
      el("ul", { classe: "liste-messages" }, r.avertissements.map((x) => el("li", { texte: x }))),
    ]));
  }
  if (r.corps && r.corps.length) {
    const avecBaies = r.corps.some((c) => c.baies && c.baies !== "aucune");
    const numeriques = new Set(["Nervures", "Tronçons", "Couches", "Hauteur", "Masse", "Trajet"]);
    carte.append(el("h4", { texte: "Corps" }), el("div", { classe: "defile" }, [el("table", {}, [
      el("thead", {}, [el("tr", {}, ["Corps", "Imprimable", "Nervures", "Tronçons", "Couches", "Hauteur", "Masse", "Trajet", "Trous",
        ...(avecBaies ? ["Baies de servo"] : [])]
        .map((t) => el("th", { classe: numeriques.has(t) ? "nombre" : null, texte: t })))]),
      el("tbody", {}, r.corps.map((c) => el("tr", {}, [
        el("td", { texte: c.nom }), el("td", { classe: c.ok ? "ok-oui" : "ok-non", texte: c.ok ? "oui" : "non" }),
        el("td", { classe: "nombre", texte: String(c.nervures) }), el("td", { classe: "nombre", texte: String(c.troncons) }),
        el("td", { classe: "nombre", texte: String(c.couches) }), el("td", { classe: "nombre", texte: `${nombre(c.hauteur, 1)} mm` }),
        el("td", { classe: "nombre", texte: `${nombre(c.masse, 1)} g` }), el("td", { classe: "nombre", texte: `${nombre(c.trajet, 1)} m` }),
        el("td", { texte: c.trous || "" }),
        avecBaies ? el("td", { texte: c.baies || "" }) : null,
      ]))),
    ])]));
  }
  const morceaux = tableauMorceauxEssai(r.corps);
  // tableau utile dès qu'une pièce est découpée ou tournée sur le plateau
  const lignes = (r.corps || []).flatMap((c) => c.liste_morceaux || []);
  if (morceaux && (lignes.length > (r.corps || []).length || lignes.some((m) => m.rotation))) {
    carte.append(el("h4", { texte: "Morceaux imprimés" }), el("div", { classe: "defile" }, [morceaux]));
  }
  if (r.statut !== "en cours") {
    carte.append(el("div", { classe: "sorties" }, [...actionsEssai(projet, essai, r, (m) => vider(erreurs).append(message(m))), erreurs]));
  }
  return carte;
}
