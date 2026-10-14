"""Réglages des évidements de nervures (`ribs_hollow`, surcharge par couche `rib_layers[*].hollow`)."""

import pytest

from beaver.config import ConfigError, HollowSettings, load_config


def test_hollow_defaults_off():
    cfg = load_config()
    h = cfg.ribs_hollow
    assert (h.mode, h.bridge_width, h.pitch, h.keep_ratio, h.keep_min) == ("off", 4.0, 30.0, 0.3, 2.0)
    assert cfg.layer_hollow(cfg.rib_layers[0]) == h


def test_hollow_global_and_layer_override():
    cfg = load_config(overrides={
        "ribs_hollow": {"mode": "croisements", "bridge_width": 5},
        "rib_layers": [{}, {"name": "peau", "depth": 3, "spacing": 10, "hollow": {"mode": "off"}},
                       {"name": "trois", "hollow": {"keep_ratio": 0.5}}],
    })
    first, skin, third = (cfg.layer_hollow(layer) for layer in cfg.rib_layers)
    assert first.mode == "croisements" and first.bridge_width == 5
    assert skin.mode == "off" and skin.bridge_width == 5  # surcharge partielle : le reste vient du réglage global
    assert third.mode == "croisements" and third.keep_ratio == 0.5


@pytest.mark.parametrize(
    "over, msg",
    [
        ({"mode": "partout"}, r"ribs_hollow.mode : off, croisements ou pas"),
        ({"bridge_width": 0}, r"bridge_width : largeur de pont > 0 mm attendue"),
        ({"pitch": -1}, r"pitch : pas des ponts > 0 mm attendu"),
        ({"keep_ratio": 0}, r"keep_ratio : proportion gardée entre 0 et 1"),
        ({"keep_ratio": 1.5}, r"keep_ratio : proportion gardée entre 0 et 1"),
        ({"keep_min": 0.5}, r"keep_min 0.5 mm < 2 × largeur de ligne"),
        ({"mode": "pas", "pitch": 4, "bridge_width": 4}, r"pitch 4.0 mm ≤ largeur de pont"),
    ],
)
def test_hollow_settings_errors_in_french(over, msg):
    with pytest.raises(ConfigError, match=msg):
        load_config(overrides={"ribs_hollow": over})


def test_layer_override_validated_with_layer_name():
    with pytest.raises(ConfigError, match=r"rib_layers 'peau' : évidements.*keep_ratio"):
        load_config(overrides={"rib_layers": [{"name": "peau", "hollow": {"keep_ratio": 2}}]})
    with pytest.raises(ConfigError, match=r"rib_layers 'peau' : évidements.*réglage inconnu : largeur"):
        load_config(overrides={"rib_layers": [{"name": "peau", "hollow": {"largeur": 2}}]})


def test_hollow_settings_schema_keeps_bounds_for_the_gui():
    props = HollowSettings.model_json_schema()["properties"]
    assert props["mode"]["enum"] == ["off", "croisements", "pas"]
    assert props["bridge_width"]["exclusiveMinimum"] == 0
