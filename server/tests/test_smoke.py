import importlib

import commons

LAYERS = ("domain", "privacy", "ranking", "adapters", "store", "service", "mcp", "cli")


def test_package_imports() -> None:
    assert commons.__version__


def test_every_layer_imports() -> None:
    for layer in LAYERS:
        assert importlib.import_module(f"commons.{layer}").__doc__
