# Deribit Testnet Setup Guide

> ⚠️ **SUPERSEDED — options-era doc.** This covers the retired Deribit **options** testnet (now in `legacy_options/`). The "Testnet" tab in the current dashboard is **Freyr's Hyperliquid testnet** account, written by Freyr and read read-only here — *not* Deribit. Read **[`../CONTEXT.md`](../CONTEXT.md)**. Kept only as a `legacy_options/` reference.

This guide walks you through connecting the BTC Wheel Bot to
[test.deribit.com](https://test.deribit.com) so that real API orders are
placed with **fake BTC** — the safest possible validation before going live.

---

## Prerequisites

- Python dependencies installed (`pip install -r requirements.txt`)
- Bot farm running and at least one config in paper mode

---

## Step 1 — Create a Deribit Testnet Account

1. Go to **https://test.deribit.com** and create an account (separate from mainnet).
2. Log in and navigate to **Account → Settings → API**.
3. Click **"Create API Key"**.
4. Give it a name (e.g. `wheel-bot-testnet`) and grant it the scope:
   - ✅ **trade:read_write** — required to place and cancel orders
5. Copy your **Client ID** and **Client Secret** — you will only see the secret once.

---

## Step 2 — Fill in Your Credentials

Open `config/deribit_testnet.json` (created automatically in the project root):

```json
{
  "client_id":     "YOUR_TESTNET_CLIENT_ID",
  "client_secret": "YOUR_TESTNET_CLIENT_SECRET",
  "environment":   "testnet",
  "base_url":      "https://test.deribit.com/api/v2"
}
```

Replace the placeholder values with your real testnet credentials.

> **Security note:** `config/deribit_testnet.json` is listed in `.gitignore`
> and will never be committed to version control.

---

## Step 3 — Enable Testnet in the RL Agent Config

Open `configs/rl-agent-v1.yaml` and change:

```yaml
use_deribit_testnet: false
```

to:

```yaml
use_deribit_testnet: true
```

---

## Step 4 — Run the Bot Farm

```bash
python bot_farm.py
```

The RL Agent V1 bot will now submit **real orders** to `test.deribit.com`
every time it generates a SELL_PUT, SELL_CALL, or CLOSE signal.

Watch for these log lines to confirm the executor is active:

```
[farm] rl-agent-v1: use_deribit_testnet=true AND credentials found — testnet executor ACTIVE
[testnet] PaperExecutor loaded — target: https://test.deribit.com/api/v2 | log: farm/rl-agent-v1/testnet_trades.jsonl
```

---

## Step 5 — Verify Orders on the Exchange

1. Log in to **https://test.deribit.com**.
2. Navigate to **Portfolio → Positions** or **Trading → Order History**.
3. You should see filled orders matching the bot's trade log.

The bot also writes every testnet fill to:

```
farm/rl-agent-v1/testnet_trades.jsonl
```

Each line is a JSON record:

```json
{
  "timestamp":      "2025-05-30T08:01:23+00:00",
  "action":         "SELL_PUT",
  "instrument":     "BTC-30MAY25-77000-P",
  "direction":      "sell",
  "contracts":      0.1,
  "fill_price_btc": 0.0048,
  "bid":            0.0045,
  "ask":            0.0052,
  "order_id":       "BTC-12345678",
  "status":         "filled"
}
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Log says "Placeholder credentials" | Fill in real `client_id` / `client_secret` in `config/deribit_testnet.json` |
| Log says "config/deribit_testnet.json not found" | Create the file (template already exists) |
| `Auth failed: ...` | Check that your API key has `trade:read_write` scope on testnet |
| "no matching instrument found" | Testnet sometimes has thin liquidity; try a different DTE target |
| Orders placed but not appearing on exchange | Verify you're logged into **test**.deribit.com, not mainnet |

---

## Disabling Testnet Mode

Set `use_deribit_testnet: false` in `configs/rl-agent-v1.yaml` and restart
the farm.  The bot reverts to local paper-only simulation — no API calls.

---

## Going to Mainnet

When you are satisfied with testnet results:

1. Create a **mainnet** API key at `https://www.deribit.com` with `trade:read_write`.
2. Copy credentials to `config/deribit_testnet.json` and change `base_url` to
   `https://www.deribit.com/api/v2` (and `environment` to `mainnet`).
3. Run `python main.py --mode=testnet` first for the full pre-flight check.
4. Then `python main.py --mode=live` for real-money trading.

> ⚠️ **Live trading involves real money. Never skip the testnet validation step.**
