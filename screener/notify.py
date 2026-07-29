"""Telegram delivery + institutional-style alert formatting.

Design goal: a trader must be able to judge a setup in under 10 seconds. Each
entry leads with the verdict (grade, score, confidence), then the trade plan
(entry/stop/targets/R:R), then the evidence, then the one-paragraph thesis.
Telegram caps messages at 4096 chars, so long screens are split rather than
silently truncated.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

MAX_LEN = 3900
GRADE_ICON = {"A": "🟢", "B": "🟡", "C": "⚪"}


def _n(x, dash="—"):
    try:
        v = float(x)
        return dash if not np.isfinite(v) else (f"{v:,.2f}" if abs(v) < 10000 else f"{v:,.0f}")
    except (TypeError, ValueError):
        return dash if x in (None, "") else str(x)


def _md(s):
    """Escape Telegram Markdown control characters in free text."""
    return str(s).replace("*", "").replace("_", "").replace("`", "").replace("[", "(")


def format_header(session, regime, scanned, counts) -> str:
    return (
        f"*Breakout Screen — {session}*\n"
        f"*Market:* {_md(regime.get('label'))}"
        + (f" · VIX {regime['vix']:.1f}" if np.isfinite(_num(regime.get('vix'))) else "")
        + f"\n_Scanned {scanned} · {counts.get('A', 0)} A-grade, "
          f"{counts.get('B', 0)} B-grade, {counts.get('W', 0)} watching_\n"
    )


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def format_setup(r) -> str:
    """One setup, structured so the eye lands on verdict -> levels -> reasoning."""
    icon = GRADE_ICON.get(r["grade"], "⚪")
    name = _md(r.get("company") or r["symbol"])
    base = _md(r["pattern"])
    if r.get("base_weeks"):
        base = f"{r['base_weeks']}-week {base.lower()}"

    lines = [
        f"{icon} *{r['symbol']}* — {name}  ({r['grade']} · {r['score']:.0f}/100)",
        f"   _{_md(r.get('exchange') or r.get('market'))}"
        + (f" · {_md(r.get('sector'))}" if r.get("sector") else "") + "_",
        f"   *Setup:* {base} · {_md(r['stage_name'])}",
        f"   *Confirmation:* {_n(r['vol_ratio'])}x volume · RS {_n(r['rs_rating'])}/100"
        + (" · OBV confirming" if r.get("obv_confirm") else ""),
        f"   *Trend:* weekly {r['weekly_trend'].lower()} · above 50/200-day"
        if r.get("above_50d") and r.get("above_200d") else
        f"   *Trend:* weekly {r['weekly_trend'].lower()}",
        f"   *Levels:* entry `{_n(r['entry'])}` · stop `{_n(r['stop'])}` · "
        f"targets `{_n(r['target1'])}` / `{_n(r['target2'])}` · *R:R {_n(r['rr'])}:1*",
        f"   *Fails below* `{_n(r['failure_level'])}`",
        f"   _{_md(r['thesis'])}_",
    ]
    return "\n".join(lines)


def format_watch(r) -> str:
    """One line per watchlist name — setup identified, trigger not yet given."""
    gap = abs(_num(r.get("ext_from_pivot_pct")) or 0)
    base = f"{r['base_weeks']}w {_md(r['pattern'])}" if r.get("base_weeks") else _md(r["pattern"])
    return (f"   ◦ *{r['symbol']}* — {base} · pivot `{_n(r['pivot'])}` "
            f"({gap:.1f}% away) · RS {_n(r['rs_rating'])}")


def build_messages(session, regime, scanned, rows, cfg) -> list[str]:
    counts = {}
    for r in rows:
        counts[r["grade"]] = counts.get(r["grade"], 0) + 1

    alerts = [r for r in rows if r["grade"] in (("A", "B") if cfg.include_grade_b
                                                else ("A",))][: cfg.max_alerts]
    watch = [r for r in rows if r["grade"] == "W"][: cfg.max_watch]

    header = format_header(session, regime, scanned, counts)

    msgs, cur = [], header
    if alerts:
        for r in alerts:
            block = "\n" + format_setup(r) + "\n"
            if len(cur) + len(block) > MAX_LEN:
                msgs.append(cur)
                cur = ""
            cur += block
    else:
        cur += ("\n*No confirmed breakouts today.*\n"
                "_Selectivity is the point — no forced trades._\n")

    if watch:
        block = ("\n*Approaching pivot* — set up, awaiting trigger:\n"
                 + "\n".join(format_watch(r) for r in watch) + "\n")
        if len(cur) + len(block) > MAX_LEN:
            msgs.append(cur)
            cur = ""
        cur += block

    cur += "\n_Technical analysis only — not investment advice._"
    msgs.append(cur)
    return msgs


# ------------------------------------------------------------------ transport
def send_message(text, cfg):
    if not (cfg.telegram_token and cfg.telegram_chat):
        log.info("Telegram not configured; skipping push.")
        return False
    import requests
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
            data={"chat_id": cfg.telegram_chat, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=30)
        if r.status_code != 200:
            log.warning("Telegram %s: %s", r.status_code, r.text[:300])
            # Markdown errors are common with odd tickers — retry as plain text.
            r = requests.post(
                f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
                data={"chat_id": cfg.telegram_chat, "text": text,
                      "disable_web_page_preview": True}, timeout=30)
        return r.status_code == 200
    except Exception as e:                               # noqa: BLE001
        log.error("Telegram send failed: %s", e)
        return False


def send_document(path, caption, cfg):
    if not (cfg.telegram_token and cfg.telegram_chat):
        return False
    import requests
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{cfg.telegram_token}/sendDocument",
                data={"chat_id": cfg.telegram_chat, "caption": caption[:1000]},
                files={"document": f}, timeout=60)
        return r.status_code == 200
    except Exception as e:                               # noqa: BLE001
        log.error("Telegram document failed: %s", e)
        return False
