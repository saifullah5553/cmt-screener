"""Central configuration.

Every tunable lives here and is overridable by environment variable, so the
GitHub Actions workflow can retune the engine without a code change. Defaults
are set for INSTITUTIONAL SELECTIVITY: the goal is 5-20 A/B names per day out
of a few hundred scanned, not a long tail of marginal setups.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _s(key, default):      return str(_env(key, default)).strip()
def _f(key, default):      return float(_env(key, default))
def _i(key, default):      return int(float(_env(key, default)))
def _b(key, default):      return str(_env(key, default)).strip().lower() in ("1", "true", "yes", "y")
def _list(key, default):
    raw = _env(key, default)
    return [s.strip() for s in str(raw).split(",") if s.strip()]


@dataclass
class Config:
    # ---- notifications -------------------------------------------------
    telegram_token: str | None = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat:  str | None = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # ---- delivery timing -------------------------------------------------
    # Target alert time, expressed in UTC. The workflow wakes hourly and the
    # run gate refuses to fire before this, so the alert lands at the first
    # wake-up at or after it (13:00 UTC = 17:00 Dubai). GitHub's scheduler is
    # not punctual, so this is a floor, not a guarantee — see rungate.py.
    run_not_before_utc: str = field(default_factory=lambda: _s("RUN_NOT_BEFORE_UTC", "13:00"))
    # If the target window is missed entirely (GitHub can be hours late), still
    # send once the next all-closed window arrives rather than skipping a day.
    allow_late_fallback: bool = field(default_factory=lambda: _b("ALLOW_LATE_FALLBACK", "true"))

    # ---- universe ------------------------------------------------------
    # Which markets to scan. "ALL" = every market in the registry.
    markets:      list = field(default_factory=lambda: _list("MARKETS", "ALL"))
    # Number of raw movers pulled per market before quality filtering.
    scan_limit:   int  = field(default_factory=lambda: _i("SCREENER_LIMIT", 120))

    # ---- liquidity / tradability gates ---------------------------------
    min_price:        float = field(default_factory=lambda: _f("MIN_PRICE", 5))
    min_dollar_vol:   float = field(default_factory=lambda: _f("MIN_DOLLAR_VOL", 10_000_000))
    min_change_pct:   float = field(default_factory=lambda: _f("MIN_CHANGE_PCT", 1.5))
    min_bars:         int   = field(default_factory=lambda: _i("MIN_BARS", 150))

    # Turnover floors are expressed in LOCAL currency, because a USD-sized
    # floor would erase every PSX, EGX or Qatari name outright. These are
    # rough "an institution can actually get filled" levels per market.
    turnover_floors: dict = field(default_factory=lambda: {
        "US": 10_000_000, "AU": 2_000_000, "IN": 50_000_000,
        "PK": 20_000_000, "SA": 5_000_000, "KW": 200_000,
        "EG": 5_000_000, "AE": 2_000_000, "QA": 2_000_000,
    })

    def min_turnover_for(self, market_code: str) -> float:
        return float(self.turnover_floors.get(market_code, self.min_dollar_vol))

    # ---- breakout quality ----------------------------------------------
    # Close must land in the upper part of the day's range (no heavy upper wick).
    min_close_range_pos: float = field(default_factory=lambda: _f("MIN_CLOSE_RANGE_POS", 0.55))
    # Volume expansion required to call it a breakout at all.
    min_vol_ratio:       float = field(default_factory=lambda: _f("MIN_VOL_RATIO", 1.4))
    # How far past the pivot we will still consider an entry. Beyond this the
    # stop is too far away for acceptable risk — the move is already made.
    max_ext_from_pivot:  float = field(default_factory=lambda: _f("MAX_EXT_FROM_PIVOT", 8.0))

    # ---- relative strength ---------------------------------------------
    min_rs_rating: float = field(default_factory=lambda: _f("MIN_RS_RATING", 70))

    # ---- risk -----------------------------------------------------------
    min_rr:            float = field(default_factory=lambda: _f("MIN_RR", 2.0))
    atr_stop_mult:     float = field(default_factory=lambda: _f("ATR_STOP_MULT", 1.5))
    account_risk_pct:  float = field(default_factory=lambda: _f("ACCOUNT_RISK_PCT", 1.0))
    account_size:      float = field(default_factory=lambda: _f("ACCOUNT_SIZE", 100_000))

    # ---- output ---------------------------------------------------------
    # Minimum composite score to be alerted at all.
    min_score:       float = field(default_factory=lambda: _f("MIN_SCORE", 70))
    max_alerts:      int   = field(default_factory=lambda: _i("MAX_ALERTS", 20))
    include_grade_b: bool  = field(default_factory=lambda: _b("INCLUDE_GRADE_B", "true"))
    # Watchlist: names that satisfy every quality test but have not yet cleared
    # their pivot. A technician tracks these and acts on the trigger rather than
    # anticipating it. Set WATCH_ENABLED=false to suppress.
    watch_enabled:    bool  = field(default_factory=lambda: _b("WATCH_ENABLED", "true"))
    watch_within_pct: float = field(default_factory=lambda: _f("WATCH_WITHIN_PCT", 4.0))
    max_watch:        int   = field(default_factory=lambda: _i("MAX_WATCH", 10))

    # ---- engine ----------------------------------------------------------
    benchmark:      str  = field(default_factory=lambda: _env("BENCHMARK", "SPY"))
    asx_benchmark:  str  = field(default_factory=lambda: _env("ASX_BENCHMARK", "^AXJO"))
    history_period: str  = field(default_factory=lambda: _env("HISTORY_PERIOD", "2y"))
    force_run:      bool = field(default_factory=lambda: _b("FORCE_RUN", "false"))
    log_level:      str  = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # Sector ETFs used for sector-leadership ranking (US).
    sector_etfs: list = field(default_factory=lambda: _list(
        "SECTOR_ETFS",
        "XLK,XLF,XLV,XLY,XLP,XLE,XLI,XLB,XLU,XLRE,XLC"))

    # ---- weighted scoring model -----------------------------------------
    # Five factors only, summing to 100. Trend and base quality dominate
    # because they decide whether there is anything to break out from;
    # volume and relative strength confirm; market context modulates.
    # Risk is a GATE, not a score — see analysis.apply_gates.
    w_trend:  float = field(default_factory=lambda: _f("W_TREND", 25))
    w_base:   float = field(default_factory=lambda: _f("W_BASE", 25))
    w_volume: float = field(default_factory=lambda: _f("W_VOLUME", 20))
    w_rs:     float = field(default_factory=lambda: _f("W_RS", 20))
    w_market: float = field(default_factory=lambda: _f("W_MARKET", 10))

    def weights(self):
        return {"trend": self.w_trend, "base": self.w_base,
                "volume": self.w_volume, "rs": self.w_rs, "market": self.w_market}


CONFIG = Config()
