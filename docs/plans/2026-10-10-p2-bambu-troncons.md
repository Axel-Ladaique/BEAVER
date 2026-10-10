# P2 (+ tronçons) — Projet Bambu multi-plateaux : implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal :** `aerorib run aile.step --3mf --open` produit un projet Bambu Studio prêt à imprimer (imprimante, filament PLA Aero, spiral vase repris du gabarit de l'utilisateur), avec **un tronçon par plateau** quand l'aile dépasse la hauteur d'impression, et l'ouvre dans Bambu Studio.

**Spec :** `docs/specs/2026-10-09-aerorib-design.md` §7.7, §12 ; addendum `docs/specs/2026-10-10-p1-addendum.md` (repère, pipeline). Décisions validées par l'utilisateur le 2026-10-10 : se concentrer sur les ailes ; « la séparation automatique met les différents objets sur les différentes plaques d'un projet Bambu » ; gabarit = son 3MF.

**Gabarit fourni :** `src/aerorib/templates/p1s_pla_aero.3mf` (copie de test : `tests/fixtures/bambu_p1s_pla_aero.3mf`). Contenu relevé :
- `Metadata/project_settings.config` (JSON) : `printer_model` « Bambu Lab P1S », `printable_area` `['0x0','256x0','256x256','0x256']`, `printable_height` « 250 », `spiral_mode` « 1 », `bottom_shell_layers` « 3 », `layer_height` « 0.2 », `line_width` « 0.42 », `slice_closing_radius` « 0.049 », `filament_settings_id` `['Bambu PLA Aero @BBL X1C']`.
- Structure Bambu : `3D/3dmodel.model` (objets à `<components p:path="/3D/Objects/object_N.model" objectid="1">`, `<build><item … transform="1 0 0 0 1 0 0 0 1 tx ty tz">`), `3D/Objects/object_1.model` (maillage), `3D/_rels/3dmodel.model.rels`, `Metadata/model_settings.config` (objets, `<plate>` avec `plater_id` et `<model_instance object_id=… instance_id="0" identify_id=…>`, `<assemble>`), vignettes `Metadata/plate_*.png`, `plate_1.json`, `cut_information.xml`, `slice_info.config`, `[Content_Types].xml`, `_rels/.rels`.

## Global Constraints
- Python ≥ 3.12, Windows + macOS ; messages en français ; mm ; repère d'impression Z+ (emplanture z = 0).
- **On ne reconstruit jamais les réglages Bambu** : `project_settings.config`, `slice_info.config`, `[Content_Types].xml`, `_rels/.rels` du gabarit sont recopiés **à l'identique** (octets). Seuls les objets, `model_settings.config`, `3D/*` sont réécrits ; vignettes et `plate_*.json` du gabarit ne sont pas recopiés (Bambu les régénère).
- Disposition des plateaux (logique Bambu Studio, `PartPlate`) : `cols = ceil(sqrt(n))`, plateau i (0-based) : `col = i % cols`, `row = i // cols`, origine `(col·W·1.2, −row·D·1.2)` où W×D = `printable_area` ; chaque objet centré sur son plateau, posé à z = 0. À vérifier par l'utilisateur à l'ouverture (pas de slicing CLI : `BambuStudio --slice` plante sur cette machine — `machine_full` absent).
- Hauteur utile = `printable_height` du gabarit − `segment.margin` (5 mm). Emprise XY de chaque tronçon ≤ W×D − 2·marge, sinon erreur claire.
- Les nervures sont générées sur l'aile **entière**, puis l'aile nervurée est coupée : continuité du motif et du longeron garantie.
- Tests lents marqués `slow` ; ne jamais affaiblir un test.

## Fichiers
```
src/aerorib/bambu.py          Template, read_template, plate_origin, write_project, open_in_bambu
src/aerorib/segment.py        plan_cuts, split_part, SegmentError
src/aerorib/config.py         SegmentSettings, BambuSettings ; formats += "3mf"
src/aerorib/pipeline.py       BodyResult.segments ; run_model découpe après validation
src/aerorib/export.py         STEP/STL par tronçon, 3MF
src/aerorib/cli.py            run --3mf / --open
tests/test_bambu.py, tests/test_segment.py, tests/test_pipeline_segments.py, tests/test_cli_bambu.py
```

---

### Task 1 : Lecture du gabarit et écriture d'un projet multi-plateaux (`bambu.py`)

**Produces :**
- `@dataclass(frozen=True) Template(path: Path, settings: dict, bed: tuple[float, float], height: float)` ; `read_template(path) -> Template` (lève `BambuError` en français si le fichier n'est pas un projet Bambu : pas de `Metadata/project_settings.config`). `bed` depuis `printable_area` (max des x et y), `height` depuis `printable_height`.
- `plate_origin(i: int, n: int, bed: tuple[float, float]) -> tuple[float, float]` (règle des Global Constraints).
- `write_project(template: Template, objects: list[tuple[str, trimesh.Trimesh]], path: Path) -> Path` : un objet par plateau, dans l'ordre ; maillages déjà dans le repère d'impression ; chaque maillage recentré (centre XY de sa boîte au centre du plateau, min Z = 0).
- `BambuError(ValueError)`.

Structure écrite (miroir du format Bambu relevé) : pour l'objet k (1-based) : fichier `3D/Objects/object_{k}.model` contenant `<object id="1" type="model"><mesh>…</mesh></object>` (sommets en coordonnées locales, recentrées sur l'origine XY, z ≥ 0) ; dans `3D/3dmodel.model` : `<object id="{2k}" type="model"><components><component p:path="/3D/Objects/object_{k}.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>` et `<item objectid="{2k}" transform="1 0 0 0 1 0 0 0 1 X Y 0" printable="1"/>` avec (X, Y) = origine du plateau + (W/2, D/2) ; `3D/_rels/3dmodel.model.rels` : une relation par fichier objet ; `Metadata/model_settings.config` : un `<object id="{2k}">` (metadata `name`, `extruder`=1, `<part id="1" subtype="normal_part">` avec `name` et `matrix` identité) et un `<plate>` par objet (`plater_id`=k, `plater_name`=nom, `locked` false, `<model_instance>` object_id=2k instance_id=0 identify_id=unique), plus `<assemble>` avec un `assemble_item` par objet (transform = celui du build). Garder les espaces de noms et en-têtes du gabarit (`requiredextensions="p"`, `BambuStudio:3mfVersion`, `Application`).

- [ ] Tests (`tests/test_bambu.py`) :
  - `read_template` sur la fixture → bed (256, 256), height 250, `settings["spiral_mode"] == "1"`, `settings["filament_settings_id"] == ["Bambu PLA Aero @BBL X1C"]` ; un fichier zip quelconque → `BambuError`.
  - `plate_origin` : n=1 → (0,0) ; n=3, bed 256 → i=0 (0,0), i=1 (307.2, 0), i=2 (0, −307.2).
  - `write_project` avec 3 boîtes trimesh → zip relu : `Metadata/project_settings.config` identique octet pour octet au gabarit ; 3 fichiers `3D/Objects/object_k.model` ; `model_settings.config` parsé (ElementTree) : 3 `<plate>` avec `plater_id` 1..3 et chacun un `model_instance` vers un objet distinct ; dans `3dmodel.model`, chaque `item` a sa translation au centre de son plateau (± 1e-6) ; relire chaque maillage (parser le XML des sommets/triangles) → même nombre de faces, min z = 0, centre XY = 0 (± 1e-6) ; aucune vignette `plate_*.png` du gabarit dans la sortie.
- [ ] Implémenter avec `zipfile` + `xml.etree.ElementTree` (ou chaînes) ; sommets écrits avec 6 décimales ; triangles `v1 v2 v3`.
- [ ] Commit `feat(bambu): projet 3MF multi-plateaux à partir du gabarit`.

### Task 2 : Découpe en tronçons (`segment.py`)

**Produces :**
- `plan_cuts(height: float, max_height: float, cuts_at: list[float] | None = None) -> list[float]` : hauteurs de coupe internes (vide si `height ≤ max_height`) ; auto : `n = ceil(height / max_height)` tronçons égaux ; manuel : `cuts_at` triés, chacun dans ]0, height[, et chaque tronçon ≤ max_height sinon `SegmentError` (« tronçon de X mm > hauteur utile Y mm »).
- `split_part(solid, cuts) -> list[Solid]` : coupe par plans z = cte (intersection avec une boîte par tronçon), chaque morceau doit être 1 solide valide (sinon `SegmentError`), puis translaté pour que son min Z = 0. Ordre : de l'emplanture au saumon.
- `SegmentError(ValueError)`.
- [ ] Tests : `plan_cuts(210, 245) == []` ; `plan_cuts(500, 245) == [500/3, 1000/3]` ; `plan_cuts(500, 245, [200, 400])` → SegmentError (tronçon 200 OK, 200 OK, 100 OK → en fait valide : écrire le cas valide `[200, 400]` → `[200, 400]` et un cas invalide `[300]` → erreur car 300 > 245) ; coupe hors bornes → erreur. `split_part` sur `ribbed_wing()` (envergure 100) avec cuts [40, 70] → 3 solides valides, hauteurs 40/30/30 (± 1e-3), min Z = 0 chacun, et la somme des sections à mi-hauteur de chaque morceau égale la section de la pièce entière à la même hauteur d'origine (± 1e-3 mm²) — pas de `Solid.volume`.
- [ ] Commit `feat(segment): découpe en tronçons perpendiculaires à l'envergure`.

### Task 3 : Configuration
- `SegmentSettings(mode: Literal["auto","off"]="auto", margin: float = Field(5.0, ge=0), cuts_at: list[float] = [], max_height: float | None = Field(None, gt=0))` — `max_height` force la hauteur utile (sinon gabarit − marge ; sans gabarit : table `PRINTERS` − marge).
- `BambuSettings(template: str | None = None, open: bool = False)` — `None` = gabarit embarqué `aerorib/templates/p1s_pla_aero.3mf` (trouvé via `importlib.resources`).
- `OutputSettings.formats: list[Literal["step","stl","3mf"]]` (défaut inchangé `["step","stl"]`).
- `examples/config-exemple.yaml` mis à jour (égalité aux défauts conservée).
- [ ] Tests config (défauts, `segment.margin: -1` refusé, `formats: [3mf]` accepté) puis commit `feat(config): tronçons et projet Bambu`.

### Task 3b : Profils matière stockés (demande utilisateur du 2026-10-10)
L'utilisateur veut que PLA Aero **et** ASA Aero fonctionnent, chacune avec ses **paramètres géométriques stockés** (il les trouvera par essais). Spec §10 : « défauts → preset matière → YAML → overrides ».
- Profils fichiers YAML : `src/aerorib/profiles/pla_aero.yaml` et `asa_aero.yaml` (embarqués, via `importlib.resources`), plus surcharge utilisateur : un dossier `profiles/` dans le répertoire courant ou le chemin `profiles_dir` d'une variable d'environnement `AERORIB_PROFILES` (le premier trouvé gagne sur l'embarqué, fichier par fichier).
- Contenu d'un profil = un fragment de config complet pour la géométrie : `material_settings` (line_width, layer_height, density, min_slit_width, max_overhang_deg), `slit_width`, `split` (gap, min_rib_depth), `rib_layers`, `spars.default` (clearance, sleeve, strip, slit_side, slit_width, anchor, anchor_gap), `passages`, `bambu.template` (chemin relatif au fichier profil, ou embarqué). Valeurs initiales = défauts actuels ; PLA : `line_width` **0.42** (relevé dans le gabarit), `min_slit_width` 0.10 ; ASA : idem sauf `max_overhang_deg` 40 et `bambu.template: null` (pas encore de gabarit ASA → `--3mf` refusé avec un message clair « pas de gabarit Bambu pour asa_aero : enregistrer un projet ASA Aero dans Bambu Studio et renseigner bambu.template »).
- Ordre de fusion dans `load_config` : défauts du code → profil de `material` (choisi dans la config projet ou l'override, défaut `pla_aero`) → YAML projet → overrides CLI. `MATERIALS` (dict codé en dur) disparaît au profit des profils ; `Config.preset` lit les `material_settings` fusionnés. Les validateurs existants (surplomb, fentes ≥ 1,1×seuil, manchon/attache/gap ≥ largeur de ligne, depth ≥ 2×ligne) s'appliquent au résultat fusionné.
- Commande `aerorib profils` : liste les profils trouvés (nom, origine embarqué/utilisateur, gabarit Bambu ou « aucun ») ; `aerorib profils --copier asa_aero` copie le profil embarqué dans `./profiles/` pour édition.
- `examples/config-exemple.yaml` reste égal aux défauts (pla_aero).
- [ ] Tests : `load_config(overrides={"material": "asa_aero"})` → angle max 40 ; un profil utilisateur dans `tmp_path/profiles/pla_aero.yaml` (via `AERORIB_PROFILES`) qui change `split.gap` → pris en compte, et un YAML projet le surcharge encore ; profil invalide (fente 0.05) → ConfigError mentionnant le fichier profil ; matière inconnue → ConfigError listant les profils disponibles ; `--3mf` avec asa_aero sans gabarit → code 1 et message ; `aerorib profils` liste les deux ; `--copier` crée le fichier.
- [ ] Commit `feat(config): profils matière stockés (PLA Aero, ASA Aero)`.

### Task 4 : Pipeline et export
- `BodyResult.segments: tuple[Solid, ...]` (un seul élément si pas de découpe). `run_model` : après validation de la pièce entière, si `segment.mode == "auto"` : hauteur utile (cf. Task 3), `plan_cuts`, `split_part` ; vérifier l'emprise XY de chaque tronçon vs `bed − 2·margin` → sinon `PipelineError` français (« le tronçon i fait W×D mm, plus grand que le plateau ») ; `SegmentError` → `PipelineError`. Message d'avancement « découpe en N tronçons ».
- `export_results(run, out_dir, stem, formats, template: Template | None)` : STEP = tous les tronçons nommés `<corps>_t1…` (ou le corps seul si 1 tronçon) ; STL : un fichier par tronçon (`<stem>-t1.stl`…) quand plusieurs, sinon comme avant ; `3mf` : `write_project(template, [(nom_tronçon, maillage)], out_dir/<stem>.3mf)` — maillage via le maillage natif (`slicing.to_mesh(solid, 0.02)`).
- [ ] Tests (`tests/test_pipeline_segments.py`) : aile synthétique `wing_x_bodies()` (envergure 80) avec `segment.max_height: 30` → 3 tronçons, chacun 1 solide valide, min z 0, hauteurs ≤ 30 ; export avec `formats: [step, stl, 3mf]` et le gabarit de test → 3 STL, 1 STEP relu avec 3 corps `Aile_test_t1..t3`, 1 3MF à 3 plateaux. Cas trop large : `bed` réduit via un gabarit modifié en test (réécrire `printable_area` dans une copie) → PipelineError.
- [ ] Commit `feat(pipeline): découpe en tronçons et export 3MF`.

### Task 5 : CLI `--3mf` / `--open`
- `run` : options `--3mf` (ajoute "3mf" aux formats) et `--open` (implique `--3mf` ; ouvre le projet). Lecture du gabarit au début (`BambuError` → `_fail`).
- `open_in_bambu(path)` dans `bambu.py` : macOS `open -a BambuStudio <path>` (si absent : `open <path>`) ; Windows : exécutable trouvé dans `%ProgramFiles%\Bambu Studio\bambu-studio.exe` sinon `os.startfile` ; Linux : `xdg-open`. Ne jamais bloquer (Popen). Erreur → message français, pas d'échec du run.
- Le terminal affiche les tronçons : `  tronçons : 3 (z = 0–166.7, 166.7–333.3, 333.3–500 mm) → plateaux 1 à 3`.
- [ ] Tests (`tests/test_cli_bambu.py`) : `run --3mf` sur l'aile synthétique avec `segment.max_height: 30` → fichier .3mf présent, 3 plateaux, sortie contient « tronçons : 3 » ; `--open` avec `subprocess.Popen` monkeypatché → appelé une fois avec le chemin du 3MF ; gabarit invalide → code 1, message français.
- [ ] Commit `feat(cli): run --3mf / --open`.

### Task 6 : Aile réelle, documentation
- Test lent : aile v2 (`tests/fixtures/fusion_aile_4412_simple_sbar_v2.step`) avec `formats: [stl, 3mf]` → 1 tronçon (210 ≤ 245), 3MF à 1 plateau ; même aile avec `segment.max_height: 120` → 2 tronçons de 105 mm, 3MF à 2 plateaux, chaque tronçon 1 solide valide.
- Générer pour l'utilisateur : `uv run aerorib run tests/fixtures/fusion_aile_4412_simple_sbar_v2.step --3mf --out out/aile_v2_bambu` et la même avec `-c` d'une config `segment: {max_height: 120}` dans `out/aile_v2_bambu_2troncons` (démo multi-plateaux).
- README : section « Projet Bambu » (gabarit, `--3mf`, `--open`, tronçons, `cuts_at`, comment fournir un autre gabarit : enregistrer un projet dans Bambu Studio avec imprimante/filament/vase, puis `bambu.template: chemin`) ; spec §12/§7.7 : renvoi vers ce plan ; limite connue : pas de vérification de slicing automatique.
- [ ] Commit `test: aile réelle en projet Bambu ; docs`.
