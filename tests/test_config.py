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


def test_scanner_defaults_present(monkeypatch):
    import importlib
    import memescreen.config as config
    importlib.reload(config)
    assert config.SPIKE_RATIO_MIN == 8.0
    assert config.MIN_BUYS == 15
    assert config.UNIVERSE_CAP == 1500
    assert config.POLL_INTERVAL_SEC == 60
    assert config.DB_PATH == "data/memescreen.db"
    assert config.HOLDER_GROWTH_ABS == 25


def test_scanner_env_override(monkeypatch):
    import importlib
    monkeypatch.setenv("SPIKE_RATIO_MIN", "12")
    monkeypatch.setenv("UNIVERSE_CAP", "500")
    import memescreen.config as config
    importlib.reload(config)
    assert config.SPIKE_RATIO_MIN == 12.0
    assert config.UNIVERSE_CAP == 500
