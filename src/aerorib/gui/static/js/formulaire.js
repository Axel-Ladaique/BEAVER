// Formulaire généré depuis le schéma JSON de la configuration (pydantic), partagé par Réglages et Profils.
//
// Mode « projet » : le modèle est le dictionnaire du projet (config.yaml) ; une valeur absente est héritée
// (profil ou défaut), affichée grisée, et « surcharger » la copie dans le projet ; « revenir à l'hérité »
// la retire. Mode « profil » : le modèle est le dictionnaire complet du profil, tout est éditable.

import { el, vider } from "./dom.js";

// --- chemins dans un dictionnaire imbriqué -------------------------------------------------------------
export function lire(obj, chemin) {
  let o = obj;
  for (const k of chemin) {
    if (o === null || typeof o !== "object") return undefined;
    o = o[k];
  }
  return o;
}

/** Comme `_is_set` du serveur : une liste rencontrée en chemin est posée en bloc. */
export function estPose(obj, chemin) {
  let o = obj;
  for (const k of chemin) {
    if (Array.isArray(o)) return true;
    if (o === null || typeof o !== "object" || !(k in o)) return false;
    o = o[k];
  }
  return true;
}

export function poser(obj, chemin, valeur) {
  let o = obj;
  chemin.slice(0, -1).forEach((k, i) => {
    if (o[k] === null || typeof o[k] !== "object") o[k] = typeof chemin[i + 1] === "number" ? [] : {};
    o = o[k];
  });
  o[chemin[chemin.length - 1]] = valeur;
}

/** Retire la clé et les dictionnaires parents devenus vides. */
export function retirer(obj, chemin) {
  const pile = [obj];
  let o = obj;
  for (const k of chemin.slice(0, -1)) {
    if (o === null || typeof o !== "object" || !(k in o)) return;
    o = o[k];
    pile.push(o);
  }
  delete o[chemin[chemin.length - 1]];
  for (let i = chemin.length - 2; i >= 0; i--) {
    const parent = pile[i];
    const enfant = parent[chemin[i]];
    if (enfant && typeof enfant === "object" && !Array.isArray(enfant) && Object.keys(enfant).length === 0) {
      delete parent[chemin[i]];
    } else break;
  }
}

export const copie = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
export const cleChemin = (chemin) => chemin.join(".");

// --- libellés français -----------------------------------------------------------------------------------
const LIBELLES = {
  material: "Matière (profil)",
  material_settings: "Réglages matière",
  line_width: "Largeur de ligne (mm)",
  layer_height: "Hauteur de couche (mm)",
  density: "Densité effective (g/cm³)",
  min_slit_width: "Fente min. du slicer (mm)",
  max_overhang_deg: "Surplomb max (°)",
  printer: "Imprimante",
  slit_width: "Épaisseur des nervures (mm)",
  naming: "Nommage des corps (préfixes)",
  skin: "Peau",
  spar: "Longeron",
  split: "Séparation haut / bas",
  norib: "Sans nervure",
  cut: "Découpe",
  roles: "Rôles imposés",
  validation: "Validation",
  tessellation_tol: "Tolérance de maillage (mm)",
  skin_tolerance: "Tolérance de peau (mm)",
  max_mouth_width: "Ouverture max de fente (mm)",
  orientation: "Orientation",
  gap: "Bande de séparation (mm)",
  min_rib_depth: "Profondeur min. de nervure (mm)",
  grid: "Grille surface médiane (nx, nz)",
  rib_layers: "Couches de nervures",
  name: "Nom",
  pattern: "Motif",
  angle: "Angle (°)",
  spacing: "Espacement (mm)",
  depth: "Profondeur",
  phase: "Décalage du motif (mm)",
  samples: "Échantillons",
  spars: "Longerons",
  clearance: "Jeu radial (mm)",
  sleeve: "Manchon (mm)",
  strip: "Bande sans nervure (mm)",
  slit_side: "Côté de la fente",
  anchor: "Attache côté opposé",
  anchor_gap: "Écart de l'attache (mm)",
  passages: "Passages (alésages)",
  mode: "Mode",
  max_tilt_deg: "Inclinaison max (°)",
  output: "Sortie",
  dir: "Dossier",
  formats: "Formats",
  segment: "Tronçons",
  margin: "Marge (mm)",
  cuts_at: "Coupes forcées (mm)",
  max_height: "Hauteur utile max (mm)",
  bambu: "Bambu Studio",
  template: "Gabarit (.3mf)",
  open: "Ouvrir après génération",
};
const LIBELLES_CHEMIN = {
  "split": "Séparation haut / bas",
  "naming.split": "Séparation",
  "spars.*.slit_width": "Largeur de la fente (mm)",
  "passages.slit_width": "Largeur de la fente (mm)",
  "passages.sleeve": "Manchon (mm)",
  "segment.mode": "Découpe en tronçons",
  "passages.mode": "Traitement des passages",
};
const AIDES = {
  "slit_width": "épaisseur des plaques coupées dans la pièce",
  "rib_layers.*.angle": "vide = auto (min(45°, surplomb max))",
  "segment.max_height": "vide = hauteur du gabarit Bambu − marge",
  "segment.cuts_at": "altitudes séparées par « ; »",
  "naming.*": "préfixes séparés par des virgules",
  "split.gap": "≥ largeur de ligne",
};
const CHOIX = {
  fill: "reboucher", link: "relier à la peau", off: "aucun", auto: "automatique",
  top: "dessus (extrados)", bottom: "dessous (intrados)",
  skin: "peau", spar: "longeron", split: "séparation", norib: "sans nervure", cut: "découpe", ignore: "ignorer",
  diamond: "losange", full: "jusqu'à la bande",
};

function generique(chemin) {
  return chemin.map((k) => (typeof k === "number" ? "*" : k));
}

function libelle(chemin) {
  const g = generique(chemin);
  const cle = cleChemin(chemin);
  const avecEtoile = g.map((k, i) => (i === 1 && g[0] === "spars" ? "*" : k)).join(".");
  return LIBELLES_CHEMIN[cle] || LIBELLES_CHEMIN[avecEtoile] || LIBELLES[chemin[chemin.length - 1]] ||
    String(chemin[chemin.length - 1]);
}

function aide(chemin) {
  const g = generique(chemin).join(".");
  return AIDES[g] || AIDES[`${chemin[0]}.*`] || null;
}

// --- schéma -----------------------------------------------------------------------------------------------
// Sous-schémas absents du schéma JSON (dictionnaires libres côté pydantic).
const SCHEMA_MATIERE = {
  type: "object",
  properties: {
    line_width: { type: "number", exclusiveMinimum: 0 },
    layer_height: { type: "number", exclusiveMinimum: 0 },
    density: { type: "number", exclusiveMinimum: 0 },
    min_slit_width: { type: "number", exclusiveMinimum: 0 },
    max_overhang_deg: { type: "number", exclusiveMinimum: 0, maximum: 90 },
  },
};
const SCHEMA_LONGERON = {
  type: "object",
  properties: {
    clearance: { type: "number", minimum: 0 },
    sleeve: { type: "number", exclusiveMinimum: 0 },
    strip: { type: "number", exclusiveMinimum: 0 },
    slit_side: { type: "string", enum: ["top", "bottom"] },
    slit_width: { type: "number", exclusiveMinimum: 0 },
    anchor: { type: "boolean" },
    anchor_gap: { type: "number", exclusiveMinimum: 0 },
  },
};

const PLAN = [
  { titre: "Général", cles: ["material", "slit_width", "printer"] },
  { cle: "rib_layers" },
  { cle: "material_settings" },
  { cle: "split" },
  { cle: "spars" },
  { cle: "passages" },
  { cle: "segment" },
  { titre: "Orientation et rôles des corps", cles: ["orientation", "roles"] },
  { cle: "naming" },
  { cle: "validation" },
  { cle: "bambu" },
  { cle: "output" },
];

export class Schema {
  constructor(racine) {
    this.racine = racine;
  }

  resoudre(n) {
    let x = n;
    while (x && x.$ref) x = this.racine.$defs[x.$ref.split("/").pop()];
    return x || {};
  }

  noeud(chemin) {
    let n = this.racine;
    for (const k of chemin) {
      n = this.resoudre(n);
      if (typeof k === "number") n = n.items || {};
      else if (n === this.resoudre(this.racine.properties.material_settings)) n = SCHEMA_MATIERE.properties[k] || {};
      else if (n.properties && k in n.properties) n = n.properties[k];
      else if (chemin[0] === "spars" && n.additionalProperties && n.additionalProperties.type === "object") n = SCHEMA_LONGERON;
      else if (n.additionalProperties && typeof n.additionalProperties === "object") n = n.additionalProperties;
      else return {};
    }
    if (chemin.length === 1 && chemin[0] === "material_settings") return SCHEMA_MATIERE;
    if (chemin.length === 2 && chemin[0] === "spars") return SCHEMA_LONGERON;
    return this.resoudre(n);
  }

  /** Valeur par défaut déclarée (en suivant les sous-modèles), ou undefined. */
  defaut(chemin) {
    for (let i = chemin.length; i >= 1; i--) {
      const n = this.noeud(chemin.slice(0, i));
      if (n.default !== undefined) {
        const reste = chemin.slice(i);
        return copie(reste.length ? lire(n.default, reste) : n.default);
      }
    }
    return undefined;
  }

  /** Nature du champ pour choisir le widget. */
  nature(chemin) {
    const n = this.noeud(chemin);
    if (chemin.length === 1 && chemin[0] === "orientation") return { genre: "orientation" };
    if (chemin.length === 1 && chemin[0] === "material_settings") return { genre: "groupe", noeud: SCHEMA_MATIERE };
    if (chemin.length === 1 && chemin[0] === "spars") return { genre: "dict-groupes" };
    if (n.anyOf) {
      const nonNul = n.anyOf.filter((a) => a.type !== "null").map((a) => this.resoudre(a));
      const nullable = nonNul.length < n.anyOf.length;
      const constante = nonNul.find((a) => a.const !== undefined);
      const num = nonNul.find((a) => a.type === "number" || a.type === "integer");
      if (constante && num) return { genre: "nombre-ou-const", constante: constante.const, noeud: num };
      if (nonNul.length === 1) return { ...this.natureNoeud(nonNul[0]), nullable };
      return { genre: "json" };
    }
    return this.natureNoeud(n);
  }

  natureNoeud(n) {
    if (n.const !== undefined) return { genre: "constante", noeud: n };
    if (n.enum) return { genre: "choix", noeud: n };
    if (n.type === "number" || n.type === "integer") return { genre: "nombre", noeud: n };
    if (n.type === "boolean") return { genre: "booleen" };
    if (n.type === "string") return { genre: "texte" };
    if (n.type === "array") {
      if (n.prefixItems) return { genre: "tuple", noeud: n };
      const it = this.resoudre(n.items || {});
      if (it.enum) return { genre: "multi-choix", noeud: it };
      if (it.type === "string") return { genre: "liste-texte" };
      if (it.type === "number" || it.type === "integer") return { genre: "liste-nombres" };
      if (it.type === "object" && it.properties) return { genre: "liste-objets", noeud: it };
      return { genre: "json" };
    }
    if (n.type === "object" && n.properties) return { genre: "groupe", noeud: n };
    if (n.type === "object" && n.additionalProperties && n.additionalProperties.enum) {
      return { genre: "dict-choix", noeud: n.additionalProperties };
    }
    return { genre: "json" };
  }
}

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

function texteNombre(v) {
  return v === null || v === undefined ? "" : String(v);
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
      ? (chemin[1] === "default" ? "Tous les longerons (par défaut)" : `Longeron ${chemin[1]}`)
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
        const s = this.select(id, options, valeur, inactif);
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
    const auto = !valeur || valeur === "auto";
    const s = this.select(id, ["auto", "__axe"], auto ? "auto" : "__axe", inactif,
      (v) => (v === "auto" ? "automatique" : "axe d'envergure imposé (x, y, z)"));
    const axe = (valeur && valeur.axis) || [0, 0, 1];
    const champs = [0, 1, 2].map((k) => {
      const i = el("input", { type: "number", step: "any", disabled: inactif, "aria-label": `axe ${"xyz"[k]}` });
      i.value = texteNombre(axe[k]);
      i.hidden = auto;
      return i;
    });
    const lecture = () => (s.value === "auto" ? "auto" : { axis: champs.map((i) => lireNombre(i.value, null, false)) });
    s.addEventListener("change", () => {
      champs.forEach((i) => { i.hidden = s.value === "auto"; });
      this.saisir(chemin, lecture);
    });
    champs.forEach((i) => i.addEventListener("input", () => this.saisir(chemin, lecture)));
    return el("span", { classe: "valeur" }, [s, ...champs]);
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
            onclick: () => {
              lire(this.modele, chemin).splice(i, 1);
              this.rendre();
              this.surChangement();
            },
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
const TRADUCTIONS = [
  [/^Input should be greater than or equal to (.+)$/, "doit être ≥ $1"],
  [/^Input should be greater than (.+)$/, "doit être > $1"],
  [/^Input should be less than or equal to (.+)$/, "doit être ≤ $1"],
  [/^Input should be less than (.+)$/, "doit être < $1"],
  [/^Input should be a valid number.*$/, "nombre attendu"],
  [/^Input should be a valid integer.*$/, "nombre entier attendu"],
  [/^Input should be a valid boolean.*$/, "oui/non attendu"],
  [/^Input should be a valid string.*$/, "texte attendu"],
  [/^Input should be a valid (list|array).*$/, "liste attendue"],
  [/^Input should be a valid dictionary.*$/, "dictionnaire attendu"],
  [/^Input should be (.+)$/, "valeur attendue : $1"],
  [/^Extra inputs are not permitted$/, "champ inconnu"],
  [/^Field required$/, "champ manquant"],
];

function traduit(m) {
  for (const [re, fr] of TRADUCTIONS) if (re.test(m)) return m.replace(re, fr).replace(/' or '/g, "' ou '");
  return m;
}

/**
 * Découpe un message d'erreur de validation du serveur en erreurs par champ.
 * Formats reconnus : pydantic (« chemin.0.champ\n  message [type=…] ») et
 * « champ « chemin » : message » (profils).
 */
export function erreursParChamp(texte) {
  const parChamp = new Map();
  const lignes = String(texte || "").split("\n");
  const loc = /^[A-Za-z_]\S*$/;
  const nettoie = (l) => traduit(l.trim().replace(/\s*\[type=.*$/, "").replace(/^Value error, /, ""));
  const restes = [];
  for (let i = 0; i < lignes.length; i++) {
    const l = lignes[i];
    if (/validation errors? for/.test(l) || /For further information/.test(l)) continue;
    if (loc.test(l) && i + 1 < lignes.length && /^\s{2}\S/.test(lignes[i + 1])) {
      const msg = nettoie(lignes[i + 1]);
      parChamp.set(l, parChamp.has(l) ? `${parChamp.get(l)}\n${msg}` : msg);
      i += 1;
      continue;
    }
    const t = nettoie(l);
    if (t) restes.push(t);
  }
  // format des profils : « champ « chemin » : message ; champ « … » : … »
  const general = [];
  for (const r of restes) {
    const re = /champ « ([^»]+) » : ([^;]+)/g;
    let m;
    let trouve = false;
    while ((m = re.exec(r))) {
      trouve = true;
      parChamp.set(m[1].trim(), m[2].trim());
    }
    if (!trouve) general.push(r);
  }
  // « configuration invalide : » seul n'apporte rien quand le détail est sous les champs
  const utiles = general.filter((g) => !/^configuration invalide :?$/.test(g) || !parChamp.size);
  return { parChamp, general: utiles.length ? [utiles.join("\n")] : [] };
}
