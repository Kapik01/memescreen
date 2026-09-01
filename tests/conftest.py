"""Test-wide isolation.

Several provider tests call `importlib.reload(memescreen.config)` with an API
key set in the environment. `monkeypatch` restores the environment variable but
not the reloaded module, so without this fixture those keys stayed set on the
config module for every later test in the session — which is how a test that
should skip Birdeye ends up trying to call it.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def restore_config_module():
    """Snapshot memescreen.config's namespace and put it back after each test."""
    import memescreen.config as config

    snapshot = dict(config.__dict__)
    yield
    config.__dict__.clear()
    config.__dict__.update(snapshot)
