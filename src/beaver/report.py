"""Mise en forme des rapports de validation (terminal et HTML)."""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from shapely.geometry import Polygon

from beaver.validate import Issue, Report

LABELS = {
    "connectivity": "contour non unique",
    "narrow_slit": "fente trop fine",
    "skin": "peau entamée",
    "volume": "hors plateau",
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


def format_bores(bores, filled=(), tubes=()) -> str:
    """Trous traités (décisions autonomes rendues visibles), repère d'impression, 1 décimale ; `tubes` : SBAR
    transversales / inclinées (Ø du trou, angle de l'axe avec l'envergure)."""
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
    for t in tubes:
        items.append(f"{t.name} (transversale, Ø{2 * t.radius:.1f}, axe {t.tilt:.0f}°)")
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


def _fr(v: float, digits: int = 1) -> str:
    """Nombre en français, sans zéros inutiles (4 ; 3,5)."""
    return f"{v:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def _hollow_text(h) -> str:
    if h.mode == "off":
        return "désactivés"
    where = "ponts aux croisements" if h.mode == "croisements" else f"ponts à pas régulier ({_fr(h.pitch)} mm)"
    out = f"{where}, ponts {_fr(h.bridge_width)} mm, {_fr(100 * h.keep_ratio)} % de la demi-épaisseur"
    if h.ramp_factor < 1:
        out += f", rampes × {_fr(h.ramp_factor, 2)}"
    return out


def hollow_summary(cfg) -> str | None:
    """Réglage des évidements des nervures en clair (« ponts aux croisements, ponts 4 mm, 30 % de la
    demi-épaisseur ») ; une partie par couche quand les surcharges de couche diffèrent ; None si désactivés partout."""
    per = [cfg.layer_hollow(layer) for layer in cfg.rib_layers]
    if all(h.mode == "off" for h in per):
        return None
    if all(h == per[0] for h in per):
        return _hollow_text(per[0])
    return " ; ".join(f"couche « {layer.name} » : {_hollow_text(h)}" for layer, h in zip(cfg.rib_layers, per))


def format_hollow(summary: str | None, saved_g: float, mass_g: float) -> str | None:
    """Ligne « évidements » du rapport ; `saved_g` : masse économisée estimée (profils des plaques, voir
    `pipeline.hollow_saving_g`), `mass_g` : masse de la pièce évidée."""
    if summary is None:
        return None
    line = f"  évidements : {summary}"
    if saved_g > 0:
        full = mass_g + saved_g
        line += (f" — gain estimé ≈ {_fr(saved_g)} g (−{100 * saved_g / full:.0f} %, "
                 f"≈ {_fr(full)} g sans évidement)")
    return line


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


def piece_rows(body, first_plate: int = 1) -> list[dict]:
    """Morceaux imprimés d'un corps (`pipeline.BodyResult`) : nom, étendue en Z depuis l'emplanture, emprise sur le
    plateau une fois tourné, marge (mm libres sur l'axe le plus serré), rotation, plateau (numéro dans le projet
    3MF, à partir de `first_plate`), validation."""
    n = max(len(body.segments), 1)
    names = list(body.names) if len(body.names) == n else [f"T{i}" for i in range(1, n + 1)]
    spans = list(body.spans) if len(body.spans) == n else [None] * n
    reports = list(body.piece_reports) if len(body.piece_reports) == n else [body.report] * n
    out = []
    for i, (name, span, rep) in enumerate(zip(names, spans, reports)):
        pl = body.placements[i] if i < len(body.placements) else None
        out.append({
            "nom": name,
            "z0": None if span is None else round(span[0], 2),
            "z1": None if span is None else round(span[1], 2),
            "largeur": None if pl is None else round(pl.size[0], 1),
            "profondeur": None if pl is None else round(pl.size[1], 1),
            "marge": None if pl is None else round(pl.slack, 1),  # mm libres sur l'axe le plus serré du plateau
            "rotation": None if pl is None else round(pl.angle_deg, 1),
            "tient": None if pl is None else bool(pl.fits),
            "plateau": first_plate + i,
            "ok": bool(rep.ok),
        })
    return out


def _pieces_table(rows: Sequence[dict]) -> str:
    def num(v, unit=""):
        return "—" if v is None else f"{v:g}{unit}"

    head = "".join(f"<th>{h}</th>" for h in ("Morceau", "z (mm)", "Emprise (mm)", "Marge (mm)", "Rotation", "Plateau", "Vase mode"))
    body = "".join(
        "<tr>"
        f"<td>{html.escape(r['nom'])}</td>"
        f"<td>{num(r['z0'])} – {num(r['z1'])}</td>"
        f"<td>{num(r['largeur'])} × {num(r['profondeur'])}{'' if r['tient'] in (None, True) else ' (ne tient pas)'}</td>"
        f"<td>{num(r.get('marge'))}</td>"
        f"<td>{'—' if not r['rotation'] else num(r['rotation'], '°')}</td>"
        f"<td>{r['plateau']}</td>"
        f"<td>{'✔' if r['ok'] else '✘'}</td>"
        "</tr>"
        for r in rows
    )
    return f"<table class='morceaux'><caption>Morceaux imprimés</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


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
table { border-collapse:collapse; margin:16px 0; }
caption { text-align:left; font-weight:600; padding-bottom:4px; }
th, td { border-bottom:1px solid var(--part); padding:4px 12px 4px 0; text-align:left; }
.notices { border-left:3px solid var(--stroke); padding-left:12px; }
"""


def write_html(
    report: Report, path: Path, title: str = "BEAVER", details: Sequence[str] = (), pieces: Sequence[dict] = (),
    notices: Sequence[str] = (),
) -> Path:
    """Rapport HTML ; `details` : lignes ajoutées sous le résumé (trous et gaines, baies de servo) ; `pieces` :
    tableau des morceaux (`piece_rows`) ; `notices` : décisions automatiques de découpe."""
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
    if notices:
        extra += "<ul class='notices'>" + "".join(f"<li>ℹ {html.escape(n)}</li>" for n in notices) + "</ul>"
    if pieces:
        extra += _pieces_table(pieces)
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
