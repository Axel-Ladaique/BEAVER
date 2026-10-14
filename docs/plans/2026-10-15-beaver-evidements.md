# BEAVER (renommage + GitHub) et évidements des nervures — implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal :** renommer l'outil en **BEAVER** (Bambu Engine for Aero Vase-mode Embedded Ribs), le publier sur https://github.com/Axel-Ladaique/BEAVER, puis ajouter les évidements des nervures (ponts aux croisements ou à pas régulier).

**Spec :** `docs/specs/2026-10-15-evidements-design.md` (évidements) ; cœur : `docs/specs/2026-10-09-aerorib-design.md` et suivantes.

## Global Constraints
- Aucune régression : v2 ✔ 84 plaques ≈ 909 m ; servo ✔ 111 ; double SBAR ✔ 98 ; rectangle ✔ 72 ; ×3 (`aile_trop_grande_servo.step`) ✔ 6 morceaux ; tests Bambu ✔. Évidements **désactivés par défaut** ⇒ ces chiffres ne bougent pas.
- Vase mode : chaque couche = un seul contour sans trou ; la validation est l'arbitre. Aperçu = génération.
- Messages en français ; réglages validés ; défauts sûrs. Tests rapides synthétiques, fixtures réelles en `slow`.
- Commits français + trailer ; jamais affaiblir un test ; un seul implémenteur à la fois.

---

### Task 1 : Renommage en BEAVER
- Paquet Python `aerorib` → `beaver` (`src/beaver/…`), commande `beaver` (`beaver gui`, `beaver run`, `beaver inspect`, …) ; garder `aerorib` comme **alias** de commande (même point d'entrée) pour ne rien casser.
- Variables d'environnement `AERORIB_*` → `BEAVER_*` avec repli sur l'ancien nom (ex. `BEAVER_PROFILES` puis `AERORIB_PROFILES`).
- Titre GUI, en-tête du jeton (`X-Beaver-Token`, accepter aussi l'ancien), rapports, README (titre « BEAVER — Bambu Engine for Aero Vase-mode Embedded Ribs », installation, lancement), lanceurs `lancer-beaver.command` / `.bat` (garder les anciens comme alias ou les renommer — renommer, README à jour).
- `pyproject.toml` : nom `beaver`, version inchangée, URL du dépôt.
- Tous les tests passent (rapides + lents clés).
- Commit `refactor: renommage en BEAVER`.

### Task 2 : Publication GitHub
- Remote `origin` = https://github.com/Axel-Ladaique/BEAVER.git ; `.gitignore` vérifié (`.superpowers/`, `projets/`, `out/`, `.venv/`) ; pas de fichiers lourds inutiles ; push de `main` (contrôleur, avec les identifiants git de l'utilisateur ; pas de force-push).

### Task 3 : Évidements — géométrie des profils
- `config` : `ribs_hollow` (ou `hollow` par couche, global par défaut) : `mode off|croisements|pas`, `bridge_width` 4.0, `pitch` 30.0, `keep_ratio` 0.3, `keep_min` 2.0 ; erreurs françaises.
- `features/ribs.py` : positions des ponts (croisements avec l'autre famille de la même couche, ou pas régulier ancré sur la boîte globale) ; bord intérieur échancré : pleine hauteur aux ponts, profondeur gardée ailleurs, flancs en rampe à `max_overhang_deg` **en z d'impression** ; zones pleines autour des longerons/gaines/SBAR/baies et près des coupes ; polygones simples ; aperçu identique (même fonction via `rib_options`).
- Tests : profil 2D d'une plaque (ponts aux bons s, profondeurs, pentes) ; aile synthétique évidée ✔ imprimable (chaque couche un contour, largeur de matière) ; masse/trajet inférieurs à sans évidement ; `off` ⇒ profils identiques à avant (octets / plaques) ; aperçu = génération.
- Commit `feat(nervures): évidements avec ponts`.

### Task 4 : Évidements — aperçu, GUI, rapport, docs
- Réglages : section « Évidements » (libellés français), coupe/3D montrant les nervures raccourcies, estimation du gain de masse.
- Rapport/CLI : ligne évidements + gain estimé.
- README : section « Évidements » (principe, échancrures côté bande, ponts, pentes, zones pleines).
- Tests : e2e lent via l'API GUI sur la v2 avec `croisements` ⇒ essai ok, masse < sans évidement ; fixtures réelles évidées ✔ (v2, servo) en `slow`.
- Commits `feat(gui): évidements dans l'aperçu`, `docs: évidements`.
