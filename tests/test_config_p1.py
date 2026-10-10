import pytest

from aerorib.config import BoreSettings, ConfigError, OrientationAxis, RibLayer, load_config


def test_p1_defaults():
    cfg = load_config()
    assert cfg.orientation == "auto"
    assert cfg.split.gap == 0.6 and cfg.split.min_rib_depth == 1.0 and cfg.split.grid == (101, 22)
    assert cfg.rib_layers == [RibLayer()]
    assert cfg.rib_layers[0].spacing == 30.0 and cfg.rib_layers[0].pattern == "diamond"
    assert cfg.passages.mode == "fill" and cfg.passages.strip == 1.0
    assert cfg.output.dir == "out" and cfg.output.formats == ["step", "stl"]
    assert cfg.validation.tessellation_tol == 0.02


def test_rib_angle_defaults_to_material_limit():
    assert load_config().rib_angle(RibLayer()) == 45.0
    assert load_config(overrides={"material": "asa_aero"}).rib_angle(RibLayer()) == 40.0
    cfg = load_config(overrides={"rib_layers": [{"angle": 35}]})
    assert cfg.rib_angle(cfg.rib_layers[0]) == 35.0


def test_rib_angle_above_overhang_rejected():
    with pytest.raises(ConfigError, match="surplomb"):
        load_config(overrides={"material": "asa_aero", "rib_layers": [{"angle": 45}]})


def test_partial_depth_accepted():
    cfg = load_config(overrides={"rib_layers": [{}, {"name": "peau", "spacing": 10, "depth": 3, "phase": 5}]})
    assert [layer.depth for layer in cfg.rib_layers] == ["full", 3.0]


@pytest.mark.parametrize("depth", [0, -1])
def test_partial_depth_must_be_positive(depth):
    with pytest.raises(ConfigError, match=r"depth.*> 0"):
        load_config(overrides={"rib_layers": [{"depth": depth}]})


def test_partial_depth_below_two_lines_rejected():
    # 0,5 < 2 × 0,45 : les deux murs de la nervure de peau se confondraient
    with pytest.raises(ConfigError, match=r"rib_layers 'principal' : profondeur 0.5 mm < 2 × largeur de ligne"):
        load_config(overrides={"rib_layers": [{"depth": 0.5}]})
    load_config(overrides={"rib_layers": [{"depth": 0.9}]})  # limite : 2 × 0,45


@pytest.mark.parametrize("depth", ["moitie", True, False, None, [3]])
def test_partial_depth_rejects_other_values(depth):
    with pytest.raises(ConfigError, match=r"rib_layers.depth : 'full' ou une profondeur en mm"):
        load_config(overrides={"rib_layers": [{"depth": depth}]})


def test_orientation_axis():
    cfg = load_config(overrides={"orientation": {"axis": [1, 0, 0]}})
    assert cfg.orientation == OrientationAxis(axis=(1, 0, 0))
    with pytest.raises(ConfigError, match="nul"):
        load_config(overrides={"orientation": {"axis": [0, 0, 0]}})


def test_spar_settings_merge_default_and_name():
    cfg = load_config(
        overrides={"spars": {"default": {"clearance": 0.2}, "SBAR_rear": {"slit_side": "bottom"}}}
    )
    assert cfg.spar_settings("SBAR_main") == BoreSettings(clearance=0.2)
    assert cfg.spar_settings("sbar_REAR") == BoreSettings(clearance=0.2, slit_side="bottom")


def test_spar_settings_validated_at_load():
    with pytest.raises(ConfigError, match="spars"):
        load_config(overrides={"spars": {"SBAR_x": {"clearance": -1}}})
    with pytest.raises(ConfigError, match="spars"):
        load_config(overrides={"spars": {"default": {"nope": 1}}})


def test_negative_spacing_rejected():
    with pytest.raises(ConfigError):
        load_config(overrides={"rib_layers": [{"spacing": 0}]})


@pytest.mark.parametrize(
    "overrides", [{"spars": {"default": {"sleeve": 0}}}, {"passages": {"sleeve": 0}}]
)
def test_sleeve_must_be_positive(overrides):
    with pytest.raises(ConfigError):
        load_config(overrides=overrides)


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"spars": {"default": {"sleeve": 0.3}}}, "manchon"),
        ({"spars": {"SBAR_x": {"sleeve": 0.3}}}, "manchon"),
        ({"passages": {"sleeve": 0.3}}, "manchon"),
        ({"split": {"gap": 0.4}}, "gap"),
    ],
)
def test_widths_below_line_width_rejected(overrides, match):
    with pytest.raises(ConfigError, match=match):
        load_config(overrides=overrides)


def test_p1_sleeve_defaults():
    cfg = load_config()
    assert cfg.spar_settings("SBAR").sleeve == 0.8 and cfg.passages.sleeve == 0.8


def test_slit_defaults_survive_slicer_closing():
    cfg = load_config()
    assert cfg.preset.min_slit_width == 0.10
    assert load_config(overrides={"material": "asa_aero"}).preset.min_slit_width == 0.10
    assert cfg.spar_settings("SBAR").slit_width == 0.15 and cfg.passages.slit_width == 0.15


@pytest.mark.parametrize(
    "overrides",
    [
        {"slit_width": 0.05},  # 0,05 / cos 45° = 0,07 < 1,1 × 0,10
        {"spars": {"default": {"slit_width": 0.1}}},
        {"spars": {"SBAR_x": {"slit_width": 0.1}}},
        {"passages": {"slit_width": 0.1}},
        {"material_settings": {"min_slit_width": 0.2}},  # fentes de nervure 0,1 / cos 45° = 0,14 < 0,22
    ],
)
def test_slits_narrower_than_slicer_threshold_rejected(overrides):
    with pytest.raises(ConfigError, match="fente"):
        load_config(overrides=overrides)


@pytest.mark.parametrize("grid", [[1, 22], [101, 1], [0, 0]])
def test_split_grid_needs_two_points_per_axis(grid):
    with pytest.raises(ConfigError, match="grid"):
        load_config(overrides={"split": {"grid": grid}})


# --- P1b : attache du longeron côté opposé, rebouchage des trous parasites ----------------------


def test_anchor_defaults():
    s = load_config().spar_settings("SBAR")
    assert s.anchor is True and s.anchor_gap == 1.0


@pytest.mark.parametrize(
    "overrides", [{"spars": {"default": {"anchor_gap": 0.2}}}, {"spars": {"SBAR_x": {"anchor_gap": 0.2}}}]
)
def test_anchor_gap_below_line_width_rejected(overrides):
    with pytest.raises(ConfigError, match="anchor_gap.*largeur de ligne"):
        load_config(overrides=overrides)


def test_anchor_gap_must_be_positive():
    with pytest.raises(ConfigError):
        load_config(overrides={"spars": {"default": {"anchor_gap": 0}}})


def test_anchor_gap_ignored_when_anchor_off():
    cfg = load_config(overrides={"spars": {"default": {"anchor": False, "anchor_gap": 0.2}}})
    assert cfg.spar_settings("SBAR").anchor is False


@pytest.mark.parametrize("mode", ["fill", "link", "off"])
def test_passage_modes(mode):
    assert load_config(overrides={"passages": {"mode": mode}}).passages.mode == mode


def test_passage_mode_auto_no_longer_exists():
    with pytest.raises(ConfigError):
        load_config(overrides={"passages": {"mode": "auto"}})
