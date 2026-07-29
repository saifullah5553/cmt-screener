"""Indicator library — deliberately minimal.

Scope is restricted to what a professional technician actually uses to judge a
breakout: price structure, moving averages, support/resistance, volume and
volatility (for objective stop placement). Oscillators, bands and squeeze
studies were removed: they add parameters and false precision without improving
breakout selection, and every question they answer is answered better by price
and volume directly.

All functions are pure and NaN-safe over a tidy OHLCV frame
(columns: datetime, open, high, low, close, volume).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ------------------------------------------------------------------ averages
def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window).mean()


def wilder_rma(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(alpha=1 / period, adjust=False).mean()


def ma_rising(ma: pd.Series, lookback: int = 10) -> bool:
    """Is the moving average sloping up? Direction of the MA matters more than
    a simple price-above-MA test — a rising MA is the definition of an uptrend."""
    if ma is None or len(ma.dropna()) < lookback + 1:
        return False
    now, then = float(ma.iloc[-1]), float(ma.iloc[-lookback - 1])
    return bool(np.isfinite(now) and np.isfinite(then) and now > then)


# ---------------------------------------------------------------- volatility
def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — used ONLY for objective stop distance and to
    normalise the size of the breakout move. Not used as a signal."""
    return wilder_rma(true_range(df), period)


# --------------------------------------------------------------------- price
def roc(close: pd.Series, period: int) -> float:
    """Rate of change over `period` bars, as a fraction."""
    if close is None or len(close) <= period:
        return float("nan")
    prev = float(close.iloc[-period - 1])
    if not prev:
        return float("nan")
    return float(close.iloc[-1]) / prev - 1.0


def swing_points(df: pd.DataFrame, window: int = 60, order: int = 3):
    """Fractal swing highs/lows — the raw material for structure and S/R."""
    seg = df.iloc[-window:].reset_index(drop=True)
    highs, lows = [], []
    for i in range(order, len(seg) - order):
        if seg["high"].iloc[i] >= seg["high"].iloc[i - order:i + order + 1].max():
            highs.append((i, float(seg["high"].iloc[i])))
        if seg["low"].iloc[i] <= seg["low"].iloc[i - order:i + order + 1].min():
            lows.append((i, float(seg["low"].iloc[i])))
    return highs, lows


def higher_highs_lows(df: pd.DataFrame, window: int = 120, order: int = 4):
    """Classic Dow structure test: are swing highs AND swing lows rising?

    This is the most fundamental definition of an uptrend and deserves more
    weight than any indicator reading.
    """
    if df is None or len(df) < 30:
        return False, False
    highs, lows = swing_points(df, window=min(window, len(df)), order=order)
    hh = len(highs) >= 2 and highs[-1][1] > highs[0][1]
    hl = len(lows) >= 2 and lows[-1][1] > lows[0][1]
    return bool(hh), bool(hl)


def resistance_level(df: pd.DataFrame, lookback: int = 50) -> float:
    """Objective overhead resistance: highest high of the prior `lookback`
    sessions, excluding today's bar."""
    if df is None or len(df) < 5:
        return float("nan")
    seg = df["high"].iloc[-lookback - 1:-1]
    return float(seg.max()) if len(seg) else float("nan")


def overhead_supply(df: pd.DataFrame, price: float, lookback: int = 250) -> float:
    """Fraction of the past year's volume transacted ABOVE the current price.

    Trapped buyers overhead are the single most common cause of a failed
    breakout — they sell into strength to get out at breakeven. Low overhead
    supply (price near 52-week highs) is what makes a breakout 'clean'.
    """
    if df is None or len(df) < 30 or not np.isfinite(price):
        return float("nan")
    seg = df.iloc[-lookback:]
    tot = float(seg["volume"].sum())
    if tot <= 0:
        return float("nan")
    above = float(seg.loc[seg["close"] > price, "volume"].sum())
    return above / tot


# -------------------------------------------------------------------- volume
def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — the standard accumulation/distribution proxy."""
    sign = np.sign(df["close"].diff().fillna(0))
    return (sign * df["volume"]).cumsum()


def accumulation_days(df: pd.DataFrame, window: int = 25):
    """Count up-days and down-days that occurred on ABOVE-average volume.

    This is the professional read on institutional footprints: large buyers
    cannot hide their volume. A surplus of accumulation days over distribution
    days during a base is direct evidence of sponsorship.
    """
    if df is None or len(df) < window + 20:
        return 0, 0
    seg = df.iloc[-window:]
    avg = df["volume"].rolling(20).mean().iloc[-window:]
    heavy = seg["volume"] > avg
    chg = seg["close"].diff()
    return int(((chg > 0) & heavy).sum()), int(((chg < 0) & heavy).sum())


def volume_dryup(df: pd.DataFrame, base_bars: int) -> bool:
    """Did volume contract through the consolidation? Quiet bases mean sellers
    are exhausted; the subsequent expansion is then meaningful."""
    if df is None or len(df) < base_bars + 10 or base_bars < 10:
        return False
    seg = df.iloc[-base_bars - 1:-1]
    half = len(seg) // 2
    early = float(seg["volume"].iloc[:half].mean() or 0)
    late = float(seg["volume"].iloc[half:].mean() or 0)
    return bool(early > 0 and late < early * 0.85)
