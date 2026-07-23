# Automated CMT Breakout Screener

A fully autonomous daily stock screener that runs on **GitHub Actions** — no server, no cost, no human intervention. Every US market trading day, after the close, it:

1. **Checks the calendar** — exits immediately on weekends and NYSE/NASDAQ holidays (and half-days), using the official exchange calendar with a built-in pure-Python fallback. You never maintain a holiday list.
2. **Finds candidates** — pulls the day's top gainers (positive movers) from Yahoo Finance via `yfinance`. No Barchart, no login.
3. **Downloads data** — one year of daily OHLCV for each candidate (plus SPY as a benchmark), in a single bulk request.
4. **Analyzes like a CMT** — multi-factor, confluence-based technical scoring (trend structure, ADX, Bollinger squeeze/%B, volume expansion + OBV, RSI with divergence, MACD, ATR-based stops, measured-move targets, relative strength) and grades each name **A / B / C**.
5. **Alerts you** — pushes the confirmed setups to Telegram and saves a full sorted CSV.

> **Not investment advice.** Every level is a mechanical derivation from historical price/volume data, meant to support your own decisions.

---

## What each file does

| File | Purpose |
|---|---|
| `cmt_screener.py` | The whole pipeline: gate → candidates → data → analysis → alert. |
| `nyse_cal.py` | Zero-dependency NYSE holiday calendar (fallback for the gate). |
| `.github/workflows/screener.yml` | The GitHub Actions schedule + run definition. |
| `requirements.txt` | Python dependencies. |

---

## One-time setup (about 15 minutes)

### 1. Create the repository
Create a new **private** GitHub repo and add these files (keep the folder layout, including `.github/workflows/screener.yml`). Either upload them in the web UI or:

```bash
git init
git add .
git commit -m "CMT breakout screener"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Scheduled workflows only run from the **default branch** (`main`), so make sure the files are there.

### 2. Create a Telegram bot (for alerts)
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts. It gives you a **bot token** like `123456789:AAE...`.
2. Send your new bot any message (e.g. "hi") so it's allowed to message you.
3. Get your **chat ID**: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read `result[].message.chat.id`. (Or message **@userinfobot**, which replies with your ID.)

*(To alert a group instead: add the bot to the group, send a message there, and read the negative group chat ID from `getUpdates`.)*

### 3. Add GitHub secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the BotFather token |
| `TELEGRAM_CHAT_ID` | your chat ID |

*(Skip these to run in "CSV-only" mode — the job still runs and uploads the results file as a build artifact.)*

### 4. Enable and test
1. Open the **Actions** tab and enable workflows if prompted.
2. Click **CMT Breakout Screener → Run workflow**. Set **force_run = true** to bypass the trading-day gate so you can test on a weekend/holiday.
3. Watch the run log. On success you'll get a Telegram message and a `screener-results` artifact (the CSV) attached to the run.

That's it — from then on it runs itself every trading day at 21:30 UTC.

---

## Scheduling notes

- **Time:** `21:30 UTC` weekdays = **after** the 4:00pm ET close year-round (17:30 ET in summer, 16:30 ET in winter). GitHub cron is UTC and does **not** observe daylight saving, so a fixed UTC time is intentional. To run at a different time, edit the `cron:` line in `screener.yml` ([crontab.guru](https://crontab.guru) helps).
- **Weekends** are excluded by the `1-5` (Mon–Fri) day-of-week filter; **holidays** are handled inside the script.
- **GitHub delays:** scheduled runs can start a few minutes late under load — harmless here since the daily bar is already final.
- **60-day inactivity:** GitHub disables scheduled workflows in repos with no activity for 60 days. Any push re-enables it; the monthly artifact/commit activity from normal use usually keeps it alive, or add a tiny periodic commit if the repo goes idle.

## Tuning (edit the `env:` block in `screener.yml`)

| Variable | Default | Meaning |
|---|---|---|
| `SCREENER_LIMIT` | `50` | Max gainers to analyze per day. |
| `MIN_PRICE` | `5` | Skip names under this price. |
| `MIN_CHANGE_PCT` | `2.0` | Candidate must be up at least this % on the day. |
| `MIN_DOLLAR_VOL` | `5000000` | Liquidity floor (price × volume). |
| `INCLUDE_GRADE_B` | `true` | Also send constructive (B) setups, not just A. |
| `MIN_RR` | `2.0` | Reward:risk below this is flagged lower-quality. |

## About the data source

`yfinance` scrapes Yahoo Finance and occasionally rate-limits datacenter IPs. This screener keeps its footprint tiny (one screener call + one bulk download per day), which avoids the problem in practice. If Yahoo ever returns nothing, the run exits cleanly and tells you rather than sending bad data. A paid, more robust alternative (e.g. reinstating the Twelve Data pull for OHLCV) can be swapped into `get_ohlcv()` if you outgrow the free tier.

## Local test (optional)

```bash
pip install -r requirements.txt
FORCE_RUN=true python cmt_screener.py      # runs today regardless of the calendar
```

Set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in your shell to test alerts locally.
