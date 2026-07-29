"""Stan Weinstein Stage Analysis — trend maturity.

This is the highest-value filter in the whole system and it uses nothing but
price and a single moving average. The same breakout bar is a high-expectancy
entry in Stage 2 and a trap in Stage 3 distribution or a Stage 4 downtrend.
Trading only Stage 2 removes more bad breakouts than any indicator can.

    Stage 1  Basing      flat 30-week MA after a decline; price oscillating
    Stage 2  Advancing   rising 30-week MA; price above it  <-- the only buy zone
    Stage 3  Topping     MA flattening after an advance; price stalling
    Stage 4  Declining   falling 30-week MA; price below it

Computed on the WEEKLY series with the 30-week MA, exactly as Weinstein defines.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import sma

STAGE_NAMES = {
    1: "Stage 1 - Basing",
    2: "Stage 2 - Advancing",
    3: "Stage 3 - Topping",
    4: "Stage 4 - Declining",
    0: "Undetermined",
}


def analyse(weekly: pd.DataFrame, ma_len: int = 30) -> dict:
    out = {"stage": 0, "name": STAGE_NAMES[0], "confidence": 0.0,
           "ma_slope": float("nan"), "above_ma": False,
           "pct_from_52w_high": float("nan"), "range_position": float("nan")}
    if weekly is None or len(weekly) < ma_len + 8:
        return out

    close = weekly["close"]
    ma = sma(close, ma_len)
    price = float(close.iloc[-1])
    ma_now = float(ma.iloc[-1])
    if not np.isfinite(ma_now) or ma_now <= 0:
        return out

    # Slope of the 30-week MA over the last 10 weeks, in % per week.
    prev = float(ma.iloc[-11]) if len(ma.dropna()) > 11 else float("nan")
    slope = ((ma_now / prev) ** (1 / 10) - 1) * 100 if np.isfinite(prev) and prev > 0 else 0.0

    above = price > ma_now
    look = close.iloc[-52:] if len(close) >= 52 else close
    hi52, lo52 = float(look.max()), float(look.min())
    rng_pos = (price - lo52) / (hi52 - lo52) if hi52 > lo52 else 0.5
    pct_from_high = (price / hi52 - 1) * 100 if hi52 else float("nan")

    flat = abs(slope) < 0.15

    if above and slope > 0.15:
        stage = 2
        conf = _clamp(0.45 + min(slope, 1.5) * 0.20 + rng_pos * 0.35)
    elif not above and slope < -0.15:
        stage = 4
        conf = _clamp(0.50 + min(abs(slope), 2.0) * 0.25)
    elif flat:
        # A flat MA reached from ABOVE after a run and near highs = topping;
        # otherwise the stock is building a base.
        stage = 3 if (above and rng_pos > 0.65) else 1
        conf = 0.55
    elif above:
        stage = 2 if rng_pos > 0.5 else 1
        conf = 0.40
    else:
        stage = 4 if rng_pos < 0.4 else 3
        conf = 0.40

    # Far-extended above the 30-week MA is late Stage 2 / early Stage 3 —
    # the reward is behind us and risk is elevated.
    if stage == 2 and price / ma_now - 1 > 0.60:
        stage, conf = 3, 0.50

    out.update(stage=stage, name=STAGE_NAMES[stage], confidence=round(_clamp(conf), 2),
               ma_slope=round(slope, 3), above_ma=bool(above),
               pct_from_52w_high=round(pct_from_high, 2) if np.isfinite(pct_from_high) else float("nan"),
               range_position=round(rng_pos, 3))
    return out


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))
