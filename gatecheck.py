#!/usr/bin/env python3
"""Cheap pre-flight gate for CI. Standard library only — no pandas, no yfinance.

GitHub's scheduler is not punctual (this repo has seen 5-10 hour delays), so the
workflow wakes up many times a day and lets the run gate pick a suitable moment.
Running that decision BEFORE `pip install` keeps a skipped wake-up down to a few
seconds instead of a full dependency install, which is what makes frequent
wake-ups affordable — and frequent wake-ups are what get delivery close to the
17:00 Dubai target without any external service.

Writes `proceed=true|false` to $GITHUB_OUTPUT and always exits 0.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from screener.config import CONFIG          # noqa: E402
from screener import rungate                # noqa: E402


def main() -> int:
    now = datetime.now(ZoneInfo("UTC"))
    dubai = now.astimezone(ZoneInfo("Asia/Dubai"))
    proceed, _signature, reason = rungate.should_run(CONFIG, now)

    print(f"now: {now:%Y-%m-%d %H:%M} UTC  ({dubai:%H:%M} Dubai)")
    print(f"target: not before {CONFIG.run_not_before_utc} UTC")
    print(f"decision: {'RUN' if proceed else 'SKIP'} — {reason}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        try:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"proceed={'true' if proceed else 'false'}\n")
        except OSError as e:
            # Never fail the job over the handshake file. Emitting nothing
            # leaves `proceed` empty, which the workflow treats as "skip" —
            # the safe direction, since a missed wake-up is harmless while a
            # mid-session run would produce a degraded alert.
            print(f"warning: could not write GITHUB_OUTPUT ({e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
