from pathlib import Path

from shapely.geometry import box

from aerorib.report import format_text, write_html
from aerorib.validate import Issue, Report

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
    assert "<li>trous : SBAR_CABLE_1 (gaine, Ø6.0)</li>" in html
    assert "SERVO&lt;1&gt; (baie extrados" in html


def _body_with_pieces():
    from types import SimpleNamespace

    from aerorib.layout import Placement

    ok, bad = Report((), STATS, {}), failing()
    return SimpleNamespace(
        segments=(object(), object()), names=("A", "B<1>"), spans=((0.0, 120.0), (0.0, 120.0)),
        piece_reports=(ok, bad), report=bad,
        placements=(Placement(0.0, True, 10.0, (193.07, 51.4)), Placement(-37.52, True, 2.0, (240.0, 60.0))),
    )


def test_piece_rows_number_plates_and_keep_rotation():
    from aerorib.report import piece_rows

    rows = piece_rows(_body_with_pieces(), first_plate=3)
    assert [(r["nom"], r["plateau"], r["rotation"], r["ok"]) for r in rows] == [("A", 3, 0.0, True), ("B<1>", 4, -37.5, False)]
    assert (rows[0]["z0"], rows[0]["z1"], rows[0]["largeur"], rows[0]["profondeur"]) == (0.0, 120.0, 193.1, 51.4)


def test_html_pieces_table_and_notices(tmp_path: Path):
    from aerorib.report import piece_rows

    out = write_html(Report((), STATS, {}), tmp_path / "r.html", title="aile", pieces=piece_rows(_body_with_pieces()),
                     notices=["découpe automatique : coupe dans la corde en 2 morceaux"])
    html = out.read_text(encoding="utf-8")
    assert "<caption>Morceaux imprimés</caption>" in html and "<th>Rotation</th>" in html and "<th>Plateau</th>" in html
    assert "<td>A</td><td>0 – 120</td><td>193.1 × 51.4</td><td>—</td><td>1</td><td>✔</td>" in html
    assert "<td>B&lt;1&gt;</td><td>0 – 120</td><td>240 × 60</td><td>-37.5°</td><td>2</td><td>✘</td>" in html
    assert "<li>ℹ découpe automatique : coupe dans la corde en 2 morceaux</li>" in html
