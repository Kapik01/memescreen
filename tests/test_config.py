import importlib


def test_defaults_present(monkeypatch):
    # setenv("") not delenv: reload() re-runs load_dotenv(), which would
    # otherwise repopulate the key from a developer's real .env file.
    monkeypatch.setenv("BIRDEYE_API_KEY", "")
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


def test_discord_webhook_unset_by_default(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    import memescreen.config as config
    importlib.reload(config)
    assert config.DISCORD_WEBHOOK_URL is None


def test_data_dir_default_and_derived_paths(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("LOG_PATH", raising=False)
    monkeypatch.delenv("MISSED_LOG_PATH", raising=False)
    import memescreen.config as config
    importlib.reload(config)
    assert config.DATA_DIR == "data"
    assert config.DB_PATH == "data/memescreen.db"
    assert config.LOG_PATH == "data/memescreen.log"
    assert config.MISSED_LOG_PATH == "data/missed_alerts.log"


def test_data_dir_override_flows_into_paths(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/data")
    for k in ("DB_PATH", "LOG_PATH", "MISSED_LOG_PATH"):
        monkeypatch.delenv(k, raising=False)
    import memescreen.config as config
    importlib.reload(config)
    assert config.DB_PATH == "/data/memescreen.db"
    assert config.LOG_PATH == "/data/memescreen.log"
    assert config.MISSED_LOG_PATH == "/data/missed_alerts.log"


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
