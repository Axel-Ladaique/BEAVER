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

`run` produit, pour chaque corps `SKIN`/`Aile`/…, une pièce avec nervures internes en diagonale
(motif en losange) et des fentes de 0,1 mm vers les logements de longerons. Dans le dossier `--out` :

- `<nom>.step` : pièce nervurée, dans le repère d'impression (Z vers le haut, emplanture au plateau) ;
- `<nom>.stl` ;
- `<nom>-rapport.html` : rapport vase mode (couches en défaut s'il y en a).

Un STL et un rapport par corps s'il y en a plusieurs. La commande retourne le code d'erreur 1 quand le
résultat n'est pas imprimable ; les fichiers sont écrits quand même, pour inspection.

- L'orientation suit l'axe du premier longeron détecté (`orientation: auto`), ou un axe imposé par la config.
- `check --orient` sert à vérifier un fichier CAO brut (ex. l'export d'origine) : il l'oriente d'abord selon le longeron.
  Le `.step` généré est déjà orienté : le vérifier avec `check` simple.
- Les alésages existants (ex. joncs carbone) sont reliés automatiquement à la peau par une fente ;
  désactivable avec `passages: {mode: off}`.
- Durée indicative : environ 1 minute (mesuré 54 s sur un Mac) pour une aile de 200 × 210 mm ;
  variable selon la machine et la complexité.
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
