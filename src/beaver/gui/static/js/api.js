// Accès à l'API locale : jeton (lu dans l'URL de la page), erreurs JSON en français, perte de connexion.

const ENTETE = "X-Beaver-Token";
const SANTE_MS = 2000;

function lireJeton() {
  const p = new URLSearchParams(window.location.search);
  const j = p.get("token");
  try {
    if (j) sessionStorage.setItem("beaver-jeton", j);
    else return sessionStorage.getItem("beaver-jeton") || "";
  } catch {
    /* stockage indisponible : le jeton de l'URL suffit */
  }
  return j || "";
}

export const jeton = lireJeton();

/** Erreur d'API : `statut` HTTP (0 = réseau) et message français du serveur. */
export class ErreurApi extends Error {
  constructor(statut, message, corps = null) {
    super(message);
    this.statut = statut;
    this.corps = corps;
  }
}

// --- bandeau (connexion perdue, jeton) ------------------------------------------------------------
let surveillance = null;
const ecouteursReconnexion = new Set();

function bandeau(texte) {
  const b = document.getElementById("bandeau");
  if (!b) return;
  b.textContent = texte || "";
  b.hidden = !texte;
}

export function surReconnexion(fn) {
  ecouteursReconnexion.add(fn);
}

function connexionPerdue() {
  bandeau("Serveur arrêté — relancez beaver gui (puis rouvrez l'adresse affichée).");
  if (surveillance) return;
  surveillance = setInterval(async () => {
    try {
      const r = await fetch("/api/sante", { headers: { [ENTETE]: jeton }, cache: "no-store" });
      if (r.ok) {
        clearInterval(surveillance);
        surveillance = null;
        bandeau("");
        for (const fn of ecouteursReconnexion) fn();
      } else if (r.status === 401) {
        clearInterval(surveillance);
        surveillance = null;
        jetonRefuse();
      }
    } catch {
      /* toujours arrêté */
    }
  }, SANTE_MS);
}

function jetonRefuse() {
  bandeau("Jeton refusé : le serveur a été relancé. Ouvrez la nouvelle adresse affichée par beaver gui.");
}

if (!jeton) bandeau("Jeton absent : ouvrez l'adresse complète affichée par « beaver gui » (…/?token=…).");

/** URL d'API avec le jeton en paramètre (liens ouverts dans un nouvel onglet, EventSource). */
export function urlAvecJeton(chemin) {
  const sep = chemin.includes("?") ? "&" : "?";
  return `${chemin}${sep}token=${encodeURIComponent(jeton)}`;
}

/** Segment d'URL sûr pour un nom de projet / d'essai / de fichier. */
export function seg(texte) {
  return encodeURIComponent(texte);
}

/**
 * Appel d'API. `corps` : objet (JSON), FormData, ou rien. Renvoie {statut, donnees, reponse}.
 * Lève ErreurApi (statut ≥ 400 ou réseau) avec le message `erreur` du serveur.
 * `brut: true` → donnees = ArrayBuffer.
 */
export async function appel(methode, chemin, corps, { brut = false, signal } = {}) {
  const init = { method: methode, headers: { [ENTETE]: jeton }, cache: "no-store", signal };
  if (corps instanceof FormData) init.body = corps;
  else if (corps !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(corps);
  }
  let r;
  try {
    r = await fetch(chemin, init);
  } catch (e) {
    if (e && e.name === "AbortError") throw e;
    connexionPerdue();
    throw new ErreurApi(0, "serveur injoignable");
  }
  if (r.status === 401) jetonRefuse();
  let donnees = null;
  if (brut && r.ok) donnees = await r.arrayBuffer();
  else {
    const texte = await r.text();
    try {
      donnees = texte ? JSON.parse(texte) : null;
    } catch {
      donnees = texte;
    }
  }
  if (!r.ok) {
    const msg = (donnees && typeof donnees === "object" && donnees.erreur) || `erreur ${r.status}`;
    throw new ErreurApi(r.status, msg, donnees);
  }
  return { statut: r.status, donnees, reponse: r };
}

export const api = {
  get: (c, o) => appel("GET", c, undefined, o).then((x) => x.donnees),
  put: (c, corps) => appel("PUT", c, corps).then((x) => x.donnees),
  post: (c, corps) => appel("POST", c, corps).then((x) => x.donnees),
};
