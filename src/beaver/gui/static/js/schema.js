// Lecture du schéma JSON de la configuration (pydantic) : nœud d'un chemin, défaut, nature du widget.

import { copie, lire } from "./chemins.js";

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

export const PLAN = [
  { titre: "Général", cles: ["material", "slit_width", "printer"] },
  { cle: "rib_layers" },
  { cle: "material_settings" },
  { cle: "split" },
  { cle: "spars" },
  { cle: "passages" },
  { cle: "servo" },
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

