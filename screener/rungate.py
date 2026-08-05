"""Run gate — decides whether THIS invocation should produce an alert.

Why this exists
---------------
GitHub Actions cron is not a scheduler you can rely on for timing. Measured on
this repository: a `0 13 * * *` schedule actually executed at 18:21, 23:02,
20:31, 20:31, 22:13 and 22:03 UTC on consecutive days — between 5 and 10 hours
late, every day, at irregular times.

That is not merely cosmetic. Firing mid-session silently degrades the screen:
candidates are discovered from *today's* intraday movers while the analysis
correctly uses the last *completed* candle, so those names show no breakout and
are all rejected. That is precisely why US names disappeared from the alerts
while Asia and Gulf names survived.

The fix is to stop depending on when the job fires. The workflow now wakes up
often, and this gate decides whether the moment is actually suitable:

  1. If any covered market is still trading -> skip (data would be partial).
  2. If this exact set of sessions has already been alerted -> skip (no repeats).

So the alert lands as soon after the all-closed window opens as GitHub allows,
exactly once per session set, with every market complete.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import markets as mk

log = logging.getLogger(__name__)

STATE_FILE = os.environ.get("SCREENER_STATE_FILE", ".screener_state.json")


def _read_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(signature: str, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_signature": signature,
                       "last_alert_utc_date": now_utc.strftime("%Y-%m-%d"),
                       "last_alert_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")},
                      f, indent=2)
    except OSError as e:                                  # noqa: BLE001
        log.warning("could not persist run state: %s", e)


def should_run(cfg, now_utc: datetime | None = None) -> tuple[bool, str, str]:
    """Return (proceed, signature, reason).

    Dedup is keyed on the UTC DATE, not on the session set. There are two
    all-closed windows in a UTC day — around 12:00-13:00 UTC (the US has not
    opened, so it reports yesterday) and again after the US close from roughly
    20:00 UTC (the US reports today). Those are different session sets, so
    keying on sessions alone would alert twice daily. One alert per UTC day,
    earliest suitable window wins, which lands in the Dubai late afternoon.
    """
    now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
    signature = mk.session_signature(cfg.markets, now_utc)

    if cfg.force_run:
        return True, signature, "forced"

    still_open = mk.open_markets(cfg.markets, now_utc)
    if still_open:
        return (False, signature,
                f"{', '.join(still_open)} still trading — waiting for the "
                f"all-closed window so every market has a completed candle")

    state = _read_state()
    if state.get("last_alert_utc_date") == now_utc.strftime("%Y-%m-%d"):
        return False, signature, "already alerted today"
    if state.get("last_signature") == signature:
        return False, signature, "these sessions have already been alerted"

    return True, signature, "all markets closed; sessions not yet reported"
