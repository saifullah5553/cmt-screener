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


# ---------------------------------------------------------------- markets
class TestMultiMarket:
    """The job runs once at a moment when every market is closed, but must
    still be correct if GitHub fires it late and a market has re-opened."""

    from zoneinfo import ZoneInfo as _Z
    UTC = _Z("UTC")

    def _at(self, y, m, d, h, mi=0):
        return datetime(y, m, d, h, mi, tzinfo=self.UTC)

    def test_all_markets_closed_at_scheduled_time(self):
        """13:00 UTC (17:00 Dubai) must be all-closed in BOTH DST regimes."""
        from screener import markets as mk
        for month, day in ((1, 14), (7, 15)):
            t = self._at(2026, month, day, 13, 0)
            still_open = [m.code for m in mk.resolve(["ALL"]) if m.is_open(t)]
            assert still_open == [], f"month {month}: {still_open} open at 13:00Z"

    def test_us_reports_previous_session_at_scheduled_time(self):
        """At 13:00 UTC the US has not opened yet, so it reports yesterday."""
        from screener import markets as mk
        t = self._at(2026, 7, 29, 13, 0)          # Wednesday
        us = mk.get("US")
        assert us.is_open(t) is False
        assert us.last_completed_session(t) == date(2026, 7, 28)

    def test_asia_gulf_report_same_day_at_scheduled_time(self):
        """Asia and Gulf have closed by 13:00 UTC, so they report TODAY."""
        from screener import markets as mk
        t = self._at(2026, 7, 29, 13, 0)
        for code in ("IN", "PK", "SA", "AE", "QA", "KW"):
            assert mk.get(code).last_completed_session(t) == date(2026, 7, 29), code

    def test_open_market_falls_back_to_previous_session(self):
        """At 09:15 Dubai the Indian market is mid-session -> yesterday."""
        from screener import markets as mk
        t = self._at(2026, 7, 29, 5, 15)          # 09:15 Dubai, Wednesday
        india = mk.get("IN")
        assert india.is_open(t) is True
        assert india.last_completed_session(t) == date(2026, 7, 28)

    def test_closed_market_uses_todays_session(self):
        from screener import markets as mk
        t = self._at(2026, 7, 29, 21, 30)         # after every close
        assert mk.get("IN").last_completed_session(t) == date(2026, 7, 29)

    def test_gulf_weekend_is_friday_saturday(self):
        from screener import markets as mk
        sa = mk.get("SA")
        assert sa.is_trading_day(date(2026, 7, 3)) is False   # Friday
        assert sa.is_trading_day(date(2026, 7, 4)) is False   # Saturday
        assert sa.is_trading_day(date(2026, 7, 5)) is True    # Sunday trades

    def test_us_weekend_is_saturday_sunday(self):
        from screener import markets as mk
        us = mk.get("US")
        assert us.is_trading_day(date(2026, 8, 1)) is False   # Saturday
        assert us.is_trading_day(date(2026, 7, 31)) is True   # Friday

    def test_market_inferred_from_suffix(self):
        from screener import markets as mk
        assert mk.market_of("RELIANCE.NS") == "IN"
        assert mk.market_of("OGDC.KA") == "PK"
        assert mk.market_of("2222.SR") == "SA"
        assert mk.market_of("BHP.AX") == "AU"
        assert mk.market_of("AAPL") == "US"

    def test_static_universes_are_populated_where_discovery_fails(self):
        from screener import markets as mk
        for code in ("PK", "AE", "QA"):
            m = mk.get(code)
            assert m.region is None and len(m.universe) >= 5

    def test_partial_bar_not_repaired_when_market_open(self):
        """Guards the multi-market data hazard: patching a NaN close from a
        live quote while the market trades would fabricate a candle."""
        df = make_df(60)
        n = len(df)
        df.loc[df.index[-1], "close"] = np.nan
        out = marketdata.repair_last_bar(df.copy(), "RELIANCE.NS",
                                         {"regularMarketPrice": 999.0},
                                         allow_quote=False)
        assert len(out) == n - 1
        assert 999.0 not in out["close"].values

    def test_synthetic_holiday_bars_are_dropped(self):
        """Non-US markets emit holidays as volume-0 bars with OHLC identical.
        Leaving them in understates ATR and inflates the volume ratio."""
        df = make_df(80)
        i = df.index[40]
        px = float(df.loc[i, "close"])
        df.loc[i, ["open", "high", "low", "close"]] = px
        df.loc[i, "volume"] = 0
        out = marketdata.drop_synthetic_bars(df.copy())
        assert len(out) == len(df) - 1

    def test_real_zero_volume_bar_with_movement_is_kept(self):
        """Only flat AND zero-volume bars are holidays; don't over-delete."""
        df = make_df(80)
        df.loc[df.index[40], "volume"] = 0          # price still moved
        assert len(marketdata.drop_synthetic_bars(df.copy())) == len(df)

    def test_synthetic_bars_understate_atr(self):
        """Documents the damage the cleaner prevents."""
        from screener.indicators import atr
        df = make_df(120)
        dirty = df.copy()
        for k in range(-30, -1, 4):                 # inject holiday placeholders
            i = dirty.index[k]
            px = float(dirty.loc[i, "close"])
            dirty.loc[i, ["open", "high", "low", "close"]] = px
            dirty.loc[i, "volume"] = 0
        clean = marketdata.drop_synthetic_bars(dirty.copy())
        assert float(atr(clean, 14).iloc[-1]) > float(atr(dirty, 14).iloc[-1])

    def test_psx_universe_is_broad_enough_to_be_useful(self):
        from screener import markets as mk
        assert len(mk.get("PK").universe) >= 60

    def test_trim_to_session_drops_future_bars(self):
        df = make_df(40)
        target = df["datetime"].iloc[-3].date()
        out = marketdata.trim_to_session(df, target)
        assert out["datetime"].iloc[-1].date() == target
        assert len(out) == len(df) - 2


# --------------------------------------------------------------- run gate
class TestRunGate:
    """GitHub cron fired 5-10 hours late every day, landing mid-US-session.
    The gate must refuse those moments rather than emit a US-less alert."""

    from zoneinfo import ZoneInfo as _Z
    UTC = _Z("UTC")

    def _cfg(self, tmp_path, markets=None):
        cfg = Config()
        cfg.markets = markets or ["US", "IN", "PK", "SA"]
        cfg.force_run = False
        return cfg

    def _at(self, h, mi=0):
        return datetime(2026, 7, 29, h, mi, tzinfo=self.UTC)

    def _isolate(self, tmp_path, monkeypatch):
        from screener import rungate
        monkeypatch.setattr(rungate, "STATE_FILE", str(tmp_path / "state.json"))
        return rungate

    def test_refuses_to_run_while_us_is_trading(self, tmp_path, monkeypatch):
        """18:21 UTC — the actual observed run time. US is mid-session."""
        rg = self._isolate(tmp_path, monkeypatch)
        ok, _, reason = rg.should_run(self._cfg(tmp_path), self._at(18, 21))
        assert ok is False and "US" in reason

    def test_runs_once_all_markets_closed(self, tmp_path, monkeypatch):
        rg = self._isolate(tmp_path, monkeypatch)
        ok, sig, _ = rg.should_run(self._cfg(tmp_path), self._at(21, 30))
        assert ok is True and sig

    def test_does_not_repeat_the_same_session_set(self, tmp_path, monkeypatch):
        rg = self._isolate(tmp_path, monkeypatch)
        cfg = self._cfg(tmp_path)
        t = self._at(21, 30)
        ok, sig, _ = rg.should_run(cfg, t)
        assert ok is True
        rg.write_state(sig, t)
        ok2, _, reason = rg.should_run(cfg, self._at(22, 30))
        assert ok2 is False and "already" in reason

    def test_new_session_set_alerts_again_next_day(self, tmp_path, monkeypatch):
        rg = self._isolate(tmp_path, monkeypatch)
        cfg = self._cfg(tmp_path)
        _, sig, _ = rg.should_run(cfg, self._at(21, 30))
        rg.write_state(sig, self._at(21, 30))
        tomorrow = datetime(2026, 7, 30, 21, 30, tzinfo=self.UTC)
        ok, sig2, _ = rg.should_run(cfg, tomorrow)
        assert ok is True and sig2 != sig

    def test_only_one_alert_per_utc_day_across_both_windows(self, tmp_path, monkeypatch):
        """A UTC day has TWO all-closed windows (midday, and after the US
        close). They carry different session sets, so without a date-keyed
        guard the user would be alerted twice every day."""
        rg = self._isolate(tmp_path, monkeypatch)
        cfg = self._cfg(tmp_path, markets=["US", "IN"])
        midday = self._at(12, 30)
        ok, sig, _ = rg.should_run(cfg, midday)
        assert ok is True
        rg.write_state(sig, midday)
        ok2, sig2, reason = rg.should_run(cfg, self._at(21, 30))
        assert sig2 != sig                    # genuinely a different session set
        assert ok2 is False and "today" in reason

    def test_force_run_bypasses_the_gate(self, tmp_path, monkeypatch):
        rg = self._isolate(tmp_path, monkeypatch)
        cfg = self._cfg(tmp_path)
        cfg.force_run = True
        ok, _, reason = rg.should_run(cfg, self._at(18, 21))   # mid-US-session
        assert ok is True and reason == "forced"

    def test_signature_captures_every_market_session(self, tmp_path):
        from screener import markets as mk
        sig = mk.session_signature(["US", "IN"], self._at(21, 30))
        assert "US:" in sig and "IN:" in sig


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
