# AeroRib

Transforme une pièce CAO (STEP) en pièce imprimable en **vase mode** sur Bambu Lab :
nervures internes, logements de longerons, découpe en tronçons.
Voir la spec : `docs/specs/2026-10-09-aerorib-design.md`.

## Installation

1. Installer [uv](https://docs.astral.sh/uv/) : `brew install uv` (macOS) ou `winget install astral-sh.uv` (Windows).
2. Dans ce dossier : `uv sync`

## Usage (P0)

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
(motif en losange), fentes de 0,1 mm vers les logements de longerons, et exporte STEP et STL
dans le dossier `--out`. Un rapport de validation vase mode est calculé sur le résultat.

- L'orientation suit l'axe du premier longeron détecté (`orientation: auto`), ou un axe imposé par la config.
- Les alésages existants (ex. joncs carbone) sont reliés automatiquement à la peau par une fente ;
  désactivable avec `passages: {mode: off}`.
- Durée indicative : environ 3 à 4 minutes pour une aile de 200 × 210 mm.
- `examples/config-exemple.yaml` liste toutes les clés avec leurs valeurs par défaut.

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
