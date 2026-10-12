"""Mise en forme des rapports de validation (terminal et HTML)."""

from __future__ import annotations

import html
from collections import defaultdict
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


def format_bays(bays) -> str:
    """Baies de servo : côté d'ouverture, dimensions utiles (corde × envergure), longueur d'ouverture (rampes
    comprises)."""
    items = []
    for b in bays:
        lo, hi = b.opening
        items.append(
            f"{b.name} (baie {b.side_name}, {b.x1 - b.x0:.1f} × {b.z1 - b.z0:.1f} mm utiles, "
            f"ouverture {hi - lo:.1f} mm, profondeur {b.depth:.1f} mm" + (
                (", renfort partiel)" if b.rf_lost else ", renfort)") if b.rf_offset > 0 else ")"
            )
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


def write_html(report: Report, path: Path, title: str = "AeroRib") -> Path:
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
    body = (
        f"<main><h1>{html.escape(title)}</h1><pre>{html.escape(format_text(report))}</pre>"
        f"{''.join(figures)}</main>"
    )
    path.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path
