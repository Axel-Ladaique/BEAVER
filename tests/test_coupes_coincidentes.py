"""Coupes en Z sur des faces coïncidentes (bout de longeron, de gaine…) : le planificateur s'en écarte, la découpe se
replie en décalant la coupe si le booléen échoue malgré tout (retour utilisateur : aile ×3, « coupe dans la corde
impossible ('NoneType' object has no attribute 'solids') »)."""

import time
from pathlib import Path

import numpy as np
import pytest
from build123d import Cylinder, Location, Shape

from beaver.classify import classify
from beaver.config import BoreSettings, load_config
from beaver.io_step import NamedBody, read_step
from beaver.pipeline import critical_planes, rib_crossings, run_model
from beaver.segment import CUT_CLEARANCE, CutFailure, SegmentError, cut_chain, plan_pieces, split_part
from beaver.slicing import layer_heights
from beaver.testing.synthetic import naca_wing, ribbed_wing

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = FIXTURES / "bambu_p1s_pla_aero.3mf"
BED = (256.0, 256.0)


def _square(zs):
    return [np.array([(0, 0), (50, 0), (50, 10), (0, 10)], float) for _ in zs]


# --- planificateur ------------------------------------------------------------------------------------------------


def test_auto_cut_moved_off_critical_plane():
    plan = plan_pieces(200, 110, _square, BED, 5.0)
    assert plan.cuts == (100.0,)
    plan = plan_pieces(200, 110, _square, BED, 5.0, critical=[100.0, 100.2])
    (c,) = plan.cuts
    assert min(abs(c - 100.0), abs(c - 100.2)) >= CUT_CLEARANCE - 1e-9
    assert c == pytest.approx(99.5) and plan.moved == ()  # le plus proche ; coupe automatique : pas de « moved »
    assert all(p.fits for p in plan.placements)


def test_auto_cut_keeps_usable_height():
    # 200 mm en 2 × 100 mm utiles exactement : aucune position libre ne garde les deux tronçons ≤ 100 mm → coupe
    # nominale gardée (le repli de la découpe s'en charge)
    plan = plan_pieces(200, 100, _square, BED, 5.0, critical=[100.0])
    assert plan.cuts == (100.0,)
    # 100,4 mm utiles : 99,5 laisse 100,5 au-dessus, 100,5 fait 100,5 en dessous : coupe nominale gardée
    plan = plan_pieces(200, 100.4, _square, BED, 5.0, critical=[100.0])
    assert plan.cuts == (100.0,)


def test_explicit_cut_moved_with_notice():
    plan = plan_pieces(200, 245, _square, BED, 5.0, cuts_at=[80.0], critical=[80.1])
    assert plan.cuts == (pytest.approx(79.6),)
    assert plan.moved == ((80.0, pytest.approx(79.6)),)


def test_critical_planes_bore_ends():
    from beaver.features.bores import Bore

    b = Bore("SBAR", 30, 0, 4.2, 1.0, 1.0, 1, True, 0.0, 40.0, 0.15, 0.2)
    zs = critical_planes([b], [], [], 0.0, 80.0)
    assert 40.0 in zs and pytest.approx(40.2) in zs


def test_rib_crossings_every_half_period():
    """Plaques à 45°, pas 30 : croisements des deux familles tous les 15 mm depuis le bas de la boîte de calage — dont
    210 et 420 (aile ×3 : 630 / 3), où le booléen de la coupe en Z échouait."""
    cfg = load_config(overrides={"rib_layers": [{"spacing": 30, "angle": 45}]})
    zs = rib_crossings(cfg, 0.0, 0.0, 630.0)
    assert zs == pytest.approx([15.0 * j for j in range(43)])
    assert rib_crossings(cfg, -7.0, 0.0, 30.0) == pytest.approx([8.0, 23.0])


# --- couches de validation ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("height", [209.5, 210.0, 209.4999999999, 39.5, 0.5, 100.1])
def test_layer_heights_strictly_inside(height):
    zs = layer_heights(0.0, height, 0.2)
    assert len(zs) and zs[0] == pytest.approx(0.1) and zs[-1] < height - 1e-4
    assert height - zs[-1] <= 0.2 + 1e-9  # pas de couche manquante sous le sommet (milieu à moins d'une couche)


# --- repli de la découpe ------------------------------------------------------------------------------------------


def test_cut_chain_retries_shared_cut():
    """Le morceau 2 échoue quand sa coupe du bas est la coupe nominale : la coupe partagée est décalée et le morceau 1
    est recoupé avec elle (les deux morceaux restent jointifs)."""
    calls = []

    def make(i, lo, hi, shift):
        calls.append((i, lo, hi))
        if i == 1 and lo == 50.0:
            raise CutFailure("vide")
        return (lo, hi)

    moved = []
    out = cut_chain([50.0], make, lambda a, b: moved.append((a, b)))
    assert out == [(None, 50.05), (50.05, None)]
    assert moved == [(50.0, 50.05)]


def test_cut_chain_gives_up_in_french():
    def make(i, lo, hi, shift):
        if (hi is not None and 49 < hi < 51) or (lo is not None and 49 < lo < 51):
            raise CutFailure("intersection vide")
        return i

    with pytest.raises(CutFailure, match=r"coupe à z = 50 mm impossible \(géométrie coïncidente\)"):
        cut_chain([50.0], make)


def test_cut_chain_keeps_genuine_error():
    def make(i, lo, hi, shift):
        raise SegmentError("tronçon 1 : 2 solides obtenus au lieu de 1")

    with pytest.raises(SegmentError, match="2 solides"):
        cut_chain([50.0], make)


def test_cut_chain_plane_shift():
    def make(i, lo, hi, shift):
        if shift == 0.0:
            raise CutFailure("plan de corde coïncident")
        return shift

    assert cut_chain([], make, shifts=True) == [0.05]


def test_split_part_retries_when_boolean_fails(monkeypatch):
    part = ribbed_wing(span=80)
    real = Shape.__and__

    def flaky(self, other):
        bb = other.bounding_box()
        if abs(bb.max.Z - 40.0) < 1e-6 or abs(bb.min.Z - 40.0) < 1e-6:
            return None  # booléen vide à la position nominale (faces coïncidentes)
        return real(self, other)

    monkeypatch.setattr(Shape, "__and__", flaky)
    moved = []
    pieces = split_part(part, [40.0], lambda a, b: moved.append((a, b)))
    assert moved == [(40.0, pytest.approx(40.05))]
    assert len(pieces) == 2
    assert pieces[0].bounding_box().max.Z == pytest.approx(40.05, abs=1e-3)
    assert pieces[1].bounding_box().max.Z == pytest.approx(80 - 40.05, abs=1e-3)


def test_split_part_all_retries_fail(monkeypatch):
    part = ribbed_wing(span=80)
    real = Shape.__and__

    def broken(self, other):
        bb = other.bounding_box()
        if 39 < bb.max.Z < 41 or 39 < bb.min.Z < 41:
            return None
        return real(self, other)

    monkeypatch.setattr(Shape, "__and__", broken)
    with pytest.raises(SegmentError, match="géométrie coïncidente") as exc:
        split_part(part, [40.0])
    assert "NoneType" not in str(exc.value)


# --- génération : longeron qui s'arrête pile sur la coupe ---------------------------------------------------------


def _spar_to_cut_bodies():
    """Aile de 80 mm, longeron Ø8 de z = 0 à 40 : avec 40 mm utiles, la coupe nominale est à z = 40 (bout du
    longeron, du trou et de sa fente)."""
    wing, camber = naca_wing(chord=100, span=80)
    spar = Cylinder(4, 40).moved(Location((30, camber(30), 20)))
    to_x = Location((0, 0, 0), (0, 90, 0))
    return [
        NamedBody(("Aile_test",), wing.moved(to_x).solids()[0]),
        NamedBody(("SBAR_8",), spar.moved(to_x).solids()[0]),
    ]


def test_generation_cut_off_spar_end():
    cfg = load_config(overrides={
        "material_settings": {"layer_height": 0.2},
        "rib_layers": [{"spacing": 20}],
        "bambu": {"template": str(TEMPLATE)},
        "segment": {"max_height": 41},
    })
    messages = []
    run = run_model(classify(_spar_to_cut_bodies(), cfg), cfg, progress=messages.append)
    (body,) = run.bodies
    assert len(body.segments) == 2
    h0 = body.segments[0].bounding_box().max.Z
    # bouts du longeron et du trou (± jeu) à ≥ 0,5 mm de la coupe, quel que soit le sens de l'orientation
    bb = body.result.bounding_box(optimal=True)
    crit = [z - bb.min.Z for z in critical_planes(body.bores, body.bays, body.filled, bb.min.Z, bb.max.Z)]
    assert any(abs(z - 40.0) < 1e-3 for z in crit)
    crit += rib_crossings(cfg, 0.0, 0.0, 80.0)  # plaques à 45°, pas 20 : croisements tous les 10 mm (dont 40)
    assert all(abs(h0 - z) >= CUT_CLEARANCE - 1e-3 for z in crit), (h0, crit)
    assert not any("booléen en échec" in m for m in messages)  # le planificateur suffit, pas le repli
    # l'aile synthétique (corde 100) a des bords trop fins pour être imprimable, mais aucune couche n'est perdue
    assert not [i for i in body.report.issues if i.check == "connectivity"]


# --- aile réelle ×3 (retour utilisateur) --------------------------------------------------------------------------


@pytest.mark.slow
def test_wing_x3_with_servo():
    """aile_trop_grande_servo.step : corde 600, envergure 630, servo, gaine et 2 SBAR ; 3 tronçons en Z coupés dans
    la corde. Aucune exception, coupes en Z écartées des bouts de SBAR_14mm_1 / gaine (z = 210) par le planificateur,
    sans repli, et tous les morceaux valides."""
    cfg = load_config(overrides={"bambu": {"template": str(TEMPLATE)}})
    t0 = time.perf_counter()
    messages = []
    run = run_model(classify(read_step(FIXTURES / "aile_trop_grande_servo.step"), cfg), cfg, messages.append)
    (body,) = run.bodies
    assert 4 <= len(body.segments) <= 12
    assert not any("booléen en échec" in m for m in messages), [m for m in messages if "booléen" in m]
    assert body.report.ok, [i.message for i in body.report.issues if i.severity == "error"]
    print(f"{len(body.segments)} morceaux en {time.perf_counter() - t0:.0f} s")
