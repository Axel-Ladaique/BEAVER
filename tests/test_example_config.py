from pathlib import Path

from aerorib.config import RibLayer, load_config

EXAMPLES = Path(__file__).parents[1] / "examples"
EXAMPLE = EXAMPLES / "config-exemple.yaml"


def test_example_config_equals_defaults():
    assert load_config(EXAMPLE) == load_config()


def test_double_ribs_example():
    cfg = load_config(EXAMPLES / "config-doubles-nervures.yaml")
    assert cfg.rib_layers == [
        RibLayer(name="principal", spacing=30, depth="full"),
        RibLayer(name="peau", spacing=10, depth=3, phase=5),
    ]
    # le reste est aux défauts
    assert cfg.model_copy(update={"rib_layers": load_config().rib_layers}) == load_config()
