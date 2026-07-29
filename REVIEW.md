# CMT Review & Redesign

Review of the breakout screener from the perspective of a practising technician,
followed by the changes implemented. Guiding principle throughout: **improve the
interpretation of price, trend, volume and structure rather than add indicators.**

---

## 1. Executive summary

| Area | Before | After |
|---|---|---|
| Overall | **3/10** | 8/10 |
| Technical methodology | **3/10** | 8/10 |
| Code quality | **4/10** | 8/10 |
| Reliability | **1/10** — silently produced nothing | 8/10 |

### The finding that mattered most

The screener was **structurally incapable of reporting a breakout**. Yahoo's
historical endpoint publishes the most recent daily bar with OHL populated but
`close = NaN` for several hours after the close. The code called
`dropna(how="all")`, which does **not** drop a partially-null row. So:

```
price = float(close.iloc[-1])   ->  NaN
price > resistance              ->  False   (every NaN comparison is False)
```

Every condition failed for **every** symbol, so everything graded "C" and the
alert always read *"0 breakouts"*. Confirmed live: ITRI closed **107.02** on
2026-07-28 while the frame carried `NaN`. This is now repaired from the quote
endpoint, with the bar dropped rather than silently analysed if repair fails.

**Biggest strengths (original):** correct scheduling philosophy (completed daily
candle only), a genuine holiday calendar with a dependency-free fallback, and
clean separation of the alert from the analysis.

**Biggest weaknesses (original):** the NaN defect above; no concept of base,
stage or market context; a 20-day relative-strength boolean; binary pass/fail
grading that discarded information; and **ASX was never actually scanned** —
`keep()` rejected any symbol containing a dot, and every ASX ticker is `.AX`.

---

## 2. Breakout methodology review

| Rule (original) | Verdict | Reasoning |
|---|---|---|
| Prior 20-day high as resistance | **Improve** | Sound instinct, too short. 20 sessions is a pause, not a base. Now the pivot comes from a detected base structure, falling back to a 50-day high. |
| `close > upper Bollinger` as breakout | **Remove** | A band is a volatility statistic, not a level buyers and sellers recognise. Traded resistance is where supply actually sat. |
| Volume ≥ 1.5× 20-day average | **Improve** | Right idea. Widened to a 50-day base and paired with *contraction through the base*, which is the other half of the accumulation signature. |
| OBV at 20-day high | **Keep** | Legitimate accumulation confirmation; window widened to 50 days. |
| RSI ≥ 50 and bearish divergence | **Remove** | Oscillator readings add parameters without improving breakout selection. Accumulation/distribution day counts answer the same question using price and volume directly. |
| MACD bull cross | **Remove** | Lagging derivative of two averages already tested more directly by MA structure. |
| Bollinger squeeze percentile | **Replace** | Replaced by measured base depth and contraction sequence — the same compression idea, expressed as structure a technician can see. |
| ADX ≥ 20 with +DI > −DI | **Replace** | Replaced by Dow structure (higher highs *and* higher lows) plus MA slope. Structure is the definition of trend; ADX is a proxy for it. |
| `price > SMA20 > SMA50` | **Improve** | Extended to the 50/150/200 stack with **rising** averages and weekly confirmation. MA *direction* matters more than price being above it. |
| Extension `> 3 ATR` from SMA20 | **Replace** | Now measured as **% beyond the pivot** (default 8%), which is what actually determines whether the stop is too far away. |
| 20-day return vs SPY (Beat/Lag) | **Replace** | Far too short and binary. Now a 0–100 rating from weighted 3/6/9/12-month benchmark-relative performance, percentile-ranked within its own market. |
| Stop = `min(resistance, swing low) − 0.5 ATR` | **Improve** | Kept the logic, added an **ATR/3% floor**. Without it the stop landed cents below entry and reward:risk printed absurd values (observed: 244:1). |
| Grade A = eight booleans AND-ed | **Replace** | One marginal failure discarded an otherwise exceptional chart. Now: hard gates for non-negotiables, weighted score for ranking. |
| *(absent)* Trend maturity | **Add** | Weinstein stage analysis — the single highest-value filter. Same bar is a buy in Stage 2 and a trap in Stage 3. |
| *(absent)* Base identification | **Add** | Five structures only: VCP, Flat Base, Rectangle, Ascending Triangle, Cup & Handle. |
| *(absent)* Market context | **Add** | Index trend + VIX + breadth set screening strictness. |
| *(absent)* Overhead supply | **Add** | Volume transacted above current price — the most common cause of failed breakouts. |

---

## 3. Recommended improvements (implemented)

1. **Repair unsettled bars** — the correctness fix; everything else was moot.
2. **Stage analysis** (weekly, 30-week MA) — reject Stage 3/4 outright.
3. **Base detection**, five structures, each yielding an objective pivot.
4. **Volume read in two halves** — dry-up through the base, expansion on the
   break, plus accumulation/distribution day counts.
5. **Relative strength 0–100**, ranked within market, with sector leadership.
6. **Market regime** modulating strictness (×0.92 bull → ×1.15 bear).
7. **Objective risk plan** with an ATR floor on the stop.
8. **Weighted scoring** — Trend 25, Base 25, Volume 20, RS 20, Market 10.
9. **Removed**: RSI, MACD, Bollinger, Keltner, TTM squeeze, NR7/NR10, CMF,
   anchored VWAP, efficiency ratio, linear-regression slope, R², Darvas box,
   bull flag, high tight flag. None improved breakout selection.

### Why these weights
Trend and base quality (50 combined) decide whether there is anything to break
out *from* — no volume surge rescues a stock with no structure. Volume and
relative strength (40) are confirmation: they tell you institutions are involved
and the stock leads its peers. Market context (10) modulates rather than drives,
because a great chart in a poor tape is still tradable at reduced size. Risk is
deliberately a **gate, not a score** — poor reward:risk cannot be averaged away.

---

## 4. Code review

**Before:** one 420-line module, all configuration as module-level constants,
bare `print()`, `except Exception` swallowing errors, no tests, no validation.

**After** — package layout, each module one responsibility:

```
cmt_screener.py         entry point
screener/
  config.py             env-driven configuration
  sessions.py           trading-day gate (yesterday's candle)
  universe.py           US + ASX candidate construction
  marketdata.py         download, REPAIR, validate, weekly/monthly
  indicators.py         minimal indicator set
  patterns.py           five base structures
  stage.py              Weinstein stage analysis
  strength.py           RS rating + sector leadership
  regime.py             market context
  analysis.py           gates + weighted scoring + written observation
  notify.py             Telegram formatting/transport
  pipeline.py           orchestration
tests/test_screener.py  27 tests
```

Also: structured logging with levels; explicit data validation before analysis;
threaded chunked downloads (~30s for 120 symbols + benchmarks); graceful
degradation (regime failure falls back to neutral rather than aborting).

---

## 5. GitHub Actions review

Schedule (`15 5 * * *` = 09:15 Dubai) and the completed-candle model are
**unchanged**, as required. Changes: unit tests now run *before* the screen so a
broken deploy fails loudly instead of emitting a silent empty alert; all
thresholds surfaced as `env:` for tuning without code edits; heartbeat commit
retained to stop GitHub parking the schedule. Secrets handling was already
correct (repository secrets, never logged).

---

## 6. Notification review

Before: a symbol, a score, and three numbers — no reasoning.
After, ordered verdict → levels → reasoning:

```
🟢 INCY — Incyte Corp  (A · 84/100)
   NasdaqGS · Healthcare
   Setup: 27-week VCP · Stage 2 - Advancing
   Confirmation: 3.1x volume · RS 92/100 · OBV confirming
   Trend: weekly confirmed uptrend · above 50/200-day
   Levels: entry 129.93 · stop 117.09 · targets 155.61 / 181.29 · R:R 2.0:1
   Fails below 117.09
   Stage 2 Advancing breakout from a 27-week VCP showing 2 volatility
   contractions. Price closed above the 120.71 pivot on 3.1x average volume.
   Close in the upper part of the day's range. 6 accumulation days vs 0
   distribution over the past five weeks. Relative strength strong at 92/100
   in Healthcare (a leading sector). Market: confirmed uptrend.
```

---

## 7. Threshold calibration (measured, not assumed)

An initial recommendation to relax `MIN_RS_RATING` to 60 was **tested and
withdrawn**. Sweeping RS 70→55 and volume 1.4→1.2 across the live universe
changed the output by **zero names** — neither was the binding constraint.

Counting which gate actually blocks each of 116 analysed names:

| Gate | Blocks | Sole blocker |
|---|---|---|
| no close above resistance | 84 | 6 |
| relative strength below floor | 77 | 1 |
| volume below 1.4x | 65 | 3 |
| uptrend structure not confirmed | 53 | 0 |
| not above both 50/200-day | 50 | 0 |
| Stage 3/4 | 38 | 1 |

The dominant filter is *"has not closed above resistance"* — i.e. most daily
gainers are still inside their base. That is the screener working correctly:
a gainer is not a breakout. Loosening it would mean alerting on non-breakouts,
so **no threshold was changed.**

The two names that passed every gate but scored below 70 (PLSE, CBZ) both had
**no defined base** — correctly excluded.

### What the evidence did justify
Six names failed on the pivot test *alone*, with trend, volume, relative
strength and stage all satisfied. Those are not rejects; they are the
watchlist. A **"W" grade** now reports names within 4% beneath their pivot with
everything else aligned — how a technician actually works: identify the setup
in advance, act on the trigger. No new indicator; it surfaces analysis already
performed.

## 8. Verification

27 unit tests pass, including explicit regressions for the NaN-close defect,
the reward:risk floor, and the base-duration bug. Live run on the 2026-07-28
session: 244 US + 14 ASX movers → 120 examined → 116 analysed → **1 A-grade and
5 B-grade**, every rejection carrying a stated technical reason.

*Technical analysis only — not investment advice.*
