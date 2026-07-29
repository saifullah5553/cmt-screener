"""Breakout evaluation — the CMT decision process, in order.

A professional technician does not average twenty indicators. They ask a short
sequence of questions and reject the setup the moment one is answered badly:

    1. Is the stock in a confirmed uptrend?          (Stage 2, Dow structure, MAs)
    2. Did it build a real base?                     (pattern + duration + depth)
    3. Is it clearing well-defined resistance today? (pivot, close quality)
    4. Is volume confirming the move?                (expansion + accumulation)
    5. Is it outperforming the market and its group? (relative strength)
    6. Does the market environment support it?       (index trend)
    7. Is the risk acceptable and objectively defined?

Structure of this module mirrors that sequence. Hard GATES enforce the
non-negotiables; a weighted score then ranks whatever survives, so the trader
sees the best few names first rather than a flat pass/fail list.

Weights: Trend 25 | Base 25 | Volume 20 | Relative Strength 20 | Market 10.
Trend and base quality dominate because they determine whether a breakout has
anything to break out FROM; volume and RS confirm it; market context modulates.
Risk is a gate, not a score — a poor reward:risk disqualifies outright.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import patterns as pat
from . import stage as stage_mod
from .indicators import (accumulation_days, atr, higher_highs_lows, ma_rising,
                         obv, overhead_supply, resistance_level, sma,
                         volume_dryup)


def _clamp(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return lo


def _f(x, default=float("nan")):
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _r(x, n=2):
    v = _f(x)
    return round(v, n) if np.isfinite(v) else float("nan")


# --------------------------------------------------------------- 1. the trend
def assess_trend(df, weekly, monthly, st) -> dict:
    """Dow structure + moving-average alignment + trend maturity.

    Deliberately no ADX/oscillators: 'higher highs and higher lows above a
    rising long-term average' IS the professional definition of an uptrend.
    """
    c = df["close"]
    px = _f(c.iloc[-1])
    ma50, ma150, ma200 = sma(c, 50), sma(c, 150), sma(c, 200)
    m50 = _f(ma50.iloc[-1])
    m150 = _f(ma150.iloc[-1]) if len(df) >= 150 else float("nan")
    m200 = _f(ma200.iloc[-1]) if len(df) >= 200 else float("nan")

    above50 = np.isfinite(m50) and px > m50
    above200 = np.isfinite(m200) and px > m200
    stacked = np.isfinite(m50) and np.isfinite(m200) and m50 > m200
    rising50 = ma_rising(ma50, 10)
    rising200 = ma_rising(ma200, 20) if len(df) >= 220 else rising50
    hh, hl = higher_highs_lows(df)

    # Weekly confirmation: price above a rising 30-week average.
    wk_close = weekly["close"] if weekly is not None and len(weekly) > 32 else None
    wk_ma = sma(wk_close, 30) if wk_close is not None else None
    weekly_ok = bool(wk_ma is not None and _f(wk_close.iloc[-1]) > _f(wk_ma.iloc[-1])
                     and ma_rising(wk_ma, 4))
    # Monthly structure: simply that the longer trend is not broken.
    mo_close = monthly["close"] if monthly is not None and len(monthly) > 12 else None
    mo_ma = sma(mo_close, 10) if mo_close is not None else None
    monthly_ok = bool(mo_ma is not None and _f(mo_close.iloc[-1]) > _f(mo_ma.iloc[-1]))

    score = _clamp(0.20 * (hh and hl) + 0.10 * (hh or hl)
                   + 0.15 * above50 + 0.15 * above200
                   + 0.10 * stacked + 0.10 * rising50 + 0.05 * rising200
                   + 0.10 * weekly_ok + 0.05 * monthly_ok)
    # Stage 2 is the only genuinely constructive maturity.
    if st.get("stage") == 2:
        score = _clamp(score * 1.10)

    return {"score": round(score, 3), "higher_highs": hh, "higher_lows": hl,
            "above_50d": bool(above50), "above_200d": bool(above200),
            "ma_stacked": bool(stacked), "rising_50d": bool(rising50),
            "weekly_ok": weekly_ok, "monthly_ok": monthly_ok,
            "weekly_trend": "Confirmed uptrend" if weekly_ok else "Not confirmed",
            "monthly_trend": "Uptrend" if monthly_ok else "Not confirmed",
            "sma50": _r(m50), "sma200": _r(m200)}


# ----------------------------------------------------------- 2/3. base + break
def assess_breakout(df, pt, cfg) -> dict:
    """Is price clearing a well-defined ceiling, and is the candle convincing?"""
    last = df.iloc[-1]
    o, h, l, c = _f(last["open"]), _f(last["high"]), _f(last["low"]), _f(last["close"])
    prev = _f(df["close"].iloc[-2])
    rng = h - l

    pivot = _f(pt.get("pivot"), resistance_level(df, 50))
    above_pivot = bool(np.isfinite(pivot) and c > pivot)
    ext_pct = (c / pivot - 1) * 100 if (np.isfinite(pivot) and pivot) else float("nan")

    # Close near the high = buyers in control into the bell; a long upper wick
    # means the move was sold into.
    range_pos = (c - l) / rng if rng > 0 else 0.5
    upper_wick = (h - max(c, o)) / rng if rng > 0 else 0.0
    gap_pct = (o / prev - 1) * 100 if prev else 0.0

    a = _f(atr(df, 14).iloc[-2])
    move_atr = abs(c - prev) / a if a else float("nan")

    supply = overhead_supply(df, c)

    score = _clamp(0.35 * pt.get("best_score", 0.0)
                   + 0.25 * _clamp((range_pos - 0.4) / 0.5)
                   + 0.20 * (1.0 if above_pivot else 0.0)
                   + 0.10 * _clamp(1 - upper_wick / 0.4)
                   + 0.10 * (1 - _clamp(supply / 0.35) if np.isfinite(supply) else 0.5))

    return {"score": round(score, 3), "pivot": _r(pivot), "above_pivot": above_pivot,
            "ext_from_pivot_pct": _r(ext_pct), "range_pos": _r(range_pos),
            "upper_wick": _r(upper_wick), "gap_pct": _r(gap_pct),
            "move_atr": _r(move_atr), "overhead_supply": _r(supply)}


# ----------------------------------------------------------------- 4. volume
def assess_volume(df, base_bars: int) -> dict:
    """Contraction through the base, expansion on the breakout, net accumulation."""
    v = df["volume"]
    avg50 = _f(v.rolling(50).mean().iloc[-1])
    ratio = _f(v.iloc[-1]) / avg50 if avg50 else float("nan")

    o = obv(df)
    obv_now, obv_max = _f(o.iloc[-1]), _f(o.iloc[-50:].max())
    obv_confirm = bool(np.isfinite(obv_now) and np.isfinite(obv_max)
                       and obv_now >= obv_max * 0.995)

    acc, dist = accumulation_days(df, 25)
    dryup = volume_dryup(df, base_bars) if base_bars >= 15 else False

    score = _clamp(0.45 * _clamp((ratio - 1.0) / 1.5 if np.isfinite(ratio) else 0)
                   + 0.20 * (1.0 if obv_confirm else 0.25)
                   + 0.20 * _clamp((acc - dist + 3) / 8)
                   + 0.15 * (1.0 if dryup else 0.35))

    return {"score": round(score, 3), "vol_ratio": _r(ratio),
            "obv_confirm": obv_confirm, "acc_days": acc, "dist_days": dist,
            "volume_dryup": dryup, "avg_vol_50": _r(avg50, 0)}


# -------------------------------------------------------------------- 7. risk
def build_risk_plan(df, pivot, cfg) -> dict:
    """Objective trade parameters derived only from chart levels.

    The stop is the tightest LOGICAL level: below the pivot (a breakout that
    loses its pivot has failed) or below the recent swing low, whichever is
    more sensible, with an ATR floor so the stop is never unrealistically tight.
    """
    c = _f(df["close"].iloc[-1])
    a = _f(atr(df, 14).iloc[-1])
    swing_low = _f(df["low"].iloc[-10:].min())

    candidates = [x for x in (pivot * 0.97 if np.isfinite(pivot) else np.nan,
                              swing_low * 0.99) if np.isfinite(x) and x < c]
    stop = max(candidates) if candidates else (c - 1.5 * a if np.isfinite(a) else c * 0.92)

    # FLOOR: a stop closer than 1 ATR (or 3% of price) is noise, not a level.
    # Without this the reward:risk ratio explodes to meaningless values.
    min_risk = max(a if np.isfinite(a) else 0.0, c * 0.03)
    if c - stop < min_risk:
        stop = c - min_risk

    risk = c - stop
    if risk <= 0:
        return {"entry": _r(c), "stop": float("nan"), "rr": float("nan"),
                "target1": float("nan"), "target2": float("nan"),
                "atr": _r(a), "atr_pct": float("nan"), "risk_per_share": float("nan"),
                "failure_level": _r(pivot * 0.97 if np.isfinite(pivot) else np.nan),
                "swing_stop": _r(swing_low), "position_shares": 0}

    # Measured move: project the base's own height from the pivot.
    base_hi = _f(df["high"].iloc[-120:].max())
    base_lo = _f(df["low"].iloc[-120:].min())
    height = base_hi - base_lo if np.isfinite(base_hi) and np.isfinite(base_lo) else np.nan
    t1 = c + (height * 0.5 if np.isfinite(height) else 2 * risk)
    t1 = max(t1, c + 2 * risk)
    t2 = c + (height if np.isfinite(height) else 3.5 * risk)
    t2 = max(t2, t1 + risk)

    rr = (t1 - c) / risk
    risk_capital = cfg.account_size * (cfg.account_risk_pct / 100.0)

    return {"entry": _r(c), "stop": _r(stop), "swing_stop": _r(swing_low),
            "failure_level": _r(pivot * 0.97 if np.isfinite(pivot) else np.nan),
            "target1": _r(t1), "target2": _r(t2), "rr": _r(rr), "atr": _r(a),
            "atr_pct": _r(a / c * 100 if c else np.nan),
            "risk_per_share": _r(risk),
            "position_shares": int(risk_capital / risk) if risk > 0 else 0}


# ------------------------------------------------------------------- gates
def apply_gates(st, tr, bo, vol, rp, rs_rating, cfg, strictness) -> list[str]:
    """Non-negotiables. Each maps to an accepted technical-analysis principle."""
    out = []

    if st.get("stage") in (3, 4):
        out.append(f"{st.get('name')}: buy only Stage 2")
    if not (tr["above_50d"] and tr["above_200d"]):
        out.append("price not above both 50/200-day averages")
    if not (tr["higher_lows"] or tr["weekly_ok"]):
        out.append("uptrend structure not confirmed")

    if not bo["above_pivot"]:
        out.append("no close above resistance")
    if _f(bo["range_pos"], 0) < cfg.min_close_range_pos:
        out.append(f"closed weakly in range ({bo['range_pos']})")
    # Chasing an extended breakout is where risk:reward is destroyed.
    if _f(bo["ext_from_pivot_pct"], 0) > cfg.max_ext_from_pivot * (2 - strictness):
        out.append(f"extended {bo['ext_from_pivot_pct']}% beyond pivot")

    if _f(vol["vol_ratio"], 0) < cfg.min_vol_ratio * strictness:
        out.append(f"volume {vol['vol_ratio']}x < {cfg.min_vol_ratio}x required")
    if vol["dist_days"] >= vol["acc_days"] + 3:
        out.append(f"distribution dominant ({vol['dist_days']}v{vol['acc_days']})")

    if np.isfinite(rs_rating) and rs_rating < cfg.min_rs_rating * strictness:
        out.append(f"relative strength {rs_rating:.0f} < {cfg.min_rs_rating:.0f}")

    if _f(rp["rr"], 0) < cfg.min_rr:
        out.append(f"reward:risk {rp['rr']} < {cfg.min_rr}")

    return out


# ---------------------------------------------------------------- main entry
def analyse(symbol, df, weekly, monthly, ctx) -> dict:
    cfg, regime = ctx["cfg"], ctx["regime"]
    strictness = regime.get("strictness", 1.0)

    st = stage_mod.analyse(weekly)
    pt = pat.detect_all(df)
    tr = assess_trend(df, weekly, monthly, st)
    bo = assess_breakout(df, pt, cfg)
    vol = assess_volume(df, pt.get("base_weeks", 0) * 5)
    rp = build_risk_plan(df, _f(bo["pivot"]), cfg)

    rs = ctx["rs"].get(symbol, {})
    rs_rating = _f(rs.get("rs_rating"))
    sec_score, sec_label = ctx["sector_of"](symbol)

    subs = {
        "trend": tr["score"],
        "base": _clamp(0.55 * pt["best_score"] + 0.45 * bo["score"]),
        "volume": vol["score"],
        "rs": _clamp(rs_rating / 100) if np.isfinite(rs_rating) else 0.4,
        "market": _clamp(0.6 * regime.get("health", 0.5) + 0.4 * sec_score),
    }
    w = cfg.weights()
    score = sum(subs[k] * w[k] for k in w) / (sum(w.values()) or 1) * 100

    gates = apply_gates(st, tr, bo, vol, rp, rs_rating, cfg, strictness)

    # Watchlist: everything is right except that price has not yet cleared the
    # pivot, and it is close beneath it. This is how a technician actually
    # works — the setup is identified in advance, the trigger is awaited.
    dist_below = -_f(bo["ext_from_pivot_pct"], -99)
    is_watch = (cfg.watch_enabled
                and gates == ["no close above resistance"]
                and 0 <= dist_below <= cfg.watch_within_pct
                and score >= cfg.min_score)

    if is_watch:
        grade = "W"
    elif gates:
        grade = "C"
    elif score >= 82 and pt["best_score"] >= 0.5:
        grade = "A"
    elif score >= cfg.min_score:
        grade = "B"
    else:
        grade = "C"

    row = {
        "symbol": symbol, "market": rs.get("market", "US"),
        "grade": grade, "score": round(score, 1),
        "stage": st.get("stage"), "stage_name": st.get("name"),
        "pattern": pt["best_name"], "base_weeks": pt.get("base_weeks", 0),
        "vcp_contractions": pt.get("vcp", {}).get("contractions", 0),
        "trend_score": round(subs["trend"] * 100),
        "base_score": round(subs["base"] * 100),
        "volume_score": round(subs["volume"] * 100),
        "rs_rating": _r(rs_rating), "sector_label": sec_label,
        "weekly_trend": tr["weekly_trend"], "monthly_trend": tr["monthly_trend"],
        "higher_highs": tr["higher_highs"], "higher_lows": tr["higher_lows"],
        "above_50d": tr["above_50d"], "above_200d": tr["above_200d"],
        "vol_ratio": vol["vol_ratio"], "obv_confirm": vol["obv_confirm"],
        "acc_days": vol["acc_days"], "dist_days": vol["dist_days"],
        "volume_dryup": vol["volume_dryup"],
        "pivot": bo["pivot"], "range_pos": bo["range_pos"],
        "ext_from_pivot_pct": bo["ext_from_pivot_pct"],
        "gap_pct": bo["gap_pct"], "overhead_supply": bo["overhead_supply"],
        "regime": regime.get("label"),
        "disqualifiers": "; ".join(gates),
        **{k: rp[k] for k in ("entry", "stop", "swing_stop", "failure_level",
                              "target1", "target2", "rr", "atr", "atr_pct",
                              "risk_per_share", "position_shares")},
    }
    row["thesis"] = write_observation(row, st, pt, vol, regime)
    return row


def write_observation(row, st, pt, vol, regime) -> str:
    """A technician's written read of the chart — plain, specific, no jargon padding."""
    if row["grade"] == "W":
        gap = -_f(row["ext_from_pivot_pct"], 0)
        return (f"Set up but not triggered: {row['base_weeks']}-week "
                f"{row['pattern']} in {st.get('name', '')}, "
                f"{gap:.1f}% below the {row['pivot']} pivot. "
                f"Relative strength {row['rs_rating']:.0f}/100. "
                f"Await a close above the pivot on expanding volume.")
    if row["disqualifiers"]:
        return f"Rejected — {row['disqualifiers']}."

    base = row["pattern"]
    weeks = row["base_weeks"]
    lead = f"{st.get('name', '').replace(' - ', ' ')} breakout"
    if base != "No defined base" and weeks:
        # Keep acronyms upper-case; lower-case the descriptive names.
        label = base if base.isupper() else base.lower()
        lead += f" from a {weeks}-week {label}"
        if pt.get("vcp", {}).get("detected") and row["vcp_contractions"]:
            lead += f" showing {row['vcp_contractions']} volatility contractions"

    parts = [lead + "."]
    vr = _f(row["vol_ratio"])
    if np.isfinite(vr):
        parts.append(f"Price closed above the {row['pivot']} pivot on {vr:.1f}x average volume")
        if vol.get("volume_dryup"):
            parts[-1] += " after volume dried up through the base"
        parts[-1] += "."
    if _f(row["range_pos"], 0) >= 0.7:
        parts.append("Close in the upper part of the day's range.")
    if row["acc_days"] > row["dist_days"]:
        parts.append(f"{row['acc_days']} accumulation days vs {row['dist_days']} distribution "
                     "over the past five weeks.")
    if np.isfinite(_f(row["rs_rating"])):
        rs_word = "improving" if _f(row["rs_rating"]) < 85 else "strong"
        parts.append(f"Relative strength {rs_word} at {row['rs_rating']:.0f}/100"
                     + (f" in {row['sector_label']}" if "unranked" not in str(row["sector_label"]) else "")
                     + ".")
    parts.append(f"Market: {regime.get('label').lower()}.")
    parts.append(f"Stop {row['stop']} below support; first objective {row['target1']} "
                 f"({row['rr']}:1).")
    return " ".join(parts)
