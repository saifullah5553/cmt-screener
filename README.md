# Global CMT Breakout Screener

An autonomous daily breakout screener running on **GitHub Actions** — no server, no cost, no intervention. Once a day it scans nine markets, analyses only completed daily candles, and pushes a technician's read of the qualifying setups to Telegram.

The methodology is deliberately narrow: **price, trend, volume, relative strength, support and resistance**. Nothing else. The objective is 5–20 high-quality names, not a long tail of marginal signals.

> **Not investment advice.** Every level is a mechanical derivation from historical price and volume.

---

## Markets covered

| Code | Market | Discovery | Benchmark |
|---|---|---|---|
| `US` | NYSE / NASDAQ / AMEX | Yahoo screener | SPY |
| `AU` | ASX | Yahoo screener | `^AXJO` |
| `IN` | NSE / BSE India | Yahoo screener | `^NSEI` |
| `PK` | Pakistan Stock Exchange | static universe (90 names) | cohort |
| `SA` | Saudi Tadawul | Yahoo screener | `^TASI.SR` |
| `KW` | Boursa Kuwait | Yahoo screener | cohort |
| `EG` | EGX Egypt | Yahoo screener | cohort |
| `AE` | DFM / ADX (UAE) | static universe | cohort |
| `QA` | Qatar Exchange | static universe | cohort |

Yahoo's screener returns no candidates for Pakistan, UAE and Qatar, so those use curated ticker lists — every symbol verified for usable history and real turnover. "Cohort" means relative strength is ranked within that market rather than against an index, because no usable index ticker exists.

Oman and Bahrain are **not** covered — Yahoo has no usable data for them.

---

## Schedule

Runs daily at **13:00 UTC = 17:00 Dubai** (`cron: '0 13 * * *'`).

This is inside the only window where **every** market is closed, year-round through both DST regimes (measured: 12:30–14:00 UTC in January, 12:00–13:00 UTC in July). Consequently:

- Asia and Gulf markets report **that day's** completed session.
- The US has not opened yet (13:30 UTC EDT / 14:30 UTC EST), so it reports **yesterday's** session — intended.

Each market resolves its own last completed session independently, so nothing is ever analysed mid-trade even if GitHub fires the job late.

---

## What it looks for

1. **Trend** — Dow structure (higher highs *and* higher lows), price above rising 50/200-day averages, weekly confirmation.
2. **Stage** — Weinstein stage analysis on the weekly 30-week MA. Stage 3 (topping) and Stage 4 (declining) are rejected outright. This is the single highest-value filter.
3. **Base** — five structures only: VCP, Flat Base, Rectangle, Ascending Triangle, Cup & Handle. Each yields an objective pivot.
4. **Breakout** — a close above that pivot, in the upper part of the day's range, not extended beyond it.
5. **Volume** — contraction through the base, expansion on the break, plus accumulation vs distribution day counts.
6. **Relative strength** — a 0–100 rating from weighted 3/6/9/12-month benchmark-relative performance, percentile-ranked within its own market.
7. **Market context** — index trend, VIX and breadth set screening strictness automatically.
8. **Risk** — entry, stop, two targets and reward:risk, all derived from chart levels.

Score = Trend 25 · Base 25 · Volume 20 · Relative Strength 20 · Market 10. Risk is a **gate**, not a score — poor reward:risk cannot be averaged away.

Grades: **A** (confirmed), **B** (constructive), **W** (set up, awaiting the pivot trigger), **C** (rejected, with reasons).

Deliberately **not** used: RSI, MACD, Bollinger, Keltner, TTM squeeze, NR7/NR10, CMF, anchored VWAP, regression slope. See [REVIEW.md](REVIEW.md) for why each was removed.

---

## Layout

```
cmt_screener.py          entry point
screener/
  config.py              env-driven configuration
  markets.py             per-market registry: hours, timezone, sessions, universe
  sessions.py            US trading-day helpers
  universe.py            candidate discovery (screener + static)
  marketdata.py          download, repair, validate, weekly/monthly resample
  indicators.py          minimal indicator set
  patterns.py            the five base structures
  stage.py               Weinstein stage analysis
  strength.py            relative-strength rating + sector leadership
  regime.py              market context
  analysis.py            gates, weighted scoring, written observation
  notify.py              Telegram formatting and transport
  pipeline.py            orchestration
tests/test_screener.py   46 tests
```

---

## Setup

1. **Telegram** — message `@BotFather` → `/newbot` for a token; get your chat ID from `@userinfobot`.
2. **Secrets** — repo **Settings → Secrets and variables → Actions**: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Without them the run still completes and uploads the CSV artifact.
3. **Test** — **Actions → Global CMT Breakout Screener → Run workflow**, `force_run = true`.

## Tuning

All thresholds are `env:` entries in [.github/workflows/screener.yml](.github/workflows/screener.yml) — no code edit needed.

| Variable | Default | Meaning |
|---|---|---|
| `MARKETS` | `ALL` | Markets to scan, e.g. `US,IN,PK` |
| `SCREENER_LIMIT` | `80` | Movers examined per discovered market |
| `MIN_VOL_RATIO` | `1.4` | Volume expansion vs the 50-day average |
| `MIN_RS_RATING` | `70` | Minimum relative-strength rating |
| `MIN_CLOSE_RANGE_POS` | `0.55` | Close must land in the upper part of the range |
| `MAX_EXT_FROM_PIVOT` | `8.0` | Don't chase extended breakouts (%) |
| `MIN_RR` | `2.0` | Minimum reward:risk |
| `MIN_SCORE` | `70` | Composite score floor |
| `WATCH_ENABLED` | `true` | Report names approaching their pivot |

## Local run

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
FORCE_RUN=true MARKETS=US python cmt_screener.py
```

## Data notes

`yfinance` is the sole source. Two quirks are handled explicitly, both of which silently corrupted results before being fixed:

- **Unsettled bars** — Yahoo publishes the newest daily bar with `close = NaN` for hours after a close. `dropna(how="all")` does not remove a partially-null row, so price became `NaN`, every comparison returned `False`, and *every* stock graded C. Repaired from the quote endpoint — but never for a market that is still open, where a live quote is not a close.
- **Fabricated holidays** — non-US exchanges report holidays as volume-0 bars with all four prices equal. These understate ATR (up to +18.6%), inflate the volume ratio (~1.14×) and mimic volatility contraction. They are stripped on load.
