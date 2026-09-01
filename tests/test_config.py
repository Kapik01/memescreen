import importlib


def test_defaults_present(monkeypatch):
    monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    import memescreen.config as config
    importlib.reload(config)
    assert config.HTTP_TIMEOUT_SEC == 10.0
    assert config.FLAG_LIQ_RED == 10_000.0
    assert config.VERDICT_TRADE_MIN == 70
    assert config.BIRDEYE_API_KEY is None
    assert config.DEXSCREENER_BASE == "https://api.dexscreener.com"


def test_env_override(monkeypatch):
    monkeypatch.setenv("FLAG_LIQ_RED", "5000")
    monkeypatch.setenv("BIRDEYE_API_KEY", "abc123")
    import memescreen.config as config
    importlib.reload(config)
    assert config.FLAG_LIQ_RED == 5000.0
    assert config.BIRDEYE_API_KEY == "abc123"


def test_env_helper_casts():
    import memescreen.config as config
    assert config.env("NOPE_MISSING", 3, int) == 3
