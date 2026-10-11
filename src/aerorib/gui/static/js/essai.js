// Résumé d'un essai (onglets Générer et Historique) : chiffres, messages, tableau des corps, fichiers, actions.

import { api, seg, urlAvecJeton } from "./api.js";
import { badgeStatut, duree, el, message, nombre, vider } from "./dom.js";

/** Liens et boutons d'un essai terminé . */
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

/** Carte de résumé d'un essai (chiffres, erreurs, avertissements, corps, liens et actions). */
export function carteEssai(projet, essai, r) {
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
  if (r.statut !== "en cours") {
    carte.append(el("div", { classe: "actions liens" }, actionsEssai(projet, essai, r, (m) => vider(erreurs).append(message(m)))), erreurs);
  }
  return carte;
}
