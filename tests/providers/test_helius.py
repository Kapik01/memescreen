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


# --- fetch_wallet_buys -----------------------------------------------------

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
TX_URL = f"https://api.helius.xyz/v0/addresses/{WALLET}/transactions"
MINT_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
WSOL = "So11111111111111111111111111111111111111112"


def _tx(sig, *, transfers):
    return {"signature": sig, "timestamp": 1735689600, "tokenTransfers": transfers}


def _xfer(to, mint, amount=100.0):
    return {"toUserAccount": to, "mint": mint, "tokenAmount": amount}


@respx.mock
def test_wallet_buys_returns_none_without_key(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", None)
    from memescreen.providers import helius
    assert helius.fetch_wallet_buys(WALLET) == []


@respx.mock
def test_wallet_buys_extracts_incoming_transfers(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    from memescreen.providers import helius
    respx.get(TX_URL).mock(return_value=httpx.Response(200, json=[
        _tx("sig1", transfers=[_xfer(WALLET, MINT_A)]),
    ]))
    buys = helius.fetch_wallet_buys(WALLET)
    assert len(buys) == 1
    assert buys[0].signature == "sig1" and buys[0].mint == MINT_A and buys[0].amount == 100.0
    assert buys[0].timestamp is not None


@respx.mock
def test_wallet_buys_ignores_sol_and_stable_and_outgoing(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    from memescreen.providers import helius
    respx.get(TX_URL).mock(return_value=httpx.Response(200, json=[
        _tx("sig1", transfers=[
            _xfer(WALLET, WSOL),                 # wrapped SOL -> ignored
            _xfer("someone-else", MINT_A),        # not to our wallet -> ignored
        ]),
    ]))
    assert helius.fetch_wallet_buys(WALLET) == []


@respx.mock
def test_wallet_buys_stops_at_until_sig(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    from memescreen.providers import helius
    respx.get(TX_URL).mock(return_value=httpx.Response(200, json=[
        _tx("new2", transfers=[_xfer(WALLET, "B" * 44)]),
        _tx("new1", transfers=[_xfer(WALLET, "C" * 44)]),
        _tx("old", transfers=[_xfer(WALLET, "D" * 44)]),  # stop here, not included
    ]))
    buys = helius.fetch_wallet_buys(WALLET, until_sig="old")
    assert [b.signature for b in buys] == ["new2", "new1"]


@respx.mock
def test_wallet_buys_provider_error_returns_empty(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    from memescreen.providers import helius
    respx.get(TX_URL).mock(return_value=httpx.Response(500))
    assert helius.fetch_wallet_buys(WALLET) == []


@respx.mock
def test_wallet_buys_tolerates_malformed_rows(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    from memescreen.providers import helius
    respx.get(TX_URL).mock(return_value=httpx.Response(200, json=[
        "not a dict",
        {"signature": None, "tokenTransfers": [_xfer(WALLET, MINT_A)]},
        {"signature": "ok", "tokenTransfers": "not a list"},
        {"signature": "ok2", "tokenTransfers": [{"toUserAccount": WALLET, "mint": None}]},
    ]))
    assert helius.fetch_wallet_buys(WALLET) == []
