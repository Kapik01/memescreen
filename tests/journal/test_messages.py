from memescreen.journal.messages import (
    alert_kind,
    extract_address,
    extract_symbol,
    parse_command,
)

_ADDR = "So11111111111111111111111111111111111111112"

_SPIKE_MSG = f"""```
🚨 VOLUME SPIKE — $WIF  (score 82/100)

5m vol   $47.2k   ·   18.0x baseline
Contract: {_ADDR}
Chart: https://dexscreener.com/solana/{_ADDR}
Run: screen {_ADDR}
```"""

_PUMP_MSG = f"""```
📈 NEW PUMP — $RUN   +24% 1h  ·  age 3h 5m  ·  vol/mcap 1.8x  ·  top10 22%  ·  liq $61k
unvetted — no safety gate ran, DYOR
{_ADDR}
https://dexscreener.com/solana/{_ADDR}
```"""


def test_extract_address_prefers_contract_line():
    assert extract_address(_SPIKE_MSG) == _ADDR


def test_extract_address_from_dexscreener_link():
    assert extract_address(f"see https://dexscreener.com/solana/{_ADDR} now") == _ADDR


def test_extract_address_bare_fallback():
    assert extract_address(f"aped {_ADDR}") == _ADDR


def test_extract_address_none_when_absent():
    assert extract_address("no address here") is None
    assert extract_address(None) is None


def test_alert_kind():
    assert alert_kind(_SPIKE_MSG) == "spike"
    assert alert_kind(_PUMP_MSG) == "pump"
    assert alert_kind("❌ TOK dropped: holders flat") is None
    assert alert_kind(None) is None


def test_extract_symbol():
    assert extract_symbol(_SPIKE_MSG) == "WIF"
    assert extract_symbol(_PUMP_MSG) == "RUN"
    assert extract_symbol("no ticker") is None


def test_parse_in_price_only():
    c = parse_command("in 0.0021")
    assert (c.kind, c.price, c.size) == ("in", 0.0021, None)


def test_parse_in_with_size_and_dollar_sign():
    c = parse_command("in $0.0021 50")
    assert (c.kind, c.price, c.size) == ("in", 0.0021, 50.0)


def test_parse_in_scientific_notation():
    assert parse_command("in 1.2e-7").price == 1.2e-7


def test_parse_in_rejects_nonpositive_or_garbage():
    assert parse_command("in 0") is None
    assert parse_command("in -1") is None
    assert parse_command("in abc") is None
    assert parse_command("in") is None


def test_parse_out():
    c = parse_command("out 0.009")
    assert (c.kind, c.price) == ("out", 0.009)


def test_parse_buy_sell_aliases():
    assert parse_command("buy 1").kind == "in"
    assert parse_command("sell 1").kind == "out"


def test_parse_note_keeps_the_rest_verbatim():
    c = parse_command("note strong CT presence, dev doxxed")
    assert (c.kind, c.note) == ("note", "strong CT presence, dev doxxed")


def test_parse_bang_commands():
    assert parse_command("!stats").kind == "stats"
    assert parse_command("!open").kind == "open"
    assert parse_command("!help").kind == "help"


def test_parse_returns_none_for_chatter():
    assert parse_command("gm") is None
    assert parse_command("") is None
    assert parse_command("this looks good") is None


# --- wallet-watch commands --------------------------------------------

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def test_parse_watch_with_label():
    c = parse_command(f"!watch {WALLET} whale1")
    assert (c.kind, c.address, c.note) == ("watch", WALLET, "whale1")


def test_parse_watch_without_label():
    c = parse_command(f"!watch {WALLET}")
    assert (c.kind, c.address, c.note) == ("watch", WALLET, None)


def test_parse_watch_label_keeps_spaces():
    c = parse_command(f"!watch {WALLET} the OG whale")
    assert c.note == "the OG whale"


def test_parse_watch_malformed_returns_watch_help():
    assert parse_command("!watch").kind == "watch_help"
    assert parse_command("!watch not-an-address").kind == "watch_help"


def test_parse_unwatch():
    c = parse_command(f"!unwatch {WALLET}")
    assert (c.kind, c.address) == ("unwatch", WALLET)


def test_parse_unwatch_malformed_returns_watch_help():
    assert parse_command("!unwatch").kind == "watch_help"
    assert parse_command("!unwatch nope").kind == "watch_help"


def test_parse_wallets_list_command():
    assert parse_command("!wallets").kind == "wallets"
    assert parse_command("!watched").kind == "wallets"
