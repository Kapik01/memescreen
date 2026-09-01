# memescreen

Solana meme-coin due-diligence screener.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env      # optional: add BIRDEYE_API_KEY / HELIUS_API_KEY for extra data
```

## Usage

```bash
memescreen screen <TOKEN_MINT_ADDRESS>
```

Prints market data, safety flags, a 0–100 score, and a TRADE / CAUTION / AVOID
verdict. Works with no API keys (DexScreener + RugCheck); adding Birdeye/Helius
keys enriches holder data and metadata.

### Known caveat: unverified provider field paths

The Birdeye and Helius JSON field paths — `data.holder`, `data.items[].percentage`
(Birdeye) and `result.content.metadata.name` / `.symbol` (Helius) — are mapped
from vendor documentation and have **not** been verified against live responses.
DexScreener and RugCheck were. If holder counts, top-10 concentration, or the
token name/symbol fallback look wrong, check those paths against a real response
first — the fixtures in `tests/fixtures/` encode the same assumption.

## scan — continuous volume-spike scanner

    memescreen scan

Long-running. Polls a wide universe of Solana tokens every ~60s, learns each
token's own normal 5-minute volume, and alerts when one spikes ≥8x its own
baseline **and** the spike is buy-driven, accelerating on the hour scale, not
draining liquidity, and fresh — then only if the token passes a strict safety
gate (LP locked ≥90%, mint & freeze renounced, ≥$20k liquidity, ≥60 min old,
holder count rising alongside volume, no RugCheck "danger").

Alerts go to a Discord channel when `DISCORD_WEBHOOK_URL` is set in `.env`
(Channel → Edit → Integrations → Webhooks → New Webhook → Copy Webhook URL);
otherwise the scanner prints them and appends to `<DATA_DIR>/missed_alerts.log`.
Needs `BIRDEYE_API_KEY` for the holder-growth check — without it, every hit
fails that check (fail-closed) and nothing alerts.

State lives in `<DATA_DIR>/memescreen.db` (SQLite); `DATA_DIR` defaults to
`data`. Stop with Ctrl+C — it finishes the current cycle, prunes, and exits
cleanly. To run it 24/7 without your machine on, see `docs/DEPLOY.md`
(Railway + Discord).

**This is not a money printer.** Most Solana volume spikes are manufactured to
trip exactly this kind of screener. The scanner makes you faster and better-
informed; it does not make you right. Thresholds live in `.env` (see
`config.py`) — expect to tune `SPIKE_RATIO_MIN`, `MIN_LIQUIDITY`, and
`MIN_AGE_MIN` against real results.

## Tests

```bash
pytest
```

## What the verdict means

It is advisory. AVOID = at least one disqualifying red flag or a very low score.
CAUTION = notable yellow flags or a middling score. TRADE = clean on every check
this tool can see. None of it is financial advice, and manufactured volume /
fake liquidity can still pass. Always read the flag list, not just the verdict.
