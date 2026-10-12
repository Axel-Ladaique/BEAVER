"""Baies de servo : poche avec rampes, contournement par les nervures (servo, tâche 4)."""

import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString

from aerorib import preview
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.features.servo import (
    SKIN_MARGIN,
    ServoError,
    hole_meets_pocket,
    pocket_profile,
    pocket_zones_2d,
    servo_bays,
)
from aerorib.io_step import read_step, write_step
from aerorib.midsurface import MidSurface
from aerorib.orient import orient
from aerorib.pipeline import PipelineError, run_model
from aerorib.report import format_bays
from aerorib.slicing import to_mesh
from aerorib.testing.synthetic import servo_wing_bodies

FIXTURES = Path(__file__).parent / "fixtures"
OVER = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "passages": {"mode": "link"}}
PLAIN = {**OVER, "servo": {"reinforce": False}}  # sans renfort : comportement de la tâche 4


def _oriented(bodies, cfg):
    model = classify(bodies, cfg)
    o = orient(model, cfg)
    skin = model.of("skin")[0].body.solid.moved(o.location)
    servos = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("servo")]
    return skin, servos


def _bays(bodies, cfg=None):
    cfg = cfg or load_config(overrides=OVER)
    skin, servos = _oriented(bodies, cfg)
    mid = MidSurface.from_skin(skin, *cfg.split.grid)
    return servo_bays(servos, skin, mid, cfg), skin, mid


def test_bay_side_box_and_ramp():
    cfg = load_config(overrides=OVER)
    (top,), skin, mid = _bays(servo_wing_bodies("extrados"), cfg)
    assert top.name == "SERVO_1" and top.side == 1
    assert top.x1 - top.x0 == pytest.approx(20.0, abs=0.05) and top.z1 - top.z0 == pytest.approx(35.0, abs=0.05)
    ym, th = mid.at(np.linspace(top.x0, top.x1, 21), np.full(21, top.z0))
    depth = float(np.max(ym + th / 2 - top.y_floor))
    assert top.depth == pytest.approx(depth, abs=0.1)
    tan = math.tan(math.radians(cfg.preset.max_overhang_deg))
    assert top.ramp == pytest.approx(top.depth / tan)
    lo, hi = top.opening
    assert lo == pytest.approx(top.z0 - top.ramp) and hi == pytest.approx(top.z1 + top.ramp)
    (bot,), _, _ = _bays(servo_wing_bodies("intrados"), cfg)
    assert bot.side == -1 and bot.depth > 0


def test_pocket_profile_ramps_follow_max_overhang():
    cfg = load_config(overrides={**OVER, "material_settings": {"layer_height": 1.0, "max_overhang_deg": 40}})
    (bay,), _, _ = _bays(servo_wing_bodies("extrados"), cfg)
    prof = pocket_profile(bay)
    pts = list(prof.exterior.coords)[:-1]
    assert len(pts) == 4
    # (z, y) : fond de z0 à z1, rampes jusqu'au-delà de la peau
    floor = [p for p in pts if p[1] == pytest.approx(bay.y_floor)]
    assert sorted(z for z, _ in floor) == pytest.approx([bay.z0, bay.z1])
    for z, y in pts:
        if y != pytest.approx(bay.y_floor):
            dz = (bay.z0 - z) if z < bay.z0 else (z - bay.z1)
            # inclinaison de la rampe par rapport à Z (direction d'impression) = surplomb max
            assert math.degrees(math.atan2(y - bay.y_floor, dz)) == pytest.approx(40)


def test_pocket_zones_cover_the_pocket_with_strip():
    cfg = load_config(overrides=PLAIN)
    (bay,), skin, _ = _bays(servo_wing_bodies("extrados"), cfg)
    zmin = skin.bounding_box().min.Z
    d0, d1 = math.sin(math.radians(45)), math.cos(math.radians(45))
    # plaque qui traverse le milieu de la boîte : x = c + s·d0, z = zmin + s·d1
    s_mid = ((bay.z0 + bay.z1) / 2 - zmin) / d1
    c = (bay.x0 + bay.x1) / 2 - s_mid * d0
    (zone,) = pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1)
    from shapely.geometry import Point

    assert zone.contains(Point(s_mid, bay.y_floor + 1))
    assert zone.contains(Point(s_mid, bay.y_floor - 1.9))  # bande de 2 mm sous le fond
    assert not zone.contains(Point(s_mid, bay.y_floor - 2.2))
    # plaque qui passe loin de la boîte : aucune zone
    assert pocket_zones_2d(bay, c + 200, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1) == []
    # avec renfort : la zone couvre aussi la fente (décalée de la largeur de ligne) + la bande
    (rbay,), _, _ = _bays(servo_wing_bodies("extrados"), load_config(overrides=OVER))
    # zone de la poche d'abord, puis celles des ponts que la plaque traverse (bande BRIDGE_STRIP)
    rzone, *bridges = pocket_zones_2d(rbay, c, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1)
    assert bridges
    edge = rbay.y_floor - rbay.reach
    assert rbay.reach == pytest.approx(1.2 * 0.42 + 0.15)  # paroi doublée (1,2 ligne) + fente
    assert rzone.contains(Point(s_mid, edge - 1.9)) and not rzone.contains(Point(s_mid, edge - 2.2))


def test_pocket_zones_stop_just_beyond_the_skin():
    cfg = load_config(overrides=PLAIN)
    (bay,), skin, mid = _bays(servo_wing_bodies("extrados"), cfg)
    zmin = skin.bounding_box().min.Z
    d0, d1 = math.sin(math.radians(45)), math.cos(math.radians(45))
    s_mid = ((bay.z0 + bay.z1) / 2 - zmin) / d1
    c = (bay.x0 + bay.x1) / 2 - s_mid * d0
    (zone,) = pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1)
    # peau d'ouverture la plus haute autour de la poche (corde ± bande, rampes ± bande)
    xs, zs = np.meshgrid(np.linspace(bay.x0 - 2.05, bay.x1 + 2.05, 30), np.linspace(bay.z0 - bay.ramp - 2, bay.z1 + bay.ramp + 2, 30))
    ym, th = mid.at(xs.ravel(), zs.ravel())
    top = float(np.max((ym + th / 2)[th > 0]))
    assert bay.y_skin == pytest.approx(top, abs=0.05)
    assert top < zone.bounds[3] <= top + SKIN_MARGIN + 2.05 + 1e-6  # pas jusqu'à y_out
    assert zone.bounds[3] < bay.y_out
    # en envergure, la zone ne déborde de l'ouverture que de la bande (+ la marge de peau)
    zlo, zhi = zmin + zone.bounds[0] * d1, zmin + zone.bounds[2] * d1
    lo, hi = bay.opening
    assert zlo > lo - (SKIN_MARGIN + 2.05) * (1 + bay.run) - 1 and zhi < hi + (SKIN_MARGIN + 2.05) * (1 + bay.run) + 1


def test_hole_meets_pocket_follows_floor_and_ramps():
    (bay,), _, _ = _bays(servo_wing_bodies("extrados"))
    xm = (bay.x0 + bay.x1) / 2
    # trou dont le haut dépasse le fond de 1 mm : rencontre sur la boîte et le début des rampes, ± 1 mm
    za, zb = hole_meets_pocket(bay, xm, bay.y_floor - 2, 3.0, 0.0, 120.0)
    assert za == pytest.approx(bay.z0 - 1 * bay.run - 1.0) and zb == pytest.approx(bay.z1 + 1 * bay.run + 1.0)
    # trou arrêté avant la boîte : limité à son étendue
    assert hole_meets_pocket(bay, xm, bay.y_floor - 2, 3.0, 0.0, bay.z0 + 5)[1] == pytest.approx(bay.z0 + 5)
    # sous le fond sans l'atteindre, ou à côté de la boîte : aucune rencontre
    assert hole_meets_pocket(bay, xm, bay.y_floor - 3.5, 3.0, 0.0, 120.0) is None
    assert hole_meets_pocket(bay, bay.x1 + 3.5, bay.y_floor, 3.0, 0.0, 120.0) is None
    # sous une rampe seulement, plus loin que la boîte : rencontre limitée à la rampe
    lo, hi = bay.opening
    assert hole_meets_pocket(bay, xm, bay.y_floor + 2, 1.0, 0.0, bay.z0 - 4 * bay.run) is None


def test_bay_errors_are_french():
    with pytest.raises(ServoError, match="SERVO_1 : la boîte ne traverse aucune peau"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, 2.0, 6.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : le fond de la baie laisse .* mm avant l'intrados"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, -1.0, 16.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : la boîte traverse l'extrados et l'intrados"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, -8.0, 16.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : baie non alignée sur la corde / l'envergure"):
        _bays(servo_wing_bodies(tilt_deg=10))
    # léger défaut d'alignement (< 2°) toléré
    (bay,), _, _ = _bays(servo_wing_bodies(tilt_deg=1))
    assert bay.side == 1


def test_bay_error_reaches_pipeline_in_french():
    cfg = load_config(overrides=OVER)
    with pytest.raises(PipelineError, match="SERVO_1 : la boîte ne traverse aucune peau"):
        run_model(classify(servo_wing_bodies(box=(22.0, 42.0, 2.0, 6.0, 40.0, 75.0)), cfg), cfg)


def test_reinforce_settings():
    cfg = load_config()
    assert cfg.servo.reinforce and cfg.reinforce_offset() == pytest.approx(1.2 * cfg.preset.line_width)
    assert load_config(overrides={"servo": {"reinforce_offset": 0.6}}).reinforce_offset() == 0.6
    with pytest.raises(ValueError, match="servo : reinforce_offset 0.3 mm < largeur de ligne"):
        load_config(overrides={"servo": {"reinforce_offset": 0.3}})
    with pytest.raises(ValueError, match="servo : fente .*slicer la refermerait"):
        load_config(overrides={"servo": {"slit_width": 0.1}})
    load_config(overrides={"servo": {"reinforce": False, "slit_width": 0.1}})  # sans renfort : fente inutilisée


def test_min_floor_gap_counts_the_reinforcement():
    # fond à ≈ 1,2 mm de l'intrados : assez sans renfort, trop avec (paroi + fente ≈ 0,65 mm de plus)
    box = (22.0, 42.0, -0.5, 16.0, 40.0, 75.0)
    with pytest.raises(ServoError, match="avec le renfort"):
        _bays(servo_wing_bodies(box=box), load_config(overrides={**OVER, "servo": {"min_floor_gap": 1.0}}))
    _bays(servo_wing_bodies(box=box), load_config(overrides={**PLAIN, "servo": {"reinforce": False, "min_floor_gap": 1.0}}))


def test_strip_defaults_to_welded_ribs():
    cfg = load_config()
    lw = cfg.preset.line_width
    assert cfg.servo.strip is None and cfg.servo_strip() == pytest.approx(1.2 * lw)
    assert load_config(overrides={"servo": {"strip": 2.0}}).servo_strip() == 2.0
    with pytest.raises(ValueError, match="servo : strip 0.3 mm < largeur de ligne"):
        load_config(overrides={"servo": {"strip": 0.3}})


def test_pocket_zones_follow_the_local_skin_at_the_ramps():
    """Le long d'une plaque, la zone s'arrête là où la rampe (agrandie) sort de la peau à cet endroit, pas de la
    peau la plus haute autour de la baie : une peau plus basse que `y_skin` raccourcit la zone en envergure."""
    cfg = load_config(overrides=PLAIN)
    (bay,), skin, mid = _bays(servo_wing_bodies("extrados"), cfg)
    zmin = skin.bounding_box().min.Z
    # plaque presque parallèle à l'envergure (10°) : elle reste dans l'étendue en corde de la boîte sur les deux rampes
    d0, d1 = math.sin(math.radians(10)), math.cos(math.radians(10))
    s_mid = ((bay.z0 + bay.z1) / 2 - zmin) / d1
    c = (bay.x0 + bay.x1) / 2 - s_mid * d0
    ss = np.linspace(-5, 300, 3000)
    (wide,) = pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=0.5, slit=0.1)
    low = bay.y_skin - 3.0  # peau 3 mm sous la plus haute
    (local,) = pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=0.5, slit=0.1, skin=(ss, np.full(len(ss), low)))
    # rampe (dz/dy = run) : 3 mm plus bas ⇒ ≈ 3·run de moins en z à chaque bout (s = z / d1), à la bande près
    # (0,55 mm : coin en onglet de la zone écrêtée à y_skin, contre la rampe agrandie coupée à la peau locale)
    for a, b in ((local.bounds[0], wide.bounds[0]), (wide.bounds[2], local.bounds[2])):
        assert (a - b) * d1 == pytest.approx(3.0 * bay.run, abs=0.75)
    # la zone descend encore jusqu'à la peau locale (+ marge) au bout de la plaque
    y_at_end = local.intersection(LineString([(local.bounds[2] - 1e-6, -1e3), (local.bounds[2] - 1e-6, 1e3)])).bounds
    assert y_at_end[1] <= low + SKIN_MARGIN + 1e-6
    # hors matière (NaN) : aucune zone
    assert pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=0.5, slit=0.1, skin=(ss, np.full(len(ss), np.nan))) == []


def test_min_floor_gap_setting():
    cfg = load_config(overrides={**OVER, "servo": {"min_floor_gap": 7.0}})
    with pytest.raises(ServoError, match=r"servo\.min_floor_gap = 7"):
        _bays(servo_wing_bodies("extrados"), cfg)
    with pytest.raises(ValueError, match="servo"):
        load_config(overrides={"servo": {"strip": 0}})


_RUNS: dict = {}


@pytest.fixture(scope="module")
def run_of(tmp_path_factory):
    """(STEP, génération) de l'aile synthétique avec baie, une seule par côté pour tout le module. Lue depuis le
    STEP écrit, comme l'aperçu (l'aller-retour STEP déplace légèrement l'orientation)."""
    root = tmp_path_factory.mktemp("servo")

    def get(side):
        if side not in _RUNS:
            cfg = load_config(overrides=PLAIN if side == "sans_renfort" else OVER)
            if side == "gaine":
                bodies = servo_wing_bodies("extrados", cable=True)
            else:
                bodies = servo_wing_bodies("extrados" if side == "sans_renfort" else side)
            step = write_step(root / f"servo_{side}.step", bodies)
            _RUNS[side] = step, run_model(classify(read_step(step), cfg), cfg)
        return _RUNS[side]

    yield get
    _RUNS.clear()


@pytest.fixture(scope="module", params=["extrados", "intrados"])
def servo_run(request, run_of):
    return request.param, load_config(overrides=OVER), run_of(request.param)[1]


def test_cable_entering_bay_is_printable(run_of):
    """Gaine qui débouche dans la baie sous son fond (comme la fixture servo) : son bord dans la poche est une
    ouverture voulue, pas une « peau entamée »."""
    _, run = run_of("gaine")
    (body,) = run.bodies
    assert [b.cable for b in body.bores] == [True] and len(body.bays) == 1
    # anneau sans nervure autour de la gaine là où elle débouche dans la poche ; pont de la fente de renfort autour
    (bay,) = body.bays
    (b,) = body.bores
    ((x, y, ring, za, zb),) = bay.rings
    # anneau = servo.strip (défaut 1,2 ligne) : les nervures se soudent au trou comme à la baie
    assert (x, y) == (b.x, b.y) and ring == pytest.approx(b.radius + 1.2 * 0.42) and za < bay.z0 < zb
    assert body.report.ok, [(i.z, i.message, i.bounds) for i in body.report.errors][:5]
    # ponts par tranche : autour de la gaine là où elle rejoint la baie, pont minimal ailleurs
    z_hole, z_free = (bay.z0 + b.z1) / 2, (b.z1 + bay.z1) / 2 + 5  # dans la bande de la gaine / au-delà de son bout
    assert bay.z0 < z_hole < b.z1 < z_free < bay.z1

    def gap_at(z):
        (g,) = [g[2:] for g in bay.rf_gaps if g[0] <= z <= g[1]]
        return g

    ga, gb = gap_at(z_hole)
    assert ga <= b.x - b.radius - 1.0 and gb >= b.x + b.radius + 1.0
    ga, gb = gap_at(z_free)
    assert gb - ga == pytest.approx(1.0)
    assert bay.rf_lost == ()
    # le fond reste doublé hors de la bande de la gaine : fente sous le fond sur toute la corde sauf le pont
    from shapely.geometry import LineString

    from aerorib.slicing import slice_layers

    def floor_slit(z):
        (layer,) = slice_layers(body.result, [z], 0.02)
        y = bay.y_floor - bay.rf_offset - bay.rf_width / 2
        line = LineString([(bay.x0, y), (bay.x1, y)])
        return line.length - sum(line.intersection(p).length for p in layer.polygons)

    assert floor_slit(z_free) > (bay.x1 - bay.x0) - 1.0 - 0.3  # tout le fond sauf le pont minimal
    # dans la bande de la gaine : fente + trou, moins les deux ponts de 1 mm de part et d'autre du trou
    assert floor_slit(z_hole) == pytest.approx((bay.x1 - bay.x0) - 2.0, abs=0.3)


def test_preview_equals_generation_with_cable(run_of):
    cfg = load_config(overrides=OVER)
    step, run = run_of("gaine")
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    assert prep.bays == run.bodies[0].bays
    plates = preview.plates_2d(prep, cfg)
    assert len(plates) == run.bodies[0].plates
    (bay,) = prep.bays
    for z in (bay.z0 - bay.ramp / 2, (bay.z0 + bay.z1) / 2, bay.z1 - 2):
        view = preview.section(prep, plates, cfg, z)
        assert not [d for d in view.defects if d.check == "connectivity"], z


def test_hole_at_box_edge_loses_a_branch_with_warning():
    from aerorib.features.bores import Bore
    from aerorib.features.servo import reinforce_warnings

    cfg = load_config(overrides=OVER)
    (bay,), skin, mid = _bays(servo_wing_bodies("extrados"), cfg)
    _, servos = _oriented(servo_wing_bodies("extrados"), cfg)
    hole = Bore("SBAR_bord", bay.x0, bay.y_floor, 2.0, 0.5, 0.42, 1, True, 0.0, 120.0, 0.15, 0.15, cable=True)
    (edge,) = servo_bays(servos, skin, mid, cfg, [hole])
    assert edge.rf_lost and all(g[2] >= edge.x0 - edge.reach - 1e-9 for g in edge.rf_gaps)  # bornés
    (msg,) = reinforce_warnings([edge])
    assert msg.startswith("SERVO_1 : renfort incomplet") and reinforce_warnings([bay]) == []
    assert format_bays([edge]).endswith(", renfort partiel)")


def test_cable_breaching_skin_elsewhere_is_still_reported():
    """La partie du trou exclue de la référence (rencontre avec la poche) ne masque pas une percée de la peau
    ailleurs le long de la gaine : ici une encoche de l'intrados près de l'emplanture, que le trou traverse."""
    from build123d import Box, Location

    from aerorib.io_step import NamedBody

    wing, servo, tube = servo_wing_bodies("extrados", cable=True)
    notch = Box(12, 9.5, 12).moved(Location((32, -5.25, 14)))  # intrados ramené à y = -0,5 (bas du trou : -1,15)
    bodies = [NamedBody(wing.path, (wing.solid - notch).solids()[0]), servo, tube]
    cfg = load_config(overrides=OVER)
    (body,) = run_model(classify(bodies, cfg), cfg).bodies
    skin = [i for i in body.report.errors if i.check == "skin"]
    assert skin, "percée de la gaine dans l'intrados non signalée"
    assert all(i.z < 30 for i in skin)  # seulement à l'encoche, rien autour de la baie


def test_servo_wing_is_printable(servo_run):
    side, cfg, run = servo_run
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:5]
    assert not [i for i in body.report.issues if i.check == "connectivity"]  # un seul contour par couche
    (bay,) = body.bays
    assert bay.side == (1 if side == "extrados" else -1)
    # la poche est bien ouverte : à mi-baie, plus de matière dans la boîte
    from aerorib.slicing import slice_layers

    (layer,) = slice_layers(body.result, [(bay.z0 + bay.z1) / 2], cfg.validation.tessellation_tol)
    from shapely.geometry import box

    inner = box(bay.x0 + 0.5, min(bay.y_floor, bay.y_box) + 0.5, bay.x1 - 0.5, max(bay.y_floor, bay.y_box) - 0.5)
    assert sum(p.intersection(inner).area for p in layer.polygons) < 0.5
    text = format_bays(body.bays)
    assert f"SERVO_1 (baie {side}, 20.0 × 35.0 mm utiles, ouverture" in text and text.endswith(", renfort)")


def _opening_mouths(body, bay, cfg) -> int:
    """Bouches sur la peau d'ouverture, à mi-baie, dans la largeur de la baie (renfort et bande compris)."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    from aerorib.slicing import slice_layers

    z = (bay.z0 + bay.z1) / 2
    (orig,), (res,) = (slice_layers(s, [z], cfg.validation.tessellation_tol) for s in (body.original, body.result))
    window = box(bay.x0 - bay.reach - 1.0, -1e3, bay.x1 + bay.reach + 1.0, 1e3)
    skin = unary_union([p.exterior for p in orig.polygons]).intersection(window)
    kept = unary_union([p.boundary for p in res.polygons]).buffer(cfg.validation.skin_tolerance)
    missing = skin.difference(kept)
    return len([g for g in getattr(missing, "geoms", [missing]) if g.length > 0.05])


def test_reinforcement_doubles_the_pocket_walls(run_of):
    cfg = load_config(overrides=OVER)
    (body,) = run_of("extrados")[1].bodies
    (plain,) = run_of("sans_renfort")[1].bodies
    (bay,), (pbay,) = body.bays, plain.bays
    assert pbay.rf_offset == 0 and pbay.rf_gaps == () and bay.rf_offset == pytest.approx(1.2 * cfg.preset.line_width)
    assert plain.report.ok and format_bays(plain.bays).endswith("mm)")  # pas de « renfort »
    # 2 bouches de plus sur la peau d'ouverture : les deux branches de la fente
    assert _opening_mouths(body, bay, cfg) == _opening_mouths(plain, pbay, cfg) + 2
    # paroi supplémentaire parallèle à chaque paroi latérale : matière | fente | matière, puis la poche
    from shapely.geometry import Point

    from aerorib.slicing import slice_layers

    (layer,) = slice_layers(body.result, [(bay.z0 + bay.z1) / 2], cfg.validation.tessellation_tol)
    y = bay.y_floor + 1.0
    o, w = bay.rf_offset, bay.rf_width

    def solid(x):
        return any(p.contains(Point(x, y)) for p in layer.polygons)

    for wall, out in ((bay.x0, -1), (bay.x1, 1)):
        assert not solid(wall - out * 0.1)  # poche
        assert solid(wall + out * o / 2)  # paroi doublée (entre poche et fente)
        assert not solid(wall + out * (o + w / 2))  # fente
        assert solid(wall + out * (o + w + 0.15))  # matière au-delà
    # paroi doublée ≥ largeur de ligne, mesurée dans la coupe : parois latérales et fond
    from shapely.geometry import LineString

    lw = cfg.preset.line_width
    lines = [
        LineString([(bay.x0 - bay.reach - 0.1, y), (bay.x0 + 0.05, y)]),
        LineString([(bay.x1 + bay.reach + 0.1, y), (bay.x1 - 0.05, y)]),
        LineString([(bay.x0 + 2, bay.y_floor - bay.reach - 0.1), (bay.x0 + 2, bay.y_floor + 0.05)]),
    ]
    for line in lines:
        cut = line.intersection(layer.polygons[0])
        pieces = sorted(getattr(cut, "geoms", [cut]), key=lambda g: g.distance(line.interpolate(1.0, normalized=True)))
        # lignes de la matière vers la poche ; morceau côté poche (la ligne finit 0,05 mm dans la poche) : la paroi doublée, jusqu'à la fente
        assert pieces[0].length >= lw - 0.02 and pieces[0].length == pytest.approx(o, abs=0.05)
    (pl,) = slice_layers(plain.result, [(pbay.z0 + pbay.z1) / 2], cfg.validation.tessellation_tol)
    assert any(p.contains(Point(pbay.x0 - pbay.rf_offset - 0.42, pbay.y_floor + 1.0)) for p in pl.polygons)


def test_ramps_have_no_overhang_beyond_limit(servo_run):
    _, cfg, run = servo_run
    (body,) = run.bodies
    (bay,) = body.bays
    limit = cfg.preset.max_overhang_deg
    mesh = to_mesh(body.result, cfg.validation.tessellation_tol)
    c = mesh.triangles_center
    n = mesh.face_normals
    lo, hi = bay.opening
    ylo, yhi = sorted((bay.y_floor, bay.y_out))
    near = (
        (c[:, 0] > bay.x0 - 0.3) & (c[:, 0] < bay.x1 + 0.3)
        & (c[:, 2] > lo - 0.3) & (c[:, 2] < hi + 0.3)
        & (c[:, 1] > ylo - 0.3) & (c[:, 1] < yhi + 0.3)
    )
    assert near.sum() > 10
    down = near & (n[:, 2] < 0)
    over = np.degrees(np.arcsin(np.clip(-n[down, 2], 0, 1)))
    assert over.max() <= limit + 1.0
    # les rampes existent aux deux bouts (pente à la limite, vers le haut et vers le bas)
    assert over.max() == pytest.approx(limit, abs=1.0)
    up = near & (n[:, 2] > 0)
    assert np.degrees(np.arcsin(np.clip(n[up, 2], 0, 1))).max() == pytest.approx(limit, abs=1.0)


def test_preview_plates_equal_generation_with_servo(run_of):
    cfg = load_config(overrides=OVER)
    step, run = run_of("extrados")
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    assert prep.bays == run.bodies[0].bays
    assert len(plates) == run.bodies[0].plates
    # la baie retire bien de la matière de plaques (zones de poche + ombre)
    from dataclasses import replace

    free = preview.plates_2d(replace(prep, bays=()), cfg)
    assert sum(p.polygon.area for p in plates) < sum(p.polygon.area for p in free) - 50
    # coupe d'aperçu à mi-baie : la poche y est retirée, sans défaut de connexité
    (bay,) = prep.bays
    view = preview.section(prep, plates, cfg, (bay.z0 + bay.z1) / 2)
    from shapely.geometry import Point

    assert not any(p.contains(Point((bay.x0 + bay.x1) / 2, bay.y_floor + 0.5)) for p in view.polygons)
    assert not [d for d in view.defects if d.check == "connectivity"]


def test_prepare_key_follows_servo_and_material():
    base = preview.prepare_key(load_config())
    assert base != preview.prepare_key(load_config(overrides={"servo": {"min_floor_gap": 2.0}}))
    assert base != preview.prepare_key(load_config(overrides={"material_settings": {"max_overhang_deg": 40}}))
    # hauteur de couche (calage des ponts) et épaisseur des nervures (y_skin) entrent dans les baies en cache
    assert base != preview.prepare_key(load_config(overrides={"material_settings": {"layer_height": 0.3}}))
    assert base != preview.prepare_key(load_config(overrides={"slit_width": 0.2}))


def test_prepare_follows_layer_height(run_of):
    """Aperçu préparé avec une hauteur de couche puis une autre : les baies suivent (pas de cache périmé)."""
    step, _ = run_of("extrados")
    preview.clear_cache()
    a = preview.prepare(step, load_config(overrides=OVER))
    cfg_b = load_config(overrides={**OVER, "material_settings": {"layer_height": 0.3}})
    b = preview.prepare(step, cfg_b)
    (bay_a,), (bay_b,) = a.bays, b.bays
    assert bay_a.rf_gaps != bay_b.rf_gaps
    for za, zb, _, _ in bay_b.rf_gaps:
        for z in (za, zb):
            k = (z - bay_b.zmin) / 0.3
            assert k == pytest.approx(round(k), abs=1e-6)


@pytest.mark.slow
def test_servo_fixture_end_to_end():
    cfg = load_config()
    run = run_model(classify(read_step(FIXTURES / "aile_4412_servo_extrados.step"), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message, i.bounds) for i in body.report.errors][:10]
    (bay,) = body.bays
    assert bay.name == "SERVO_1" and bay.side == 1 and bay.rf_offset > 0
    print(format_bays(body.bays), body.plates, body.report.stats, body.warnings)


def test_bridge_bands_snap_between_layers():
    """Bornes des tranches de ponts calées sur zmin + k·hauteur de couche (mi-chemin entre deux couches) : aucune
    couche ne tombe sur une borne, l'aperçu et la génération choisissent la même tranche (une seule par couche)."""
    from aerorib.features.bores import Bore
    from aerorib.slicing import layer_heights

    lh = 0.3
    cfg = load_config(overrides={**OVER, "material_settings": {"layer_height": lh}})
    skin, servos = _oriented(servo_wing_bodies("extrados"), cfg)
    mid = MidSurface.from_skin(skin, *cfg.split.grid)
    (bay,) = servo_bays(servos, skin, mid, cfg)
    xm = (bay.x0 + bay.x1) / 2
    hole = Bore("SBAR_CABLE_x", xm, bay.y_floor - 2.0, 1.5, 0.5, 0.42, 1, True, 0.0, 57.37, 0.15, 0.15, cable=True)
    # bout du trou (avec son jeu) à 0,2 couche au-dessus d'une borne : le plus proche le rognerait
    from dataclasses import replace

    hole = replace(hole, z1=bay.zmin + 191.2 * lh - hole.clearance)
    (bay,) = servo_bays(servos, skin, mid, cfg, [hole])
    assert len(bay.rf_gaps) > 3
    for za, zb, _, _ in bay.rf_gaps:
        for z in (za, zb):
            k = (z - bay.zmin) / lh
            assert k == pytest.approx(round(k), abs=1e-6), z
    # étendue de la fente du trou arrondie vers l'extérieur : juste sous son bout, le pont contourne encore le trou
    from aerorib.features.bores import bore_extent

    _, top = bore_extent(hole, bay.zmin, bay.zmax)
    assert 0.1 < (top - bay.zmin) / lh % 1 < 0.5  # le plus proche serait en dessous : le cas à couvrir
    (g,) = [g for g in bay.rf_gaps if g[0] <= top - 0.01 <= g[1]]
    assert g[2] <= xm - hole.slit / 2 and g[3] >= xm + hole.slit / 2
    lo, hi = bay.rf_gaps[0][0], bay.rf_gaps[-1][1]
    for z in layer_heights(bay.zmin, bay.zmax, lh):
        if lo < z < hi:
            assert sum(za <= z <= zb for za, zb, _, _ in bay.rf_gaps) == 1, z


def test_planform_lists_bays(run_of):
    """Vue de dessus : baie (boîte utile, ouverture rampes comprises, côté, renfort), sérialisée ; avertissements
    du renfort dans les estimations (une seule fois)."""
    from dataclasses import replace

    cfg = load_config(overrides=OVER)
    step, _ = run_of("extrados")
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    (bay,) = prep.bays
    d = preview.planform(prep, plates, cfg).to_dict()
    (b,) = d["baies"]
    lo, hi = bay.opening
    assert b["nom"] == "SERVO_1" and b["cote"] == "extrados" and b["renfort"] is True
    assert (b["x0"], b["x1"], b["z0"], b["z1"]) == pytest.approx((bay.x0, bay.x1, bay.z0, bay.z1), abs=1e-3)
    assert b["ouverture"] == pytest.approx([lo, hi], abs=1e-3) and lo < b["z0"] < b["z1"] < hi
    assert b["profondeur"] == pytest.approx(bay.depth, abs=1e-3) and b["renfort_partiel"] is False
    import json

    json.dumps(d)
    lost = replace(bay, rf_lost=((40.0, 45.0),))
    lost_prep = replace(prep, bays=(lost,))
    view = preview.planform(lost_prep, plates, cfg)
    assert view.to_dict()["baies"][0]["renfort_partiel"] is True
    # avertissement du renfort émis une seule fois, avec les estimations (comme les murs)
    assert not any("renfort incomplet" in w for w in view.warnings)
    est = preview.estimates(lost_prep, plates, cfg, cached=True)
    assert sum(w.startswith("SERVO_1 : renfort incomplet") for w in est["avertissements"]) == 1
    plain = preview.planform(replace(prep, bays=(replace(bay, rf_offset=0.0, rf_width=0.0),)), plates, cfg)
    assert plain.to_dict()["baies"][0]["renfort"] is False


def test_section_draws_the_reinforcement_slit(run_of):
    """Coupe d'aperçu à mi-baie : la fente de renfort (même code que la génération) est retirée de la matière, sauf
    au pont."""
    from shapely.geometry import LineString

    cfg = load_config(overrides=OVER)
    step, run = run_of("extrados")
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    (bay,) = prep.bays
    z = (bay.z0 + bay.z1) / 2
    view = preview.section(prep, plates, cfg, z)
    y = bay.y_floor - bay.rf_offset - bay.rf_width / 2
    line = LineString([(bay.x0, y), (bay.x1, y)])
    solid = sum(line.intersection(p).length for p in view.polygons)
    (gap,) = [g for g in bay.rf_gaps if g[0] <= z <= g[1]]
    assert solid == pytest.approx(gap[3] - gap[2], abs=0.2)  # seule matière sur la ligne de la fente : le pont


def test_html_report_lists_bays_and_conduits(run_of, tmp_path):
    from aerorib.export import export_results

    _, run = run_of("gaine")
    (path,) = [p for p in export_results(run, tmp_path, "aile", []) if p.suffix == ".html"]
    html = path.read_text(encoding="utf-8")
    assert "SBAR_CABLE_SERVO (gaine, Ø" in html
    assert "baies : SERVO_1 (baie extrados" in html and "renfort)" in html


def test_format_bays_names_the_segments():
    (bay,), _, _ = _bays(servo_wing_bodies("extrados"))
    lo, hi = (z - bay.zmin for z in bay.opening)
    assert "tronçon" not in format_bays([bay], [120.0])  # pièce non découpée
    assert format_bays([bay], [hi + 10, 50.0]).endswith(", renfort, tronçon 1)")
    assert format_bays([bay], [lo + 5, 200.0]).endswith(", renfort, tronçons 1 et 2)")
    assert format_bays([bay], [lo - 5, 200.0]).endswith(", renfort, tronçon 2)")
    assert format_bays([bay], [lo + 1, 2.0, 200.0]).endswith(", renfort, tronçons 1, 2 et 3)")


@pytest.mark.slow
def test_bay_at_root_ramp_leaves_through_root_face(tmp_path):
    """Baie près de l'emplanture (z0 = 3) : la rampe basse sort par la face d'emplanture ; imprimable, et l'aperçu
    donne les mêmes baies et plaques que la génération (~20 s)."""
    cfg = load_config(overrides=OVER)
    step = write_step(tmp_path / "racine.step", servo_wing_bodies(box=(22.0, 42.0, 3.0, 16.0, 3.0, 38.0), cable=True))
    (body,) = run_model(classify(read_step(step), cfg), cfg).bodies
    (bay,) = body.bays
    assert bay.opening[0] == pytest.approx(bay.zmin) and bay.z0 - bay.ramp < bay.zmin  # rampe coupée par l'emplanture
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:5]
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    assert prep.bays == body.bays and len(preview.plates_2d(prep, cfg)) == body.plates


def _rib_gaps(step, cfg, zs):
    """Distance minimale, sur les coupes d'aperçu aux hauteurs zs, entre les fentes de nervure (dans la peau) et la
    fente de renfort / la poche."""
    from shapely.ops import unary_union

    from aerorib.features.servo import reinforce_section

    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    (bay,) = prep.bays
    best = math.inf
    for z in zs:
        view = preview.section(prep, plates, cfg, z)
        feet = unary_union([f.polygon for f in view.footprints]).intersection(unary_union(view.skin))
        slit = unary_union(reinforce_section(bay, z))
        if not feet.is_empty and not slit.is_empty:
            best = min(best, feet.distance(slit))
    return best, bay


def test_ribs_are_welded_to_the_bay_at_mid_bay(run_of):
    """Défaut : entre le bout d'une fente de nervure et le bord extérieur de la fente de renfort, une matière de
    1 à 1,6 largeur de ligne — gardée par le slicer, et assez fine pour souder la nervure à la paroi doublée."""
    cfg = load_config(overrides=OVER)
    lw = cfg.preset.line_width
    step, run = run_of("extrados")
    (bay,) = run.bodies[0].bays
    zs = np.arange(bay.z0 + 0.5, bay.z1 - 0.5, 0.05)  # fin : chaque nervure rencontre la paroi à une hauteur précise
    gap, _ = _rib_gaps(step, cfg, zs)
    assert lw <= gap <= 1.6 * lw, gap
    assert run.bodies[0].report.ok


def test_fixture_layers_by_the_cable_bridge_are_printable():
    """Fixture servo, couches z = 163,5 et 166,5 (bande réduite à 0,6 / 1 mm avant correctif : « matière trop
    fine »). Une nervure basse coupée par l'anneau de la gaine finissait en pointe oblique à ≈ 1 mm sous le bout
    de la branche en J (pont autour de la gaine) : l'érosion d'une demi-ligne (onglets) y séparait le contour.
    Les nervures gardent BRIDGE_STRIP autour des ponts."""
    from aerorib.features.servo import BRIDGE_STRIP

    step = FIXTURES / "aile_4412_servo_extrados.step"
    for strip in (None, 0.6, 1.0):
        cfg = load_config(overrides={"servo": {"strip": strip}} if strip else {})
        prep = preview.prepare(step, cfg)
        plates = preview.plates_2d(prep, cfg)
        (bay,) = prep.bays
        for z in (163.5, 166.5):
            view = preview.section(prep, plates, cfg, z)
            assert not [d for d in view.defects if d.severity == "error"], (strip, z, view.defects)
            # bout de la branche en J côté emplanture du pont de la gaine : aucune nervure à moins de BRIDGE_STRIP
            ((xa, _),) = [g[2:] for g in bay.rf_gaps if g[0] <= z <= g[1]]
            from shapely.geometry import Point
            from shapely.ops import unary_union

            end = Point(xa, bay.y_floor - bay.reach)
            feet = unary_union([f.polygon for f in view.footprints]).intersection(unary_union(view.skin))
            assert feet.distance(end) >= BRIDGE_STRIP - 0.05
