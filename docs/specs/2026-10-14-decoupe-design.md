# Découpe pour le volume d'impression, imprimantes, SBAR transversales — conception

- **Date :** 2026-10-14 · **Statut :** choix validés par l'utilisateur (ci-dessous), détails techniques décidés en autonomie (nuit).
- **Complète :** spec principale (tronçons, Bambu), spec servo, spec GUI.

## Choix de l'utilisateur
- On **choisit l'imprimante** : taille **P1S** ou taille **H2S**. Gabarits Bambu fournis : `p1s_pla_aero`, `p1s_asa_aero`, `h2s_pla_aero`, `h2s_asa_aero` (H2S : buse 0,4, vase mode, 340 × 320 × 340 mm).
- Ordre de découpe : **(1)** pièce entière, tournée sur le plateau si besoin ; **(2)** sinon **coupes en Z** (envergure) si elles suffisent ; **(3)** sinon (le profil ne rentre pas, même en diagonale) **coupe dans la corde**.
- Coupe dans la corde : **plane** (pas de languette) — des **SBAR transversales** pourront la traverser (alignement + renfort) ; position **au milieu** (morceaux de cordes égales), plusieurs longerons probables.
- Découpe **automatique** ou **faite dans la CAO** : un STEP avec **n corps WING** ⇒ n morceaux.
- Toute découpe automatique est **signalée** à l'utilisateur et **visible dans l'onglet Aperçu**.
- **SBAR non parallèles à l'envergure** (transversales, inclinées) : à prendre en charge.

## Conception

### 1. Imprimantes et gabarits
- `printer: p1s | h2s` (config, profil, GUI). `PRINTERS` : p1s 256 × 256 × 256, h2s 340 × 320 × 340.
- Gabarit choisi par (imprimante, matière) : `templates/<printer>_<material>.3mf` ; `bambu.template` explicite reste prioritaire. Volume utile = celui du gabarit (aire imprimable, hauteur) moins la marge.
- Le vase mode reste imposé à l'écriture (mécanisme existant `VASE_SETTINGS`).

### 2. Placement sur le plateau
- Empreinte d'un morceau = enveloppe convexe de sa projection XY. Si elle ne rentre pas telle quelle dans le plateau (moins `segment.margin`), on cherche l'angle de rotation autour de Z qui la fait rentrer (calipers tournants sur l'enveloppe ; choisir l'angle de plus grande marge). Rotation écrite dans la transformation de l'objet du 3MF ; la géométrie STEP/STL reste dans le repère de la pièce.
- Rapport : par morceau, empreinte, rotation, marge.

### 3. Coupes en Z
- Hauteur : règle actuelle (`plan_cuts`).
- Empreinte : si un tronçon ne rentre pas même tourné (flèche, dièdre), on augmente le nombre de tronçons (n+1, n+2 …) tant que la section la plus grande du tronçon rentre seule. Si une **section** isolée ne rentre pas même tournée, les coupes en Z ne suffisent pas ⇒ coupe dans la corde.
- `cuts_at` explicite : respecté ; si un tronçon imposé ne rentre pas ⇒ coupe dans la corde (et avertissement).

### 4. Coupe dans la corde
- Nombre de morceaux k minimal tel que chaque morceau rentre (tourné). Plans de coupe : contiennent l'axe Y (épaisseur) ; plan unique par coupe.
  - *Révision 2026-10-16 (aile réelle effilée à forte flèche : 5 morceaux cisaillés au lieu de 3)* : plans **parallèles**, verticaux de préférence, sinon de la pente du bord d'attaque, du bord de fuite ou du milieu de la corde ; le moins de morceaux, puis la plus faible inclinaison, puis des morceaux équilibrés ; chaque plan traverse toute la hauteur du tronçon. Repli : plans aux points i/k de la corde à l'emplanture et au saumon (suivent la flèche et l'effilement).
  - Une SBAR transversale qui traverse une coupe en biais la frôle au début et à la fin de la traversée : là où la paroi trou ↔ face serait sous 2 largeurs de ligne, le trou s'ouvre sur la face (« pont »), sans nervure autour.
- **Inclinaison** du plan par rapport à Z ≤ `max_overhang_deg` de la matière, sinon erreur française (proposer coupes en Z plus courtes / imprimante plus grande).
- **Évitement** : un plan qui coupe un longeron/gaine *parallèle à l'envergure* (+ manchon + bande), une baie servo (+ bande) est décalé vers la position libre la plus proche (en corde) ; s'il n'y en a pas ⇒ erreur française listant les obstacles. Les SBAR transversales **traversent** la coupe (c'est voulu).
- Les nervures sont générées sur la pièce entière **puis** la pièce est coupée (comme les tronçons) ⇒ motif continu. La face coupée devient une paroi ; les fentes qui y débouchent restent valides (reliées à l'extérieur). Chaque morceau est **validé** séparément.
- Nommage : `T1`, `T2` (Z) puis `A`, `B` (corde, du bord d'attaque au bord de fuite) ⇒ `T1-A`, `T1-B`.

### 5. Plusieurs corps WING
- Un STEP avec plusieurs peaux : orientation commune (repère du modèle entier), **motif de nervures ancré sur la boîte englobante globale** (les nervures se prolongent d'un morceau à l'autre), longerons/gaines/baies attribués au(x) corps qu'ils traversent (coupés par corps). Chaque corps = un morceau (puis éventuellement re-découpé s'il ne rentre pas, avec avertissement).

### 6. SBAR transversales / inclinées
- Un longeron dont l'axe n'est pas parallèle à Z (au-delà de la tolérance actuelle) n'est plus refusé : trou **en goutte d'eau** (pointe vers +Z, flancs à `max_overhang_deg`) pour que le plafond du trou soit imprimable ; jeu `clearance`.
- Le trou doit **déboucher** : par une face extérieure (peau, face de coupe, emplanture/saumon). Sinon une **fente** relie le trou à la peau la plus proche (plan contenant l'axe), comme les longerons.
- Zones sans nervure : section du tube (goutte + manchon + bande) dans chaque plan de plaque ⇒ mêmes règles d'« ombre ». Aperçu = génération.
- Rapport : « SBAR_x (transversale, Ø…, angle …°) ».

### 7. Aperçu, GUI, notification
- Préparation : plan de découpe complet (tronçons, coupes dans la corde, rotations) calculé dans l'aperçu (sans booléens) et identique à la génération.
- Vue de dessus : coupes en Z (tirets), coupes dans la corde (traits), SBAR transversales ; liste des morceaux (nom, dimensions, rotation, plaque). 3D : morceaux en couleurs distinctes.
- Avertissement français à chaque découpe automatique (« découpe automatique : 2 tronçons en Z, coupe dans la corde en 2 morceaux (corde 380 mm > 340 mm en diagonale) »), dans la CLI, le rapport, la GUI.
- Réglages : `printer`, `segment.chord: auto | off` (off ⇒ erreur si ça ne rentre pas), etc.

## Hors périmètre
Languettes / queues d'aronde (schéma présenté, non retenu), inserts, X2D.
