# Baies de servo et gaines de câble — conception

- **Date :** 2026-10-11 · **Statut :** choix validés par l'utilisateur (ci-dessous), détails techniques décidés en autonomie (nuit).
- **Complète :** spec principale, addendum P1 (orientation, alésages, attache), spec GUI.

## Choix de l'utilisateur
- Une baie de servo est décrite dans la CAO par un **corps nommé `SERVO…`** (boîte = espace utile du servo + jeu). Elle peut s'ouvrir sur **l'extrados ou l'intrados**.
- Le **passage de câble** est un corps `SBAR…` dont le nom contient **`CABLE`** ; traité en **gaine allégée** (trou + fente vers la peau, **sans attache**, manchon réduit). Si le résultat déçoit, on reviendra au traitement longeron.
- **Rampes à 45°** (surplomb max de la matière) aux deux bouts de la poche dans le sens de l'envergure ; **l'ouverture dans la peau est allongée** de la longueur des rampes (la boîte reste l'espace utile).
- **Emplanture** : automatique (bout où sortent les gaines de câble ; sinon règle actuelle) + réglage `orientation.root: auto | debut | fin`.
- **Renfort** autour de la poche (paroi doublée par une fente parallèle) **activé par défaut**, désactivable.
- Fixation du servo (inserts) : plus tard, hors périmètre.
- Fichiers de test fournis : `tests/fixtures/aile_4412_simple_sbar_v3.step`, `aile_4412_double_sbar.step`, `aile_4412_servo_extrados.step` (servo `SERVO_1` 32×20×46,5 mm ouvert sur l'extrados, gaine `SBAR_CABLES_SERVO` Ø11,5 de la baie jusqu'à l'emplanture).

## Constat sur les fichiers fournis (état actuel)
- Simple et double SBAR : ✔ imprimables (84 et 98 nervures).
- Servo : `SERVO_1` est classé **peau** (repli sur le nom du composant « Aile… ») → « plusieurs peaux ». Les noms `SBAR_2` / `SBAR_CABLES_SERVO` sont **ambigus** (même nombre de faces) : la correspondance nom ↔ solide peut s'inverser.
- L'emplanture choisie automatiquement est au bout opposé à la sortie de la gaine (aile rectangulaire : sections égales).

## Conception

### 1. Noms et rôles
- Correspondance nom ↔ solide fiable : en plus du nombre de faces, signature géométrique lue dans le texte STEP (types de surfaces et rayons des `CYLINDRICAL_SURFACE`, ordre des entités) comparée aux faces OCC ; l'avertissement « nom ambigu » ne subsiste que si la signature ne départage pas.
- Nouveau rôle **`servo`** (préfixes par défaut `SERVO`), prioritaire sur le repli « peau » par nom de composant. Un corps dont **seul un niveau parent** correspond à un préfixe de peau ne doit plus être classé peau s'il porte lui‑même un préfixe connu.
- Gaine : un `spar` dont le nom contient `CABLE` (insensible à la casse ; liste configurable `naming.cable_markers`) est une **gaine**.

### 2. Emplanture
- `orientation.root: auto | debut | fin` (`debut` = extrémité de coordonnée minimale le long de l'axe d'envergure du modèle d'origine, quel que soit le sens de l'axe : sens fixé par sa composante dominante).
- `auto` : s'il existe une gaine qui touche une extrémité de l'envergure (à la tolérance près), cette extrémité est l'emplanture ; sinon règle actuelle (plus grande section). La source est rapportée (`orientation : … emplanture côté gaine SBAR_CABLES_SERVO`).

### 3. Gaine allégée
- Trou de rayon r + jeu, **fente** vers la peau (côté `slit_side` par défaut), **sans attache**, manchon réduit à 1,2 × largeur de ligne (≈ 0,5 mm ; la validation refuse 0 : paroi trou / vide de nervure < largeur de ligne), bande sans nervure réduite (`spars.cable.strip`, défaut = largeur de ligne). Étendue en envergure limitée au corps (comme les longerons). Elle peut déboucher dans une baie de servo.

### 4. Baie de servo
- **Côté d'ouverture** : la face de peau que la boîte traverse (extrados si la boîte dépasse en +Y, intrados en −Y). Erreur française si la boîte ne traverse aucune peau, traverse les deux, ou si son fond laisse moins de `servo.min_floor_gap` (défaut 1,0 mm) avant la peau opposée.
- **Poche** = boîte prolongée vers l'extérieur au‑delà de la peau, et dans l'envergure par deux **rampes** : le fond remonte vers la peau avec une pente telle que l'angle de surplomb ≤ `max_overhang_deg` de la matière ; longueur d'une rampe Δ = profondeur / tan(max_overhang) où profondeur = écart maximal entre le fond et la peau d'ouverture sur l'étendue en corde de la boîte. Les parois latérales (en corde) restent parallèles à l'envergure.
- Dans chaque couche, la poche est une **encoche** du contour : un seul contour, valide en vase mode.
- **Renfort** (`servo.reinforce: true`, défaut) : une fente de largeur `servo.slit_width` (défaut 0,15 mm : fente verticale, vue telle quelle en tranche) parallèle aux parois et au fond de la poche (rampes comprises), à `servo.reinforce_offset` de la poche ⇒ paroi doublée. `reinforce_offset` = épaisseur de la paroi doublée (face de poche → bord proche de la fente), défaut 1,2 × largeur de ligne, refusé si < largeur de ligne ; `min_floor_gap` compte en plus la paroi + la fente sous le fond.
  - Dans chaque couche, la fente forme **deux J** ouverts sur la peau, séparés par **un pont** de matière (un U débouchant aux deux bords isolerait la paroi doublée ; deux ponts laisseraient un tronçon de fente fermé). Le pont est choisi **par tranche de z** : dans les rampes, autour du point haut de la peau d'ouverture (peau unimodale le long de la corde) ; là où un trou rencontre la poche ou la fente, ou là où la fente d'un trou traverse la paroi doublée, autour du trou / de sa fente ; ailleurs, pont minimal (max(1 mm, 2 largeurs de ligne)) au point haut. Ponts bornés à la boîte ± renfort ; si un trou supprime une branche entière, avertissement « renfort incomplet ».
  - Bornes des tranches calées sur zmin + k·hauteur de couche (mi-chemin entre deux couches) ; étendues des trous arrondies vers l'extérieur.
- **Trous qui débouchent dans la poche** : là où ils la rejoignent, un anneau sans nervure de rayon r + `servo.strip` les entoure (les nervures s'y soudent comme à la baie) ; leur rencontre avec la poche est retirée de la référence « peau entamée » de la validation.
- **Nervures soudées à la baie** : la poche (et son renfort) agrandie de `servo.strip` est une zone interdite pour les plaques, avec la même « ombre » vers la bande centrale que les alésages (pas de fragment isolé). `servo.strip` = **matière laissée entre le bout d'une fente de nervure et la baie** : bord extérieur de la fente de renfort (paroi de poche sans renfort) et trous qui débouchent dans la poche. Défaut 1,2 × largeur de ligne (≈ 0,5 mm), refusé si < largeur de ligne : le slicer garde ce col, et le passage aller-retour de la buse le long des deux fentes le remplit, ce qui soude la paroi de la nervure à la paroi doublée (2,0 mm auparavant : nervures arrêtées avant la baie).
  - Le long d'une plaque, la zone s'arrête là où la rampe (agrandie) sort de la peau d'ouverture **à cet endroit** (+ 0,5 mm), et non de la peau la plus haute autour de la baie : les nervures reprennent au bout des rampes.
  - **Ponts** : autour de chaque pont de la fente de renfort (tranche de z, étendue en x ± 2 mm, sans déborder en x de la zone de la poche), les nervures gardent une bande de 2 mm (`BRIDGE_STRIP`) : le pont est le seul passage de la paroi doublée vers le reste de la couche, et une fente de nervure en pointe oblique (coupée par l'anneau d'un trou) juste sous le bout d'une branche en J le pincerait (contrôle « matière trop fine »).
  - Limite : dans une rampe, le bout d'une nervure est vertical (selon l'épaisseur) et la rampe inclinée ; la soudure se fait près de la peau, l'écart grandit vers la bande centrale (une fente de nervure qui s'arrêterait sous la rampe sans la toucher serait fermée dans sa couche).
- Les gaines et longerons qui entrent dans la boîte sont simplement découpés par la poche.
- Le rapport liste les baies (nom, côté, dimensions utiles, longueur d'ouverture, profondeur, renfort, tronçon(s) si la pièce est découpée).

### 5. Aperçu et GUI
- `aerorib.preview` : la poche (section de la boîte avec rampes, renfort) entre dans la coupe et dans les zones des plaques ⇒ **comptes de plaques identiques** à la génération ; vue de dessus : rectangle d'ouverture + rampes ; gaines dessinées comme les trous.
- Réglages : sections `servo` et `orientation.root` générées depuis le schéma (libellés français).

## Hors périmètre
Inserts / pattes de fixation, trappes, cadres imprimés à part, servos dans un fuselage.
