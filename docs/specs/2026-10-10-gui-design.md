# GUI navigateur — conception

- **Date :** 2026-10-10 · **Statut :** validée par l'utilisateur (choix ci-dessous), détails techniques décidés en autonomie (nuit).
- **Complète :** spec principale §5 (P7) et addendum P1.

## Choix de l'utilisateur
- Page dans le **navigateur** (pas d'application de bureau).
- Périmètre **C** : lanceur + **aperçu instantané** + **éditeur de profils matière**.
- Lancement : `aerorib gui` **et** un raccourci à double-cliquer (macOS `.command`, Windows `.bat`).
- Accès : **cet ordinateur seulement** (127.0.0.1).
- **Historique** de toutes les générations, comparable.
- Aperçu : **coupe à hauteur réglable**, **vue de dessus du motif**, **chiffres estimés**, **3D approximative**.
- Ailes uniquement pour l'instant ; interface en français.

## Architecture
```
navigateur (HTML/CSS/JS modules, sans étape de build ; three.js chargé depuis un CDN, version figée)
   │  HTTP JSON + flux SSE (progression)
   ▼
serveur local FastAPI/uvicorn (127.0.0.1, port libre) — package aerorib.gui
   ├─ projets  : stockage sur disque (dossier ./projets par défaut)
   ├─ aperçu   : aerorib.preview (sans booléens, rapide, mis en cache)
   ├─ travaux  : génération complète dans un processus séparé (spawn), 1 à la fois, annulable
   └─ profils  : lecture/écriture des profils utilisateur (./profiles)
   ▼
cœur existant : config, io_step, classify, orient, midsurface, features, pipeline, export, bambu
```
Principe : la GUI n'ajoute **aucune géométrie** ; elle appelle le cœur. Tout calcul est testable sans navigateur.

## Données sur disque
```
projets/<nom>/
  piece.step            copie du STEP importé
  config.yaml           config du projet (mêmes règles que la CLI : défauts → profil → projet)
  essais/<AAAAMMJJ-HHMMSS>/
     config.yaml        config résolue utilisée
     resume.json        statut, durée, nervures, tronçons, masse, trajet, erreurs/avertissements, fichiers
     <nom>.step/.stl/.3mf, <nom>-rapport.html
```
Racine configurable par `aerorib gui --dossier`. Noms de projets assainis (pas de `..`, pas de séparateurs).

## Écrans (une page, onglets)
1. **Projets** : liste, créer (import STEP par sélection de fichier), ouvrir ; après import, liste des corps et rôles (comme `inspect`) avec avertissements.
2. **Réglages** : formulaire généré depuis le schéma de config (matière, nervures — couches multiples —, séparation, longerons, passages, tronçons, sortie) ; valeurs héritées du profil affichées en grisé, surcharge explicite ; erreurs de validation en français à côté du champ ; enregistrement dans `config.yaml` du projet.
3. **Aperçu** (se met à jour à chaque modification, < ~2 s après la préparation initiale) :
   - **Coupe** : curseur de hauteur ; polygone de la section avec fentes (nervures, longeron, attache), bande centrale, cols trop fins (< largeur de ligne) en rouge ; approximation 2D sans booléens 3D.
   - **Vue de dessus** : plan X–Z (corde × envergure) avec les lignes des deux réseaux, les étendues réellement coupées, le longeron, les coupes de tronçons.
   - **Chiffres estimés** : nombre de plaques par couche, tronçons et hauteurs, masse et trajet estimés (sur un échantillon de couches), durée estimée (plaques × coût mesuré).
   - **3D approximative** : peau transparente + plaques des nervures (solides des plaques maillés, sans découpe) dans three.js.
4. **Générer** : lance la génération complète (comme `run --3mf`) ; progression en direct ; annulation ; à la fin, résumé + rapport + boutons « Ouvrir dans Bambu », « Ouvrir le dossier ».
5. **Historique** : tableau des essais (date, statut, nervures, masse, trajet, avertissements), sélection de deux essais → différences de réglages ; rouvrir le rapport, la 3D (STL) ou le projet Bambu.
6. **Profils** : liste (embarqué / utilisateur), éditeur des paramètres géométriques d'un profil (formulaire), validation en direct, enregistrement dans `./profiles/<nom>.yaml` (fusion sur l'embarqué : seules les valeurs modifiées sont écrites), copie d'un profil.

## Aperçu : calcul (module `aerorib.preview`)
- `prepare(step, cfg)` → orientation, peau orientée, `MidSurface`, trous (longerons, passages), maillage peau ; mis en cache par (empreinte du fichier, paramètres d'orientation/grille).
- `plates_2d(prep, cfg)` → pour chaque couche de nervures, les profils 2D (s, y) déjà produits par `plate_profiles_2d` + leur ligne (c, d0, d1) — rapide, pas de booléen.
- `section(prep, plates, z)` → polygone de la peau à z (`slice_layers`), moins les empreintes des plaques coupant z (segments en y à x = c + s·d0 où z = zmin + s·d1, largeur slit/cos), moins trous/fentes/attache ; puis `slicer_closing` + `check_connectivity` + `check_material_width` pour marquer les défauts. C'est une **approximation** de la coupe réelle (affichée comme telle).
- `planform(prep, plates)` → segments 2D (x, z) des parties coupées de chaque plaque.
- `estimates(prep, plates, cfg)` → comptes, tronçons (`plan_cuts`), trajet/masse sur ~20 couches échantillonnées, durée = plaques × 1,5 s (paramètre).
- `plates_mesh(prep, plates)` → maillage (glTF ou STL binaire) des plaques solides pour la 3D.

## Travaux
Un seul à la fois ; processus séparé (`multiprocessing` spawn, fonction de module) qui exécute le pipeline et renvoie la progression par une queue ; le serveur relaie en SSE ; annulation = arrêt du processus ; résultat écrit dans `essais/<horodatage>/`.

## Sécurité / robustesse
Écoute 127.0.0.1 uniquement ; jeton aléatoire par session dans l'URL ouverte (empêche une autre page web locale d'appeler l'API) ; taille d'import limitée (200 Mo) ; chemins confinés au dossier des projets ; erreurs du cœur renvoyées en français.

## Dépendances ajoutées
`fastapi`, `uvicorn`, `python-multipart` (import de fichier). three.js depuis jsDelivr (version figée) — nécessite Internet au premier chargement (limite documentée).

## Hors périmètre (pour l'instant)
Fuselages, évidements, édition de géométrie dans la page, multi-utilisateur, accès réseau.
