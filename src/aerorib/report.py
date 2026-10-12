"""Mise en forme des rapports de validation (terminal et HTML)."""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from shapely.geometry import Polygon

from aerorib.validate import Issue, Report

LABELS = {
    "connectivity": "contour non unique",
    "narrow_slit": "fente trop fine",
    "skin": "peau entamée",
    "material_width": "matière trop fine",
}


def format_text(report: Report, title: str = "") -> str:
    head = "✔ imprimable en vase mode" if report.ok else "✘ NON imprimable en vase mode"
    lines = [f"{title} : {head}" if title else head]
    by_check: dict[str, list[Issue]] = defaultdict(list)
    for issue in report.errors:
        by_check[issue.check].append(issue)
    for check, issues in by_check.items():
        zs = sorted({i.z for i in issues})
        lines.append(
            f"  - {LABELS.get(check, check)} : {len(zs)} couche(s), "
            f"z = {zs[0]:.1f} – {zs[-1]:.1f} mm, ex. : {issues[0].message}"
        )
    warn_by_check: dict[str, list[Issue]] = defaultdict(list)
    for issue in report.warnings:
        warn_by_check[issue.check].append(issue)
    for check, issues in warn_by_check.items():
        n = len({i.z for i in issues})
        lines.append(f"  ⚠ {LABELS.get(check, check)} : {n} couche(s) (avertissement)")
    s = report.stats
    lines.append(
        f"  couches : {int(s['layers'])} · hauteur : {s['height_mm']:.1f} mm · "
        f"trajet : {s['path_length_m']:.1f} m · masse ≈ {s['mass_g']:.1f} g"
    )
    return "\n".join(lines)


def format_bores(bores, filled=()) -> str:
    """Trous traités (décisions autonomes rendues visibles), repère d'impression, 1 décimale."""
    items = []
    for b in bores:
        if not b.cut_hole:
            items.append(f"{b.name} (Ø{2 * b.radius:.1f}, x={b.x:.1f}, y={b.y:.1f})")
        elif b.cable:
            items.append(f"{b.name} (gaine, Ø{2 * b.radius:.1f})")
        elif b.anchor_gap > 0:  # l'attache est du côté opposé à la fente
            items.append(f"{b.name} (longeron, Ø{2 * b.radius:.1f}, attache {'intrados' if b.side > 0 else 'extrados'})")
        else:
            items.append(f"{b.name} (longeron, Ø{2 * b.radius:.1f})")
    groups: dict[str, list] = {}
    for f in filled:
        groups.setdefault(f"{2 * f.r:.1f}", []).append(f)
    if groups:
        items.append(
            "rebouchés : "
            + " ; ".join(
                f"{len(fs)} × Ø{d} (" + " / ".join(f"x={f.x:.1f}, y={f.y:.1f}" for f in fs) + ")"
                for d, fs in groups.items()
            )
        )
    return "  trous : " + (" ; ".join(items) if items else "aucun")


def segment_heights(segments) -> list[float]:
    """Hauteurs des tronçons (solides posés à z = 0), de l'emplanture au saumon."""
    out = []
    for s in segments:
        bb = s.bounding_box()
        out.append(bb.max.Z - bb.min.Z)
    return out


def _bay_segments(bay, heights: Sequence[float]) -> list[int]:
    """Numéros (1…) des tronçons que l'ouverture de la baie (rampes comprises) traverse."""
    lo, hi = (z - bay.zmin for z in bay.opening)
    out, z = [], 0.0
    for k, h in enumerate(heights, 1):
        if lo < z + h and hi > z:
            out.append(k)
        z += h
    return out


def format_bays(bays, heights: Sequence[float] = ()) -> str:
    """Baies de servo : côté d'ouverture, dimensions utiles (corde × envergure), longueur d'ouverture (rampes
    comprises) ; `heights` (hauteurs des tronçons) : tronçon(s) de chaque baie quand la pièce est découpée."""
    items = []
    for b in bays:
        lo, hi = b.opening
        where = ""
        if len(heights) > 1:
            ks = _bay_segments(b, heights)
            if ks:
                names = ", ".join(map(str, ks[:-1])) + f" et {ks[-1]}" if len(ks) > 1 else str(ks[0])
                where = f", tronçon{'s' if len(ks) > 1 else ''} {names}"
        items.append(
            f"{b.name} (baie {b.side_name}, {b.x1 - b.x0:.1f} × {b.z1 - b.z0:.1f} mm utiles, "
            f"ouverture {hi - lo:.1f} mm, profondeur {b.depth:.1f} mm"
            + ((", renfort partiel" if b.rf_lost else ", renfort") if b.rf_offset > 0 else "")
            + where + ")"
        )
    return "  baies : " + (" ; ".join(items) if items else "aucune")


def _ring_path(coords) -> str:
    pts = list(coords)
    return "M" + " L".join(f"{x:.3f},{-y:.3f}" for x, y in pts) + " Z"


def _svg(polys: tuple[Polygon, ...], issues: list[Issue]) -> str:
    if not polys:
        return "<svg viewBox='0 0 1 1'></svg>"
    x0 = min(p.bounds[0] for p in polys)
    y0 = min(p.bounds[1] for p in polys)
    x1 = max(p.bounds[2] for p in polys)
    y1 = max(p.bounds[3] for p in polys)
    pad = max(x1 - x0, y1 - y0) * 0.03
    vb = f"{x0 - pad:.3f} {-y1 - pad:.3f} {x1 - x0 + 2 * pad:.3f} {y1 - y0 + 2 * pad:.3f}"
    d = " ".join(_ring_path(r.coords) for p in polys for r in (p.exterior, *p.interiors))
    marks = "".join(
        f"<rect x='{b[0]:.3f}' y='{-b[3]:.3f}' width='{max(b[2] - b[0], pad / 4):.3f}' "
        f"height='{max(b[3] - b[1], pad / 4):.3f}' class='bad'/>"
        for i in issues
        if (b := i.bounds) is not None
    )
    return (
        f"<svg viewBox='{vb}' preserveAspectRatio='xMidYMid meet'>"
        f"<path d='{d}' class='part' fill-rule='evenodd'/>{marks}</svg>"
    )


_CSS = """
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --part:#d6d3d1; --stroke:#57534e; --bad:#dc2626; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --part:#44403c; --stroke:#d6d3d1; --bad:#f87171; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:960px; margin:0 auto; }
pre { white-space:pre-wrap; color:var(--muted); }
figure { margin:24px 0; }
svg { width:100%; height:auto; max-height:420px; }
.part { fill:var(--part); stroke:var(--stroke); stroke-width:0.1; vector-effect:non-scaling-stroke; }
.bad { fill:none; stroke:var(--bad); stroke-width:2; vector-effect:non-scaling-stroke; }
"""


def write_html(report: Report, path: Path, title: str = "AeroRib", details: Sequence[str] = ()) -> Path:
    """Rapport HTML ; `details` : lignes ajoutées sous le résumé (trous et gaines, baies de servo)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figures = []
    for z, polys in sorted(report.failed_layers.items()):
        issues = [i for i in report.errors if i.z == z]
        items = "".join(
            f"<li>{html.escape(LABELS.get(i.check, i.check))} : {html.escape(i.message)}</li>" for i in issues
        )
        figures.append(
            f"<figure><figcaption>z = {z:.2f} mm</figcaption>{_svg(polys, issues)}<ul>{items}</ul></figure>"
        )
    items = "".join(f"<li>{html.escape(d.strip())}</li>" for d in details)
    extra = f"<ul class='details'>{items}</ul>" if items else ""
    body = (
        f"<main><h1>{html.escape(title)}</h1><pre>{html.escape(format_text(report))}</pre>{extra}"
        f"{''.join(figures)}</main>"
    )
    path.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
