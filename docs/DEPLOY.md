# Running `memescreen scan` 24/7 on Railway

The scanner is a long-running worker (no web port). Railway keeps it up, a
persistent volume keeps its SQLite history across restarts, and it posts alerts
to a Discord channel webhook.

**You do the account/click steps below. Everything in the repo (`railway.json`,
config) is already done.**

---

## 1. Create the Discord webhook (2 min)

1. In your Discord server, pick or make a channel for alerts (e.g. `#meme-scan`).
2. Channel name → **Edit Channel** → **Integrations** → **Webhooks** →
   **New Webhook**.
3. Name it (e.g. `memescreen`), **Copy Webhook URL**. It looks like
   `https://discord.com/api/webhooks/<id>/<token>`.
4. Keep it handy — it goes into Railway as `DISCORD_WEBHOOK_URL`. Treat it like
   a password (anyone with it can post to that channel).

## 2. Put the repo on GitHub (private)

Railway deploys from a Git repo.

1. On https://github.com/new create an **empty private** repo named `memescreen`
   (no README, no .gitignore — the repo already has both).
2. From `C:\Users\alexa\memescreen`:

   ```bash
   git remote add origin https://github.com/<your-username>/memescreen.git
   git push -u origin main
   ```

   `.env` and `data/` are gitignored, so your Birdeye key and local DB do **not**
   get pushed. Good.

## 3. Create the Railway project

1. https://railway.app → sign in with GitHub.
2. **New Project** → **Deploy from GitHub repo** → pick `memescreen`.
3. Railway reads `railway.json` and starts building. The first deploy will
   probably **fail or idle** until you finish steps 4–5 — that's expected.

## 4. Add a persistent volume (so history survives restarts)

Without this, every redeploy/restart wipes `token_history` and the scanner is
blind for ~45 min while it rebuilds.

1. In the service → **Settings** (or the **Volumes** tab) → **New Volume**.
2. **Mount path:** `/data`  ·  **Size:** 1 GB is plenty.
3. Attach it to the service.

## 5. Set environment variables

Service → **Variables** → add:

| Name | Value |
|---|---|
| `BIRDEYE_API_KEY` | your Birdeye key (from https://bds.birdeye.so) |
| `DISCORD_WEBHOOK_URL` | the webhook URL from step 1 |
| `DATA_DIR` | `/data`  ← must match the volume mount path |

Optional tuning knobs (all have sane defaults — leave them off until you've
watched a real run): `SPIKE_RATIO_MIN`, `MIN_LIQUIDITY`, `MIN_AGE_MIN`,
`HOLDER_GROWTH_MIN`, `HOLDER_GROWTH_ABS`, `POLL_INTERVAL_SEC`,
`SEED_INTERVAL_SEC`, `UNIVERSE_CAP`, `ALERT_COOLDOWN_H`.

## 6. Deploy & verify

1. Railway redeploys automatically after the variable change (or hit **Deploy**).
2. Open **Deployments → the running one → Logs**. Within ~1 minute you should see:

   ```
   INFO memescreen.scanner db: /data/memescreen.db
   INFO memescreen.scanner scan started
   INFO memescreen.scanner.universe seed: feeds=NN universe=NN
   INFO memescreen.scanner.poller poll: polled=NN pruned=0
   ```

   A `poll:` line every ~60s and a `seed:` line every ~180s = healthy.
3. It won't alert for a while — tokens need ≥45 min of history first, and most
   spikes won't clear the strict gate. That's the design.

## 7. After a day — tune

The `hits` table is the tuning instrument. From the Railway service:

```bash
railway run python -c "import sqlite3; c=sqlite3.connect('/data/memescreen.db'); \
  [print(r) for r in c.execute('SELECT fail_reason, COUNT(*) FROM hits GROUP BY fail_reason ORDER BY 2 DESC')]"
```

(`railway run` executes in the deployed environment with the volume attached.)
If everything reads `"age <60m"` or `"holders flat"`, loosen `MIN_AGE_MIN` /
`HOLDER_GROWTH_*` a notch. If the table is empty, `SPIKE_RATIO_MIN` is too high
for current market conditions — drop it toward 5–6.

---

## Notes / gotchas

- **Redeploying** (git push) restarts the process. The volume keeps the DB, so
  it's back to full sensitivity in one poll cycle, not 45 minutes.
- **Birdeye free tier is rate-limited.** You'll see occasional
  `birdeye feed ... failed: 429` lines — handled, the seed just skips that feed
  that cycle.
- **Cost:** a single always-on worker + a 1 GB volume is at or near Railway's
  free/hobby allowance; check your usage after a few days.
- **Rotating the Discord webhook:** delete it in Discord, make a new one, update
  the `DISCORD_WEBHOOK_URL` variable. The old one stops working immediately.
- **Local vs. deployed** both read the same env var names — a local `.env` and
  Railway Variables are interchangeable.
