import importlib
import json
from pathlib import Path

import httpx
import respx

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def _load(name):
    return json.loads((FIX / name).read_text())


def _reload(monkeypatch, key):
    # "" not delenv: reload() re-runs load_dotenv(), which would otherwise
    # repopulate the key from a developer's real .env file.
    monkeypatch.setenv("HELIUS_API_KEY", key or "")
    import memescreen.config as config
    importlib.reload(config)
    import memescreen.providers.helius as helius
    importlib.reload(helius)
    return config, helius


def test_returns_none_without_key(monkeypatch):
    _config, helius = _reload(monkeypatch, None)
    assert helius.fetch_metadata_fallback(ADDR) is None


@respx.mock
def test_extracts_name_and_symbol(monkeypatch):
    config, helius = _reload(monkeypatch, "testkey")
    respx.post(config.HELIUS_RPC).mock(
        return_value=httpx.Response(200, json=_load("helius_getasset.json"))
    )
    assert helius.fetch_metadata_fallback(ADDR) == ("dogwifhat", "WIF")


@respx.mock
def test_swallows_errors(monkeypatch):
    config, helius = _reload(monkeypatch, "testkey")
    respx.post(config.HELIUS_RPC).mock(return_value=httpx.Response(500))
    assert helius.fetch_metadata_fallback(ADDR) is None
