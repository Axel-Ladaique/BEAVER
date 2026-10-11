// Erreurs de validation du serveur → erreurs par champ (pydantic, profils), messages courants traduits.

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
