#!/usr/bin/env python3
"""Automated CMT-grade institutional breakout screener — entry point.

Pipeline (see screener/pipeline.py):
  1. SESSION GATE   Resolve the latest COMPLETED US session. The job fires at
                    09:15 Dubai (00:15-01:15 ET), so that is yesterday's candle.
                    Weekends/holidays self-skip.
  2. UNIVERSE       Day's movers across NYSE / NASDAQ / AMEX and ASX.
  3. DATA           Bulk daily OHLCV + weekly/monthly resample. Unsettled final
                    bars are REPAIRED from the quote endpoint (see marketdata).
  4. REGIME         S&P/Nasdaq/ASX trend, VIX and breadth set screening strictness.
  5. ANALYSIS       Stage analysis, VCP + base patterns, trend quality, volume
                    quality, relative strength, risk plan -> weighted 0-100 score.
  6. NOTIFY         Institutional-style Telegram note + full CSV.

Configuration is entirely environment-driven (screener/config.py).
Technical analysis only — not investment advice.
"""
from __future__ import annotations

import sys

from screener.pipeline import run


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        return 130
    except Exception:                                    # noqa: BLE001
        import logging
        logging.getLogger("screener").exception("Fatal error in screener run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
