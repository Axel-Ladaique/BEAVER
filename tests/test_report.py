from pathlib import Path

from shapely.geometry import box

from beaver.report import format_text, write_html
from beaver.validate import Issue, Report

STATS = {"layers": 500.0, "height_mm": 100.0, "path_length_m": 153.2, "mass_g": 11.04}


def failing():
    issues = (
        Issue("connectivity", "error", 0.1, "trou isolé (non relié à la peau)", (8, 3, 12, 7)),
        Issue("connectivity", "error", 0.3, "trou isolé (non relié à la peau)", (8, 3, 12, 7)),
        Issue("narrow_slit", "error", 0.3, "fente plus étroite que 0.08 mm", (9.9, 5, 10.1, 10)),
    )
    layers = {0.1: (box(0, 0, 20, 10),), 0.3: (box(0, 0, 20, 10),)}
    return Report(issues, STATS, layers)


def test_text_ok():
    text = format_text(Report((), STATS, {}), title="aile.step")
    assert text.splitlines()[0] == "aile.step : ✔ imprimable en vase mode"
    assert "couches : 500 · hauteur : 100.0 mm · trajet : 153.2 m · masse ≈ 11.0 g" in text


def test_text_failing_groups_by_check():
    text = format_text(failing())
    lines = text.splitlines()
    assert lines[0] == "✘ NON imprimable en vase mode"
    assert "  - contour non unique : 2 couche(s), z = 0.1 – 0.3 mm, ex. : trou isolé (non relié à la peau)" in lines
    assert "  - fente trop fine : 1 couche(s), z = 0.3 – 0.3 mm, ex. : fente plus étroite que 0.08 mm" in lines


def test_html_contains_svg_per_failed_layer(tmp_path: Path):
    out = write_html(failing(), tmp_path / "r.html", title="aile")
    html = out.read_text(encoding="utf-8")
    assert html.count("<svg") == 2
    assert "z = 0.10 mm" in html and "z = 0.30 mm" in html
    assert "trou isolé" in html
    assert "<title>aile</title>" in html


def test_write_html_creates_parent_dir(tmp_path: Path):
    out = write_html(failing(), tmp_path / "a" / "b" / "r.html")
    assert out.exists()


def test_text_lists_warnings():
    issues = (Issue("narrow_slit", "warning", 0.3, "fente plus étroite que 0.08 mm (refermée par le slicer)"),)
    text = format_text(Report(issues, STATS, {}))
    assert text.splitlines()[0] == "✔ imprimable en vase mode"
    assert "  ⚠ fente trop fine : 1 couche(s) (avertissement)" in text


def test_html_lists_details_escaped(tmp_path: Path):
    out = write_html(Report((), STATS, {}), tmp_path / "r.html", title="aile",
                     details=["  trous : SBAR_CABLE_1 (gaine, Ø6.0)", "  baies : SERVO<1> (baie extrados, …)"])
    html = out.read_text(encoding="utf-8")
    assert "<dt>trous</dt><dd>SBAR_CABLE_1 (gaine, Ø6.0)</dd>" in html
    assert "SERVO&lt;1&gt; (baie extrados" in html


def test_html_summary_tiles_and_empty_layer(tmp_path: Path):
    """En-tête structuré (statut, tuiles, encadré d'erreurs) ; une couche vide n'est pas dessinée (pas de SVG vide)."""
    issues = (Issue("connectivity", "error", 209.5, "T1-A : couche vide"),)
    out = write_html(Report(issues, STATS, {209.5: ()}), tmp_path / "r.html", title="aile")
    html = out.read_text(encoding="utf-8")
    assert "<span class='statut non-imprimable'>Non imprimable</span>" in html
    assert "<strong>≈ 11.0 g</strong><span>Masse</span>" in html
    assert "<strong>Non imprimable en vase mode</strong>" in html and "contour non unique : 1 couche, z = 209.5 mm" in html
    assert "<svg" not in html and "Aucune matière à cette hauteur." in html
    assert "<pre>" not in html


def _body_with_pieces():
    from types import SimpleNamespace

    from beaver.layout import Placement

    ok, bad = Report((), STATS, {}), failing()
    return SimpleNamespace(
        segments=(object(), object()), names=("A", "B<1>"), spans=((0.0, 120.0), (0.0, 120.0)),
        piece_reports=(ok, bad), report=bad,
        placements=(Placement(0.0, True, 10.0, (193.07, 51.4)), Placement(-37.52, True, 2.0, (240.0, 60.0))),
    )


def test_piece_rows_number_plates_and_keep_rotation():
    from beaver.report import piece_rows

    rows = piece_rows(_body_with_pieces(), first_plate=3)
    assert [(r["nom"], r["plateau"], r["rotation"], r["ok"]) for r in rows] == [("A", 3, 0.0, True), ("B<1>", 4, -37.5, False)]
    assert (rows[0]["z0"], rows[0]["z1"], rows[0]["largeur"], rows[0]["profondeur"]) == (0.0, 120.0, 193.1, 51.4)
    assert [r["marge"] for r in rows] == [10.0, 2.0]  # spec §2 : marge de chaque morceau sur le plateau


def test_html_pieces_table_and_notices(tmp_path: Path):
    from beaver.report import piece_rows

    out = write_html(Report((), STATS, {}), tmp_path / "r.html", title="aile", pieces=piece_rows(_body_with_pieces()),
                     notices=["découpe automatique : coupe dans la corde en 2 morceaux"])
    html = out.read_text(encoding="utf-8")
    assert "<h2>Morceaux imprimés</h2>" in html
    assert "<th class='nombre'>Rotation</th>" in html and "<th class='nombre'>Plateau</th>" in html
    assert "<th class='nombre'>Marge (mm)</th>" in html
    flat = html.replace(" class='nombre'", "").replace(" class='centre'", "")  # cellules sans leurs classes d'alignement
    assert "<td>A</td><td>0 – 120</td><td>193.1 × 51.4</td><td>10</td><td>—</td><td>1</td><td><span class=ok-oui>✔</span></td>" in flat
    assert ("<td>B&lt;1&gt;</td><td>0 – 120</td><td>240 × 60</td><td>2</td><td>-37.5°</td><td>2</td>"
            "<td><span class=ok-non>✘</span></td>") in flat
    assert "<li>découpe automatique : coupe dans la corde en 2 morceaux</li>" in html


def test_hollow_summary_modes_and_layer_overrides():
    from beaver.config import load_config
    from beaver.report import hollow_summary

    assert hollow_summary(load_config()) is None  # désactivés par défaut : pas de ligne
    assert hollow_summary(load_config(overrides={"ribs_hollow": {"mode": "croisements"}})) == (
        "ponts à 1 croisement sur 3, ponts 4 mm, 20 % de la demi-épaisseur"
    )
    each = load_config(overrides={"ribs_hollow": {"mode": "croisements", "every": 1, "keep_ratio": 0.3}})
    assert hollow_summary(each) == "ponts aux croisements, ponts 4 mm, 30 % de la demi-épaisseur"
    pas = load_config(overrides={"ribs_hollow": {"mode": "pas", "pitch": 25, "bridge_width": 3.5, "keep_ratio": 0.25,
                                                 "ramp_factor": 0.5}})
    assert hollow_summary(pas) == "ponts à pas régulier (25 mm), ponts 3,5 mm, 25 % de la demi-épaisseur, rampes × 0,5"
    # surcharge par couche : une ligne par couche quand elles diffèrent
    two = load_config(overrides={"ribs_hollow": {"mode": "croisements"},
                                 "rib_layers": [{"name": "a"}, {"name": "b", "spacing": 10, "depth": 3, "hollow": {"mode": "off"}}]})
    assert hollow_summary(two) == (
        "couche « a » : ponts à 1 croisement sur 3, ponts 4 mm, 20 % de la demi-épaisseur ; couche « b » : désactivés"
    )


def test_format_hollow_with_estimated_gain():
    from beaver.report import format_hollow

    assert format_hollow(None, 0.0, 10.0) is None
    line = format_hollow("ponts aux croisements, ponts 4 mm, 30 % de la demi-épaisseur", 9.8, 51.3)
    assert line == ("  évidements : ponts aux croisements, ponts 4 mm, 30 % de la demi-épaisseur — "
                    "gain estimé ≈ 9,8 g (−16 %, ≈ 61,1 g sans évidement)")
    assert format_hollow("ponts aux croisements", 0.0, 51.3) == "  évidements : ponts aux croisements"
