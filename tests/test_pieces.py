"""STEP à plusieurs corps d'aile (découpés dans la CAO) : un morceau par corps, nervures alignées à la jonction."""

import math

import pytest

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.features.ribs import diamond_profiles, plate_lines
from aerorib.midsurface import MidSurface
from aerorib.orient import orient
from aerorib.pipeline import rib_options, run_model, skins_anchor
from aerorib.testing.synthetic import split_wing_bodies

FAST = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}]})
JOINT_Z = 35.0  # pas un multiple de l'espacement : sans calage commun, les plans des deux corps seraient décalés


def _oriented(bodies, cfg=FAST):
    model = classify(bodies, cfg)
    o = orient(model, cfg)
    return model, o, [s.body.solid.moved(o.location) for s in model.of("skin")]


def _planes(skin, cfg, anchor):
    """Plans des plaques d'un corps, dans le repère global : (famille, x en z = 0, z min, z max de la plaque)."""
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, cfg.validation.tessellation_tol)
    out = set()
    for i, layer in enumerate(cfg.rib_layers):
        for p in diamond_profiles(bb, mid, [], layer, **rib_options(cfg, bb, i, (), anchor)):
            s0, s1 = p.polygon.bounds[0], p.polygon.bounds[2]
            z0, z1 = max(bb.min.Z + s0 * p.d1, bb.min.Z), min(bb.min.Z + s1 * p.d1, bb.max.Z)
            x_at_0 = p.c - p.d0 / p.d1 * bb.min.Z
            out.add((p.family, round(x_at_0, 6), round(z0, 3), round(z1, 3)))
    return out


def _on_lattice(planes, anchor, cfg):
    layer = cfg.rib_layers[0]
    tan = math.tan(math.radians(cfg.rib_angle(layer)))
    for fam, x0, _, _ in planes:
        k = (x0 + fam * tan * anchor.min.Z - anchor.min.X - layer.phase) / layer.spacing  # x en z = anchor.min.Z
        if abs(k - round(k)) > 1e-6:
            return False
    return True


def test_plate_lines_anchor_on_own_box_is_unchanged():
    _, _, (skin,) = _oriented(split_wing_bodies("z")[::2])
    bb = skin.bounding_box()
    layer = FAST.rib_layers[0]
    assert plate_lines(bb, layer, 45.0, bb) == plate_lines(bb, layer, 45.0)


def test_orientation_of_split_wing_is_the_whole_wing():
    """Orientation commune : emplanture au bas de l'envergure totale, corde et extrados de l'aile entière."""
    for split in ("z", "chord"):
        _, _, skins = _oriented(split_wing_bodies(split, at=JOINT_Z if split == "z" else None))
        bbs = [s.bounding_box() for s in skins]
        assert min(b.min.Z for b in bbs) == pytest.approx(0.0, abs=1e-3)
        assert max(b.max.Z for b in bbs) == pytest.approx(80.0, abs=1e-3)
        assert max(b.max.X for b in bbs) - min(b.min.X for b in bbs) == pytest.approx(100.0, abs=0.5)
    _, _, whole = _oriented(split_wing_bodies("z")[::2])  # premier corps seul (et le longeron) : même sens de corde et d'extrados
    _, _, (one, _) = _oriented(split_wing_bodies("z"))
    ba, bb = one.bounding_box(), whole[0].bounding_box()
    for a, b in zip((*ba.min, *ba.max), (*bb.min, *bb.max)):
        assert a == pytest.approx(b, abs=0.05)


def test_split_in_z_ribs_on_the_same_planes_at_the_joint():
    _, _, skins = _oriented(split_wing_bodies("z", at=JOINT_Z))
    anchor = skins_anchor(skins)
    low, high = (_planes(s, FAST, anchor) for s in skins)
    assert _on_lattice(low, anchor, FAST) and _on_lattice(high, anchor, FAST)
    # plaques qui arrivent à la jonction (par le bas) et qui en partent (par le haut) : mêmes plans
    # (loin des bords d'attaque et de fuite, où les plaques s'arrêtent avant les parois raides)
    x_lo, x_hi = anchor.min.X + 10, anchor.max.X - 10
    tan = math.tan(math.radians(FAST.rib_angle(FAST.rib_layers[0])))

    def inner(f, x):
        return x_lo < x + f * tan * JOINT_Z < x_hi

    at_joint_low = {(f, x) for f, x, _, z1 in low if z1 > JOINT_Z - 1 and inner(f, x)}
    at_joint_high = {(f, x) for f, x, z0, _ in high if z0 < JOINT_Z + 1 and inner(f, x)}
    assert len(at_joint_low) >= 4 and at_joint_low == at_joint_high
    # sans calage commun, les plans du corps haut seraient décalés (le test a du sens)
    unanchored = {(f, x) for f, x, z0, _ in _planes(skins[1], FAST, None) if z0 < JOINT_Z + 1 and inner(f, x)}
    assert unanchored != at_joint_low


def test_split_in_chord_ribs_on_the_same_planes():
    _, _, skins = _oriented(split_wing_bodies("chord"))
    anchor = skins_anchor(skins)
    lead, trail = (_planes(s, FAST, anchor) for s in skins)
    assert lead and trail
    assert _on_lattice(lead, anchor, FAST) and _on_lattice(trail, anchor, FAST)
    assert {(f, x) for f, x, _, _ in lead} & {(f, x) for f, x, _, _ in trail}  # plans prolongés d'un corps à l'autre
    assert not _on_lattice(_planes(skins[1], FAST, None), anchor, FAST)


# --- génération complète (booléens) ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_z():
    return run_model(classify(split_wing_bodies("z", at=JOINT_Z), FAST), FAST)


def test_split_in_z_gives_one_printable_piece_per_body(run_z):
    assert [b.name for b in run_z.bodies] == ["WING_1", "WING_2"]
    assert run_z.ok, [i.message for b in run_z.bodies for i in b.report.errors][:5]
    low, high = (b.result.bounding_box() for b in run_z.bodies)
    assert low.min.Z == pytest.approx(0.0, abs=1e-3) and low.max.Z == pytest.approx(JOINT_Z, abs=1e-3)
    assert high.min.Z == pytest.approx(JOINT_Z, abs=1e-3) and high.max.Z == pytest.approx(80.0, abs=1e-3)
    for b in run_z.bodies:
        assert b.plates > 0 and len(b.segments) == 1 and not b.notices


def test_spar_across_a_spanwise_joint_is_held_in_both_bodies(run_z):
    for b in run_z.bodies:
        (spar,) = [x for x in b.bores if x.cut_hole]
        assert spar.name == "SBAR_8"
    # trou débouchant à la jonction des deux côtés (le longeron traverse)
    lo, hi = (next(x for x in b.bores if x.cut_hole) for b in run_z.bodies)
    assert lo.z1 > JOINT_Z and hi.z0 < JOINT_Z


def test_split_in_chord_gives_one_printable_piece_per_body():
    run = run_model(classify(split_wing_bodies("chord"), FAST), FAST)
    assert [b.name for b in run.bodies] == ["WING_1", "WING_2"]
    assert run.ok, [i.message for b in run.bodies for i in b.report.errors][:5]
    lead, trail = run.bodies
    assert [x.name for x in lead.bores if x.cut_hole] == ["SBAR_8"]  # le longeron est dans le corps avant
    assert not [x for x in trail.bores if x.cut_hole]
    assert lead.plates > 0 and trail.plates > 0


def _no_plates(monkeypatch):
    """Génération rapide (sans plaque) ; renvoie les ancres reçues par `diamond_plates`."""
    from aerorib import pipeline

    seen = []

    def fake(bb, mid, bores, layer, **opts):
        seen.append(opts["anchor"])
        return []

    monkeypatch.setattr(pipeline, "diamond_plates", fake)
    return seen


def test_generation_uses_the_global_box_for_every_body(monkeypatch):
    seen = _no_plates(monkeypatch)
    run = run_model(classify(split_wing_bodies("z", at=JOINT_Z), FAST), FAST)
    assert len(seen) == 2 and seen[0] is seen[1]
    assert (seen[0].min.Z, seen[0].max.Z) == (pytest.approx(0.0, abs=1e-3), pytest.approx(80.0, abs=1e-3))
    assert [b.name for b in run.bodies] == ["WING_1", "WING_2"]


def test_single_skin_has_no_anchor(monkeypatch):
    seen = _no_plates(monkeypatch)
    run_model(classify(split_wing_bodies("z")[::2], FAST), FAST)
    assert seen == [None]


def test_body_still_too_tall_is_cut_again_with_a_notice(monkeypatch):
    _no_plates(monkeypatch)
    cfg = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}],
                                 "segment": {"max_height": 30}})
    run = run_model(classify(split_wing_bodies("z", at=JOINT_Z), cfg), cfg)
    low, high = run.bodies
    assert len(low.segments) == 2 and len(high.segments) == 2
    for b in run.bodies:
        assert any("déjà découpé dans la CAO" in n and "2 morceaux" in n for n in b.notices), b.notices


def test_servo_bay_goes_to_the_body_it_crosses(monkeypatch):
    from build123d import Box, Location

    from aerorib.io_step import NamedBody
    from aerorib.testing.synthetic import SERVO_BOXES

    _no_plates(monkeypatch)
    x0, x1, y0, y1, z0, z1 = SERVO_BOXES["extrados"]  # z de 40 à 75 : dans le corps haut seulement
    box = Box(x1 - x0, y1 - y0, z1 - z0).moved(Location(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)))
    bodies = split_wing_bodies("z", at=JOINT_Z, spar_x=None) + [NamedBody(("SERVO_1",), box.solids()[0])]
    cfg = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}],
                                 "orientation": {"axis": [0, 0, 1]}})
    low, high = run_model(classify(bodies, cfg), cfg).bodies
    assert low.bays == () and [b.name for b in high.bays] == ["SERVO_1"]


def test_export_of_several_bodies(run_z, tmp_path):
    """Un fichier STL et un rapport par corps, un plateau par morceau dans le 3MF, noms des corps."""
    import zipfile
    from pathlib import Path

    from aerorib.bambu import read_template
    from aerorib.export import export_results

    template = read_template(Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf")
    paths = export_results(run_z, tmp_path, "aile", ["step", "stl", "3mf"], template=template)
    names = sorted(p.name for p in paths)
    assert names == sorted([
        "aile.step", "aile-WING_1.stl", "aile-WING_2.stl", "aile-WING_1-rapport.html", "aile-WING_2-rapport.html",
        "aile.3mf",
    ])
    with zipfile.ZipFile(tmp_path / "aile.3mf") as z:
        model = z.read("3D/3dmodel.model").decode()
        settings = z.read("Metadata/model_settings.config").decode()
    assert model.count("<item ") == 2
    assert "WING_1" in settings and "WING_2" in settings
    assert settings.count("<plate>") == 2


# --- aperçu ---------------------------------------------------------------------------------------------------


def _prep(tmp_path, bodies):
    from aerorib import preview
    from aerorib.io_step import write_step

    return preview.prepare(write_step(tmp_path / "aile.step", bodies), FAST)


def test_preview_of_several_bodies_matches_generation(run_z, tmp_path):
    from aerorib import preview

    prep = _prep(tmp_path, split_wing_bodies("z", at=JOINT_Z))
    assert [p.name for p in prep.parts] == ["WING_1", "WING_2"]
    assert prep.zrange == (pytest.approx(0.0, abs=1e-3), pytest.approx(80.0, abs=1e-3))
    plates = preview.plates_2d(prep, FAST)
    for k, b in enumerate(run_z.bodies):  # mêmes plaques que la génération, corps par corps
        assert sum(p.body == k for p in plates) == b.plates
    est = preview.estimates(prep, plates, FAST)
    assert est["plaques"] == sum(b.plates for b in run_z.bodies) and est["troncons"] is not None
    plan = preview.planform(prep, plates, FAST).to_dict()
    assert [c["nom"] for c in plan["corps"]] == ["WING_1", "WING_2"]
    assert plan["aile"][1] == pytest.approx(0.0, abs=1e-3) and plan["aile"][3] == pytest.approx(80.0, abs=1e-3)
    assert sorted(t["nom"] for t in plan["trous"]) == ["SBAR_8", "SBAR_8"]
    for z in (JOINT_Z / 2, (JOINT_Z + 80) / 2):  # un seul corps à chaque hauteur, avec son longeron
        view = preview.section(prep, plates, FAST, z)
        assert len(view.polygons) == 1 and view.footprints and view.defects == []
        assert [h["nom"] for h in view.holes] == ["SBAR_8"]
    assert {d["nom"] for d in prep.to_dict()["morceaux"]} == {"WING_1", "WING_2"}
    assert preview.plates_mesh(prep, plates) and preview.skin_mesh(prep)


def test_preview_section_shows_every_body_at_that_height(tmp_path):
    from aerorib import preview

    prep = _prep(tmp_path, split_wing_bodies("chord"))
    plates = preview.plates_2d(prep, FAST)
    assert {p.body for p in plates} == {0, 1}
    view = preview.section(prep, plates, FAST, 40.5)
    assert len(view.polygons) == 2  # corps avant et corps arrière, imprimés séparément
    xs = sorted(p.bounds[0] for p in view.polygons)
    assert xs[1] - xs[0] == pytest.approx(50.0, abs=1.0)
