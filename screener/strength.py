"""Relative Strength Rating (0-100) and sector leadership.

Why the old implementation had to be replaced: it compared a single 20-day
return against SPY and emitted a binary Beat/Lag. That is far too short a
window to identify genuine market leadership, it ignores how the stock ranks
against its *peers*, and a boolean throws away all the information a rating
carries.

The replacement mirrors the institutional approach (IBD-style): a weighted
blend of 3/6/12-month performance, emphasising the most recent quarter,
measured relative to the benchmark, then percentile-ranked across the scanned
universe and normalised to 0-100. A momentum-persistence term rewards stocks
that trend steadily rather than gapping once and stalling.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import roc

# Weighted like IBD's RS: recent quarter carries the most information about
# current institutional sponsorship, but a full year filters out flashes.
PERIOD_WEIGHTS = ((63, 0.40), (126, 0.30), (189, 0.15), (252, 0.15))


def raw_rs(df: pd.DataFrame, bench: pd.DataFrame | None) -> float:
    """Benchmark-relative weighted performance score (unbounded, ~0 = in line)."""
    if df is None or len(df) < 70:
        return float("nan")
    close = df["close"]
    b_close = bench["close"] if bench is not None and len(bench) >= 70 else None

    total, wsum = 0.0, 0.0
    for period, w in PERIOD_WEIGHTS:
        if len(close) <= period:
            continue
        r = roc(close, period)
        if not np.isfinite(r):
            continue
        if b_close is not None and len(b_close) > period:
            br = roc(b_close, period)
            if np.isfinite(br):
                r -= br                      # excess return over the benchmark
        total += w * r
        wsum += w
    if wsum == 0:
        return float("nan")
    return total / wsum


def momentum_persistence(df: pd.DataFrame, window: int = 63) -> float:
    """Fraction of recent weeks that closed up.

    Distinguishes a stock advancing steadily on sustained demand from one that
    gapped once and went sideways — the former is what institutional buying
    looks like on a chart.
    """
    if df is None or len(df) < window + 5:
        return float("nan")
    weekly = df["close"].iloc[-window:].iloc[::5]
    if len(weekly) < 4:
        return float("nan")
    return float((weekly.diff().dropna() > 0).mean())


def rate_universe(frames: dict[str, pd.DataFrame], benches: dict[str, pd.DataFrame],
                  markets: dict[str, str]) -> dict[str, dict]:
    """Percentile-rank every symbol's raw RS into a 0-100 rating.

    Ranking is done WITHIN a market so ASX names are not unfairly compared to a
    stronger US tape. Symbols are rated against the scanned universe, which is
    already a momentum cohort — so a 70+ here is a genuinely strong reading.
    """
    raw: dict[str, float] = {}
    for sym, df in frames.items():
        mkt = markets.get(sym, "US")
        raw[sym] = raw_rs(df, benches.get(mkt))

    out: dict[str, dict] = {}
    by_market: dict[str, list] = {}
    for sym, v in raw.items():
        if np.isfinite(v):
            by_market.setdefault(markets.get(sym, "US"), []).append((sym, v))

    for mkt, items in by_market.items():
        vals = np.array([v for _, v in items], dtype=float)
        for sym, v in items:
            pct = float((vals < v).mean()) * 100 if len(vals) > 1 else 50.0
            out[sym] = {
                "rs_rating": round(pct, 1),
                "rs_raw": round(v * 100, 2),
                "persistence": _round(momentum_persistence(frames.get(sym))),
                "market": mkt,
            }
    for sym in frames:
        out.setdefault(sym, {"rs_rating": float("nan"), "rs_raw": float("nan"),
                             "persistence": float("nan"),
                             "market": markets.get(sym, "US")})
    return out


def _round(x, n=2):
    return round(float(x), n) if x is not None and np.isfinite(x) else float("nan")


# ------------------------------------------------------------ sector leadership
def rank_sectors(sector_frames: dict[str, pd.DataFrame],
                 bench: pd.DataFrame | None) -> dict[str, dict]:
    """Rank sector ETFs by benchmark-relative strength.

    Buying a breakout in a leading sector materially raises the odds: sector
    beta supplies a tailwind and institutional rotation tends to persist for
    weeks-to-months, which is exactly the swing horizon we trade.
    """
    scores = {}
    for etf, df in sector_frames.items():
        v = raw_rs(df, bench)
        if np.isfinite(v):
            scores[etf] = v
    if not scores:
        return {}
    vals = np.array(list(scores.values()), dtype=float)
    out = {}
    for etf, v in scores.items():
        pct = float((vals < v).mean()) * 100 if len(vals) > 1 else 50.0
        out[etf] = {"rs": round(v * 100, 2), "rank_pct": round(pct, 1),
                    "leading": pct >= 60}
    return out


# Map a Yahoo sector label to its SPDR sector ETF so a stock inherits the
# leadership reading of the group it actually trades with.
SECTOR_TO_ETF = {
    "technology": "XLK", "information technology": "XLK",
    "financial services": "XLF", "financial": "XLF",
    "healthcare": "XLV", "health care": "XLV",
    "consumer cyclical": "XLY", "consumer discretionary": "XLY",
    "consumer defensive": "XLP", "consumer staples": "XLP",
    "energy": "XLE",
    "industrials": "XLI", "industrial": "XLI",
    "basic materials": "XLB", "materials": "XLB",
    "utilities": "XLU",
    "real estate": "XLRE",
    "communication services": "XLC",
}


def sector_score(sector_name: str | None, sector_ranks: dict) -> tuple[float, str]:
    """Return (0-1 leadership score, human label) for a stock's sector."""
    if not sector_name:
        return 0.5, "Sector unknown"
    etf = SECTOR_TO_ETF.get(str(sector_name).strip().lower())
    if not etf or etf not in sector_ranks:
        return 0.5, f"{sector_name} (unranked)"
    r = sector_ranks[etf]
    tier = ("a leading sector" if r["rank_pct"] >= 70 else
            "an improving sector" if r["rank_pct"] >= 50 else "a lagging sector")
    return r["rank_pct"] / 100.0, f"{sector_name} ({tier})"
