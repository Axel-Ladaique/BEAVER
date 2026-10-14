# Évidements des nervures — conception

- **Date :** 2026-10-15 · **Statut :** choix validés par l'utilisateur (ci-dessous), détails techniques décidés en autonomie (nuit).
- **Complète :** spec principale (nervures, bande centrale), addendum P1 (profils 2D des plaques, « marche, pas rampe »), spec servo, spec découpe.

## Besoin
Les nervures principales (moitié extrados et moitié intrados, séparées par la bande centrale) n'ont pas besoin de descendre jusqu'à la bande sur toute leur longueur : on les **évide** et on ne garde la pleine hauteur qu'à des **ponts**. Gain de masse et de trajet au milieu de l'aile.

## Choix de l'utilisateur
- Placement des ponts : **les deux modes, réglables** — `croisements` (ponts aux nœuds du losange, défaut) et `pas` (ponts à pas régulier le long de la nervure).
- Hauteur gardée sous la peau dans les zones évidées : **proportion de la demi-épaisseur locale**, avec un minimum en mm.
- Largeur des ponts : **≈ 4 mm**, réglable.

## Contraintes vase mode (imposées)
- Une paroi de nervure = deux bords d'une fente **reliée à la peau**. Un évidement ne peut pas être un trou fermé dans la nervure (fente isolée) : c'est une **échancrure côté bande centrale** — la nervure y est moins haute.
- Les flancs d'une échancrure sont **en pente** : dans chaque couche, le fond de fente ne doit pas avancer vers la bande de plus que `tan(max_overhang) × hauteur de couche` d'une couche à l'autre (45° PLA, 40° ASA). Les flancs sont donc des rampes à l'angle de surplomb, mesurées dans le plan de la plaque **en z d'impression** (la plaque est inclinée : s ↔ z via d1).
- Cette rampe est l'exception voulue à la règle « marche, pas rampe » (qui protège la bande centrale) : ici la rampe s'arrête à la profondeur évidée, loin de la bande ; aux ponts, la nervure rejoint la bande par une **marche verticale** comme aujourd'hui. Vérifier que la bande n'est pas pincée (validation : largeur de matière).
- Zones toujours **pleines** (pas d'évidement) : autour des longerons/gaines/SBAR transversales (zone + bande), des baies servo (zone + bande), près des coupes en Z et dans la corde (± une largeur de pont), et là où la nervure est déjà peu profonde (évidement < 1 mm inutile).

## Conception
### Réglages (`ribs_hollow`, par couche de nervures ou global — global par défaut, surcharge possible par couche)
- `mode: off | croisements | pas` — défaut **`off`** (aucune pièce existante ne change ; l'utilisateur l'active dans Réglages ou la config, `croisements` recommandé).
- `bridge_width` (mm, défaut 4.0) : largeur pleine hauteur d'un pont (mesurée le long de la plaque, en abscisse s du plan de la plaque ; soit bridge_width·cos(angle) en z d'impression).
- `pitch` (mm, mode `pas`, défaut 30.0) : distance entre ponts.
- `keep_ratio` (défaut 0.3) : hauteur de nervure gardée sous la peau = `keep_ratio × demi-épaisseur locale`, au moins `keep_min` (défaut 2.0 mm), au plus la hauteur pleine.
- Pente des flancs : `max_overhang_deg` de la matière, multipliée par `ramp_factor` (≤ 1, défaut 1) pour des rampes plus douces.

### Géométrie (dans `features/ribs.py`, profils 2D (s, y) des plaques)
- Pour chaque plaque, positions des ponts le long de s :
  - `croisements` : abscisses s des croisements avec les plaques de l'autre famille (même couche de nervures), qui sont régulières (pas = spacing / (2·sin(angle)) le long de la plaque, c.-à-d. spacing / (2·tan(angle)) en z ; décalage selon `phase` et la boîte de calage) ;
  - `pas` : s = s0 + k·pitch (ancré sur la boîte globale comme le motif, pour un rendu régulier entre morceaux).
- Bord intérieur de la plaque (côté bande) : pleine hauteur sur [s_pont ± bridge_width/2], profondeur évidée ailleurs, raccords en rampe à l'angle de surplomb (en z d'impression).
- Les profils restent des polygones simples (pas de trou) ⇒ zones, ombres, découpe, aperçu inchangés dans leur principe ; preview = génération (même fonction).
- Couches peu profondes (`depth` en mm) : évidement seulement si la couche descend plus bas que la profondeur évidée.

### Rapport / aperçu
- Masse et trajet estimés reflètent l'évidement ; la coupe montre les nervures raccourcies ; la vue 3D montre les plaques échancrées.
- Rapport : ligne « évidements : croisements, ponts 4 mm, 30 % » + gain de masse estimé par rapport à sans évidement (calcul aperçu).

## Hors périmètre
Trous dans la peau, évidements des longerons, évidements de fuselage.
