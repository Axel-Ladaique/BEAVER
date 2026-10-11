// Libellés, aides et choix en français des champs de configuration.

import { cleChemin } from "./chemins.js";

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
export const CHOIX = {
  fill: "reboucher", link: "relier à la peau", off: "aucun", auto: "automatique",
  top: "dessus (extrados)", bottom: "dessous (intrados)",
  skin: "peau", spar: "longeron", split: "séparation", norib: "sans nervure", cut: "découpe", ignore: "ignorer",
  diamond: "losange", full: "jusqu'à la bande",
};

function generique(chemin) {
  return chemin.map((k) => (typeof k === "number" ? "*" : k));
}

export function libelle(chemin) {
  const g = generique(chemin);
  const cle = cleChemin(chemin);
  const avecEtoile = g.map((k, i) => (i === 1 && g[0] === "spars" ? "*" : k)).join(".");
  return LIBELLES_CHEMIN[cle] || LIBELLES_CHEMIN[avecEtoile] || LIBELLES[chemin[chemin.length - 1]] ||
    String(chemin[chemin.length - 1]);
}

export function aide(chemin) {
  const g = generique(chemin).join(".");
  return AIDES[g] || AIDES[`${chemin[0]}.*`] || null;
}
