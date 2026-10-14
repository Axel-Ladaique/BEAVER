// Chemins dans un dictionnaire imbriqué (même sémantique que `_is_set` / `_flatten` du serveur).

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

/** JSON canonique (clés triées à tous les niveaux) : comparer un état édité à l'état enregistré. */
export function canonique(v) {
  const trier = (x) => {
    if (Array.isArray(x)) return x.map(trier);
    if (x === null || typeof x !== "object") return x;
    return Object.fromEntries(Object.keys(x).sort().filter((k) => x[k] !== undefined).map((k) => [k, trier(x[k])]));
  };
  return JSON.stringify(trier(v));
}
