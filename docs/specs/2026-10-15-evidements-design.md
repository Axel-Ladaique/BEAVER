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
- `every` (mode `croisements`, défaut **3**, ajouté le 2026-10-21 — voir « Révision v2 ») : pont à un rang de croisements sur `every` ; 1 = à chaque croisement (comportement d'origine).
- `bridge_width` (mm, défaut 4.0) : largeur pleine hauteur d'un pont (mesurée le long de la plaque, en abscisse s du plan de la plaque ; soit bridge_width·cos(angle) en z d'impression).
- `pitch` (mm, mode `pas`, défaut 30.0) : distance entre ponts.
- `keep_ratio` (défaut 0.2, 0.3 avant la révision v2) : hauteur de nervure gardée sous la peau = `keep_ratio × demi-épaisseur locale`, au moins `keep_min` (défaut 2.0 mm), au plus la hauteur pleine.
- Pente des flancs : `max_overhang_deg` de la matière, multipliée par `ramp_factor` (≤ 1, défaut 1) pour des rampes plus douces.

### Géométrie (dans `features/ribs.py`, profils 2D (s, y) des plaques)
- Pour chaque plaque, positions des ponts le long de s :
  - `croisements` : (un rang sur `every`, voir « Révision v2 ») abscisses s des croisements avec les plaques de l'autre famille (même couche de nervures), qui sont régulières (pas = spacing / (2·sin(angle)) le long de la plaque, c.-à-d. spacing / (2·tan(angle)) en z ; décalage selon `phase` et la boîte de calage) ;
  - `pas` : s = s0 + k·pitch (ancré sur la boîte globale comme le motif, pour un rendu régulier entre morceaux).
- Bord intérieur de la plaque (côté bande) : pleine hauteur sur [s_pont ± bridge_width/2], profondeur évidée ailleurs, raccords en rampe à l'angle de surplomb (en z d'impression).
- Les profils restent des polygones simples (pas de trou) ⇒ zones, ombres, découpe, aperçu inchangés dans leur principe ; preview = génération (même fonction).
- Couches peu profondes (`depth` en mm) : évidement seulement si la couche descend plus bas que la profondeur évidée.

### Rapport / aperçu
- Masse et trajet estimés reflètent l'évidement ; la coupe montre les nervures raccourcies ; la vue 3D montre les plaques échancrées.
- Rapport : ligne « évidements : croisements, ponts 4 mm, 30 % » + gain de masse estimé par rapport à sans évidement (calcul aperçu).

## Révision v2 (2026-10-21) : un pont tous les N croisements
**Constat** (aile Dotitos, 850 → 256 mm de corde, ~95 mm d'épaisseur à l'emplanture, H2S, PLA aéro) : pièce ≈ 726 g dont
494 g de nervures ; `croisements` d'origine (ponts 4 mm, 30 %, 2 mm) ne retirait que 84 g (17 % des nervures, ~12 % de la
pièce). Cause : en montant, le fond de fente ne peut avancer vers la bande que de tan(surplomb)·d1 ≈ 0,71 mm par mm de s
(45°, plaque à 45°) ; une échancrure de profondeur D coûte donc D/0,71 mm de rampe, alors que les croisements sont tous
les spacing/(2·sin angle) ≈ 21 mm le long de la plaque : sur une aile épaisse, il ne reste que des triangles de ~20 mm.
Baisser `keep_ratio` n'y change presque rien (0,1 : −88 g) ; seul l'écart entre ponts compte. Un trou fermé dans la
nervure ou une échancrure côté peau restent impossibles en vase mode : le profil en dents de scie (marche verticale
au-dessus du pont, rampe au surplomb max avant le pont suivant) est déjà l'évidement maximal pour des ponts donnés.

**Choix de l'utilisateur** : option « 1 croisement sur N » (parmi : réglages seuls en mode `pas` ; 1 sur N ; 1 sur N + rampes
plus raides / ponts 2 mm), gain visé ≈ −30 % de la pièce ; **intouchables** : surplomb max 45° des rampes, garde mini ≥ 2 mm.

**Conception** : `every` = N ; croisements de rang j ≡ 0 (mod N), j = (s − s_ref)·2·|d0| / spacing, compté depuis le bas de
la boîte de calage du motif. Les croisements de rang j sont tous dans le plan z = z_calage + j·spacing/(2·tan angle) : les
nœuds gardés forment des rangées en z communes aux deux familles et à tous les corps (les deux plaques d'un nœud y ont leur
pont, structure en treillis conservée). Défauts : `every` 3, `keep_ratio` 0,2 ; `mode` reste `off` par défaut.

**Croisements sans pont** (constaté à la première génération complète de Dotitos : morceau T1-C non imprimable,
« couche vide » z = 150–174 mm, STL non étanche) : l'autre plaque passe au même point (x, z), donc à la même épaisseur ;
une borne d'évidement fixée par le gain (≥ 1 mm, près du bord de fuite) qui y tombe pour l'une y tombe pour l'autre, au
même milieu d'échantillons. Les marches des deux plaques sont alors au même point 3D, et le booléen laisse des micro-faces
(arête nulle, 0,02 mm²) que le maillage ne triangule pas. Règle : aucune borne d'évidement à moins de bridge_width/2 d'un
croisement sans pont (ramenée à bridge_width/2, côté évidement, comme les marches d'un pont) ; vaut aussi pour le mode
`pas`. Les marches peuvent donc tomber à ± bridge_width·d1/2 de chaque rangée de croisements : plans critiques de la
découpe en Z (`rib_crossings`), à chaque rangée, plus les ponts du mode `pas`.

**Mesures Dotitos** (masse des nervures, profils 2D ∩ épaisseur, formule de `hollow_saving_g`) :

| réglage | nervures | gain |
|---|---|---|
| sans évidement | 494 g | — |
| croisements N = 1, 30 %, 2 mm (ancien défaut) | 410 g | −84 g |
| N = 2, 20 %, 2 mm | 327 g | −168 g |
| **N = 3, 20 %, 2 mm (nouveau défaut)** | **278 g** | **−216 g (≈ −30 % de la pièce)** |
| N = 3, 10 %, 2 mm | 268 g | −227 g |
| N = 4, 20 %, 2 mm | 250 g | −245 g |
| pas 100 mm, 10 %, 1 mm (non retenu : ponts hors des nœuds, garde < 2 mm) | 217 g | −278 g |

## Hors périmètre
Trous dans la peau, évidements des longerons, évidements de fuselage.
