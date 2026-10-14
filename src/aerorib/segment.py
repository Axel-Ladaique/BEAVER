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


class CutFailure(SegmentError):
    """Booléen de découpe en échec (résultat vide ou exception OpenCascade), typiquement sur des faces coïncidentes
    avec le plan de coupe : un léger décalage de la coupe le résout en général (`cut_chain`)."""


MIN_SEGMENT = 10.0  # mm : longueur minimale d'un tronçon
CUT_CLEARANCE = 0.5  # mm : écart minimal entre une coupe en Z planifiée et un plan critique (bout de trou, baie…)
# décalages successifs (mm) d'une coupe dont le booléen échoue (faces coïncidentes non prévues par le planificateur)
RETRY_OFFSETS = (0.05, -0.05, 0.2, -0.2, 0.5, -0.5)


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
    # coupes imposées (`cuts_at`) déplacées hors d'un plan critique : (position demandée, position retenue)
    moved: tuple[tuple[float, float], ...] = ()

    @property
    def fits(self) -> bool:
        return all(p.fits for p in self.placements)


def _samples(z0: float, z1: float, height: float) -> list[float]:
    k = max(1, math.ceil((z1 - z0) / SECTION_STEP - 1e-9))
    return [float(z) for z in np.clip(np.linspace(z0, z1, k + 1), _EDGE, height - _EDGE)]


def _extent(pts: np.ndarray) -> tuple[float, float]:
    w, d = np.ptp(pts, axis=0)
    return float(w), float(d)


def clear_of(
    z: float, critical: Sequence[float], lo: float, hi: float, ok: Callable[[float], bool] = lambda z: True,
    clearance: float = CUT_CLEARANCE,
) -> float | None:
    """Position la plus proche de `z`, dans ]lo, hi[, à au moins `clearance` de chaque plan critique et admise par
    `ok` ; None si aucune. `z` lui-même s'il convient."""
    cands = {z} | {c + d for c in critical for d in (-clearance, clearance)}
    for c in sorted(cands, key=lambda c: (abs(c - z), c)):
        if lo < c < hi and all(abs(c - k) >= clearance - 1e-9 for k in critical) and ok(c):
            return c
    return None


def plan_pieces(
    height: float, max_height: float, sections: Sections, bed: tuple[float, float], margin: float = 0.0,
    cuts_at: list[float] | None = None, critical: Sequence[float] = (),
) -> PiecePlan:
    """Coupes en Z de `_plan_pieces`, écartées d'au moins CUT_CLEARANCE des plans critiques `critical` (mm depuis
    l'emplanture : bouts de trous et de leur jeu, baies de servo, rebouchages… — faces de la pièce générée
    perpendiculaires à Z, sur lesquelles le booléen de découpe d'OpenCascade échoue sans erreur). Coupe automatique :
    position libre la plus proche qui garde les tronçons voisins dans la hauteur utile (et sur le plateau s'ils y
    tenaient) ; coupe imposée : déplacée du minimum, signalée (`moved`)."""
    cache: dict[float, np.ndarray] = {}

    def get(zs: list[float]) -> list[np.ndarray]:
        missing = sorted({z for z in zs if z not in cache})
        if missing:
            cache.update(zip(missing, sections(missing)))
        return [cache[z] for z in zs]

    plan = _plan_pieces(height, max_height, get, bed, margin, cuts_at)
    crit = sorted(float(c) for c in critical if 0.0 < c < height)
    if not plan.cuts or not crit:
        return plan

    def placement(a: float, b: float) -> Placement:
        pts = [p for p in get(_samples(a, b, height)) if len(p)]
        return fit_on_bed(np.concatenate(pts), bed, margin) if pts else Placement(0.0, True, 0.0, (0.0, 0.0))

    nominal = list(plan.cuts)
    out: list[float] = []
    for i, c in enumerate(nominal):
        prev = out[-1] if out else 0.0
        nxt = nominal[i + 1] if i + 1 < len(nominal) else float(height)
        was_fit = plan.placements[i].fits

        def ok(z: float, prev=prev, nxt=nxt, was_fit=was_fit, last=i + 1 == len(nominal)) -> bool:
            if not (MIN_SEGMENT - 1e-9 <= z - prev <= max_height * (1 + 1e-9) and nxt - z >= MIN_SEGMENT - 1e-9):
                return False
            if last and float(height) - z > max_height * (1 + 1e-9):
                return False
            return cuts_at is not None or not was_fit or placement(prev, z).fits

        z = clear_of(c, crit, prev, nxt, ok)
        out.append(c if z is None else z)  # aucune position libre : la découpe se repliera (`cut_chain`)
    if out == nominal:
        return plan
    placements = tuple(placement(a, b) for a, b in zip([0.0, *out], [*out, float(height)]))
    moved = tuple((a, b) for a, b in zip(nominal, out) if a != b) if cuts_at is not None else ()
    return PiecePlan(tuple(out), placements, plan.height_n, plan.reason, plan.chord, moved)


def _plan_pieces(
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


def cut_chain(
    cuts: Sequence[float], make: Callable[..., object], notice: Callable[[float, float], None] = lambda a, b: None,
    shifts: bool = False, where: Callable[[float], str] = lambda z: f"z = {z:g} mm",
) -> list:
    """Morceaux successifs séparés par les coupes `cuts` : `make(i, lo, hi, shift)` découpe le morceau i entre lo et
    hi (None : au-delà de la pièce) et lève SegmentError en cas d'échec. Une coupe qui échoue (booléen vide, exception
    OpenCascade, nombre de solides inattendu) est réessayée décalée de RETRY_OFFSETS — la coupe partagée, donc les
    deux morceaux qui la bordent — ; puis, si `shifts`, le morceau est recoupé avec `shift` (décalage des plans de
    coupe dans la corde) pris dans RETRY_OFFSETS. `notice(nominale, retenue)` signale chaque coupe en Z déplacée.
    Échec persistant : CutFailure en français (« géométrie coïncidente »), ou l'erreur d'origine si le booléen avait
    réussi (nombre de solides, solide invalide : défaut réel de la pièce)."""
    nominal = [float(c) for c in cuts]
    cur = list(nominal)
    n = len(nominal) + 1
    out: list = [None] * n

    def bounds(i: int, at: list[float]) -> tuple[float | None, float | None]:
        return (at[i - 1] if i > 0 else None, at[i] if i < n - 1 else None)

    def fits_between(z: float, lo: float | None, hi: float | None) -> bool:
        return (lo is None or z > lo + 1e-6) and (hi is None or z < hi - 1e-6)

    for i in range(n):
        lo, hi = bounds(i, cur)
        try:
            out[i] = make(i, lo, hi, 0.0)
            continue
        except SegmentError as exc:
            first = exc
        done = False
        if i < n - 1:  # coupe du haut (pas encore utilisée par un morceau déjà découpé)
            nxt = nominal[i + 1] if i + 1 < n - 1 else None
            for d in RETRY_OFFSETS:
                z = nominal[i] + d
                if not fits_between(z, lo, nxt):
                    continue
                try:
                    out[i] = make(i, lo, z, 0.0)
                except SegmentError:
                    continue
                cur[i], done = z, True
                break
        if not done and i > 0:  # coupe du bas : le morceau précédent est recoupé avec elle
            for d in RETRY_OFFSETS:
                z = nominal[i - 1] + d
                if not fits_between(z, cur[i - 2] if i > 1 else None, hi):
                    continue
                try:
                    prev = make(i - 1, cur[i - 2] if i > 1 else None, z, 0.0)
                    this = make(i, z, hi, 0.0)
                except SegmentError:
                    continue
                out[i - 1], out[i], cur[i - 1], done = prev, this, z, True
                break
        if not done and shifts:  # plans de coupe dans la corde du morceau
            for d in RETRY_OFFSETS:
                try:
                    out[i] = make(i, lo, hi, d)
                except SegmentError:
                    continue
                done = True
                break
        if not done:
            if not isinstance(first, CutFailure):
                raise first
            z = nominal[i] if i < n - 1 else nominal[i - 1]
            raise CutFailure(f"coupe à {where(z)} impossible (géométrie coïncidente) : {first}") from first
    for a, b in zip(nominal, cur):
        if a != b:
            notice(a, b)
    return out


def split_part(
    solid: Shape, cuts: list[float], notice: Callable[[float, float], None] = lambda a, b: None
) -> list[Solid]:
    """Coupe par plans z = cte ; chaque tronçon est ramené à min Z = 0 (emplanture → saumon).

    Applique MIN_SEGMENT mais pas la hauteur utile : l'appelant fournit des coupes déjà planifiées
    (voir plan_cuts). Booléen en échec sur une coupe : coupe décalée (`cut_chain`), `notice(nominale, retenue)`.
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

    def make(i: int, lo: float | None, hi: float | None, _shift: float) -> Solid:
        z0 = bb.min.Z - pad if lo is None else lo
        z1 = bb.max.Z + pad if hi is None else hi
        try:
            common = solid & (Pos(cx, cy, (z0 + z1) / 2) * Box(sx, sy, z1 - z0))
            solids = [] if common is None else list(common.solids())
        except Exception as exc:  # erreurs OCC (Standard_Failure, etc.)
            raise CutFailure(f"tronçon {i + 1} : booléen en échec ({type(exc).__name__})") from exc
        if not solids:
            raise CutFailure(f"tronçon {i + 1} : intersection vide")
        if len(solids) != 1:
            raise SegmentError(f"tronçon {i + 1} : {len(solids)} solides obtenus au lieu de 1")
        s = solids[0]
        if not s.is_valid:
            raise SegmentError(f"tronçon {i + 1} : solide invalide")
        zmin = s.bounding_box(optimal=True).min.Z
        return Pos(0, 0, -zmin) * s

    return cut_chain(cuts, make, notice)
