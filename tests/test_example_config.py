from pathlib import Path

from aerorib.config import load_config

EXAMPLE = Path(__file__).parents[1] / "examples" / "config-exemple.yaml"


def test_example_config_equals_defaults():
    assert load_config(EXAMPLE) == load_config()
