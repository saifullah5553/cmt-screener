#!/usr/bin/env python3
"""
Automated CMT-Grade Breakout Screener  (GitHub Actions / yfinance / Telegram)
=============================================================================

Fully autonomous daily pipeline:

  1. TRADING-DAY GATE   Exit immediately on weekends and US market (NYSE/NASDAQ)
                        holidays -- via pandas_market_calendars (XNYS) if present,
                        else a built-in zero-dependency NYSE holiday calendar.
  2. CANDIDATES         Pull the day's top gainers (positive movers) from Yahoo
                        Finance via yfinance -- no Barchart, no login.
  3. DATA               Bulk daily OHLCV (1y) for the candidates via yfinance.
  4. ANALYSIS           Multi-factor, confluence-based CMT scoring; grade A/B/C.
  5. NOTIFY             Push confirmed setups to Telegram (optional) + write CSV.

No human intervention required. Configure via environment variables (see README).

This is a technical-analysis tool, not investment advice. Every level is a
mechanical derivation from historical price/volume data.
"""

import os
import sys
import json
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

# ------------------------------------------------------------------ configuration
def env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default

TELEGRAM_TOKEN   = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT    = env("TELEGRAM_CHAT_ID")
SCREENER_LIMIT   = int(env("SCREENER_LIMIT", "50"))     # max candidates to analyze
MIN_PRICE        = float(env("MIN_PRICE", "5"))         # skip sub-$5 names
MIN_CHANGE_PCT   = float(env("MIN_CHANGE_PCT", "2.0"))  # candidate must be up >= this %
MIN_DOLLAR_VOL   = float(env("MIN_DOLLAR_VOL", "5e6"))  # liquidity floor (price*vol)
INCLUDE_GRADE_B  = env("INCLUDE_GRADE_B", "true").lower() == "true"
MIN_RR           = float(env("MIN_RR", "2.0"))          # reward:risk quality flag
FORCE_RUN        = env("FORCE_RUN", "false").lower() == "true"  # bypass gate for testing
BENCHMARK        = env("BENCHMARK", "SPY")
HISTORY_PERIOD   = env("HISTORY_PERIOD", "1y")          # yfinance history window
NY = ZoneInfo("America/New_York")

# ---------------------------------------------------------------- trading-day gate
def is_trading_day(d):
    """True if `d` (a date) is a NYSE/NASDAQ session. Prefer the exchange calendar
    library; fall back to the bundled pure-Python NYSE holiday rules."""
    try:
        import pandas_market_calendars as mcal
        cal = mcal.get_calendar("XNYS")
        sessions = cal.valid_days(start_date=d.isoformat(), end_date=d.isoformat())
        return len(sessions) > 0
    except Exception:
        from nyse_cal import is_trading_day as fallback
        return fallback(d)

# ------------------------------------------------------------------- data sourcing
def get_candidates(limit):
    """Return a list of (symbol, exchange, price, change_pct) for the day's gainers."""
    import yfinance as yf
    rows = []

    def keep(sym, exch, price, chg, vol):
        if not sym or sym.endswith((".", "-")) or "." in sym:
            return  # skip odd/foreign tickers; keep clean US symbols
        exch = (exch or "")
        if not any(k in exch for k in ("Nasdaq", "NYSE", "NMS", "NGM", "NCM", "NYQ", "ASE")):
            return
        if "Arca" in exch:      # mostly ETFs
            return
        if price and price < MIN_PRICE:
            return
        if chg is not None and chg < MIN_CHANGE_PCT:
            return
        if price and vol and price * vol < MIN_DOLLAR_VOL:
            return
        rows.append((sym, exch, price, chg))

    # Primary: yfinance predefined screener
    try:
        res = yf.screen("day_gainers", count=min(limit * 3, 250))
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        for q in quotes:
            keep(q.get("symbol"),
                 q.get("fullExchangeName") or q.get("exchange"),
                 q.get("regularMarketPrice"),
                 q.get("regularMarketChangePercent"),
                 q.get("regularMarketVolume"))
        if rows:
            print(f"yfinance screen('day_gainers'): {len(quotes)} raw -> {len(rows)} kept")
    except Exception as e:
        print(f"yf.screen failed ({e}); trying EquityQuery fallback")

    # Fallback: EquityQuery (works on some yfinance versions where the string key doesn't)
    if not rows:
        try:
            from yfinance import EquityQuery
            q = EquityQuery("and", [
                EquityQuery("gt", ["percentchange", MIN_CHANGE_PCT]),
                EquityQuery("gt", ["dayvolume", 100000]),
                EquityQuery("eq", ["region", "us"]),
            ])
            res = yf.screen(q, sortField="percentchange", sortAsc=False, size=min(limit * 3, 250))
            for qd in (res.get("quotes", []) if isinstance(res, dict) else []):
                keep(qd.get("symbol"),
                     qd.get("fullExchangeName") or qd.get("exchange"),
                     qd.get("regularMarketPrice"),
                     qd.get("regularMarketChangePercent"),
                     qd.get("regularMarketVolume"))
            print(f"EquityQuery fallback -> {len(rows)} kept")
        except Exception as e:
            print(f"EquityQuery fallback failed: {e}")

    # de-dup, sort by % change desc, cap at limit
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: (x[3] or 0), reverse=True):
        if r[0] in seen:
            continue
        seen.add(r[0]); out.append(r)
        if len(out) >= limit:
            break
    return out

def get_ohlcv(symbols):
    """Bulk-download daily OHLCV. Returns {symbol: DataFrame(open,high,low,close,volume)}."""
    import yfinance as yf
    tickers = list(dict.fromkeys(symbols + [BENCHMARK]))
    data = yf.download(tickers, period=HISTORY_PERIOD, interval="1d",
                       group_by="ticker", auto_adjust=False, threads=True,
                       progress=False)
    out = {}
    multi = isinstance(data.columns, pd.MultiIndex)
    for sym in tickers:
        try:
            if multi:
                if sym not in data.columns.get_level_values(0):
                    continue
                sub = data[sym].copy()
            else:
                sub = data.copy()          # single-ticker download
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            # reset index FIRST, then lowercase every column (incl. the date index)
            sub = sub.reset_index()
            sub.columns = [str(c).lower() for c in sub.columns]
            sub = sub.rename(columns={"date": "datetime", "index": "datetime"})
            need = {"open", "high", "low", "close", "volume"}
            if not need.issubset(sub.columns) or "datetime" not in sub.columns:
                continue
            sub = sub[["datetime", "open", "high", "low", "close", "volume"]]
            out[sym] = sub.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"  {sym}: OHLCV parse failed ({e})")
    return out

# ------------------------------------------------------------------ indicator math
def wilder_rma(s, period):
    return s.ewm(alpha=1/period, adjust=False).mean()

def rsi(close, period=14):
    delta = close.diff()
    avg_gain = wilder_rma(delta.clip(lower=0), period)
    avg_loss = wilder_rma(-delta.clip(upper=0), period)
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return wilder_rma(tr, period)

def adx(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, down = h.diff(), -l.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    a = wilder_rma(tr, period)
    pdi = 100 * wilder_rma(plus_dm, period) / a.replace(0, 1e-12)
    mdi = 100 * wilder_rma(minus_dm, period) / a.replace(0, 1e-12)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-12)
    return wilder_rma(dx, period), pdi, mdi

def macd(close, fast=12, slow=26, sig=9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal

def obv(df):
    sign = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (sign * df["volume"]).cumsum()

def higher_highs_lows(df, window=40, order=3):
    seg = df.iloc[-window:].reset_index(drop=True)
    highs, lows = [], []
    for i in range(order, len(seg) - order):
        if seg["high"].iloc[i] == seg["high"].iloc[i-order:i+order+1].max():
            highs.append(seg["high"].iloc[i])
        if seg["low"].iloc[i] == seg["low"].iloc[i-order:i+order+1].min():
            lows.append(seg["low"].iloc[i])
    hh = len(highs) >= 2 and highs[-1] > highs[0]
    hl = len(lows) >= 2 and lows[-1] > lows[0]
    return hh, hl

# ---------------------------------------------------------------------- analysis
def analyze(symbol, df, bench_ret=None):
    if df is None or len(df) < 25:
        return {"symbol": symbol, "grade": "N/A", "note": f"insufficient bars ({0 if df is None else len(df)})"}

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    price = float(close.iloc[-1])

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    std20 = close.rolling(20).std()
    bb_upper, bb_lower = sma20 + 2*std20, sma20 - 2*std20
    bandwidth = (bb_upper - bb_lower) / sma20
    pct_b = (close - bb_lower) / (bb_upper - bb_lower).replace(0, 1e-12)

    rsi14 = rsi(close); atr14 = atr(df); adx14, pdi, mdi = adx(df)
    macd_line, macd_sig, macd_hist = macd(close); obv_ = obv(df)
    vol20 = vol.rolling(20).mean()

    v = lambda s: float(s.iloc[-1])
    sma20_l = v(sma20); sma50_l = v(sma50) if len(df) >= 50 else float("nan")
    sma200_l = v(sma200) if len(df) >= 200 else float("nan")
    bb_up_l = v(bb_upper); bw_l = v(bandwidth); pctb_l = v(pct_b)
    rsi_l = v(rsi14); atr_l = v(atr14); adx_l = v(adx14); pdi_l = v(pdi); mdi_l = v(mdi)
    vol_l = float(vol.iloc[-1]); vol20_l = v(vol20)
    vol_ratio = vol_l / vol20_l if vol20_l else float("nan")

    resistance = float(df["high"].iloc[-21:-1].max())   # prior 20-day high excl. today
    sw_low = float(df["low"].iloc[-10:].min())

    hh, hl = higher_highs_lows(df)
    stack_ok = (price > sma20_l) and (pd.isna(sma50_l) or (sma20_l > sma50_l and price > sma50_l))
    sma20_slope = sma20_l - float(sma20.iloc[-6])
    above200 = (not pd.isna(sma200_l)) and price > sma200_l
    trend_up = stack_ok and sma20_slope > 0 and hl

    closed_above_res = price > resistance
    closed_above_bb = price > bb_up_l
    vol_expansion = (not pd.isna(vol_ratio)) and vol_ratio >= 1.5
    vol_above_avg = (not pd.isna(vol_ratio)) and vol_ratio > 1.0
    obv_confirm = v(obv_) >= obv_.iloc[-20:].max() * 0.999
    bw_hist = bandwidth.iloc[-40:].dropna()
    squeeze = len(bw_hist) > 5 and bw_l <= bw_hist.quantile(0.25)
    ext_atr = (price - sma20_l) / atr_l if atr_l else 0
    overextended = ext_atr > 3.0
    px_hh = price >= close.iloc[-20:].max() * 0.999
    rsi_at_hh = rsi_l >= rsi14.iloc[-20:].max() * 0.999
    bearish_div = px_hh and not rsi_at_hh
    macd_bull = (v(macd_line) > v(macd_sig)) and (v(macd_hist) > float(macd_hist.iloc[-2]))
    adx_ok = adx_l >= 20 and pdi_l > mdi_l

    ret20 = price / float(close.iloc[-21]) - 1 if len(df) >= 21 else float("nan")
    rs_beat = (bench_ret is not None) and (not pd.isna(ret20)) and (ret20 > bench_ret)

    entry = price
    stop = min(resistance, sw_low) - 0.5 * atr_l
    risk = entry - stop
    mm = resistance - sw_low
    target = resistance + mm if mm > 0 else entry + 2*risk
    rr = (target - entry) / risk if risk > 0 else float("nan")
    rr_ok = (not pd.isna(rr)) and rr >= MIN_RR

    score = 0.0
    score += 20 * (1.0 if trend_up else (0.5 if stack_ok else 0.0))
    score += 20 * (1.0 if (closed_above_res or closed_above_bb) else 0.0) * (1.0 if closed_above_res else 0.7)
    score += 20 * (1.0 if vol_expansion else (0.6 if vol_above_avg else 0.0)) * (0.6 if not obv_confirm else 1.0)
    score += 15 * (0.0 if bearish_div else (1.0 if (rsi_l >= 50 and macd_bull) else 0.6))
    score += 10 * (1.0 if squeeze else 0.4) * (0.0 if overextended else 1.0)
    score += 10 * (1.0 if rr_ok else (0.4 if (not pd.isna(rr) and rr >= 1) else 0.0))
    score += 5 * (1.0 if rs_beat else 0.0)
    score *= (1.0 if adx_ok else 0.4)

    core_confirmed = ((closed_above_res or closed_above_bb) and vol_above_avg and trend_up
                      and not bearish_div and not overextended and rr_ok and obv_confirm)
    constructive = (closed_above_res or closed_above_bb) and stack_ok and not bearish_div
    grade = "A" if core_confirmed else ("B" if constructive else "C")

    reasons = []
    if not (closed_above_res or closed_above_bb): reasons.append("no close above resistance/upper BB")
    if not vol_above_avg: reasons.append(f"vol {vol_ratio:.2f}x (<avg)")
    elif not vol_expansion: reasons.append(f"vol only {vol_ratio:.2f}x")
    if not obv_confirm: reasons.append("OBV not confirming")
    if bearish_div: reasons.append("bearish RSI divergence")
    if overextended: reasons.append(f"overextended ({ext_atr:.1f} ATR)")
    if not adx_ok: reasons.append(f"weak trend (ADX {adx_l:.0f})")
    if not pd.isna(rr) and not rr_ok: reasons.append(f"R:R {rr:.2f}<{MIN_RR}")

    return {
        "symbol": symbol, "grade": grade, "score": round(score, 1), "price": round(price, 2),
        "trend": "Up" if trend_up else ("Constructive" if stack_ok else "Weak/Sideways"),
        "adx": round(adx_l, 1), "above_200d": "Yes" if above200 else ("n/a" if pd.isna(sma200_l) else "No"),
        "resistance": round(resistance, 2),
        "close>res": "Y" if closed_above_res else "N", "close>bb": "Y" if closed_above_bb else "N",
        "vol_x_avg": round(vol_ratio, 2) if not pd.isna(vol_ratio) else None,
        "obv_confirm": "Y" if obv_confirm else "N", "squeeze": "Y" if squeeze else "N",
        "pct_b": round(pctb_l, 2), "rsi14": round(rsi_l, 1),
        "rsi_div": "Bearish" if bearish_div else "None", "macd": "Bull" if macd_bull else "Soft",
        "sma20": round(sma20_l, 2), "sma50": round(sma50_l, 2) if not pd.isna(sma50_l) else None,
        "atr14": round(atr_l, 2), "ext_atr": round(ext_atr, 2),
        "rs_vs_bench": ("Beat" if rs_beat else "Lag") if bench_ret is not None else "n/a",
        "entry": round(entry, 2) if grade in ("A", "B") else None,
        "stop": round(stop, 2) if grade in ("A", "B") else None,
        "target": round(target, 2) if grade in ("A", "B") else None,
        "reward_risk": round(rr, 2) if (grade in ("A", "B") and not pd.isna(rr)) else None,
        "note": "; ".join(reasons) if reasons else "clean setup",
    }

# ------------------------------------------------------------------------- telegram
def telegram_message(text):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        print("Telegram not configured; skipping push.")
        return
    import requests
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          data={"chat_id": TELEGRAM_CHAT, "text": text,
                                "parse_mode": "Markdown", "disable_web_page_preview": True},
                          timeout=30)
        print("Telegram message:", r.status_code)
    except Exception as e:
        print("Telegram message failed:", e)

def telegram_document(path, caption=""):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        return
    import requests
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                              data={"chat_id": TELEGRAM_CHAT, "caption": caption[:1000]},
                              files={"document": f}, timeout=60)
        print("Telegram document:", r.status_code)
    except Exception as e:
        print("Telegram document failed:", e)

def build_alert(df, day, n_scanned):
    a = df[df["grade"] == "A"]
    b = df[df["grade"] == "B"]
    lines = [f"*CMT Breakout Screen — {day}*",
             f"_Scanned {n_scanned} gainers · {len(a)} A / {len(b)} B setups_", ""]
    if a.empty and not (INCLUDE_GRADE_B and not b.empty):
        lines.append("No confirmed (Grade A) breakouts today.")
    for _, r in a.iterrows():
        lines.append(f"🟢 *{r['symbol']}*  score {r['score']}  (RSI {r['rsi14']}, ADX {r['adx']})\n"
                     f"   entry `{r['entry']}`  stop `{r['stop']}`  target `{r['target']}`  "
                     f"R:R `{r['reward_risk']}`")
    if INCLUDE_GRADE_B and not b.empty:
        lines.append("\n*Grade B (constructive, watch for retest):*")
        for _, r in b.head(8).iterrows():
            lines.append(f"🟡 {r['symbol']}  entry `{r['entry']}` stop `{r['stop']}` "
                         f"target `{r['target']}` R:R `{r['reward_risk']}`")
    lines.append("\n_Technical analysis only — not investment advice._")
    return "\n".join(lines)[:4000]

# ----------------------------------------------------------------------------- main
def main():
    today = datetime.now(NY).date()
    print(f"=== CMT auto-screener run for {today} (America/New_York) ===")

    if not FORCE_RUN and not is_trading_day(today):
        print(f"{today} is not a US market trading day (weekend/holiday). Exiting cleanly.")
        return

    candidates = get_candidates(SCREENER_LIMIT)
    print(f"Candidates: {len(candidates)}")
    if not candidates:
        telegram_message(f"*CMT Breakout Screen — {today}*\nNo gainer candidates returned "
                         f"(data source issue). No analysis run.")
        print("No candidates; exiting.")
        return
    syms = [c[0] for c in candidates]

    ohlcv = get_ohlcv(syms)
    print(f"OHLCV downloaded for {len(ohlcv)} / {len(syms)} symbols")

    bench_ret = None
    if BENCHMARK in ohlcv and len(ohlcv[BENCHMARK]) >= 21:
        bc = ohlcv[BENCHMARK]["close"]
        bench_ret = float(bc.iloc[-1]) / float(bc.iloc[-21]) - 1

    rows, failed = [], []
    for s in syms:
        if s in ohlcv:
            rows.append(analyze(s, ohlcv[s], bench_ret))
        else:
            failed.append(s)

    df = pd.DataFrame(rows)
    if not df.empty:
        rank = {"A": 0, "B": 1, "C": 2, "N/A": 3}
        df["_g"] = df["grade"].map(rank).fillna(3)
        df = df.sort_values(["_g", "score"], ascending=[True, False]).drop(columns="_g")
        df.to_csv("cmt_breakout_setups.csv", index=False)
        print("\n" + df[["symbol","grade","score","price","trend","adx","vol_x_avg",
                          "rsi14","entry","stop","target","reward_risk","note"]].to_string(index=False))

    n_a = int((df["grade"] == "A").sum()) if not df.empty else 0
    n_b = int((df["grade"] == "B").sum()) if not df.empty else 0
    print(f"\nGrade A: {n_a} | Grade B: {n_b} | skipped(no data): {len(failed)}")

    if not df.empty:
        telegram_message(build_alert(df, today, len(candidates)))
        if n_a or (INCLUDE_GRADE_B and n_b):
            telegram_document("cmt_breakout_setups.csv", f"CMT breakout table {today}")

if __name__ == "__main__":
    main()
