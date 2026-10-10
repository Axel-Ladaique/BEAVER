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
logements de longerons et les alésages existants. Dans le dossier `--out` :

- `<nom>.step` : pièce nervurée, dans le repère d'impression (Z vers le haut, emplanture au plateau) ;
- `<nom>.stl` ;
- `<nom>-rapport.html` : rapport vase mode (couches en défaut s'il y en a).

La commande retourne le code d'erreur 1 quand le résultat n'est pas imprimable ; les fichiers sont écrits
quand même, pour inspection. Elle affiche aussi la liste des trous reliés à la peau
(`trous reliés : SBAR_14mm_1 (longeron, Ø14.3) ; passage 1 (Ø2.4, x=…, y=…) ; …`, repère d'impression).

Défauts principaux : jeu du longeron 0,15 mm, manchon sans nervure 0,7 mm autour de chaque trou,
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
- Les alésages existants (ex. joncs carbone) sont reliés automatiquement à la peau par une fente ;
  désactivable avec `passages: {mode: off}`.
- Durée indicative : 1 à 3 minutes pour une aile de 200 × 210 mm (54 s mesurées sur un Mac au repos ;
  2 min 17 s sur le même Mac très chargé) ; variable selon la machine et la complexité.
- `examples/config-exemple.yaml` montre les réglages principaux (le détail complet est dans la spec §10).

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
