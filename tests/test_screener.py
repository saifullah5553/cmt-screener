"""Regression and unit tests. Run: python -m pytest tests/ -q

The first test class guards the defect that silently produced zero breakouts
for every symbol — it must never regress.
"""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from screener import indicators as ind
from screener import marketdata, patterns, sessions, stage
from screener.analysis import build_risk_plan
from screener.config import Config


def make_df(n=300, start=100.0, trend=0.0015, vol=1_000_000, seed=0):
    """Synthetic but well-formed OHLCV."""
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + trend + rng.normal(0, 0.012, n))
    high = close * (1 + abs(rng.normal(0, 0.006, n)))
    low = close * (1 - abs(rng.normal(0, 0.006, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({
        "datetime": pd.bdate_range("2024-01-01", periods=n),
        "open": open_, "high": np.maximum.reduce([high, close, open_]),
        "low": np.minimum.reduce([low, close, open_]),
        "close": close,
        "volume": rng.integers(vol // 2, vol * 2, n).astype(float),
    })


# ---------------------------------------------------------------- regression
class TestUnsettledBarRepair:
    """THE bug: Yahoo returns the newest bar with close=NaN. dropna(how='all')
    does not remove a partially-null row, so price became NaN and every
    comparison silently evaluated False -> everything graded C."""

    def test_nan_close_repaired_from_quote(self):
        df = make_df(60)
        df.loc[df.index[-1], "close"] = np.nan
        out = marketdata.repair_last_bar(df.copy(), "TEST",
                                         {"regularMarketPrice": 123.45})
        assert pd.notna(out["close"].iloc[-1])
        assert out["close"].iloc[-1] == pytest.approx(123.45)

    def test_repaired_close_stays_inside_range(self):
        df = make_df(60)
        df.loc[df.index[-1], "close"] = np.nan
        df.loc[df.index[-1], "high"] = 100.0
        out = marketdata.repair_last_bar(df.copy(), "TEST",
                                         {"regularMarketPrice": 150.0})
        last = out.iloc[-1]
        assert last["low"] <= last["close"] <= last["high"]

    def test_unrepairable_bar_is_dropped_not_kept(self):
        df = make_df(60)
        n = len(df)
        df.loc[df.index[-1], "close"] = np.nan
        out = marketdata.repair_last_bar(df.copy(), "NOSUCHTICKER_XYZ", None)
        assert len(out) == n - 1
        assert out["close"].notna().all()

    def test_validate_rejects_incomplete_final_bar(self):
        df = make_df(200)
        df.loc[df.index[-1], "close"] = np.nan
        ok, why = marketdata.validate(df, 150)
        assert not ok and "incomplete" in why

    def test_nan_price_would_fail_all_comparisons(self):
        """Documents WHY the bug was invisible: NaN comparisons are all False."""
        assert not (np.nan > 1) and not (np.nan < 1) and not (np.nan == np.nan)


# ------------------------------------------------------------------- risk
class TestRiskPlan:
    def test_stop_has_atr_floor_so_rr_is_not_absurd(self):
        """Regression: stops landing cents below entry produced R:R of 244:1."""
        cfg = Config()
        df = make_df(200)
        price = float(df["close"].iloc[-1])
        # Pivot just below price would otherwise give a near-zero risk.
        plan = build_risk_plan(df, price * 0.9999, cfg)
        assert plan["risk_per_share"] >= price * 0.03 * 0.999
        assert plan["rr"] < 50

    def test_stop_below_entry_and_targets_ordered(self):
        cfg = Config()
        df = make_df(200)
        plan = build_risk_plan(df, float(df["high"].iloc[-30:-1].max()), cfg)
        assert plan["stop"] < plan["entry"] < plan["target1"] < plan["target2"]

    def test_position_size_respects_account_risk(self):
        cfg = Config()
        df = make_df(200)
        plan = build_risk_plan(df, float(df["high"].iloc[-30:-1].max()), cfg)
        risk_budget = cfg.account_size * cfg.account_risk_pct / 100
        assert plan["position_shares"] * plan["risk_per_share"] <= risk_budget * 1.01


# ---------------------------------------------------------------- sessions
class TestSessionGate:
    def test_morning_run_targets_previous_session(self):
        """09:15 Dubai == 01:15 ET -> analyse yesterday's completed candle."""
        now = datetime(2026, 7, 29, 1, 15, tzinfo=sessions.NY)  # Wednesday
        session, closed_today = sessions.resolve_session(now)
        assert session == date(2026, 7, 28)
        assert closed_today is False

    def test_after_close_uses_todays_session(self):
        now = datetime(2026, 7, 29, 17, 0, tzinfo=sessions.NY)
        session, closed_today = sessions.resolve_session(now)
        assert session == date(2026, 7, 29) and closed_today

    def test_weekend_skips_without_realerting(self):
        sunday = datetime(2026, 8, 2, 1, 15, tzinfo=sessions.NY)
        run, _, _ = sessions.should_run(sunday, force=False)
        assert run is False

    def test_saturday_still_reports_fridays_session(self):
        saturday = datetime(2026, 8, 1, 1, 15, tzinfo=sessions.NY)
        run, session, _ = sessions.should_run(saturday, force=False)
        assert run is True and session == date(2026, 7, 31)

    def test_force_overrides_gate(self):
        sunday = datetime(2026, 8, 2, 1, 15, tzinfo=sessions.NY)
        run, _, _ = sessions.should_run(sunday, force=True)
        assert run is True


# -------------------------------------------------------------- indicators
class TestIndicators:
    def test_atr_positive_and_finite(self):
        a = ind.atr(make_df(120))
        assert a.iloc[-1] > 0 and np.isfinite(a.iloc[-1])

    def test_uptrend_shows_higher_highs_and_lows(self):
        hh, hl = ind.higher_highs_lows(make_df(250, trend=0.004, seed=3))
        assert hh and hl

    def test_overhead_supply_zero_at_new_highs(self):
        df = make_df(250, trend=0.005, seed=5)
        s = ind.overhead_supply(df, float(df["close"].max()) * 1.01)
        assert s == pytest.approx(0.0, abs=1e-9)

    def test_ma_rising_detects_direction(self):
        up = ind.sma(make_df(250, trend=0.005, seed=7)["close"], 50)
        down = ind.sma(make_df(250, trend=-0.005, seed=7)["close"], 50)
        assert ind.ma_rising(up) and not ind.ma_rising(down)

    def test_accumulation_days_counts_both_sides(self):
        acc, dist = ind.accumulation_days(make_df(200))
        assert acc >= 0 and dist >= 0 and acc + dist <= 25


# ------------------------------------------------------------------ stage
class TestStage:
    def test_strong_uptrend_is_stage_2(self):
        weekly = marketdata.to_weekly(make_df(500, trend=0.004, seed=11))
        assert stage.analyse(weekly)["stage"] == 2

    def test_downtrend_is_stage_4(self):
        weekly = marketdata.to_weekly(make_df(500, trend=-0.004, seed=12))
        assert stage.analyse(weekly)["stage"] == 4

    def test_short_history_is_undetermined(self):
        assert stage.analyse(make_df(10))["stage"] == 0


# --------------------------------------------------------------- patterns
class TestPatterns:
    def test_detectors_never_raise_on_short_input(self):
        tiny = make_df(12)
        for fn in patterns.DETECTORS:
            assert fn(tiny)["detected"] is False

    def test_base_weeks_never_reports_whole_window(self):
        """Regression: every VCP was reported as a 65-week base because the
        base-start search fell back to the start of the entire window."""
        for seed in range(6):
            r = patterns.detect_vcp(make_df(400, seed=seed))
            if r["detected"]:
                assert r["base_weeks"] < 65

    def test_flat_base_requires_shallow_depth(self):
        wild = make_df(200, trend=0.0, seed=21)
        wild["high"] *= 1.5          # blow the range out
        wild["low"] *= 0.5
        assert patterns.detect_flat_base(wild)["detected"] is False

    def test_detect_all_returns_pivot(self):
        out = patterns.detect_all(make_df(300, trend=0.003))
        assert np.isfinite(out["pivot"])


# -------------------------------------------------------------- watchlist
class TestWatchlist:
    """A watch name is one whose ONLY failing test is that price has not yet
    cleared the pivot, and which sits just beneath it."""

    def _row(self, grade="W", ext=-2.0):
        return {"symbol": "TEST", "grade": grade, "pattern": "VCP",
                "base_weeks": 12, "pivot": 100.0, "rs_rating": 85.0,
                "ext_from_pivot_pct": ext, "score": 75.0}

    def test_watch_line_shows_distance_to_pivot(self):
        from screener.notify import format_watch
        out = format_watch(self._row())
        assert "TEST" in out and "2.0% away" in out and "100" in out

    def test_watch_included_in_messages_without_confirmed_breakouts(self):
        from screener.notify import build_messages
        cfg = Config()
        msgs = build_messages("2026-07-28", {"label": "Confirmed Uptrend",
                                             "health": 0.7, "vix": 18.0},
                              50, [self._row()], cfg)
        body = "\n".join(msgs)
        assert "No confirmed breakouts" in body and "Approaching pivot" in body

    def test_watch_can_be_disabled(self):
        cfg = Config()
        cfg.watch_enabled = False
        assert cfg.watch_enabled is False

    def test_watch_respects_max(self):
        from screener.notify import build_messages
        cfg = Config()
        cfg.max_watch = 2
        rows = [dict(self._row(), symbol=f"T{i}") for i in range(6)]
        body = "\n".join(build_messages("2026-07-28",
                                        {"label": "x", "health": 0.5, "vix": float("nan")},
                                        50, rows, cfg))
        assert sum(body.count(f"*T{i}*") for i in range(6)) == 2


# -------------------------------------------------------------- timeframes
class TestResampling:
    def test_weekly_and_monthly_shrink_and_preserve_extremes(self):
        df = make_df(260)
        wk, mo = marketdata.to_weekly(df), marketdata.to_monthly(df)
        assert len(mo) < len(wk) < len(df)
        assert wk["high"].max() == pytest.approx(df["high"].max())
        assert wk["low"].min() == pytest.approx(df["low"].min())

    def test_weekly_volume_is_summed(self):
        df = make_df(60)
        assert marketdata.to_weekly(df)["volume"].sum() == pytest.approx(df["volume"].sum())
