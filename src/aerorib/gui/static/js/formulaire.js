// Formulaire généré depuis le schéma JSON de la configuration (pydantic), partagé par Réglages et Profils.
//
// Mode « projet » : le modèle est le dictionnaire du projet (config.yaml) ; une valeur absente est héritée
// (profil ou défaut), affichée grisée, et « surcharger » la copie dans le projet ; « revenir à l'hérité »
// la retire. Mode « profil » : le modèle est le dictionnaire complet du profil, tout est éditable.

import { el, vider } from "./dom.js";
import { cleChemin, copie, estPose, lire, poser, retirer } from "./chemins.js";
import { aide, CHOIX, IMPRIMANTES, libelle } from "./libelles.js";
import { PLAN } from "./schema.js";

// --- lecture / contrôle des saisies -------------------------------------------------------------------------
class Saisie extends Error {}

function controleNombre(v, n) {
  if (!n) return v;
  if (n.type === "integer" && !Number.isInteger(v)) throw new Saisie("nombre entier attendu");
  if (n.exclusiveMinimum !== undefined && !(v > n.exclusiveMinimum)) throw new Saisie(`doit être > ${n.exclusiveMinimum}`);
  if (n.minimum !== undefined && !(v >= n.minimum)) throw new Saisie(`doit être ≥ ${n.minimum}`);
  if (n.exclusiveMaximum !== undefined && !(v < n.exclusiveMaximum)) throw new Saisie(`doit être < ${n.exclusiveMaximum}`);
  if (n.maximum !== undefined && !(v <= n.maximum)) throw new Saisie(`doit être ≤ ${n.maximum}`);
  return v;
}

function lireNombre(texte, n, nullable) {
  const t = String(texte).trim().replace(",", ".");
  if (t === "") {
    if (nullable) return null;
    throw new Saisie("valeur requise");
  }
  const v = Number(t);
  if (!Number.isFinite(v)) throw new Saisie("nombre attendu");
  return controleNombre(v, n);
}

const RACINES = {
  auto: "auto (bout de la gaine de câble, sinon plus grande section)",
  debut: "début (coordonnée min. le long de l'axe)",
  fin: "fin (coordonnée max. le long de l'axe)",
};

/** Orientation lue dans la config : axe imposé (ou null) et emplanture (« auto » par défaut). */
export function lireOrientation(valeur) {
  if (!valeur || typeof valeur !== "object") return { axe: null, racine: "auto" };
  return { axe: Array.isArray(valeur.axis) ? valeur.axis : null, racine: valeur.root || "auto" };
}

/** Valeur minimale de `orientation` : « auto », sinon seulement les clés qui s'écartent du défaut. */
export function valeurOrientation(axe, racine) {
  const v = {};
  if (axe) v.axis = axe;
  if (racine && racine !== "auto") v.root = racine;
  return Object.keys(v).length ? v : "auto";
}

function texteNombre(v) {
  return v === null || v === undefined ? "" : String(v);
}

/**
 * Clés d'erreur après suppression de l'élément `i` de la liste `prefixe` :
 * « prefixe.i.* » disparaissent, « prefixe.j.* » (j > i) deviennent « prefixe.(j-1).* ».
 */
export function reindexer(erreurs, prefixe, i) {
  const out = new Map();
  const debut = `${prefixe}.`;
  for (const [k, msg] of erreurs) {
    if (!k.startsWith(debut)) {
      out.set(k, msg);
      continue;
    }
    const [tete, ...reste] = k.slice(debut.length).split(".");
    const j = Number(tete);
    if (!Number.isInteger(j) || tete === "") out.set(k, msg);
    else if (j > i) out.set([prefixe, j - 1, ...reste].join("."), msg);
    else if (j < i) out.set(k, msg);
  }
  return out;
}

// --- formulaire ----------------------------------------------------------------------------------------------
let compteur = 0;

export class Formulaire {
  /**
   * @param {object} o
   * @param {Schema} o.schema
   * @param {"projet"|"profil"} o.mode
   * @param {Set<string>} [o.masques] chemins (clés pointées) non affichés
   * @param {object} [o.speciaux] chemin → (valeur) => Node : affichage en lecture seule
   * @param {Function} o.surChangement appelé après chaque modification du modèle
   */
  constructor({ schema, mode, masques = new Set(), speciaux = {}, surChangement = () => {} }) {
    this.schema = schema;
    this.mode = mode;
    this.masques = masques;
    this.speciaux = speciaux;
    this.surChangement = surChangement;
    this.element = el("div", { classe: "formulaire" });
    this.haut = el("div", { classe: "erreurs-formulaire" });
    this.corps = el("div");
    this.element.append(this.haut, this.corps);
    this.modele = {};
    this.herite = () => ({ valeur: undefined, origine: "défaut" });
    this.provenance = new Map();
    this.modifies = new Set();
    this.choix = {};
    this.erreursServeur = new Map();
    this.erreursLocales = new Map();
  }

  /** Mode projet : `projet` = config.yaml (dict), `herite(chemin)` → {valeur, origine}. */
  chargerProjet({ projet, herite, choix = {} }) {
    this.modele = copie(projet || {});
    this.herite = herite;
    this.choix = choix;
    this.erreursLocales.clear();
    this.rendre();
  }

  /** Mode profil : `valeurs` complètes, `provenance` [{chemin, origine}]. */
  chargerProfil({ valeurs, provenance = [], choix = {} }) {
    this.modele = copie(valeurs || {});
    this.provenance = new Map(provenance.map((p) => [cleChemin(p.chemin), p.origine]));
    this.modifies.clear();
    this.choix = choix;
    this.erreursLocales.clear();
    this.rendre();
  }

  donnees() {
    return copie(this.modele);
  }

  aDesErreursLocales() {
    return this.erreursLocales.size > 0;
  }

  // --- erreurs -----------------------------------------------------------------------------------------
  /** `parChamp` : Map "a.b.0.c" → message ; `general` : messages sans champ reconnu. */
  afficherErreurs(parChamp = new Map(), general = []) {
    this.erreursServeur = new Map();
    const restes = [...general];
    for (const [cle, msg] of parChamp) {
      const cible = this.ligneProche(cle);
      if (cible) this.erreursServeur.set(cible, [...(this.erreursServeur.get(cible) || []), msg]);
      else restes.push(`${cle} : ${msg}`);
    }
    vider(this.haut);
    if (restes.length) this.haut.append(el("div", { classe: "message erreur", role: "alert", texte: restes.join("\n") }));
    this.majErreurs();
  }

  effacerErreurs() {
    this.afficherErreurs();
  }

  ligneProche(cle) {
    const parties = cle.split(".");
    for (let i = parties.length; i >= 1; i--) {
      const k = parties.slice(0, i).join(".");
      if (this.corps.querySelector(`[data-chemin="${CSS.escape(k)}"]`)) return k;
    }
    return null;
  }

  majErreurs() {
    for (const ligne of this.corps.querySelectorAll("[data-chemin]")) {
      const zone = ligne.querySelector(":scope > .erreur-champ");
      if (!zone) continue;
      const k = ligne.dataset.chemin;
      const msgs = [this.erreursLocales.get(k), ...(this.erreursServeur.get(k) || [])].filter(Boolean);
      zone.textContent = msgs.join("\n");
      zone.hidden = msgs.length === 0;
      const champ = ligne.querySelector("input, select, textarea");
      if (champ) champ.setAttribute("aria-invalid", String(msgs.length > 0));
    }
  }

  // --- rendu ---------------------------------------------------------------------------------------------
  clesVisibles() {
    const props = Object.keys(this.schema.racine.properties || {});
    const cles = this.mode === "profil" ? props.filter((k) => k in this.modele) : props;
    return cles.filter((k) => !this.masques.has(k));
  }

  rendre(focusSur = null) {
    vider(this.corps);
    const visibles = new Set(this.clesVisibles());
    const places = new Set();
    for (const bloc of PLAN) {
      const cles = (bloc.cles || [bloc.cle]).filter((k) => visibles.has(k));
      if (!cles.length) continue;
      cles.forEach((k) => places.add(k));
      if (bloc.cle) this.corps.append(this.blocCle(bloc.cle));
      else {
        const fs = el("fieldset", {}, [el("legend", { texte: bloc.titre })]);
        for (const k of cles) fs.append(this.ligneOuGroupe([k]));
        this.corps.append(fs);
      }
    }
    const autres = [...visibles].filter((k) => !places.has(k));
    if (autres.length) {
      const fs = el("fieldset", {}, [el("legend", { texte: "Autres" })]);
      for (const k of autres) fs.append(this.ligneOuGroupe([k]));
      this.corps.append(fs);
    }
    this.majErreurs();
    if (focusSur) {
      const cible = this.corps.querySelector(`[data-chemin="${CSS.escape(focusSur)}"] :is(input, select, textarea, button)`);
      cible?.focus();
    }
  }

  blocCle(cle) {
    const nat = this.schema.nature([cle]);
    if (nat.genre === "groupe" || nat.genre === "dict-groupes") return this.groupe([cle], nat);
    const fs = el("fieldset", {}, [el("legend", { texte: libelle([cle]) })]);
    fs.append(this.ligne([cle], nat));
    return fs;
  }

  ligneOuGroupe(chemin) {
    const nat = this.schema.nature(chemin);
    if (nat.genre === "groupe" || nat.genre === "dict-groupes") return this.groupe(chemin, nat);
    return this.ligne(chemin, nat);
  }

  clesGroupe(chemin, nat) {
    const masque = (k) => this.masques.has(cleChemin([...chemin, k]));
    if (nat.genre === "dict-groupes") {
      const noms = new Set(Object.keys(lire(this.modele, chemin) || {}));
      if (this.mode === "projet") Object.keys(this.herite(chemin).valeur || {}).forEach((k) => noms.add(k));
      return [...noms].filter((k) => !masque(k)).sort((a, b) => (a === "default" ? -1 : b === "default" ? 1 : a.localeCompare(b)));
    }
    const declarees = Object.keys(nat.noeud.properties || {});
    if (this.mode === "profil") {
      const presentes = lire(this.modele, chemin) || {};
      return declarees.filter((k) => k in presentes && !masque(k));
    }
    return declarees.filter((k) => !masque(k));
  }

  groupe(chemin, nat) {
    const titre = chemin[0] === "spars" && chemin.length === 2
      ? (chemin[1] === "default" ? "Tous les longerons (par défaut)"
        : chemin[1] === "cable" ? "Gaines de câble (SBAR…CABLE…)" : `Longeron ${chemin[1]}`)
      : libelle(chemin);
    const fs = el("fieldset", { dataset: { groupe: cleChemin(chemin) } }, [el("legend", { texte: titre })]);
    for (const k of this.clesGroupe(chemin, nat)) fs.append(this.ligneOuGroupe([...chemin, k]));
    return fs;
  }

  /** Valeur affichée, origine et éditabilité d'une feuille. */
  etatFeuille(chemin) {
    const cle = cleChemin(chemin);
    if (this.mode === "profil") {
      const v = lire(this.modele, chemin);
      const origine = this.modifies.has(cle) ? "modifié" : this.provenance.get(cle) || "embarqué";
      return { valeur: v === undefined ? this.schema.defaut(chemin) : v, origine, editable: true, pose: true };
    }
    if (estPose(this.modele, chemin)) {
      return { valeur: lire(this.modele, chemin), origine: "projet", editable: true, pose: true };
    }
    const h = this.herite(chemin);
    return { valeur: h.valeur, origine: h.origine, editable: false, pose: false };
  }

  ligne(chemin, nat) {
    const cle = cleChemin(chemin);
    const id = `champ-${++compteur}`;
    const st = this.etatFeuille(chemin);
    const special = this.speciaux[cle];
    const large = nat.genre === "liste-objets" ? " large" : "";
    const ligne = el("div", { classe: `champ${large}${st.pose ? "" : " herite"}`, dataset: { chemin: cle } });
    const etiquette = el("label", { for: id, classe: "libelle" }, [el("span", { classe: "libelle-texte", texte: libelle(chemin) })]);
    if (!special) {
      const nomOrigine = st.origine === "modifié" ? "modifie" : st.origine.normalize("NFD").replace(/[̀-ͯ]/g, "");
      etiquette.append(" ", el("span", { classe: `origine ${nomOrigine}`, texte: st.origine }));
    }
    const zone = el("div", { classe: "valeur" });
    if (special) zone.append(special(st.valeur));
    else {
      zone.append(this.widget(chemin, nat, st.valeur, !st.editable, id));
      if (this.mode === "projet") {
        if (st.pose) {
          zone.append(el("button", {
            type: "button", classe: "petit", texte: "revenir à l'hérité",
            "aria-label": `Revenir à l'hérité : ${libelle(chemin)}`,
            title: "Retirer cette valeur du projet : la valeur du profil ou le défaut s'applique",
            onclick: () => this.revenir(chemin),
          }));
        } else {
          zone.append(el("button", {
            type: "button", classe: "petit", texte: "surcharger",
            "aria-label": `Surcharger : ${libelle(chemin)}`,
            title: "Définir cette valeur dans le projet",
            onclick: () => this.surcharger(chemin),
          }));
        }
      }
    }
    const a = aide(chemin);
    if (a) zone.append(el("span", { classe: "aide", texte: a }));
    ligne.append(etiquette, zone, el("div", { classe: "erreur-champ", role: "alert", hidden: true }));
    return ligne;
  }

  surcharger(chemin) {
    const h = this.herite(chemin).valeur;
    const v = h === undefined ? this.schema.defaut(chemin) : h;
    poser(this.modele, chemin, copie(v === undefined ? null : v));
    this.rendre(cleChemin(chemin));
    this.surChangement();
  }

  revenir(chemin) {
    retirer(this.modele, chemin);
    for (const k of [...this.erreursLocales.keys()]) if (k === cleChemin(chemin) || k.startsWith(`${cleChemin(chemin)}.`)) this.erreursLocales.delete(k);
    this.rendre(cleChemin(chemin));
    this.surChangement();
  }

  /** Retire l'élément `i` d'une liste ; ses erreurs disparaissent, celles des suivants remontent d'un rang. */
  supprimerElement(chemin, i) {
    lire(this.modele, chemin).splice(i, 1);
    this.erreursLocales = reindexer(this.erreursLocales, cleChemin(chemin), i);
    this.erreursServeur = new Map(); // relues à la prochaine validation
    this.rendre();
    this.surChangement();
  }

  /** Nouvelle valeur saisie (ou erreur de saisie) pour `chemin`. */
  saisir(chemin, lecture) {
    const cle = cleChemin(chemin);
    try {
      const v = lecture();
      this.erreursLocales.delete(cle);
      poser(this.modele, chemin, v);
      if (this.mode === "profil") this.modifies.add(cle);
      this.majErreurs();
      this.surChangement();
    } catch (e) {
      if (!(e instanceof Saisie)) throw e;
      this.erreursLocales.set(cle, e.message);
      this.majErreurs();
    }
  }

  // --- widgets -------------------------------------------------------------------------------------------
  widget(chemin, nat, valeur, inactif, id) {
    const sur = (lecture) => () => this.saisir(chemin, lecture);
    switch (nat.genre) {
      case "nombre": {
        const n = nat.noeud;
        const i = el("input", {
          id, type: "number", step: n.type === "integer" ? "1" : "any", disabled: inactif,
          placeholder: nat.nullable ? "auto" : null,
        });
        i.value = texteNombre(valeur);
        i.addEventListener("input", sur(() => lireNombre(i.value, n, nat.nullable)));
        return i;
      }
      case "nombre-ou-const": {
        const estConst = valeur === nat.constante || valeur === undefined || valeur === null;
        const s = this.select(id, [nat.constante, "__nombre"], estConst ? nat.constante : "__nombre", inactif,
          (v) => (v === "__nombre" ? "valeur (mm)" : CHOIX[v] || v));
        const i = el("input", { type: "number", step: "any", disabled: inactif, "aria-label": `${libelle(chemin)} (mm)` });
        i.value = estConst ? "" : texteNombre(valeur);
        i.hidden = estConst;
        const lecture = () => (s.value === "__nombre" ? lireNombre(i.value, nat.noeud, false) : nat.constante);
        s.addEventListener("change", () => {
          i.hidden = s.value !== "__nombre";
          if (!i.hidden) i.focus();
          this.saisir(chemin, lecture);
        });
        i.addEventListener("input", sur(lecture));
        return el("span", { classe: "valeur" }, [s, i]);
      }
      case "choix": {
        const options = [...nat.noeud.enum];
        if (chemin.length === 1 && chemin[0] === "material" && this.choix.material) options.splice(0, options.length, ...this.choix.material);
        const imprimante = chemin.length === 1 && chemin[0] === "printer";
        const s = imprimante ? this.select(id, options, valeur, inactif, (v) => IMPRIMANTES[v] || v)
          : this.select(id, options, valeur, inactif);
        if (nat.nullable) s.prepend(el("option", { value: "", texte: "(aucun)" }));
        s.addEventListener("change", sur(() => (s.value === "" && nat.nullable ? null : s.value)));
        return s;
      }
      case "texte": {
        if (chemin.length === 1 && chemin[0] === "material" && this.choix.material) {
          const s = this.select(id, this.choix.material, valeur, inactif);
          s.addEventListener("change", sur(() => s.value));
          return s;
        }
        const i = el("input", { id, type: "text", disabled: inactif, placeholder: nat.nullable ? "(aucun)" : null });
        i.value = valeur ?? "";
        i.addEventListener("input", sur(() => (i.value === "" && nat.nullable ? null : i.value)));
        return i;
      }
      case "constante":
        return el("span", { id, classe: "lecture-seule", texte: CHOIX[valeur ?? nat.noeud.const] || String(valeur ?? nat.noeud.const) });
      case "booleen": {
        const c = el("input", { id, type: "checkbox", disabled: inactif });
        c.checked = Boolean(valeur);
        c.addEventListener("change", sur(() => c.checked));
        return c;
      }
      case "tuple": {
        const items = nat.noeud.prefixItems;
        const champs = items.map((it, k) => {
          const i = el("input", { id: k === 0 ? id : null, type: "number", step: it.type === "integer" ? "1" : "any",
            disabled: inactif, "aria-label": `${libelle(chemin)} ${k + 1}` });
          i.value = texteNombre((valeur || [])[k]);
          return i;
        });
        const lecture = () => champs.map((i, k) => lireNombre(i.value, items[k], false));
        champs.forEach((i) => i.addEventListener("input", sur(lecture)));
        return el("span", { classe: "valeur" }, champs);
      }
      case "liste-texte": {
        const i = el("input", { id, type: "text", disabled: inactif, size: 40 });
        i.value = (valeur || []).join(", ");
        i.addEventListener("input", sur(() => i.value.split(",").map((s) => s.trim()).filter(Boolean)));
        return i;
      }
      case "liste-nombres": {
        const i = el("input", { id, type: "text", disabled: inactif, size: 30, placeholder: "aucune" });
        i.value = (valeur || []).join("; ");
        i.addEventListener("input", sur(() => i.value.split(/[;\s]+/).filter(Boolean).map((t) => lireNombre(t, null, false))));
        return i;
      }
      case "multi-choix": {
        const cases = nat.noeud.enum.map((opt) => {
          const c = el("input", { type: "checkbox", disabled: inactif, value: opt });
          c.checked = (valeur || []).includes(opt);
          return c;
        });
        const lecture = () => cases.filter((c) => c.checked).map((c) => c.value);
        cases.forEach((c) => c.addEventListener("change", sur(lecture)));
        return el("span", { id, classe: "valeur", role: "group" },
          cases.map((c) => el("label", {}, [c, ` ${c.value.toUpperCase()}`])));
      }
      case "orientation":
        return this.widgetOrientation(chemin, valeur, inactif, id);
      case "liste-objets":
        return this.widgetListeObjets(chemin, nat, valeur, inactif, id);
      case "dict-choix":
        return this.widgetDictChoix(chemin, nat, valeur, inactif, id);
      default: {
        const t = el("textarea", { id, rows: 2, disabled: inactif });
        t.value = JSON.stringify(valeur ?? null);
        t.addEventListener("input", sur(() => {
          try {
            return JSON.parse(t.value);
          } catch {
            throw new Saisie("JSON invalide");
          }
        }));
        return t;
      }
    }
  }

  select(id, options, valeur, inactif, etiquette = (v) => (CHOIX[v] ? `${v} — ${CHOIX[v]}` : v)) {
    const s = el("select", { id, disabled: inactif });
    const liste = [...options];
    if (valeur !== undefined && valeur !== null && !liste.includes(valeur)) liste.push(valeur);
    for (const o of liste) s.append(el("option", { value: o, texte: etiquette(o) }));
    if (valeur !== undefined && valeur !== null) s.value = valeur;
    return s;
  }

  widgetOrientation(chemin, valeur, inactif, id) {
    const { axe, racine } = lireOrientation(valeur);
    const s = this.select(id, ["auto", "__axe"], axe ? "__axe" : "auto", inactif,
      (v) => (v === "auto" ? "axe automatique" : "axe d'envergure imposé (x, y, z)"));
    const champs = [0, 1, 2].map((k) => {
      const i = el("input", { type: "number", step: "any", disabled: inactif, "aria-label": `axe ${"xyz"[k]}` });
      i.value = texteNombre((axe || [0, 0, 1])[k]);
      i.hidden = !axe;
      return i;
    });
    const emplanture = this.select(`${id}-racine`, ["auto", "debut", "fin"], racine, inactif, (v) => RACINES[v] || v);
    const lecture = () => valeurOrientation(
      s.value === "auto" ? null : champs.map((i) => lireNombre(i.value, null, false)), emplanture.value);
    s.addEventListener("change", () => {
      champs.forEach((i) => { i.hidden = s.value === "auto"; });
      this.saisir(chemin, lecture);
    });
    champs.forEach((i) => i.addEventListener("input", () => this.saisir(chemin, lecture)));
    emplanture.addEventListener("change", () => this.saisir(chemin, lecture));
    return el("span", { classe: "valeur" }, [
      s, ...champs, el("label", { for: `${id}-racine`, texte: " Emplanture " }), emplanture,
    ]);
  }

  widgetListeObjets(chemin, nat, valeur, inactif, id) {
    const liste = Array.isArray(valeur) ? valeur : [];
    const boite = el("div", { id, classe: "liste-objets", role: "group" });
    const props = Object.keys(nat.noeud.properties || {});
    liste.forEach((item, i) => {
      const titre = (item && item.name) || `couche ${i + 1}`;
      const carte = el("div", { classe: "couche" }, [
        el("div", { classe: "couche-entete" }, [
          el("strong", { texte: `Couche ${i + 1} : ${titre}` }),
          inactif ? null : el("button", {
            type: "button", classe: "petit danger", texte: "supprimer la couche",
            disabled: liste.length <= 1, title: liste.length <= 1 ? "Il faut au moins une couche" : null,
            onclick: () => this.supprimerElement(chemin, i),
          }),
        ]),
      ]);
      for (const p of props) {
        const sous = [...chemin, i, p];
        const n = this.schema.nature(sous);
        const sid = `champ-${++compteur}`;
        const v = item && p in item ? item[p] : this.schema.defaut(sous);
        const ligne = el("div", { classe: "champ", dataset: { chemin: cleChemin(sous) } }, [
          el("label", { for: sid, classe: "libelle", texte: libelle(sous) }),
          el("div", { classe: "valeur" }, [this.widget(sous, n, v, inactif, sid),
            aide(sous) ? el("span", { classe: "aide", texte: aide(sous) }) : null]),
          el("div", { classe: "erreur-champ", role: "alert", hidden: true }),
        ]);
        carte.append(ligne);
      }
      boite.append(carte);
    });
    if (!inactif) {
      boite.append(el("button", {
        type: "button", classe: "petit", texte: "ajouter une couche",
        onclick: () => {
          const l = lire(this.modele, chemin);
          const der = l[l.length - 1] || {};
          l.push({ name: `couche ${l.length + 1}`, pattern: "diamond", spacing: der.spacing ?? 30, depth: der.depth ?? "full" });
          this.rendre();
          this.surChangement();
        },
      }));
    }
    return boite;
  }

  widgetDictChoix(chemin, nat, valeur, inactif, id) {
    const dict = valeur && typeof valeur === "object" ? valeur : {};
    const boite = el("div", { id, role: "group" });
    const listeId = `corps-${++compteur}`;
    const noms = this.choix.corps || [];
    boite.append(el("datalist", { id: listeId }, noms.map((n) => el("option", { value: n }))));
    const lignes = [];
    const lecture = () => {
      const out = {};
      for (const [nom, sel] of lignes) {
        const k = nom.value.trim();
        if (!k) continue;
        if (k in out) throw new Saisie(`corps « ${k} » en double`);
        out[k] = sel.value;
      }
      return out;
    };
    const ajouterLigne = (nom, role) => {
      const n = el("input", { type: "text", list: listeId, disabled: inactif, placeholder: "nom du corps", "aria-label": "nom du corps" });
      n.value = nom;
      const s = this.select(null, nat.noeud.enum, role, inactif, (v) => CHOIX[v] || v);
      s.setAttribute("aria-label", "rôle");
      const paire = [n, s];
      lignes.push(paire);
      const rangee = el("div", { classe: "ligne-cle" }, [n, s, inactif ? null : el("button", {
        type: "button", classe: "petit danger", texte: "retirer",
        onclick: () => {
          lignes.splice(lignes.indexOf(paire), 1);
          rangee.remove();
          this.saisir(chemin, lecture);
        },
      })]);
      n.addEventListener("input", () => this.saisir(chemin, lecture));
      s.addEventListener("change", () => this.saisir(chemin, lecture));
      boite.insertBefore(rangee, bouton);
      return n;
    };
    const bouton = inactif ? null : el("button", {
      type: "button", classe: "petit", texte: "ajouter un rôle",
      onclick: () => ajouterLigne("", "ignore").focus(),
    });
    if (bouton) boite.append(bouton);
    else boite.append(el("span"));
    const fin = boite.lastChild;
    if (!bouton) {
      // en lecture seule : insertion avant le marqueur final
      for (const [k, v] of Object.entries(dict)) {
        const n = el("input", { type: "text", disabled: true, "aria-label": "nom du corps" });
        n.value = k;
        const s = this.select(null, nat.noeud.enum, v, true, (x) => CHOIX[x] || x);
        boite.insertBefore(el("div", { classe: "ligne-cle" }, [n, s]), fin);
      }
      if (!Object.keys(dict).length) boite.insertBefore(el("span", { classe: "vide", texte: "aucun (rôles déduits des noms)" }), fin);
    } else {
      for (const [k, v] of Object.entries(dict)) ajouterLigne(k, v);
    }
    return boite;
  }
}

// messages pydantic courants (anglais côté serveur) traduits pour l'affichage
