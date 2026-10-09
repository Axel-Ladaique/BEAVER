# AeroRib — Générateur de structure interne pour impression vase mode

- **Date :** 2026-10-09
- **Statut :** spécification validée en discussion, en relecture
- **Auteur :** Axel Ladaique (avec Claude)

## 1. Objectif

Outil Python qui prend une géométrie exportée d'une CAO (SolidWorks au travail, Fusion 360 à la maison) et la transforme automatiquement pour qu'elle s'imprime en **vase mode (spiral)** sur imprimante Bambu Lab, en PLA Aero ou ASA Aero : nervures internes, logements de longerons, trous d'allègement, découpe en tronçons, puis ouverture directe dans Bambu Studio avec les bons réglages.

Il remplace une macro SolidWorks existante, difficile à développer et **impossible à tester automatiquement**. La testabilité automatique est donc un objectif de premier rang.

Référence du procédé : vidéo « 3D printable wing in one continuous outline » (Fusion 360), https://www.youtube.com/watch?v=QJjhMan6T_E

## 2. Principe physique

En vase mode, le slicer imprime **un seul contour fermé par couche**, sans rétraction, et l'intérieur reste vide. Un mur interne est donc obtenu en coupant une **fente fine (0,1 mm)** reliée à l'extérieur : le contour entre dans la fente et en ressort, ce qui produit un double mur.

Règles qui en découlent :

1. Chaque couche de la pièce finale doit être **un seul polygone simplement connexe** (ni îlot, ni trou isolé).
2. Les nervures doivent être coupées en deux par une **bande de séparation** (~0,6 mm) le long d'une surface médiane. Les nervures du haut et celles du bas s'y arrêtent, le contour fait l'aller-retour et la couche reste connexe. La bande est retirée **des fentes uniquement**, jamais de la peau.
3. Un longeron est logé dans un trou relié à la peau par une fente de 0,1 mm, ce qui inclut le manchon dans le contour unique.
4. Les murs internes sont inclinés d'au plus ~45° (réglable selon la matière) par rapport à l'axe de construction.

Formule générale :

```
outils   = fentes_nervures (tous niveaux)
         − bande_de_séparation
         − dégagements_longerons
         − trous_d'allègement
         − zones NORIB
         + fentes_longerons (trou Ø+jeu + fente 0,1 mm jusqu'à la peau)
résultat = peau − outils
```

## 3. Périmètre

### Inclus (à terme)
- Ailes droites, effilées, en flèche, vrillées ; fuselages ; formes fermées quelconques.
- Plusieurs niveaux de nervures (réseau principal profond + réseau fin peu profond pour tenir la peau).
- Longerons multiples, détectés depuis la géométrie.
- Trous d'allègement automatiques.
- Zones sans nervures.
- Orientation automatique avec forçage manuel.
- Découpe automatique en tronçons pour tenir dans le volume d'impression.
- Export STEP (réouvrable en CAO), STL, projet 3MF Bambu ; ouverture de Bambu Studio.
- CLI, puis intégration SolidWorks / Fusion 360, puis GUI.
- Windows (travail) et macOS (maison).

### Exclu
- Génération directe de G-code (on passe toujours par Bambu Studio).
- Impression en mode non-vase (remplissage classique).
- Autres slicers que Bambu Studio (l'architecture ne l'empêche pas, mais ce n'est pas un objectif).

## 4. Choix techniques

| Sujet | Choix | Raison |
|---|---|---|
| Langage | Python 3.12 | écosystème géométrie, scriptable |
| Noyau géométrique | OpenCascade via `build123d` / OCP | B-rep exact : STEP de sortie propre et réouvrable dans SolidWorks/Fusion |
| Validation | maillage (`trimesh`) + découpe 2D (`shapely`) | rapide, indépendant du noyau de génération |
| Config | YAML + `pydantic` | validée, messages d'erreur clairs |
| CLI | `typer` | simple |
| Environnement | `uv` | reproductible sur Windows et macOS |
| Tests | `pytest` | |
| CI | GitHub Actions, matrice Windows + macOS | |

Approches écartées :
- **Tout en maillage (`manifold3d`)** : rapide et robuste, mais le STEP de sortie ne serait qu'un maillage. Conservé uniquement pour la validation si utile.
- **Génération couche par couche 2D / G-code** : pas de STEP, contourne Bambu Studio.

## 5. Architecture

Paquet Python `aerorib`. Chaque module a un rôle unique et une interface claire.

```
STEP ─▶ import ─▶ classify ─▶ orient ─▶ segment ─▶ [par tronçon] features ─▶ build ─▶ validate ─▶ export ─▶ bambu
```

| Module | Rôle | Entrée → Sortie |
|---|---|---|
| `config` | schéma, presets matière, table imprimantes, fusion des couches de config | fichiers/options → `Config` |
| `io_step` | lecture/écriture STEP avec noms et unités (OCC XCAF) | fichier → `[NamedBody]` ; solides → fichier |
| `classify` | noms → rôles (`SKIN`, `SPAR`, `SPLIT`, `NORIB`, `CUT`…), rapport | `[NamedBody]` → `Model` |
| `orient` | choix d'orientation (auto ou forcée) | `Model` → `Model` orienté (Z = construction) |
| `segment` | découpe en tronçons tenant dans le volume | `Model` → `[Segment]` |
| `features/*` | un générateur par feature, interface commune `make_tools(segment, cfg) -> ToolSet` | `Segment` → outils (ajouts / retraits) |
| `build` | fusion des outils par lots, coupe booléenne robuste (fuzzy, parallèle) | `Segment` + outils → solide final |
| `validate` | vérifications par couche (§8) | solide → `Report` |
| `export` | STEP, STL, 3MF Bambu depuis gabarit | solides → fichiers |
| `bambu` | localisation de Bambu Studio (Win/Mac), ouverture, slicing CLI | 3MF → app / G-code |
| `cli` | commandes utilisateur | — |

Principe : **une feature = un fichier dans `features/`**, testable seul. Ajouter un motif de nervure ou un type d'insert ne touche pas au reste.

## 6. Entrées : STEP et nommage

- Formats acceptés : STEP (AP203/AP214/AP242). STL accepté plus tard en entrée dégradée (sans export STEP propre).
- Unités lues depuis le fichier.
- Fusion 360 place les noms des corps sur les entités `MANIFOLD_SOLID_BREP` (pas sur des produits). OpenCascade ne les remonte pas : `aerorib` les relit dans le texte STEP et les associe aux solides par nombre de faces (invariant au placement), puis par ordre du fichier.
- Les rôles sont déduits des noms, en cherchant à **tous les niveaux disponibles** (produit/pièce, composant, corps), sans tenir compte de la casse, par préfixe configurable.

| Préfixe par défaut | Rôle |
|---|---|
| `SKIN`, `Wing`, `Fuse`, `Aile`, `Peau` | corps à traiter (s'il n'y a qu'un seul corps non reconnu, c'est la peau) |
| `SPAR`, `SBAR`, `Longeron` | longeron : diamètre et axe détectés (cylindre) |
| `SPLIT` | surface de séparation manuelle |
| `NORIB` | zone sans nervures |
| `CUT` | plan de découpe forcé entre tronçons |

- Les paramètres peuvent être surchargés dans le nom (ex. `SPAR_main_clear0.3`).
- En repli, la config peut assigner un rôle à un nom exact (`roles: {Body3: spar}`).
- `aerorib inspect` affiche ce qui a été compris : corps, rôles, diamètres et axes de longerons, unités, orientation proposée, nombre de tronçons prévu.
- Des exports réels SolidWorks **et** Fusion 360 servent de fixtures de test (§9).

## 7. Géométrie des features

Repère : après orientation, Z est l'axe de construction.

### 7.1 Nervures (`rib_layers`)
- Liste de niveaux. Chaque niveau a : `pattern` (`diamond` d'abord ; d'autres plus tard), `angle` (par rapport à Z), `spacing`, `depth` (`full` ou distance en mm depuis la peau), `phase` optionnelle.
- `diamond` : deux familles de plans à ±`angle`. Chaque plan devient une plaque d'épaisseur `slit_width`, répétée tous les `spacing`. Les plans contiennent la direction « épaisseur » de la pièce, pour que les nervures traversent l'épaisseur.
- `depth: full` : la plaque traverse tout le solide.
- `depth: d` : intersection avec la couche de peau d'épaisseur `d` (peau − offset intérieur de `d`). Plan B si l'offset OCC échoue : construction section par section.
- Élagage : les fragments de nervure plus courts que `min_rib_length` sont retirés (bords d'attaque et de fuite).
- La phase du motif est calculée dans le repère de la pièce complète, ce qui le garde continu entre tronçons.

### 7.2 Surface de séparation (`split`)
- `mode: auto` : sections perpendiculaires à Z à intervalle régulier. Pour chaque section, on calcule la courbe médiane (ligne de cambrure pour un profil : milieu extrados/intrados le long de la corde ; axe principal pour une section compacte type fuselage), puis loft de ces courbes et épaississement de `gap` (défaut 0,6 mm).
- `mode: body` : on utilise le corps `SPLIT`, épaissi de `gap`.
- La bande est soustraite aux fentes de nervures, jamais à la peau.

### 7.3 Longerons
- Détection du cylindre : axe, diamètre, étendue.
- Retrait des fentes dans un manchon de diamètre `Ø + 2·(clearance + sleeve)` et dans une bande de largeur `sleeve_strip` (défaut 2 mm) allant du manchon à la peau, côté `slit_side`.
- Ajout aux outils : le trou `Ø + 2·clearance`, plus une fente `slit_width` du trou jusqu'à la peau, côté `slit_side` (`top`/`bottom`).

### 7.4 Trous d'allègement
- `mode: auto` : section décalée vers l'intérieur de `margin`, moins la bande de séparation (± `margin`) et les manchons de longeron, puis loft le long de Z. Ce volume est soustrait aux fentes, ce qui fait des trous dans les murs des nervures.
- `mode: off` ou formes manuelles (plus tard).

### 7.5 Zones `NORIB`
- Les fentes de nervures sont soustraites par le corps `NORIB`.

### 7.6 Orientation
- `orientation: auto` : on évalue des orientations candidates (axes principaux, axe des longerons, normales des grandes faces planes). Score : face plane posée sur le plateau, surplombs de la peau sous la limite, une seule section connexe par couche, tenue dans le volume.
- Forçage : `{axis: [x,y,z]}` ou `{along: SPAR_main}`.
- Si aucune orientation n'est valide, l'outil explique pourquoi (ex. « la pièce doit être découpée »).

### 7.7 Découpe en tronçons (`segment`)
- Faite **avant** la génération des features.
- Plans perpendiculaires à Z. Nombre minimal de tronçons tels que hauteur ≤ Z max et emprise ≤ XY max, avec une marge (défaut 5 mm).
- Positions optimisées pour éviter les zones `NORIB`, les sections trop petites et les variations brusques de section. Forçage possible par `cuts_at: [...]` ou par des corps `CUT_*`.
- Chaque tronçon est posé sur sa face de coupe. Les longerons traversent les joints.

## 8. Validation (`aerorib check`, lancée aussi après chaque `run`)

Découpe du résultat à la hauteur de couche du preset. Pour chaque couche :

| Vérification | Seuil (défaut, dans le preset) |
|---|---|
| Exactement 1 polygone, sans trou intérieur | — (bloquant) |
| Largeur de chaque fente ≥ seuil détectable par le slicer | `min_slit_width` |
| Distance entre plis voisins | ≥ `min_fold_spacing` (~2 largeurs de ligne) |
| Fragments de nervure | ≥ `min_rib_length` |
| Inclinaison des murs internes | ≤ `max_overhang_deg` |
| Peau extérieure intacte (distance à l'original) | ≤ tolérance |
| Trou de longeron : diamètre et axe | ± tolérance |
| Tronçon dans le volume imprimante | marge incluse |

Statistiques affichées : longueur de filament estimée, masse, nombre de couches.

Sorties : rapport texte (terminal, code de retour ≠ 0 en cas d'échec bloquant) et rapport HTML avec images des couches en défaut.

## 9. Stratégie de test

1. **Unitaires** : config, nommage, générateurs de motifs, détection de cylindre, table imprimantes, scoring d'orientation.
2. **Géométriques sur pièces synthétiques générées en code** : aile NACA droite, effilée, en flèche, vrillée ; fuselage ellipsoïde ; aile à deux longerons. Pipeline complet, puis toutes les vérifications §8.
3. **Fixtures réelles** : exports SolidWorks et Fusion 360 de la même pièce simple (peau + longeron nommés), dans `tests/fixtures/`, avec tests d'import et de classification.
4. **Bout en bout Bambu** (marqueur `slow`, sauté si Bambu Studio absent) : slicing CLI avec le gabarit, analyse du G-code, 0 rétraction entre le début et la fin de la spirale.

CI : GitHub Actions, Windows + macOS, niveaux 1 à 3. Niveau 4 en local.

Le module `validate` est développé **avant** les générateurs (phase P0) : il sert d'oracle à tous les tests suivants.

## 10. Configuration

Couches, chacune écrasant la précédente :

```
défauts intégrés → preset matière → fichier projet .yaml → paramètres dans les noms CAO → options CLI
```

Exemple :

```yaml
material: pla_aero            # pla_aero | asa_aero : largeur ligne, couche, seuils §8
printer: p1s                  # p1s | h2s | x2d …

orientation: auto             # ou {axis: [0,0,1]} / {along: SPAR_main}
segment: {mode: auto, margin: 5}   # ou {cuts_at: [250, 500]}

slit_width: 0.1
split: {mode: auto, gap: 0.6}

rib_layers:
  - {name: principal, pattern: diamond, angle: 45, spacing: 30, depth: full}
  - {name: peau,      pattern: diamond, angle: 45, spacing: 8,  depth: 3}

spars:
  default: {clearance: 0.15, sleeve: 0.5, slit_side: top}
  SPAR_rear: {slit_side: bottom}

lightening_holes: {mode: auto, margin: 4}

naming:
  skin: [SKIN, Wing, Fuse, Aile, Peau]
  spar: [SPAR, SBAR, Longeron]
  split: [SPLIT]
  norib: [NORIB]
  cut: [CUT]
roles: {}                     # repli : {NomExact: role}

bambu:
  template: auto              # ou chemin vers un .3mf
  executable: auto            # ou chemin forcé
  open: false

output: {dir: out, formats: [step, stl, 3mf]}
```

## 11. Imprimantes

Table dans la config : volume utile, gabarit 3MF associé.

| Imprimante | Volume utile |
|---|---|
| P1S | 256 × 256 × 256 mm |
| H2S | à reprendre de la fiche technique Bambu officielle à l'implémentation |
| X2D | à reprendre de la fiche technique Bambu officielle à l'implémentation |

## 12. Intégration Bambu Studio

- **Approche gabarit** : l'utilisateur enregistre une fois depuis Bambu Studio un projet vide par couple imprimante/matière (filament PLA Aero ou ASA Aero, vase mode activé, couches du bas réglées). `aerorib` copie le gabarit et n'y remplace que les objets. On ne reconstruit jamais les réglages à la main, ce qui reste compatible avec les versions de Bambu Studio.
- En vase mode, il n'y a qu'**un objet par plateau** : le 3MF contient un plateau par tronçon.
- Ouverture : `open -a "Bambu Studio" <fichier>` sur macOS ; exécutable détecté dans `Program Files` sur Windows ; chemin forçable.
- Slicing CLI pour les tests de niveau 4, avec le même gabarit. Les options exactes de la ligne de commande sont à confirmer sur la version installée.

## 13. CLI

```
aerorib inspect <fichier.step> [-c config.yaml]
aerorib run     <fichier.step>  -c config.yaml [--open] [--printer p1s] [--out dir]
aerorib check   <fichier.step|stl> [-c config.yaml]
```

## 14. Intégration CAO (phase P6)

- Contrat : STEP en entrée, STEP/3MF en sortie, plus un JSON optionnel de rôles (`{"body": "role"}`) qui permet de transmettre une sélection faite dans la CAO.
- **Fusion 360** : add-in Python. Il exporte le corps sélectionné, appelle `aerorib`, réimporte le STEP résultat et peut ouvrir Bambu.
- **SolidWorks** : macro VBA ou add-in C#, même logique, appelle `aerorib.exe`.
- Distribution Windows : exécutable autonome (PyInstaller) si l'installation de Python est limitée au travail.

## 15. Phases

| Phase | Contenu | Livrable |
|---|---|---|
| P0 | Squelette, `uv`, CI Win/Mac, `config`, `io_step`, `classify`, `aerorib inspect`, **`validate` + `aerorib check`**, pièces synthétiques de test | lecture des STEP, jugement d'une pièce |
| P1 | Aile droite/effilée : séparation auto (cambrure), 1 niveau de nervures `full`, longerons, `build`, export STEP/STL | équivalent automatisé de la vidéo |
| P2 | Gabarits 3MF, `--open`, test bout en bout G-code | un clic → Bambu prêt |
| P3 | Niveaux multiples (`depth`), trous d'allègement auto, `NORIB` | parité macro et au-delà |
| P4 | Orientation auto, découpe en tronçons, multi-plateaux | grandes pièces |
| P5 | Fuselages et formes quelconques, `SPLIT` manuel | « tout faire » |
| P6 | Add-in Fusion 360, macro SolidWorks | appel depuis la CAO |
| P7 | GUI avec aperçu 3D | réglages interactifs |

Chaque phase a son propre plan d'implémentation et se termine avec ses tests au vert sur la CI.

## 16. Risques

| Risque | Mitigation |
|---|---|
| Booléens OCC lents ou instables avec des centaines de plaques de 0,1 mm | fusion des outils par lots, `fuzzy value`, parallélisme, benchmarks dans les tests |
| Offset intérieur OCC qui échoue (profondeur, trous d'allègement) | plan B section par section |
| Noms perdus à l'export STEP selon le logiciel ou les options | recherche multi-niveaux, repli `roles:`, fixtures réelles des deux logiciels |
| Surface médiane automatique mauvaise sur des formes complexes | corps `SPLIT` manuel |
| Format 3MF Bambu qui évolue | approche gabarit |
| Fentes trop fines ignorées par le slicer | seuils dans le preset + test bout en bout G-code |

## 17. Points ouverts (en attente d'éléments externes)

- Exports STEP de test depuis SolidWorks et Fusion 360 (même pièce simple, corps nommés), pour valider la conservation des noms. Attendus de l'utilisateur.
- Gabarits 3MF Bambu par imprimante et matière. À enregistrer par l'utilisateur en phase P2.
- Volumes utiles H2S et X2D, depuis les fiches techniques Bambu.
- Options exactes du slicing en ligne de commande de Bambu Studio, à vérifier sur la version installée.
