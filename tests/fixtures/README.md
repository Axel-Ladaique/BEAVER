# Fixtures réelles

Déposer ici des exports STEP **réels** depuis SolidWorks et Fusion 360 :

- la même pièce simple dans les deux logiciels : un tronçon d'aile nommé `SKIN…` / `WING…` et un longeron cylindrique nommé `SPAR…` / `SBAR…` ;
- déjà présent : `fusion_aile_4412_simple_sbar.step` (Fusion 360, corps `WING_1` avec alésage Ø14 et `SBAR_14mm_1`, envergure selon X) ;
- nom de fichier : `<logiciel>_<description>.step`, ex. `solidworks_aile_simple.step`, `fusion_aile_simple.step` ;
- au moins un export Fusion en **pouces**, pour vérifier la conversion d'unités (`fusion_aile_simple_inch.step`).

Chaque fichier doit, après classification, contenir au moins une peau et un longeron.
Les fichiers `*_inch.step` doivent avoir un longeron de même diamètre (en mm) que leur jumeau en mm.
