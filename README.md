# BEAVER — Bambu Engine for Aero Vase-mode Embedded Ribs

BEAVER transforme une pièce CAO (STEP, typiquement une aile) en pièce imprimable en **vase mode** sur Bambu Lab :
nervures internes, logements de longerons, découpe en tronçons et projet Bambu Studio prêt à trancher.
L'outil s'appelait auparavant AeroRib : la commande `aerorib` fonctionne toujours comme alias de `beaver`
(de même, les variables d'environnement `AERORIB_*` restent lues en repli des `BEAVER_*`).
Voir la spec : `docs/specs/2026-10-09-aerorib-design.md`.

## Installation

1. Installer [uv](https://docs.astral.sh/uv/) : `brew install uv` (macOS) ou `winget install astral-sh.uv` (Windows).
2. Récupérer le dépôt : `git clone https://github.com/Axel-Ladaique/BEAVER && cd BEAVER`
3. Dans ce dossier : `uv sync`

## Analyser et vérifier une pièce

```bash
uv run beaver inspect piece.step              # corps trouvés et rôles
uv run beaver check piece.step                # imprimable en vase mode ?
uv run beaver check piece.step --original origine.step --html rapport.html
```

## Générer les nervures

```bash
uv run beaver run piece.step -c examples/config-exemple.yaml --out out
uv run beaver check piece.step --orient     # oriente la pièce selon le longeron avant de la vérifier
```

`run` produit, pour le corps `SKIN`/`Aile`/…, une pièce avec nervures internes en diagonale
(motif en losange, fentes de 0,1 mm, soit 0,14 mm en tranche à 45°) et des fentes de 0,15 mm vers les
logements de longerons. Chaque longeron reçoit aussi une **attache** du côté opposé à sa fente : une fente
borgne qui part du trou et s'arrête à 1 mm de la peau opposée (`anchor_gap`), pour accrocher le manchon
aux deux peaux. Les autres alésages de la peau sont **rebouchés** par défaut. Dans le dossier `--out` :

- `<nom>.step` : pièce nervurée, dans le repère d'impression (Z vers le haut, emplanture au plateau) ;
- `<nom>.stl` ;
- `<nom>-rapport.html` : rapport vase mode (couches en défaut s'il y en a).

La commande retourne le code d'erreur 1 quand le résultat n'est pas imprimable ; les fichiers sont écrits
quand même, pour inspection. Elle affiche aussi les trous traités, dans le repère d'impression
(`trous : SBAR_14mm_1 (longeron, Ø14.3, attache intrados) ; rebouchés : 4 × Ø2.4 (x=…, y=… / …)`).

Défauts principaux : jeu du longeron 0,15 mm, manchon sans nervure 0,8 mm autour de chaque trou,
bande de séparation 0,6 mm, nervures tous les 30 mm à 45°. Le rapport signale notamment les contours
non uniques, la **matière plus fine qu'une largeur de ligne** (0,42 mm pour PLA Aero) et les fentes que le slicer
refermerait (< 0,10 mm).

Limites de la phase P1 :

- **plusieurs peaux par fichier** (corps d'aile découpés dans la CAO, ex. `WING_1`, `WING_2`) : un morceau par
  corps (re-découpé s'il ne tient pas dans le volume d'impression, avec une notice), orientation commune, motif de
  nervures calé sur la boîte de l'ensemble (mêmes plans d'un corps à l'autre). Longerons, gaines et baies vont à
  chaque corps qu'ils traversent ; un longeron dont l'axe est sur une jonction dans la corde est refusé. À une
  jonction dans la corde, les nervures s'arrêtent ≈ 1,5 mm avant la face de chaque côté (paroi verticale), d'où une
  bande sans nervure d'environ 3 mm ; près des bords d'attaque et de fuite, une plaque très courte peut exister
  d'un côté de la jonction et pas de l'autre ;
- longerons et alésages **parallèles à l'axe d'impression**, ou nettement inclinés (au-delà de
  `passages.max_tilt_deg`) : ces derniers sont des **SBAR transversales** (voir plus bas). Un longeron à peine
  incliné (entre la tolérance du trou vertical et `passages.max_tilt_deg`) est refusé comme ambigu ;
- un longeron plus court que la pièce reçoit un trou et une fente limités à sa longueur (débouchant
  à l'emplanture ou au saumon s'il y arrive) ; après la coupe, l'outil vérifie que le longeron ne
  traverse pas la matière.

- L'orientation suit l'axe du **plus long** longeron (`orientation: auto`), ou un axe imposé par la config
  (`orientation.axis`) ;
  l'emplanture se règle avec `orientation.root` (voir « Baies de servo et gaines de câble »).
- `check --orient` sert à vérifier un fichier CAO brut (ex. l'export d'origine) : il l'oriente d'abord selon le longeron.
  Le `.step` généré est déjà orienté : le vérifier avec `check` simple.
- Les alésages existants qui ne logent pas un longeron sont rebouchés (`passages: {mode: fill}`, défaut) ;
  `mode: link` les relie à la peau par une fente (ex. joncs carbone) ; `mode: off` n'y touche pas (le
  rapport les signale alors comme trous isolés). Un alésage à moins de 0,02 mm de la surface, ou incliné,
  ne peut pas être rebouché : la génération s'arrête avec un message.
- Attache du longeron : `spars: {default: {anchor: false}}` la désactive ; `anchor_gap` (défaut 1,0 mm,
  ≥ largeur de ligne) règle le pont laissé contre la peau opposée. Là où la peau opposée est trop proche
  du trou (moins de rayon + anchor_gap + 0,2 mm), l'attache est omise.
- Durée indicative : 1 à 3 minutes pour une aile de 200 × 210 mm (54 s mesurées sur un Mac au repos ;
  2 min 17 s sur le même Mac très chargé) ; variable selon la machine et la complexité.
- `examples/config-exemple.yaml` montre les réglages principaux (le détail complet est dans la spec §10).

### Doubles nervures (réseau de peau)

Par défaut, un seul réseau de nervures traverse la pièce de la peau jusqu'à la bande de séparation.
Pour ajouter un second réseau, fin et peu profond, qui tient la peau entre les nervures principales,
déclarer un second niveau avec une profondeur `depth` en mm (voir `examples/config-doubles-nervures.yaml`) :

```yaml
rib_layers:
  - {name: principal, spacing: 30, depth: full}
  - {name: peau, spacing: 10, depth: 3, phase: 5}
```

```bash
uv run beaver run piece.step -c examples/config-doubles-nervures.yaml --out out
```

- `depth: 3` : la nervure ne descend qu'à 3 mm sous la peau (mesurés selon l'épaisseur de la pièce,
  pas selon la normale à la peau ; un peu moins profond là où la peau est pentue, près du bord d'attaque).
  Là où la pièce est plus mince que `2 × depth + gap` (6,6 mm ici), la nervure va jusqu'à la bande de
  séparation, comme une nervure principale.
- `depth` doit valoir au moins 2 × la largeur de ligne (2 × 0,42 = 0,84 mm en `pla_aero`), sinon les deux murs de la
  nervure se confondent.
- `phase: 5` décale le motif d'un demi-pas : les nervures fines tombent entre les principales (une nervure
  fine qui tomberait exactement sur une principale n'est pas recoupée).
- Manchons, bandes sans nervure et attaches des longerons s'appliquent aux deux réseaux.
- Durée : chaque nervure coûte ~1,4 s de coupe sur une aile CAO réelle ; le réseau de peau à 10 mm
  multiplie le nombre de nervures : 338 au lieu d'une soixantaine sur l'aile de 200 × 210 mm,
  de 6 min 30 à une vingtaine de minutes selon la charge du Mac (contre 1 à 3 min avec le seul réseau principal),
  et un `.step` d'environ 420 Mo (le `.stl` reste à ~4 Mo).

## Projet Bambu (3MF multi-plateaux)

```bash
uv run beaver run piece.step --3mf --out out              # écrit aussi out/<nom>.3mf
uv run beaver run piece.step --open                       # idem, puis ouvre le projet dans Bambu Studio
uv run beaver run piece.step -c examples/config-2-troncons.yaml --3mf --out out
```

- Le `.3mf` part d'un **gabarit** : un projet Bambu Studio enregistré avec imprimante, filament et vase mode
  réglés. Les réglages du gabarit sont recopiés tels quels ; seuls les objets sont remplacés
  (jamais de réglage Bambu reconstruit à la main). Quatre gabarits sont embarqués,
  un par imprimante et matière (`templates/<imprimante>_<matière>.3mf` : `p1s` ou `h2s`, `pla_aero` ou `asa_aero`) :
  le réglage `printer: p1s | h2s` (menu « Imprimante » de la GUI) choisit le gabarit, donc le plateau
  (P1S 256 × 256 × 250, H2S 340 × 320 × 340) et le volume utile. `bambu.template` (chemin) impose le sien et
  l'emporte ; `null` = aucun gabarit.
- **Un plateau par tronçon** (en vase mode, un seul objet par plateau), disposés comme dans Bambu Studio,
  chaque tronçon centré et posé sur son plateau, emplanture en bas.
- Découpe automatique quand la pièce dépasse la hauteur utile = hauteur du gabarit − marge. La découpe
  se fait **après** les nervures : motif et longeron restent continus d'un tronçon à l'autre.
  Réglages `segment` (voir `examples/config-2-troncons.yaml`) :
  - `max_height` : force la hauteur utile (mm) ;
  - `margin` : marge retirée de la hauteur du gabarit (défaut 5 mm) ;
  - `cuts_at: [z, …]` : altitudes de coupe imposées (mm, emplanture = 0) ;
  - `mode: off` : pas de découpe.
- Sorties d'une pièce découpée en plusieurs tronçons : `<nom>-t1.stl`, `<nom>-t2.stl`… (un STL par tronçon,
  emplanture = t1) ; le `.step` contient tous les tronçons ; chaque tronçon est posé à z = 0 ; le `.3mf` a
  un plateau par tronçon. Sans découpe : `<nom>.stl` et `<nom>.step` seuls.
- Si un morceau ne tient pas sur le plateau malgré le plan de découpe (`segment.mode: auto`), la pièce est
  **non imprimable** : erreur de validation « hors plateau » qui nomme le morceau, son emprise et le plateau. Avec
  `segment.mode: off`, une pièce trop grande donne seulement un avertissement. Gabarit introuvable : message
  d'erreur. Les autres anomalies (repli sur la taille de l'imprimante, gabarit d'une autre imprimante que
  `printer`, volume limite) sortent en avertissements, dans l'aperçu comme à la génération.
- Autre gabarit (autre imprimante, autre réglage) : dans Bambu Studio, créer un projet avec la bonne imprimante,
  le bon filament et le vase mode, l'enregistrer en `.3mf`, puis renseigner `bambu: {template: chemin.3mf}`
  dans la config projet (chemin relatif à ce fichier).
- **Limite connue : aucune vérification de slicing automatique.** Le slicing en ligne de commande de
  Bambu Studio plante sur la machine de développement. Ouvrir le projet (`--open`), trancher, et vérifier
  que la spirale est continue (aucune rétraction entre le début et la fin de chaque plateau) ainsi que la
  disposition des plateaux.

## Volume d'impression et découpe

Chaque morceau doit tenir dans le volume de l'imprimante (plateau et hauteur du gabarit, moins
`segment.margin`). L'outil décide seul, **signale chaque découpe automatique** (ligne « ℹ découpe automatique : … »
dans la CLI, encadré « Découpe automatique » dans la GUI, liste dans le rapport HTML) et la **montre dans l'aperçu**
avant toute génération.

- **Imprimante** : `printer: p1s` (défaut, plateau 256 × 256, hauteur 250) ou `h2s` (340 × 320 × 340), menu
  « Imprimante » de la GUI. Elle choisit le gabarit Bambu, donc le volume utile.
- **Ordre des décisions** :
  1. **rotation sur le plateau** : un morceau trop large tel quel est tourné autour de Z (angle de plus grande
     marge) ; la rotation est écrite dans le 3MF, les STL/STEP restent dans le repère de la pièce ;
  2. **coupes en Z** (perpendiculaires à l'envergure) : selon la hauteur utile, puis, si un tronçon ne tient pas
     même tourné (flèche, dièdre), en tronçons plus nombreux ;
  3. **coupe dans la corde** : si une section seule ne tient pas même en diagonale, le tronçon est coupé par un
     ou plusieurs plans qui contiennent l'épaisseur et passent aux points i/k de la corde (au milieu pour 2
     morceaux) à l'emplanture et au saumon du tronçon : ils suivent la flèche et l'effilement ; k est le plus petit
     nombre de morceaux qui tiennent. Le plan est décalé si besoin pour éviter un longeron,
     une gaine (trou + manchon + bande) ou une baie de servo. Les nervures sont générées sur la pièce entière
     **puis** coupées : le motif est continu d'un morceau à l'autre. Chaque morceau est validé séparément.
- **Nommage** : `T1`, `T2`… pour les tronçons en Z (emplanture = T1) ; `A`, `B`… du bord d'attaque au bord de
  fuite pour la coupe dans la corde ; `T1-A`, `T1-B`… avec les deux. Fichiers : `<nom>-t1-a.stl`… ; un plateau par
  morceau dans le 3MF, dans cet ordre.
- **`segment.chord`** (« Coupe dans la corde » dans la GUI) : `auto` (défaut) ou `off`. Avec `off`, un tronçon qui
  ne tient pas sur le plateau arrête la génération avec un message (activer `auto` ou choisir une imprimante plus
  grande). `segment.mode: off` désactive toute découpe (avertissement si la pièce est trop grande).
- **Aperçu** (onglet Aperçu) : vue de dessus avec les coupes en Z en tirets, les coupes dans la corde en trait
  plein, le nom de chaque morceau et les SBAR transversales ; panneau « Découpe » (morceau, étendue, emprise sur le
  plateau, rotation, numéro de plateau) ; vue 3D colorée par morceau avec les plans de coupe. La coupe à hauteur
  réglable montre chaque morceau séparément (comme la validation). L'aperçu et la génération utilisent le même
  plan de découpe.
- **Fichiers à plusieurs corps d'aile** (`WING_1`, `WING_2`… déjà découpés dans la CAO) : un morceau par corps,
  re-découpé seulement s'il ne tient pas (avec une notice). À une jonction **dans la corde**, les nervures
  s'arrêtent avant la face de chaque côté : bande sans nervure d'environ 3 mm.
- **Limites** :
  - l'inclinaison d'un plan de coupe dans la corde (flèche, effilement) doit rester ≤ surplomb max de la matière
    (45° PLA Aero, 40° ASA Aero) : sinon l'outil raccourcit d'abord les tronçons en Z, puis s'arrête avec un
    message (coupes `segment.cuts_at` plus courtes ou imprimante plus grande) ;
  - un longeron dont l'axe tombe sur une jonction dans la corde entre deux corps de la CAO est refusé (déplacer le
    longeron ou la coupe dans la CAO) ; un plan de coupe automatique sans position libre (obstacles partout) aussi ;
  - au plus 6 morceaux en corde par tronçon.

## SBAR transversales

Un longeron dont l'axe s'écarte de l'envergure de plus de `passages.max_tilt_deg` (en corde, ou incliné) est une
**SBAR transversale** : elle n'est plus refusée.

- **Trou en goutte d'eau** : section du trou pointue vers le haut, flancs au surplomb max de la matière, pour que
  son plafond soit imprimable ; jeu `clearance` comme les longerons. Bout fermé dans la pièce : fond et plafond
  imprimables (face verticale au bout haut quand l'axe est trop couché pour une face perpendiculaire).
- **Fentes** : dans chaque couche, le trou doit rejoindre le contour (vase mode). S'il ne débouche pas par la peau
  ou par une face de coupe dans la corde (l'emplanture, le saumon et les coupes en Z ne comptent pas : faces
  horizontales), une fente de `slit_width` le relie à la peau la plus proche, choisie couche par couche.
  L'attache (`anchor`) et le côté de fente (`slit_side`) ne s'appliquent pas : s'ils sont réglés pour cette SBAR,
  un avertissement le dit. Une SBAR qui traverse une baie de servo est signalée
  (rencontre non traitée).
- **Coupe dans la corde** : une SBAR transversale **traverse** le plan de coupe (c'est voulu) ; chaque morceau
  reçoit sa partie du trou, qui débouche sur la face de coupe.
- **Refus** : une SBAR horizontale ouverte aux deux bouts dans les mêmes couches (du bord d'attaque au bord de
  fuite, par exemple) couperait chaque couche en deux : impossible en vase mode. Le message donne la position
  des deux bouts : en fermer un dans la pièce (SBAR plus courte), ou l'incliner pour que ses deux bouches soient à
  des hauteurs différentes. Une paroi plus fine qu'une ligne entre le trou (jeu compris) et la peau,
  ailleurs qu'à une bouche, est aussi refusée (« trop près de la peau » : éloigner la SBAR de la peau ou réduire le
  jeu). À un bout débouchant, le trou s'arrête dès qu'il est sorti de la peau (au plus 10 mm au-delà du bout) :
  il n'entaille pas une zone concave plus loin, et ce qu'il entaille au-delà de sa bouche reste signalé par la
  validation.
- **Envergure** : l'axe d'impression suit le **plus long** longeron. Si une SBAR transversale est plus longue
  que le longeron principal, imposer l'axe avec `orientation.axis` (ex. `orientation: {axis: [0, 0, 1]}`).
- Aperçu : la SBAR apparaît en bande dans la vue de dessus et dans la coupe à hauteur réglable ; rapport :
  « SBAR_x (transversale, Ø…, axe …°) ».

## Profils matière (PLA Aero / ASA Aero)

`material: pla_aero` (défaut) ou `asa_aero` charge un **profil matière** : un fichier YAML qui regroupe les
paramètres géométriques de la matière (largeur de ligne, hauteur de couche, densité, seuils de fente,
épaisseur des nervures, bande de séparation, réseau de nervures, longerons, alésages) et le **gabarit
Bambu** (`bambu.template`). Ordre de priorité : défauts du code < profil < config projet (`-c`) < options CLI.
Une clé posée dans la config de la pièce l'emporte sur le profil : `examples/config-exemple.yaml` ne pose
activement aucune clé du profil (tout y est commenté), sinon il écraserait le réglage matière.

Où ils vivent (priorité croissante) :

1. profils **embarqués** dans le paquet (`pla_aero`, `asa_aero`) ;
2. dossier `./profiles/` du répertoire de travail ;
3. dossier désigné par la variable d'environnement `BEAVER_PROFILES`.

Un profil utilisateur est **fusionné sur** le profil embarqué de même nom : n'y écrire que ce qui change
(un profil qui ne contient que `material_settings: {line_width: 0.45}` suffit). Sans équivalent embarqué,
c'est un nouveau profil, utilisé seul.

```bash
uv run beaver profils                    # liste les profils (origine, fichiers, gabarit)
uv run beaver profils --copier asa_aero  # copie le profil embarqué dans ./profiles/ pour l'éditer
```

L'**ASA Aero** a ses gabarits Bambu embarqués (`p1s_asa_aero.3mf`, `h2s_asa_aero.3mf`, filament Bambu ASA-Aero). Ce gabarit
n'est pas en vase mode : à l'écriture du `.3mf`, BEAVER **impose les réglages vase mode** (`spiral_mode`,
`wall_loops`, `top_shell_layers`, densités de remplissage…) et affiche un avertissement ; les réglages filament
et imprimante restent ceux de l'ASA. Pour un gabarit déjà en vase mode (PLA Aero), le fichier est recopié à
l'identique.

## Interface graphique

Une interface web **locale** pilote le même cœur que la ligne de commande (aucune géométrie propre à la GUI).

```bash
uv run beaver gui                                  # projets dans ./projets, port libre, ouvre le navigateur
uv run beaver gui --dossier projets --port 0 --sans-navigateur
```

La commande affiche `BEAVER : interface disponible sur http://127.0.0.1:<port>/?token=<jeton>`. Sans le jeton
de l'URL, l'API répond 401. **Double-clic** : `lancer-beaver.command` (macOS) ou `lancer-beaver.bat` (Windows),
à la racine du dépôt (`uv` doit être installé). `Ctrl+C` dans le terminal arrête le serveur et annule la
génération en cours.

Onglets :

- **Projets** : importer un STEP, voir les corps trouvés et leurs rôles.
- **Réglages** : formulaire des paramètres (valeurs héritées du profil et valeurs surchargées), aperçu en direct à côté.
- **Aperçu** : section à une hauteur réglable, vue de dessus, chiffres estimés (plaques, masse, durée), 3D approchée.
- **Générer** : progression en direct, annulation, résumé, liens vers les fichiers, « Ouvrir dans Bambu ».
- **Historique** : liste des essais, comparaison des réglages de deux essais.
- **Profils** : édition de PLA Aero / ASA Aero. Les fichiers utilisateur vont dans `./profiles` ou `BEAVER_PROFILES` ;
  seules les valeurs qui diffèrent sont écrites. Le gabarit Bambu est affiché en lecture seule. Enregistrer un profil
  depuis la GUI **perd les commentaires** de son fichier YAML.

Données sur disque :

```
projets/<nom>/piece.step
projets/<nom>/config.yaml
projets/<nom>/essais/<AAAAMMJJ-HHMMSS>/   # config résolue, resume.json, .step, .stl, .3mf, rapport
```

Sécurité : le serveur n'écoute que sur `127.0.0.1`, exige un jeton aléatoire par lancement, confine les chemins au
dossier des projets et limite l'import à 200 Mo. Rien n'est envoyé hors de la machine ; la seule requête externe est le
chargement de three.js (jsDelivr) pour la 3D, qui demande **Internet au premier chargement**.

Limites connues :

- l'aperçu est une **approximation** (calcul 2D, sans booléens 3D) ; la génération reste la référence ;
- l'estimation de durée (0,3 s par plaque) est calée sur une aile simple, machine libre : elle sous-estime avec des doubles nervures ou une machine chargée ;
- la vue 3D montre les plaques **non découpées** : elles dépassent de la peau ;
- une seule génération à la fois ;

## Nommage dans la CAO

| Préfixe | Rôle |
|---|---|
| `SKIN`, `Wing`, `Fuse`, `Aile`, `Peau` | corps à traiter |
| `SPAR`, `SBAR`, `Longeron` | longeron (diamètre et axe détectés) |
| `SPLIT` | surface de séparation manuelle |
| `NORIB` | zone sans nervures |
| `CUT` | plan de découpe forcé |
| `SERVO` | baie de servo (boîte alignée = espace utile) |
| `SBAR…CABLE…` | gaine de câble : longeron dont le nom propre contient `CABLE` (`naming.cable_markers`) |

Insensible à la casse. Repli possible dans la config : `roles: {Body3: spar}`. `roles:` est un **repli** :
le nom propre du corps est examiné d'abord, puis ses composants parents ; un rôle posé sur un composant parent
(ex. `roles: {Aile_v2: skin}`) ne remplace donc pas le préfixe propre d'un corps qu'il contient (`SERVO_1`
reste une baie). Pour forcer le rôle d'un corps précis, utiliser son propre nom.

## Baies de servo et gaines de câble

Nommer dans la CAO :

- **`SERVO…`** : une boîte (6 faces planes) **alignée** sur la corde et l'envergure, qui représente l'espace
  utile du servo (jeu compris) et dépasse de la peau du côté de l'ouverture ;
- **`SBAR…CABLE…`** (ex. `SBAR_CABLE_SERVO`) : un cylindre parallèle à l'envergure pour le passage des
  câbles ; il peut déboucher dans la baie.

Comportement :

- **Côté d'ouverture automatique** : extrados si la boîte dépasse en +Y, intrados en −Y. Erreur en français si
  la boîte ne traverse aucune peau, traverse les deux, ou si son fond laisse moins de `servo.min_floor_gap`
  (1,0 mm) avant la peau opposée (renfort en plus).
- **Ouverture allongée par deux rampes** : aux deux bouts de la boîte en envergure, le fond remonte vers la peau
  avec une pente égale au surplomb max de la matière (45° PLA Aero, 40° ASA Aero) : rien n'est imprimé dans le
  vide. L'ouverture dans la peau est donc plus longue que la boîte de 2 × profondeur / tan(surplomb)
  (`baies : SERVO_1 (baie extrados, … × … mm utiles, ouverture …, profondeur …, renfort)`).
- **Renfort** (`servo.reinforce: true`, défaut) : une fente de 0,15 mm (`servo.slit_width`) parallèle aux
  parois, au fond et aux rampes de la poche double la paroi (`servo.reinforce_offset` = épaisseur de la paroi
  doublée, défaut 1,2 × largeur de ligne). Dans chaque couche, elle forme **deux fentes en J séparées par un pont
  de matière** : un U ouvert sur la peau aux deux bords isolerait la paroi doublée du reste de la pièce (deux
  contours dans la couche, interdit en vase mode). Le pont suit le point haut de la peau dans les rampes et
  contourne les trous qui traversent la paroi ; si un trou supprime une branche, un avertissement le signale
  (« renfort incomplet »). Quand la pièce est découpée, le rapport indique le ou les tronçons de chaque baie.
- **Nervures soudées à la baie** : `servo.strip` est la matière laissée entre le bout d'une fente de nervure et
  la baie (bord extérieur de la fente de renfort, ou paroi de la poche sans renfort, et trous qui débouchent
  dans la poche) ; défaut 1,2 × largeur de ligne (≈ 0,5 mm, ≥ largeur de ligne exigée) : le slicer garde ce col
  et la paroi de la nervure se soude à la baie. Au bout des rampes, les nervures reprennent dès que la rampe sort
  de la peau ; autour des ponts du renfort, elles gardent 2 mm. Longerons et gaines qui
  entrent dans la boîte sont simplement découpés par la poche.
- **Emplanture** : `orientation.root: auto` (défaut) place l'emplanture au bout de l'aile que touche une gaine
  de câble (sinon : plus grande section, comme avant) ; `debut` / `fin` l'imposent (coordonnée min. / max. le
  long de l'axe d'envergure dans le repère d'origine, quel que soit le sens donné à `axis`). Dans la config : `orientation: {root: fin}` (avec ou sans `axis`).
- **Gaine allégée** : trou + jeu, fente vers la peau, **sans attache**, manchon réduit à 1,2 × largeur de ligne
  (≈ 0,5 mm ; 0 serait refusé par la validation), bande sans nervure = largeur de ligne ; réglages communs
  dans `spars: {cable: {…}}`.
- L'aperçu de l'interface montre la poche et le renfort dans la coupe, et dans la vue de dessus l'ouverture
  (rampes plus claires) et la boîte utile ; le rapport HTML et l'essai listent baies et gaines.

Limites :

- boîte **alignée** sur la corde et l'envergure (2° de tolérance) ; une baie inclinée est refusée ;
- la profondeur et le pont supposent une peau d'ouverture avec **un seul point haut** le long de la corde (cas
  d'un profil d'aile) ;
- pas de fixation du servo (pattes, inserts, trappe) pour l'instant.

## Tests

```bash
uv run pytest            # tous les tests
uv run pytest -m "not slow"
uv run pytest tests/test_gui_e2e.py -m slow   # bout en bout GUI sur l'aile Fusion v2 (~40 s)
```
