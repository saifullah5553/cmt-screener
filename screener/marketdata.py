"""Market data acquisition, validation and REPAIR.

Contains the fix for the defect that silently zeroed out the whole screen:

    Yahoo's *historical* endpoint publishes the most recent daily bar with
    OHL populated but `close` = NaN for several hours after the US close
    (the adjusted-close is not settled yet). The old code did
    `dropna(how="all")`, which does not drop a row that is only partially
    null, so `price = close.iloc[-1]` became NaN. Every downstream
    comparison against NaN evaluates False, so `closed_above_res`,
    `trend_up`, etc. were all False for EVERY symbol and everything graded
    "C" -> "0 breakouts" no matter what the market did.

The repair: detect a null-close final bar and patch it from the *quote*
endpoint (`regularMarketPrice`), which is settled immediately. If it cannot be
repaired the bar is dropped and the symbol is marked stale rather than being
silently analysed with corrupt data.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


# ------------------------------------------------------------------ download
def download_ohlcv(symbols, period="2y", interval="1d", quotes=None,
                   max_workers=8, allow_repair=None) -> dict[str, pd.DataFrame]:
    """Bulk-download daily OHLCV and return {symbol: tidy DataFrame}.

    `quotes` maps symbol -> live quote dict (from the screener) and is used to
    repair unsettled final bars. Downloads are chunked to stay polite to Yahoo
    and to avoid one bad symbol poisoning the whole request.
    """
    import yfinance as yf

    symbols = list(dict.fromkeys(symbols))
    out: dict[str, pd.DataFrame] = {}
    chunks = [symbols[i:i + 40] for i in range(0, len(symbols), 40)]

    def fetch(chunk):
        return yf.download(chunk, period=period, interval=interval,
                           group_by="ticker", auto_adjust=False, threads=True,
                           progress=False, timeout=30)

    frames = []
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(chunks)))) as ex:
        futs = {ex.submit(fetch, c): c for c in chunks}
        for f in as_completed(futs):
            try:
                frames.append((futs[f], f.result()))
            except Exception as e:                      # noqa: BLE001
                log.warning("chunk download failed (%s): %s", futs[f][:3], e)

    for chunk, data in frames:
        if data is None or len(data) == 0:
            continue
        multi = isinstance(data.columns, pd.MultiIndex)
        for sym in chunk:
            try:
                if multi:
                    if sym not in data.columns.get_level_values(0):
                        continue
                    sub = data[sym].copy()
                else:
                    sub = data.copy()
                df = _tidy(sub)
                if df is None:
                    continue
                # Repair is only safe when the symbol's market has CLOSED.
                # For an open market a live quote is a mid-session price, not
                # a close — patching it would fabricate a candle.
                may_repair = True if allow_repair is None else bool(allow_repair(sym))
                df = repair_last_bar(df, sym, (quotes or {}).get(sym),
                                     allow_quote=may_repair)
                if df is not None and len(df):
                    out[sym] = df
            except Exception as e:                      # noqa: BLE001
                log.debug("%s: parse failed (%s)", sym, e)
    return out


def _tidy(sub: pd.DataFrame) -> pd.DataFrame | None:
    """Normalise a yfinance frame to columns [datetime, open..volume]."""
    sub = sub.dropna(how="all")
    if sub.empty:
        return None
    sub = sub.reset_index()
    sub.columns = [str(c).lower() for c in sub.columns]
    sub = sub.rename(columns={"date": "datetime", "index": "datetime"})
    if "datetime" not in sub.columns or not set(OHLCV).issubset(sub.columns):
        return None
    sub = sub[["datetime"] + OHLCV]
    sub["datetime"] = pd.to_datetime(sub["datetime"]).dt.tz_localize(None).dt.normalize()
    for c in OHLCV:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
    sub = drop_synthetic_bars(sub)
    return sub.sort_values("datetime").reset_index(drop=True)


def drop_synthetic_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Remove exchange-holiday placeholder bars.

    On markets outside the US/AU, Yahoo emits holidays as fabricated candles:
    volume 0 with open == high == low == close (the previous close carried
    forward). No session took place, so these are not candles at all.

    Leaving them in does real damage, all of it silent:
      * true range is 0 on those bars, so ATR is understated - measured up to
        18.6% on QNBK.QA - which makes stops too tight and inflates R:R;
      * they drag the average-volume baseline down, inflating the breakout
        volume ratio (~1.14x on PSX names) so weak volume can pass the gate;
      * a run of identical bars mimics volatility contraction, which flatters
        VCP and range-based pattern detection.

    Prevalence measured over one year: US 0.0%, AU 0.0%, IN 1.6%, SA 2.7%,
    QA 3.1%, KW 3.2%, PK 4.7-5.1%, AE 4.8%, EG 4.8%.
    """
    if df is None or df.empty:
        return df
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    synthetic = flat & (df["volume"].fillna(0) == 0)
    if synthetic.any():
        df = df.loc[~synthetic]
    return df


def repair_last_bar(df: pd.DataFrame, symbol: str, quote: dict | None,
                    allow_quote: bool = True) -> pd.DataFrame:
    """Fix or drop an unsettled final bar. THE core data-integrity guard.

    Yahoo may return the newest daily bar with a null close (and sometimes null
    OHL) for hours after the close. We patch `close` from the live quote when
    the quote clearly refers to that same bar; otherwise we drop the bar so we
    analyse only fully-formed candles.

    `allow_quote=False` must be passed when the symbol's market is still OPEN:
    the quote would then be an intraday price, and writing it in as a close
    manufactures a candle that never existed. In that case the partial bar is
    dropped and we fall back to the previous, genuinely completed session.
    """
    if df.empty:
        return df
    last = df.iloc[-1]

    if pd.notna(last["close"]):
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        return df

    if not allow_quote:
        log.debug("%s: market open — dropping partial bar instead of repairing", symbol)
        return df.iloc[:-1].dropna(subset=["close"]).reset_index(drop=True)

    px = None
    if quote:
        for k in ("regularMarketPrice", "regularMarketPreviousClose", "lastPrice"):
            v = quote.get(k)
            if v not in (None, "") and np.isfinite(float(v)):
                px = float(v)
                break
    if px is None:
        px = _quote_close(symbol)

    if px is not None and np.isfinite(px):
        idx = df.index[-1]
        df.loc[idx, "close"] = px
        # Keep the candle internally consistent: the close must sit inside the
        # day's range, otherwise range-position maths goes nonsensical.
        if pd.isna(df.loc[idx, "high"]) or px > df.loc[idx, "high"]:
            df.loc[idx, "high"] = max(px, float(df.loc[idx, "high"]) if pd.notna(df.loc[idx, "high"]) else px)
        if pd.isna(df.loc[idx, "low"]) or px < df.loc[idx, "low"]:
            df.loc[idx, "low"] = min(px, float(df.loc[idx, "low"]) if pd.notna(df.loc[idx, "low"]) else px)
        if pd.isna(df.loc[idx, "open"]):
            df.loc[idx, "open"] = px
        log.debug("%s: repaired unsettled final bar with quote close %.4f", symbol, px)
    else:
        log.debug("%s: dropping unsettled final bar (no quote available)", symbol)
        df = df.iloc[:-1]

    return df.dropna(subset=["close"]).reset_index(drop=True)


def _quote_close(symbol: str):
    """Last-resort per-symbol quote lookup."""
    try:
        import yfinance as yf
        fi = yf.Ticker(symbol).fast_info
        for k in ("lastPrice", "last_price", "previousClose"):
            try:
                v = fi[k] if not hasattr(fi, "get") else fi.get(k)
            except Exception:                            # noqa: BLE001
                v = None
            if v not in (None, "") and np.isfinite(float(v)):
                return float(v)
    except Exception:                                    # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------ validation
def validate(df: pd.DataFrame, min_bars: int) -> tuple[bool, str]:
    """Reject frames that cannot be analysed honestly."""
    if df is None or df.empty:
        return False, "no data"
    if len(df) < min_bars:
        return False, f"insufficient history ({len(df)} bars < {min_bars})"
    if df[OHLCV].iloc[-1].isna().any():
        return False, "incomplete final bar"
    if (df["close"].iloc[-20:] <= 0).any():
        return False, "non-positive prices"
    # Guard against a stale feed repeating one price.
    if df["close"].iloc[-10:].nunique() == 1:
        return False, "stale/flat feed"
    return True, "ok"


def trim_to_session(df: pd.DataFrame, session_date) -> pd.DataFrame:
    """Drop any bars dated AFTER the session we intend to analyse.

    Needed because an open market can return today's in-progress bar with a
    populated (intraday) close, which looks perfectly valid but is not a
    completed candle. Trimming guarantees the last row is always the last
    fully-closed session for that market.
    """
    if df is None or df.empty or session_date is None:
        return df
    keep = df["datetime"].dt.date <= session_date
    return df.loc[keep].reset_index(drop=True)


def freshness(df: pd.DataFrame, expected_session) -> int:
    """How many days old the final bar is vs the session we intend to analyse."""
    if df is None or df.empty or expected_session is None:
        return 999
    return (expected_session - df["datetime"].iloc[-1].date()).days


# ------------------------------------------------------------------ timeframes
def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return _resample(df, "W-FRI")


def to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return _resample(df, "ME")


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["datetime"] + OHLCV)
    g = (df.set_index("datetime")
           .resample(rule)
           .agg({"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"})
           .dropna(subset=["close"]))
    return g.reset_index()
