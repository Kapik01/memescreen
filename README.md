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

`memescreen scan` — continuous volume-spike scanner — arrives in Plan 2.

## Tests

```bash
pytest
```

## What the verdict means

It is advisory. AVOID = at least one disqualifying red flag or a very low score.
CAUTION = notable yellow flags or a middling score. TRADE = clean on every check
this tool can see. None of it is financial advice, and manufactured volume /
fake liquidity can still pass. Always read the flag list, not just the verdict.
