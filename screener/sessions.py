"""Trading-session resolution.

The job fires at 09:15 Dubai = 00:15-01:15 New York, i.e. after the US close
and before the next open. The freshest COMPLETED session is therefore the
previous trading day — that is the candle we analyse. Running after 16:00 ET
(a manual afternoon test) uses today's just-closed session instead.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SYDNEY = ZoneInfo("Australia/Sydney")


def is_trading_day(d: date) -> bool:
    """NYSE/NASDAQ session check — exchange calendar if present, else fallback."""
    try:
        import pandas_market_calendars as mcal
        cal = mcal.get_calendar("XNYS")
        return len(cal.valid_days(start_date=d.isoformat(), end_date=d.isoformat())) > 0
    except Exception:                                    # noqa: BLE001
        from nyse_cal import is_trading_day as fallback
        return fallback(d)


def prev_trading_day(d: date):
    p = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(p):
            return p
        p -= timedelta(days=1)
    return None


def resolve_session(now_ny: datetime | None = None):
    """Return (session_date, closed_today) for the latest completed US session."""
    now_ny = now_ny or datetime.now(NY)
    d = now_ny.date()
    close = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    if is_trading_day(d) and now_ny >= close:
        return d, True
    return prev_trading_day(d), False


def should_run(now_ny: datetime | None = None, force: bool = False):
    """Gate the daily run. Returns (run: bool, session_date, reason)."""
    now_ny = now_ny or datetime.now(NY)
    session, closed_today = resolve_session(now_ny)
    if force:
        return True, session, "forced"
    if session is None:
        return False, None, "no recent trading session found"
    age = (now_ny.date() - session).days
    if not closed_today and age > 1:
        return False, session, (f"latest session {session} is {age} days old — "
                                "already covered (weekend/holiday)")
    return True, session, "fresh session"
