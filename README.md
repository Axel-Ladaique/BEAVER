# AeroRib

Transforme une pièce CAO (STEP) en pièce imprimable en **vase mode** sur Bambu Lab :
nervures internes, logements de longerons, découpe en tronçons.
Voir la spec : `docs/specs/2026-10-09-aerorib-design.md`.

## Installation

1. Installer [uv](https://docs.astral.sh/uv/) : `brew install uv` (macOS) ou `winget install astral-sh.uv` (Windows).
2. Dans ce dossier : `uv sync`

## Analyser et vérifier une pièce

```bash
uv run aerorib inspect piece.step              # corps trouvés et rôles
uv run aerorib check piece.step                # imprimable en vase mode ?
uv run aerorib check piece.step --original origine.step --html rapport.html
```

## Générer les nervures

```bash
uv run aerorib run piece.step -c examples/config-exemple.yaml --out out
uv run aerorib check piece.step --orient     # oriente la pièce selon le longeron avant de la vérifier
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
non uniques, la **matière plus fine qu'une largeur de ligne** (0,45 mm) et les fentes que le slicer
refermerait (< 0,10 mm).

Limites de la phase P1 :

- **une seule peau par fichier** : exporter chaque aile dans son propre STEP ;
- longerons et alésages **parallèles à l'axe d'impression** : un longeron incliné (même de 1°) est
  refusé, car le trou vertical ne le laisserait pas entrer ;
- un longeron plus court que la pièce reçoit un trou et une fente limités à sa longueur (débouchant
  à l'emplanture ou au saumon s'il y arrive) ; après la coupe, l'outil vérifie que le longeron ne
  traverse pas la matière.

- L'orientation suit l'axe du premier longeron détecté (`orientation: auto`), ou un axe imposé par la config.
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
uv run aerorib run piece.step -c examples/config-doubles-nervures.yaml --out out
```

- `depth: 3` : la nervure ne descend qu'à 3 mm sous la peau (mesurés selon l'épaisseur de la pièce,
  pas selon la normale à la peau : écart négligeable sur un profil d'aile). Là où la pièce est plus mince
  que 2 × 3 mm, la nervure va jusqu'à la bande de séparation, comme une nervure principale.
- `depth` doit valoir au moins 2 × la largeur de ligne (0,9 mm en `pla_aero`), sinon les deux murs de la
  nervure se confondent.
- `phase: 5` décale le motif d'un demi-pas : les nervures fines tombent entre les principales.
- Manchons, bandes sans nervure et attaches des longerons s'appliquent aux deux réseaux.
- Durée : chaque nervure coûte ~1,4 s de coupe sur une aile CAO réelle ; le réseau de peau à 10 mm
  multiplie le nombre de nervures : 338 au lieu d'une soixantaine sur l'aile de 200 × 210 mm,
  6 min 30 à 10 min au total selon la charge du Mac (contre 1 à 3 min avec le seul réseau principal),
  et un `.step` d'environ 420 Mo (le `.stl` reste à ~4 Mo).

## Nommage dans la CAO

| Préfixe | Rôle |
|---|---|
| `SKIN`, `Wing`, `Fuse`, `Aile`, `Peau` | corps à traiter |
| `SPAR`, `SBAR`, `Longeron` | longeron (diamètre et axe détectés) |
| `SPLIT` | surface de séparation manuelle |
| `NORIB` | zone sans nervures |
| `CUT` | plan de découpe forcé |

Insensible à la casse. Repli possible dans la config : `roles: {Body3: spar}`.

## Tests

```bash
uv run pytest            # tous les tests
uv run pytest -m "not slow"
```
