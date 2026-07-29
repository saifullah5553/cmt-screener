"""Dynamic market regime engine.

Breakout expectancy is regime-dependent: the *same* setup that works in a
confirmed uptrend fails repeatedly in a corrective tape. Rather than screening
with fixed thresholds all year, we measure the environment daily and tighten or
loosen the gates automatically.

Inputs (all free via Yahoo):
    ^GSPC  S&P 500     - primary US trend
    ^IXIC  Nasdaq      - risk appetite / leadership
    ^AXJO  ASX 200     - Australian trend
    ^VIX   volatility  - stress gauge
    RSP/SPY            - equal-weight vs cap-weight = breadth proxy
    ^NYA breadth proxy via % of indices above their 50/200 DMA

Output: a regime label, a 0-1 health score, and a `strictness` multiplier the
scoring layer applies to its qualification thresholds.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .indicators import ma_rising, sma

log = logging.getLogger(__name__)

INDEX_SYMBOLS = ["^GSPC", "^IXIC", "^AXJO", "^VIX", "RSP", "SPY"]

BULL, NEUTRAL, BEAR = "Confirmed Uptrend", "Neutral / Mixed", "Defensive / Downtrend"


def _trend_health(df: pd.DataFrame) -> float:
    """0-1 health of one index, judged the way a technician reads an index
    chart: is it above its 50- and 200-day averages, are those averages in
    bullish order, and is the 50-day rising?"""
    if df is None or len(df) < 210:
        return float("nan")
    c = df["close"]
    px = float(c.iloc[-1])
    ma50, ma200 = sma(c, 50), sma(c, 200)
    m50, m200 = float(ma50.iloc[-1]), float(ma200.iloc[-1])
    if not (np.isfinite(m50) and np.isfinite(m200)):
        return float("nan")
    return float(0.30 * (px > m50) + 0.25 * (px > m200)
                 + 0.20 * (m50 > m200)                  # bullish MA order
                 + 0.15 * ma_rising(ma50, 10)
                 + 0.10 * ma_rising(ma200, 20))


def assess(frames: dict[str, pd.DataFrame]) -> dict:
    """Compute the regime from pre-downloaded index frames."""
    sp = _trend_health(frames.get("^GSPC"))
    nq = _trend_health(frames.get("^IXIC"))
    ax = _trend_health(frames.get("^AXJO"))

    parts = [v for v in (sp, nq) if np.isfinite(v)]
    us_health = float(np.mean(parts)) if parts else 0.5

    # VIX: <16 calm, >28 stressed.
    vix = frames.get("^VIX")
    vix_last = float(vix["close"].iloc[-1]) if vix is not None and len(vix) else float("nan")
    if np.isfinite(vix_last):
        vix_score = float(np.clip((28.0 - vix_last) / 12.0, 0.0, 1.0))
    else:
        vix_score = 0.5

    # Breadth proxy: equal-weight vs cap-weight relative trend. When RSP keeps
    # pace with SPY, the advance is broad; when it lags badly, leadership is
    # narrow and breakouts outside the megacaps tend to fail.
    breadth = _breadth_proxy(frames.get("RSP"), frames.get("SPY"))

    health = float(np.nansum([
        0.45 * us_health,
        0.20 * vix_score,
        0.20 * (breadth if np.isfinite(breadth) else 0.5),
        0.15 * (ax if np.isfinite(ax) else us_health),
    ]))

    if health >= 0.70:
        label, strictness = BULL, 0.92        # allow slightly earlier entries
    elif health >= 0.45:
        label, strictness = NEUTRAL, 1.00
    else:
        label, strictness = BEAR, 1.15        # only the very best qualify

    return {
        "label": label,
        "health": round(health, 3),
        "strictness": strictness,
        "sp500": _r(sp), "nasdaq": _r(nq), "asx200": _r(ax),
        "vix": _r(vix_last), "vix_score": _r(vix_score),
        "breadth": _r(breadth),
        "summary": _summary(label, health, vix_last, breadth),
    }


def _breadth_proxy(rsp: pd.DataFrame, spy: pd.DataFrame) -> float:
    if rsp is None or spy is None or len(rsp) < 70 or len(spy) < 70:
        return float("nan")
    ratio = rsp["close"].reset_index(drop=True) / spy["close"].reset_index(drop=True).iloc[-len(rsp):].reset_index(drop=True)
    ratio = ratio.dropna()
    if len(ratio) < 65:
        return float("nan")
    now, then = float(ratio.iloc[-1]), float(ratio.iloc[-63])
    if not then:
        return float("nan")
    chg = now / then - 1
    return float(np.clip(0.5 + chg * 8, 0.0, 1.0))


def _r(x, n=2):
    return round(float(x), n) if x is not None and np.isfinite(x) else float("nan")


def _summary(label, health, vix, breadth):
    bits = [f"{label} (health {health:.0%})"]
    if np.isfinite(vix):
        bits.append(f"VIX {vix:.1f}")
    if np.isfinite(breadth):
        bits.append("broad participation" if breadth >= 0.55 else "narrow leadership")
    return ", ".join(bits)


def default() -> dict:
    """Neutral fallback when index data is unavailable — never blocks a run."""
    return {"label": NEUTRAL, "health": 0.5, "strictness": 1.0,
            "sp500": float("nan"), "nasdaq": float("nan"), "asx200": float("nan"),
            "vix": float("nan"), "vix_score": 0.5, "breadth": float("nan"),
            "summary": "Regime unavailable — using neutral defaults"}
