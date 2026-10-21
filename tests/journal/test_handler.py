from datetime import UTC, datetime, timedelta

from memescreen.journal.handler import handle
from memescreen.journal.store import JournalStore

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
ADDR = "So11111111111111111111111111111111111111112"
AUTHOR = "user-1"

PUMP_ALERT = f"""```
📈 NEW PUMP — $RUN   +24% 1h
{ADDR}
https://dexscreener.com/solana/{ADDR}
```"""

SPIKE_ALERT = f"""```
🚨 VOLUME SPIKE — $WIF
Contract: {ADDR}
```"""


def _h(store, content, replied_to=None, now=T0, author=AUTHOR):
    return handle(content=content, replied_to=replied_to, author_id=author,
                  store=store, now=now)


def test_chatter_is_ignored():
    with JournalStore() as s:
        assert _h(s, "gm frens") is None


def test_in_then_out_round_trip_tags_alert_kind():
    with JournalStore() as s:
        r = _h(s, "in 0.001 100", replied_to=PUMP_ALERT)
        assert "in" in r.text and "RUN" in r.text
        r = _h(s, "out 0.0025", replied_to=PUMP_ALERT, now=T0 + timedelta(hours=2))
        assert "+150.0%" in r.text
        summary = _h(s, "!stats").text
        assert "NEW PUMP" in summary and "win rate" in summary


def test_in_without_a_reply_explains():
    with JournalStore() as s:
        assert "reply to an alert" in _h(s, "in 0.001").text


def test_reply_to_message_without_address():
    with JournalStore() as s:
        assert "couldn't find a token address" in _h(s, "in 0.001", replied_to="hello").text


def test_double_in_is_rejected():
    with JournalStore() as s:
        _h(s, "in 0.001", replied_to=SPIKE_ALERT)
        assert "open position" in _h(s, "in 0.002", replied_to=SPIKE_ALERT).text


def test_out_without_open_position():
    with JournalStore() as s:
        assert "no open position" in _h(s, "out 0.002", replied_to=PUMP_ALERT).text


def test_open_command_lists_positions():
    with JournalStore() as s:
        _h(s, "in 0.001", replied_to=PUMP_ALERT)
        assert "RUN" in _h(s, "!open").text


def test_note_requires_an_existing_trade():
    with JournalStore() as s:
        assert "no trade on that token" in _h(s, "note nice chart", replied_to=PUMP_ALERT).text
        _h(s, "in 0.001", replied_to=PUMP_ALERT)
        assert _h(s, "note nice chart", replied_to=PUMP_ALERT).text == "noted."


def test_stats_before_any_trade():
    with JournalStore() as s:
        assert "No closed trades yet" in _h(s, "!stats").text


# --- wallet-watch commands --------------------------------------------

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def test_watch_wallet_end_to_end():
    with JournalStore() as s:
        r = _h(s, f"!watch {WALLET} whale1")
        assert "watching" in r.text and "whale1" in r.text
        r = _h(s, "!wallets")
        assert "whale1" in r.text and WALLET in r.text
        r = _h(s, f"!unwatch {WALLET}")
        assert "stopped watching" in r.text
        assert "not watching any wallets" in _h(s, "!wallets").text


def test_watch_without_label_uses_short_address():
    with JournalStore() as s:
        r = _h(s, f"!watch {WALLET}")
        assert WALLET[:8] in r.text


def test_watch_malformed_shows_usage():
    with JournalStore() as s:
        assert "usage" in _h(s, "!watch").text


def test_watch_over_the_cap(monkeypatch):
    import memescreen.journal.store as jstore_mod
    monkeypatch.setattr(jstore_mod.config, "WALLET_WATCH_MAX", 1)
    with JournalStore() as s:
        _h(s, "!watch " + "1" * 32)
        r = _h(s, "!watch " + "2" * 32)
        assert "max" in r.text


# --- candidate wallets --------------------------------------------------

def test_candidates_empty_before_any_sightings():
    with JournalStore() as s:
        assert "no repeat-buyer" in _h(s, "!candidates").text


def test_candidates_lists_repeat_buyers_with_watch_hint():
    with JournalStore() as s:
        s.record_candidate_sighting(WALLET, "mintA", "spike", T0)
        s.record_candidate_sighting(WALLET, "mintB", "pump", T0)
        r = _h(s, "!candidates")
        assert WALLET[:8] in r.text
        assert f"!watch {WALLET}" in r.text
        assert "2x" in r.text


def test_results_command_uses_injected_fn():
    asked = []
    with JournalStore() as s:
        r = handle(content="!results 3", replied_to=None, author_id=AUTHOR,
                   store=s, now=T0, results=lambda d: asked.append(d) or f"R{d}")
    assert r.text == "R3" and asked == [3]


def test_results_command_without_scanner():
    with JournalStore() as s:
        r = _h(s, "!results")
    assert "scanner" in r.text


def test_results_command_errors_are_friendly():
    def boom(days):
        raise RuntimeError("db locked")
    with JournalStore() as s:
        r = handle(content="!results", replied_to=None, author_id=AUTHOR,
                   store=s, now=T0, results=boom)
    assert "couldn't load results" in r.text


def test_help_mentions_results():
    with JournalStore() as s:
        assert "!results" in _h(s, "!help").text
