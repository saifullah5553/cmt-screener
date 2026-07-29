"""Institutional base detection — five structures only.

Deliberately limited to the consolidation types a professional technician
actually trades: Flat Base, Cup & Handle, Ascending Triangle, Rectangle and the
Volatility Contraction Pattern. Everything here answers one question: did this
stock build a healthy, well-defined consolidation that a large buyer could
accumulate into, and is price now clearing its ceiling?

A base is described by two things a CMT cares about:
    pivot  - the objective resistance level that must be cleared
    depth  - how much correction the base contains (shallow = constructive)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, swing_points

MIN_BASE_BARS = 25          # ~5 weeks: shorter is not a base, it is a pause


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _base_window(df: pd.DataFrame, max_bars: int = 325):
    """The consolidation under examination: everything except today's bar."""
    win = min(len(df) - 1, max_bars)
    return df.iloc[-win - 1:-1].reset_index(drop=True)


# ----------------------------------------------------------------------- VCP
def detect_vcp(df: pd.DataFrame, min_weeks: int = 5, max_weeks: int = 65) -> dict:
    """Volatility Contraction Pattern.

    The professional read: successively SHALLOWER pullbacks on successively
    LIGHTER volume show supply being absorbed. Price then expands out of the
    pivot. We measure the contraction sequence rather than merely drawing a
    line, because the sequence is the evidence of accumulation.
    """
    res = {"name": "VCP", "detected": False, "score": 0.0, "contractions": 0,
           "base_weeks": 0, "pivot": float("nan"), "depths": [],
           "volume_dryup": False, "max_depth": float("nan")}
    if df is None or len(df) < min_weeks * 5 + 10:
        return res

    base = _base_window(df, max_weeks * 5)
    # The base begins at the first significant supply point — the highest high
    # that still leaves room for a real consolidation behind it. Searching only
    # the leading part of the window prevents a recent high from being mistaken
    # for the base start (which would report the entire window as the "base").
    head = base.iloc[: max(MIN_BASE_BARS, int(len(base) * 0.6))]
    if len(head) < 5:
        return res
    start = int(head["high"].idxmax())
    seg = base.iloc[start:].reset_index(drop=True)
    if len(seg) < MIN_BASE_BARS:
        return res

    highs, lows = swing_points(seg, window=len(seg), order=3)
    if len(highs) < 2 or len(lows) < 1:
        return res

    depths = []
    for hi_idx, hi_px in highs:
        after = [(li, lp) for li, lp in lows if li > hi_idx]
        if not after or hi_px <= 0:
            continue
        _, lo_px = min(after, key=lambda t: t[0])
        d = (hi_px - lo_px) / hi_px * 100
        if d > 0.5:
            depths.append(d)
    if len(depths) < 2:
        return res

    # Trailing sequence of progressively tighter pullbacks.
    kept = [depths[0]]
    for d in depths[1:]:
        if d < kept[-1] * 0.92:
            kept.append(d)
        elif d > kept[-1] * 1.35:
            kept = [d]

    contractions = len(kept)
    base_weeks = round(len(seg) / 5)
    half = len(seg) // 2
    v_early = float(seg["volume"].iloc[:half].mean() or 0)
    v_late = float(seg["volume"].iloc[half:].mean() or 0)
    dryup = bool(v_early > 0 and v_late < v_early * 0.85)

    depth_ratio = kept[-1] / kept[0] if kept[0] else 1.0
    max_depth = max(kept)

    detected = (contractions >= 2 and depth_ratio < 0.85
                and min_weeks <= base_weeks <= max_weeks and max_depth < 40)

    score = 0.0
    if detected:
        score = (0.35 * _clamp((contractions - 1) / 3)
                 + 0.25 * _clamp((0.85 - depth_ratio) / 0.55)
                 + 0.20 * (1.0 if dryup else 0.3)
                 + 0.20 * _clamp((min(base_weeks, 30) - min_weeks) / 20 + 0.35))
        if max_depth > 30:
            score *= 0.85                       # a loose base is a weaker base

    res.update(detected=bool(detected), score=round(_clamp(score), 3),
               contractions=contractions, base_weeks=base_weeks,
               pivot=float(seg["high"].max()), depths=[round(d, 1) for d in kept],
               volume_dryup=dryup, max_depth=round(max_depth, 1))
    return res


# ---------------------------------------------------------------- Flat Base
def detect_flat_base(df: pd.DataFrame, min_weeks: int = 5,
                     max_depth: float = 15.0) -> dict:
    """A tight sideways shelf — the most reliable O'Neil structure.

    Shallow depth is the whole point: a base that corrects less than ~15%
    signals holders are not being shaken out, i.e. supply is scarce.
    """
    res = {"name": "Flat Base", "detected": False, "score": 0.0,
           "pivot": float("nan"), "depth_pct": float("nan"), "base_weeks": 0}
    n = min_weeks * 5
    if df is None or len(df) < n + 5:
        return res
    seg = _base_window(df, max(n, 60))
    if len(seg) < n:
        return res
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    if hi <= 0:
        return res
    depth = (hi - lo) / hi * 100
    detected = depth <= max_depth
    res.update(detected=bool(detected), depth_pct=round(depth, 2), pivot=hi,
               base_weeks=round(len(seg) / 5),
               score=round(_clamp((max_depth - depth) / max_depth * 0.85 + 0.15), 3)
               if detected else 0.0)
    return res


# ----------------------------------------------------------------- Rectangle
def detect_rectangle(df: pd.DataFrame, lookback: int = 70) -> dict:
    """Horizontal range: repeated respect of a flat ceiling AND a flat floor.

    Multiple touches at both boundaries is what distinguishes a genuine
    accumulation range from random drift.
    """
    res = {"name": "Rectangle", "detected": False, "score": 0.0,
           "pivot": float("nan"), "touches": 0}
    if df is None or len(df) < lookback + 5:
        return res
    seg = _base_window(df, lookback)
    if len(seg) < MIN_BASE_BARS:
        return res
    top, bottom = float(seg["high"].max()), float(seg["low"].min())
    if top <= 0 or top <= bottom:
        return res
    height = (top - bottom) / top * 100
    top_touch = int((seg["high"] >= top * 0.98).sum())
    bot_touch = int((seg["low"] <= bottom * 1.02).sum())
    detected = bool(height <= 25 and top_touch >= 2 and bot_touch >= 2)
    res.update(detected=detected, pivot=top, height_pct=round(height, 2),
               touches=top_touch + bot_touch, base_weeks=round(len(seg) / 5),
               score=round(_clamp(0.35 + (top_touch + bot_touch) * 0.08
                                  + (25 - height) / 50), 3) if detected else 0.0)
    return res


# -------------------------------------------------------- Ascending Triangle
def detect_ascending_triangle(df: pd.DataFrame, lookback: int = 70) -> dict:
    """Flat resistance with rising lows — demand steadily absorbing fixed supply."""
    res = {"name": "Ascending Triangle", "detected": False, "score": 0.0,
           "pivot": float("nan")}
    if df is None or len(df) < lookback + 5:
        return res
    seg = _base_window(df, lookback)
    if len(seg) < MIN_BASE_BARS:
        return res
    highs, lows = swing_points(seg, window=len(seg), order=3)
    if len(highs) < 2 or len(lows) < 2:
        return res
    h_vals = [p for _, p in highs][-3:]
    l_vals = [p for _, p in lows][-3:]
    flat_top = (max(h_vals) - min(h_vals)) / max(h_vals) < 0.04
    rising_lows = l_vals[-1] > l_vals[0] * 1.01
    detected = bool(flat_top and rising_lows)
    res.update(detected=detected, pivot=float(max(h_vals)),
               base_weeks=round(len(seg) / 5),
               score=0.70 if detected else 0.0)
    return res


# ------------------------------------------------------------- Cup & Handle
def detect_cup_handle(df: pd.DataFrame, min_bars: int = 45, max_bars: int = 160) -> dict:
    """Rounded correction, recovery to the rim, then a shallow handle.

    The handle matters: it is the final shakeout of weak holders immediately
    before the advance, and it must be shallow relative to the cup.
    """
    res = {"name": "Cup & Handle", "detected": False, "score": 0.0,
           "pivot": float("nan"), "depth_pct": float("nan")}
    if df is None or len(df) < min_bars + 10:
        return res
    seg = _base_window(df, max_bars)
    if len(seg) < min_bars:
        return res
    third = len(seg) // 3
    left_rim = float(seg["high"].iloc[:third].max())
    trough = float(seg["low"].iloc[third // 2: len(seg) - third // 2].min())
    right_rim = float(seg["high"].iloc[2 * third:].max())
    if left_rim <= 0 or trough <= 0:
        return res
    depth = (left_rim - trough) / left_rim * 100
    rim_match = abs(right_rim - left_rim) / left_rim < 0.08
    handle = seg.iloc[-10:]
    h_hi, h_lo = float(handle["high"].max()), float(handle["low"].min())
    handle_depth = (h_hi - h_lo) / h_hi * 100 if h_hi else 100
    # Handle must sit in the upper half of the cup, not near the lows.
    handle_high = h_lo > trough + (left_rim - trough) * 0.5
    detected = bool(12 <= depth <= 45 and rim_match and handle_depth <= 15 and handle_high)
    res.update(detected=detected, depth_pct=round(depth, 2),
               pivot=float(max(left_rim, right_rim)), base_weeks=round(len(seg) / 5),
               score=round(_clamp(0.45 + (45 - depth) / 90), 3) if detected else 0.0)
    return res


# ------------------------------------------------------------------ aggregate
DETECTORS = (detect_vcp, detect_flat_base, detect_rectangle,
             detect_ascending_triangle, detect_cup_handle)


def detect_all(df: pd.DataFrame) -> dict:
    """Run the five detectors and pick the best-quality base."""
    results = {}
    for fn in DETECTORS:
        try:
            r = fn(df)
        except Exception:                                 # noqa: BLE001
            continue
        results[r["name"]] = r

    found = [r for r in results.values() if r.get("detected")]
    # VCP outranks the others on a tie: it carries the most accumulation evidence.
    best = max(found, key=lambda r: (r["name"] == "VCP", r.get("score", 0)),
               default=None)

    # Fallback pivot: the prior 10-week high is still an objective resistance
    # level even when no textbook base is present.
    fallback = float(df["high"].iloc[-51:-1].max()) if df is not None and len(df) > 51 else float("nan")

    return {
        "all": results,
        "best": best,
        "best_name": best["name"] if best else "No defined base",
        "best_score": float(best.get("score", 0.0)) if best else 0.0,
        "pivot": float(best.get("pivot")) if best and np.isfinite(best.get("pivot", np.nan)) else fallback,
        "base_weeks": int(best.get("base_weeks", 0)) if best else 0,
        "detected_names": [r["name"] for r in found],
        "vcp": results.get("VCP", {}),
    }
