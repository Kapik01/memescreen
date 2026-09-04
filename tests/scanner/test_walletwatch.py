from datetime import UTC, datetime

from memescreen import config
from memescreen.journal.store import JournalStore
from memescreen.models import TokenBuy
from memescreen.scanner import walletwatch

T0 = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
MINT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _buy(sig, mint=MINT):
    return TokenBuy(signature=sig, mint=mint, amount=1.0, timestamp=T0)


def _on(monkeypatch):
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", "https://discord.com/api/webhooks/1/w")


def test_scan_noop_without_helius_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", None)
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", "https://x/y")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", str(tmp_path / "j.db"))
    assert walletwatch.scan(T0) == 0


def test_scan_noop_without_alert_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", str(tmp_path / "j.db"))
    assert walletwatch.scan(T0) == 0


def test_first_poll_bootstraps_silently(monkeypatch, tmp_path):
    _on(monkeypatch)
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    with JournalStore(db) as s:
        s.watch_wallet(address=WALLET, label="w1", user="u", now=T0)

    monkeypatch.setattr(walletwatch.helius, "fetch_wallet_buys",
                        lambda addr, until_sig=None: [_buy("sig-newest"), _buy("sig-older")])
    sent_calls = []
    monkeypatch.setattr(walletwatch.alerter, "send_wallet_buy",
                        lambda wallet, buy: sent_calls.append(buy.signature) or True)

    assert walletwatch.scan(T0) == 0
    assert sent_calls == []
    with JournalStore(db) as s:
        assert s.list_watched()[0]["last_sig"] == "sig-newest"


def test_subsequent_poll_alerts_oldest_first_and_advances_cursor(monkeypatch, tmp_path):
    _on(monkeypatch)
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    with JournalStore(db) as s:
        s.watch_wallet(address=WALLET, label="w1", user="u", now=T0)
        s.update_wallet_cursor(WALLET, "old-cursor")

    monkeypatch.setattr(walletwatch.helius, "fetch_wallet_buys",
                        lambda addr, until_sig=None: [_buy("new2"), _buy("new1")])
    order = []
    monkeypatch.setattr(walletwatch.alerter, "send_wallet_buy",
                        lambda wallet, buy: order.append(buy.signature) or True)

    assert walletwatch.scan(T0) == 2
    assert order == ["new1", "new2"]  # oldest first
    with JournalStore(db) as s:
        assert s.list_watched()[0]["last_sig"] == "new2"


def test_no_new_buys_leaves_cursor_untouched(monkeypatch, tmp_path):
    _on(monkeypatch)
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    with JournalStore(db) as s:
        s.watch_wallet(address=WALLET, label="w1", user="u", now=T0)
        s.update_wallet_cursor(WALLET, "cur")

    monkeypatch.setattr(walletwatch.helius, "fetch_wallet_buys", lambda addr, until_sig=None: [])
    assert walletwatch.scan(T0) == 0
    with JournalStore(db) as s:
        assert s.list_watched()[0]["last_sig"] == "cur"


def test_scan_counts_only_confirmed_sends(monkeypatch, tmp_path):
    _on(monkeypatch)
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    with JournalStore(db) as s:
        s.watch_wallet(address=WALLET, label="w1", user="u", now=T0)
        s.update_wallet_cursor(WALLET, "cur")

    monkeypatch.setattr(walletwatch.helius, "fetch_wallet_buys",
                        lambda addr, until_sig=None: [_buy("a"), _buy("b")])
    monkeypatch.setattr(walletwatch.alerter, "send_wallet_buy", lambda wallet, buy: False)
    assert walletwatch.scan(T0) == 0
    # cursor still advances even though delivery failed (same tradeoff as rejects)
    with JournalStore(db) as s:
        assert s.list_watched()[0]["last_sig"] == "a"
