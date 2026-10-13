"""Découpe de la pièce en tronçons perpendiculaires à l'envergure (plans z = cte)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from build123d import Box, Pos, Solid, Shape

from aerorib.layout import Placement, fit_on_bed


class SegmentError(ValueError):
    """Découpe en tronçons impossible ou invalide."""


MIN_SEGMENT = 10.0  # mm : longueur minimale d'un tronçon


def plan_cuts(height: float, max_height: float, cuts_at: list[float] | None = None) -> list[float]:
    """Hauteurs de coupe internes (vide si la pièce tient d'un seul tenant).

    Chaque tronçon doit mesurer au moins MIN_SEGMENT mm : une hauteur n'est découpable que si elle se répartit
    en n tronçons de longueur comprise entre MIN_SEGMENT et max_height ; sinon SegmentError.
    """
    if not (math.isfinite(height) and math.isfinite(max_height)) or height <= 0 or max_height <= 0:
        raise SegmentError(f"hauteur de pièce ({height}) ou hauteur utile ({max_height}) invalide")
    if cuts_at is None:
        if height <= max_height * (1 + 1e-9):
            return []
        n = math.ceil(height / (max_height * (1 + 1e-9)))
        cuts = [height * i / n for i in range(1, n)]
    else:
        cuts = sorted(float(c) for c in cuts_at)
        for c in cuts:
            if not math.isfinite(c) or not 0 < c < height:
                raise SegmentError(f"coupe à {c:g} mm hors de la pièce (0 à {height:g} mm)")
        dups = sorted({c for a, c in zip(cuts, cuts[1:]) if a == c})
        if dups:
            raise SegmentError(f"coupes en double : {', '.join(f'{c:g}' for c in dups)}")
    bounds = [0.0, *cuts, float(height)]
    for a, b in zip(bounds, bounds[1:]):
        if b - a > max_height * (1 + 1e-9):
            raise SegmentError(f"tronçon de {b - a:g} mm > hauteur utile {max_height:g} mm")
        if b - a < MIN_SEGMENT - 1e-9:
            raise SegmentError(f"tronçon de {b - a:g} mm trop court (minimum {MIN_SEGMENT:g} mm)")
    return cuts


SECTION_STEP = 5.0  # mm : pas des sections échantillonnées pour l'empreinte d'un tronçon
_EDGE = 0.01  # mm : sections d'extrémité prises juste à l'intérieur de la pièce (pas sur les faces planes)

Sections = Callable[[Sequence[float]], list[np.ndarray]]  # zs (depuis l'emplanture) → points XY de chaque section


@dataclass(frozen=True)
class ChordNeed:
    """Sections qui ne tiennent pas seules sur le plateau, même tournées : seule une coupe dans la corde aidera."""

    z0: float  # étendue (mm, depuis l'emplanture) des sections trop grandes
    z1: float
    size: tuple[float, float]  # largeur × épaisseur (sans rotation) de la plus encombrante


@dataclass(frozen=True)
class PiecePlan:
    cuts: tuple[float, ...]  # coupes internes (mm, depuis l'emplanture)
    # placement prévu de chaque tronçon d'après les sections échantillonnées ; pour un tronçon qui ne tient pas,
    # celui de fit_on_bed (angle le moins mauvais) — le placement réel est recalculé sur le tronçon généré
    placements: tuple[Placement, ...]
    height_n: int  # nombre de tronçons exigé par la hauteur seule
    reason: str | None = None  # justification d'une découpe automatique (None : pas de coupe, ou coupes imposées)
    chord: ChordNeed | None = None  # coupe dans la corde nécessaire

    @property
    def fits(self) -> bool:
        return all(p.fits for p in self.placements)


def _samples(z0: float, z1: float, height: float) -> list[float]:
    k = max(1, math.ceil((z1 - z0) / SECTION_STEP - 1e-9))
    return [float(z) for z in np.clip(np.linspace(z0, z1, k + 1), _EDGE, height - _EDGE)]


def _extent(pts: np.ndarray) -> tuple[float, float]:
    w, d = np.ptp(pts, axis=0)
    return float(w), float(d)


def plan_pieces(
    height: float, max_height: float, sections: Sections, bed: tuple[float, float], margin: float = 0.0,
    cuts_at: list[float] | None = None,
) -> PiecePlan:
    """Coupes en Z pour que chaque tronçon tienne en hauteur ET sur le plateau (rotation autour de Z permise).

    Empreinte d'un tronçon [z0, z1] = enveloppe convexe de ses sections aux deux bouts et tous les SECTION_STEP mm
    (`sections` : voir slicing.section_sampler ; aucun booléen, mêmes résultats en aperçu et en génération).
    Part du nombre de tronçons exigé par la hauteur, puis l'augmente (tronçons égaux) jusqu'à ce que tous tiennent
    ou qu'ils deviennent plus courts que MIN_SEGMENT (rien ne tient avant : coupes de hauteur seules).
    Une section trop grande seule (même tournée) ne profite pas des coupes en Z : la coupe dans la corde s'en
    chargera (`chord` renseigné). Si le reste de son tronçon tient, coupes de hauteur seules ; sinon la pièce est
    traitée par régions : les sections trop grandes isolées dans leurs propres tronçons (coupes de hauteur), les
    autres régions recoupées pour l'empreinte. Des coupes imposées (`cuts_at`) sont respectées telles quelles."""
    base = plan_cuts(height, max_height, cuts_at)
    cache: dict[float, np.ndarray] = {}

    def get(zs: list[float]) -> list[np.ndarray]:
        missing = sorted({z for z in zs if z not in cache})
        cache.update(zip(missing, sections(missing)))
        return [cache[z] for z in zs]

    def place(cuts, lo: float = 0.0, hi: float = float(height)) -> list[tuple[Placement, tuple[float, float]]]:
        bounds = [lo, *cuts, hi]
        out = []
        for a, b in zip(bounds, bounds[1:]):
            pts = [p for p in get(_samples(a, b, height)) if len(p)]
            if not pts:
                out.append((Placement(0.0, True, 0.0, (0.0, 0.0)), (0.0, 0.0)))
                continue
            hull = np.concatenate(pts)
            out.append((fit_on_bed(hull, bed, margin), _extent(hull)))
        return out

    n0 = len(base) + 1
    aw, ad = bed[0] - 2 * margin, bed[1] - 2 * margin
    zs = _samples(0.0, height, height)
    alone = [(z, p, fit_on_bed(p, bed, margin)) for z, p in zip(zs, get(zs)) if len(p)]
    bad = [(z, p, pl) for z, p, pl in alone if not pl.fits]
    chord = None
    if bad:
        worst = min(bad, key=lambda t: t[2].slack)
        chord = ChordNeed(min(z for z, _, _ in bad), max(z for z, _, _ in bad), _extent(worst[1]))
    first = place(base)
    if cuts_at is not None:
        return PiecePlan(tuple(base), tuple(pl for pl, _ in first), n0, None, chord)

    def good_part(a: float, b: float) -> np.ndarray | None:
        """Sections du tronçon [a, b] qui tiennent seules (None : aucune)."""
        pts = [p for z, p, pl in alone if a - 1e-9 <= z <= b + 1e-9 and pl.fits]
        return np.concatenate(pts) if pts else None

    def oversized(a: float, b: float) -> bool:
        return any(a - 1e-9 <= z <= b + 1e-9 for z, _, _ in bad)

    # tronçons de hauteur qui ne tiennent pas : leur empreinte (hors sections trop grandes, laissées à la coupe
    # dans la corde)
    too_big = []
    for (pl, size), a, b in zip(first, [0.0, *base], [*base, float(height)]):
        if pl.fits:
            continue
        if not oversized(a, b):
            too_big.append(size)
        elif (g := good_part(a, b)) is not None and not fit_on_bed(g, bed, margin).fits:
            too_big.append(_extent(g))
    if not too_big:  # tout tient, ou seule la coupe dans la corde aidera : coupes de hauteur
        reason = f"hauteur {height:.0f} mm > {max_height:.0f} mm utiles" if base else None
        return PiecePlan(tuple(base), tuple(pl for pl, _ in first), n0, reason, chord)

    def fit_region(a: float, b: float) -> list[float]:
        """Tronçons égaux de [a, b] : le moins possible qui tiennent tous, sinon ceux de la hauteur seule."""
        nr = max(1, math.ceil((b - a) / (max_height * (1 + 1e-9)) - 1e-9))
        n = nr
        while True:
            cuts = [a + (b - a) * i / n for i in range(1, n)]
            if all(pl.fits for pl, _ in place(cuts, a, b)):
                return cuts
            if (b - a) / (n + 1) < MIN_SEGMENT - 1e-9:
                return [a + (b - a) * i / nr for i in range(1, nr)]
            n += 1

    # régions : sections trop grandes (coupe dans la corde, coupes de hauteur seules) / autres (coupes d'empreinte)
    regions: list[list] = []
    for i, (z, _, pl) in enumerate(alone):
        is_bad = not pl.fits
        if not regions:
            regions.append([0.0, None, is_bad])
        elif is_bad != regions[-1][2]:
            # la frontière est prise sur la dernière / première section qui tient : la région trop grande
            # contient toutes ses sections
            cut = alone[i - 1][0] if is_bad else z
            regions[-1][1] = cut
            regions.append([cut, None, is_bad])
    regions[-1][1] = float(height)
    while len(regions) > 1:  # régions trop courtes fusionnées avec une voisine (trop grande de préférence)
        short = [i for i, r in enumerate(regions) if r[1] - r[0] < MIN_SEGMENT - 1e-9]
        if not short:
            break
        i = short[0]
        nbrs = [j for j in (i - 1, i + 1) if 0 <= j < len(regions)]
        j = max(nbrs, key=lambda k: (regions[k][2], -(regions[k][1] - regions[k][0])))
        lo, hi = min(i, j), max(i, j)
        regions[lo:hi + 1] = [[regions[lo][0], regions[hi][1], regions[lo][2] or regions[hi][2]]]
    cuts: list[float] = []
    for k, (a, b, is_bad) in enumerate(regions):
        if k:
            cuts.append(a)
        cuts += [a + c for c in plan_cuts(b - a, max_height)] if is_bad else fit_region(a, b)
    placed = place(cuts)
    bounds = [0.0, *cuts, float(height)]
    for (pl, _), a, b in zip(placed, bounds, bounds[1:]):
        g = good_part(a, b) if not pl.fits and oversized(a, b) else None
        if not pl.fits and (not oversized(a, b) or (g is not None and not fit_on_bed(g, bed, margin).fits)):
            cuts, placed = list(base), first  # rien ne tient avant MIN_SEGMENT : coupes de hauteur seules
            break
    if list(cuts) != list(base):
        w, d = max(too_big, key=lambda s: s[0] * s[1])
        reason = f"empreinte {w:.0f}×{d:.0f} mm > plateau {aw:.0f}×{ad:.0f} mm, même tournée"
    elif cuts:
        reason = f"hauteur {height:.0f} mm > {max_height:.0f} mm utiles"
    else:
        reason = None
    return PiecePlan(tuple(cuts), tuple(pl for pl, _ in placed), n0, reason, chord)


def split_part(solid: Shape, cuts: list[float]) -> list[Solid]:
    """Coupe par plans z = cte ; chaque tronçon est ramené à min Z = 0 (emplanture → saumon).

    Applique MIN_SEGMENT mais pas la hauteur utile : l'appelant fournit des coupes déjà planifiées
    (voir plan_cuts).
    """
    bb = solid.bounding_box(optimal=True)
    try:
        cuts = sorted(float(c) for c in cuts)
    except (TypeError, ValueError) as exc:
        raise SegmentError(f"coupes non numériques : {cuts!r}") from exc
    for c in cuts:
        if not math.isfinite(c) or not bb.min.Z < c < bb.max.Z:
            raise SegmentError(f"coupe à z = {c:g} mm hors de la pièce ({bb.min.Z:g} à {bb.max.Z:g} mm)")
    if any(a == b for a, b in zip(cuts, cuts[1:])):
        raise SegmentError("coupes en double")
    edges = [bb.min.Z, *cuts, bb.max.Z]
    for a, b in zip(edges, edges[1:]):
        if b - a < MIN_SEGMENT - 1e-9:
            raise SegmentError(f"tronçon de {b - a:g} mm trop court (minimum {MIN_SEGMENT:g} mm)")
    pad = 10.0
    sx, sy = bb.size.X + 2 * pad, bb.size.Y + 2 * pad
    cx, cy = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2
    bounds = [bb.min.Z - pad, *cuts, bb.max.Z + pad]
    out: list[Solid] = []
    for i, (z0, z1) in enumerate(zip(bounds, bounds[1:])):
        try:
            box = Pos(cx, cy, (z0 + z1) / 2) * Box(sx, sy, z1 - z0)
            solids = list((solid & box).solids())
        except Exception as exc:  # erreurs OCC (Standard_Failure, etc.)
            raise SegmentError(f"découpe impossible à z = {z1 if i < len(cuts) else z0:g} mm ({exc})") from exc
        if len(solids) != 1:
            raise SegmentError(f"tronçon {i + 1} : {len(solids)} solides obtenus au lieu de 1")
        s = solids[0]
        if not s.is_valid:
            raise SegmentError(f"tronçon {i + 1} : solide invalide")
        zmin = s.bounding_box(optimal=True).min.Z
        out.append(Pos(0, 0, -zmin) * s)
    return out
