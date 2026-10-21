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
    if h.mode == "croisements":
        where = "ponts aux croisements" if h.every == 1 else f"ponts à 1 croisement sur {h.every}"
    else:
        where = f"ponts à pas régulier ({_fr(h.pitch)} mm)"
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

    cols = (("Morceau", ""), ("z (mm)", "nombre"), ("Emprise (mm)", "nombre"), ("Marge (mm)", "nombre"),
            ("Rotation", "nombre"), ("Plateau", "nombre"), ("Vase mode", "centre"))
    head = "".join(f"<th class='{c}'>{h}</th>" if c else f"<th>{h}</th>" for h, c in cols)
    body = "".join(
        f"<tr{'' if r['tient'] in (None, True) else ' class=ne-tient-pas'}>"
        f"<td>{html.escape(r['nom'])}</td>"
        f"<td class='nombre'>{num(r['z0'])} – {num(r['z1'])}</td>"
        f"<td class='nombre'>{num(r['largeur'])} × {num(r['profondeur'])}"
        f"{'' if r['tient'] in (None, True) else ' (ne tient pas)'}</td>"
        f"<td class='nombre'>{num(r.get('marge'))}</td>"
        f"<td class='nombre'>{'—' if not r['rotation'] else num(r['rotation'], '°')}</td>"
        f"<td class='nombre'>{r['plateau']}</td>"
        f"<td class='centre'>{'<span class=ok-oui>✔</span>' if r['ok'] else '<span class=ok-non>✘</span>'}</td>"
        "</tr>"
        for r in rows
    )
    return (
        "<section class='carte'><h2>Morceaux imprimés</h2><div class='defile'><table class='morceaux'>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>"
    )


def _ring_path(coords) -> str:
    pts = list(coords)
    return "M" + " L".join(f"{x:.3f},{-y:.3f}" for x, y in pts) + " Z"


def _svg(polys: tuple[Polygon, ...], issues: list[Issue]) -> str:
    """Coupe de la couche, défauts encadrés ; chaîne vide pour une couche sans matière (rien à dessiner)."""
    if not polys:
        return ""
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


# Même direction visuelle que la GUI (gui/static/style.css) : jetons clair/sombre, encadrés à liseré gauche,
# tuiles de chiffres, tableaux aux en-têtes en petites capitales. Le rapport reste autonome (aucune ressource externe).
_CSS = """
:root {
  --fond:#f3f4f6; --surface:#ffffff; --surface-2:#f2f4f7; --texte:#15191e; --texte-doux:#5b6471;
  --bord:#e2e5ea; --bord-fort:#cdd2da; --ombre:0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.04);
  --accent:#2563eb; --marque:#b45309; --ok:#15803d; --attention:#a16207; --erreur:#c81e1e;
  --matiere:#c9d1db; --matiere-bord:#57606a;
  --rayon:8px; --rayon-carte:12px;
  --police:system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color-scheme:light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fond:#0d1014; --surface:#151a20; --surface-2:#1b2129; --texte:#e8ebf0; --texte-doux:#9aa4b1;
    --bord:#252d37; --bord-fort:#343e4b; --ombre:none;
    --accent:#5b8cff; --marque:#f59e0b; --ok:#4ac26b; --attention:#e3b341; --erreur:#ff7b72;
    --matiere:#3a4552; --matiere-bord:#a7b1bd;
  }
}
* { box-sizing:border-box; }
html, body { margin:0; }
body { background:var(--fond); color:var(--texte); font:14.5px/1.5 var(--police); -webkit-font-smoothing:antialiased; }
main { max-width:1000px; margin:0 auto; padding:1.5rem 1.25rem 4rem; }
@media (max-width:600px) { main { padding:1rem 1rem 3rem; } }
h1, h2 { line-height:1.25; margin:0; letter-spacing:-.005em; }
h1 { font-size:1.35rem; font-weight:700; }
h2 { font-size:1.02rem; font-weight:650; margin-bottom:.75rem; }
.entete { display:flex; align-items:center; flex-wrap:wrap; gap:.4rem .9rem; margin-bottom:1.1rem; }
.marque { display:flex; align-items:center; gap:.5rem; font-size:.72rem; font-weight:750; letter-spacing:.08em;
  color:var(--texte-doux); flex-basis:100%; }
.marque::before { content:""; width:.6rem; height:.6rem; border-radius:3px; background:var(--marque);
  box-shadow:.26rem .26rem 0 -.05rem color-mix(in srgb, var(--marque) 45%, transparent); }
.statut { --ton:var(--texte-doux); display:inline-flex; align-items:center; gap:.4rem; padding:.12rem .65rem;
  border-radius:999px; font-size:.8rem; font-weight:600; white-space:nowrap; color:var(--ton);
  background:color-mix(in srgb, var(--ton) 12%, transparent); }
.statut::before { content:""; width:.45rem; height:.45rem; border-radius:50%; background:currentColor; }
.statut.ok { --ton:var(--ok); }
.statut.non-imprimable { --ton:var(--erreur); }
.carte { background:var(--surface); border:1px solid var(--bord); border-radius:var(--rayon-carte);
  box-shadow:var(--ombre); padding:1.15rem 1.3rem; margin-bottom:1.1rem; }
.carte > :last-child { margin-bottom:0; }
.chiffres { display:flex; flex-wrap:wrap; gap:.6rem; margin:0 0 1.1rem; padding:0; list-style:none; }
.chiffre { flex:1 1 9rem; display:flex; flex-direction:column-reverse; justify-content:flex-end; gap:.15rem;
  background:var(--surface); border:1px solid var(--bord); border-radius:var(--rayon); box-shadow:var(--ombre);
  padding:.65rem .85rem .7rem; }
.chiffre strong { font-size:1.3rem; font-weight:650; font-variant-numeric:tabular-nums; letter-spacing:-.01em;
  line-height:1.2; }
.chiffre span { font-size:.76rem; font-weight:500; color:var(--texte-doux); }
.message { --ton:var(--texte-doux); padding:.65rem .9rem .7rem; border-radius:var(--rayon); margin:0 0 1.1rem;
  background:color-mix(in srgb, var(--ton) 7%, var(--surface));
  border:1px solid color-mix(in srgb, var(--ton) 22%, var(--surface)); border-left:3px solid var(--ton);
  font-size:.9rem; }
.message > strong { display:block; color:var(--ton); font-size:.85rem; font-weight:650; margin-bottom:.15rem; }
.message.erreur { --ton:var(--erreur); }
.message.attention { --ton:var(--attention); }
.message.notice { --ton:var(--accent); }
.message ul { margin:.2rem 0 0; padding-left:1.1rem; }
.message li { margin:.12rem 0; }
.message li::marker { color:var(--ton); }
.message .ex { color:var(--texte-doux); }
.details { display:grid; grid-template-columns:max-content 1fr; gap:.55rem 1.25rem; margin:0; font-size:.9rem; }
.details dt { font-size:.7rem; font-weight:650; text-transform:uppercase; letter-spacing:.05em; color:var(--texte-doux);
  padding-top:.18rem; }
.details dd { margin:0; }
.details ul { margin:0; padding:0; list-style:none; }
.details li + li { margin-top:.12rem; }
@media (max-width:600px) { .details { grid-template-columns:1fr; gap:.15rem; } .details dd { margin-bottom:.5rem; } }
.defile { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.87rem; }
th, td { text-align:left; padding:.5rem .65rem; border-bottom:1px solid var(--bord); vertical-align:middle; }
th { font-size:.7rem; font-weight:650; text-transform:uppercase; letter-spacing:.05em; color:var(--texte-doux);
  border-bottom-color:var(--bord-fort); white-space:nowrap; padding-top:.35rem; padding-bottom:.45rem; }
th:first-child, td:first-child { padding-left:.2rem; }
th:last-child, td:last-child { padding-right:.2rem; }
tbody tr:last-child td { border-bottom:0; }
td.nombre, th.nombre { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
td.centre, th.centre { text-align:center; }
tr.ne-tient-pas td { color:var(--erreur); }
.ok-oui { color:var(--ok); font-weight:700; }
.ok-non { color:var(--erreur); font-weight:700; }
.couches { display:grid; gap:1.1rem; }
.couches > .carte { margin:0; }
figure { margin:0; }
figcaption { font-weight:650; font-variant-numeric:tabular-nums; margin-bottom:.5rem; }
figure svg { display:block; width:100%; height:auto; max-height:420px; background:var(--surface-2);
  border:1px solid var(--bord); border-radius:var(--rayon); margin-bottom:.6rem; }
figure ul { margin:0; padding-left:1.1rem; font-size:.9rem; }
figure li::marker { color:var(--erreur); }
.vide { color:var(--texte-doux); font-style:italic; font-size:.9rem; margin:0 0 .4rem; }
.part { fill:var(--matiere); stroke:var(--matiere-bord); stroke-width:1; vector-effect:non-scaling-stroke; }
.bad { fill:color-mix(in srgb, var(--erreur) 12%, transparent); stroke:var(--erreur); stroke-width:2;
  vector-effect:non-scaling-stroke; }
"""


def _plural(n: int) -> str:
    return f"{n} couche{'s' if n > 1 else ''}"


def _z_range(zs: Sequence[float]) -> str:
    lo, hi = f"{zs[0]:.1f}", f"{zs[-1]:.1f}"
    return f"z = {lo} mm" if lo == hi else f"z = {lo} – {hi} mm"


def _summary(report: Report) -> str:
    """En-tête de page : tuiles de chiffres puis encadrés erreurs / avertissements (même contenu que `format_text`)."""
    s = report.stats
    tiles = (
        ("couches", f"{int(s['layers'])}"), ("hauteur", f"{s['height_mm']:.1f} mm"),
        ("trajet", f"{s['path_length_m']:.1f} m"), ("masse", f"≈ {s['mass_g']:.1f} g"),
    )
    out = "<ul class='chiffres'>" + "".join(
        f"<li class='chiffre'><strong>{v}</strong><span>{k.capitalize()}</span></li>" for k, v in tiles
    ) + "</ul>"
    by_check: dict[str, list[Issue]] = defaultdict(list)
    for issue in report.errors:
        by_check[issue.check].append(issue)
    if by_check:
        items = "".join(
            f"<li>{html.escape(LABELS.get(c, c))} : {_plural(len(zs := sorted({i.z for i in iss})))}, {_z_range(zs)}"
            f"<span class='ex'> — ex. : {html.escape(iss[0].message)}</span></li>"
            for c, iss in by_check.items()
        )
        out += f"<div class='message erreur'><strong>Non imprimable en vase mode</strong><ul>{items}</ul></div>"
    warn: dict[str, list[Issue]] = defaultdict(list)
    for issue in report.warnings:
        warn[issue.check].append(issue)
    if warn:
        items = "".join(
            f"<li>{html.escape(LABELS.get(c, c))} : {_plural(len(zs := sorted({i.z for i in iss})))}, {_z_range(zs)}</li>"
            for c, iss in warn.items()
        )
        out += f"<div class='message attention'><strong>Avertissements</strong><ul>{items}</ul></div>"
    return out


def _details(details: Sequence[str]) -> str:
    """Lignes « clé : valeur ; valeur… » (trous, baies, morceaux, plateau) en liste de définitions."""
    rows = []
    for line in details:
        key, sep, value = line.strip().partition(" : ")
        if not sep:
            key, value = "", key
        vals = [v for v in value.split(" ; ") if v]
        dd = f"<ul>{''.join(f'<li>{html.escape(v)}</li>' for v in vals)}</ul>" if len(vals) > 1 else html.escape(value)
        rows.append(f"<dt>{html.escape(key)}</dt><dd>{dd}</dd>")
    return f"<section class='carte'><h2>Détails</h2><dl class='details'>{''.join(rows)}</dl></section>" if rows else ""


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
        drawing = _svg(polys, issues) or "<p class='vide'>Aucune matière à cette hauteur.</p>"
        figures.append(
            f"<figure class='carte'><figcaption>z = {z:.2f} mm</figcaption>{drawing}<ul>{items}</ul></figure>"
        )
    status = (
        "<span class='statut ok'>Imprimable en vase mode</span>" if report.ok
        else "<span class='statut non-imprimable'>Non imprimable</span>"
    )
    parts = [
        f"<header class='entete'><span class='marque'>BEAVER · rapport d'essai</span>"
        f"<h1>{html.escape(title)}</h1>{status}</header>",
        _summary(report),
    ]
    if notices:
        parts.append(
            "<div class='message notice'><strong>Découpe</strong><ul>"
            + "".join(f"<li>{html.escape(n)}</li>" for n in notices) + "</ul></div>"
        )
    if pieces:
        parts.append(_pieces_table(pieces))
    parts.append(_details(details))
    if figures:
        parts.append(f"<h2>Couches en défaut</h2><div class='couches'>{''.join(figures)}</div>")
    path.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>",
        encoding="utf-8",
    )
    return path
