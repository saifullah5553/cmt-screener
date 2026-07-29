"""Pipeline orchestration: gate → universe → data → regime → analyse → alert."""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from . import analysis, marketdata, notify, regime as regime_mod, sessions, strength, universe
from .config import CONFIG

log = logging.getLogger("screener")

CSV_PATH = "cmt_breakout_setups.csv"

CSV_COLUMNS = [
    "symbol", "company", "exchange", "market", "sector", "industry",
    "grade", "score", "stage_name", "pattern", "base_weeks", "vcp_contractions",
    "trend_score", "base_score", "volume_score", "rs_rating", "sector_label",
    "weekly_trend", "monthly_trend", "higher_highs", "higher_lows",
    "above_50d", "above_200d", "vol_ratio", "obv_confirm", "volume_dryup",
    "acc_days", "dist_days", "pivot", "range_pos", "ext_from_pivot_pct",
    "gap_pct", "overhead_supply", "entry", "stop", "swing_stop",
    "failure_level", "target1", "target2", "rr", "atr", "atr_pct",
    "risk_per_share", "position_shares", "regime", "disqualifiers", "thesis",
]

GRADE_ORDER = {"A": 0, "B": 1, "W": 2, "C": 3}


def setup_logging(level="INFO"):
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S")


def run(cfg=None) -> int:
    cfg = cfg or CONFIG
    setup_logging(cfg.log_level)
    started = datetime.now()

    ok, session, reason = sessions.should_run(force=cfg.force_run)
    log.info("Session gate: %s (target session=%s)", reason, session)
    if not ok:
        log.info("Nothing to do. Exiting cleanly.")
        return 0

    # ---- 1. universe -------------------------------------------------
    cands = universe.build(cfg)
    log.info("Universe: %d candidates across markets", len(cands))
    if not cands:
        notify.send_message(
            f"*CMT Breakout Screen — {session}*\nNo candidates returned "
            f"(upstream data issue). No analysis run.", cfg)
        return 0

    quotes = {c.symbol: c.quote for c in cands}
    markets = {c.symbol: c.market for c in cands}
    symbols = [c.symbol for c in cands]

    # ---- 2. data (candidates + benchmarks + sector ETFs + indices) ----
    aux = list(dict.fromkeys(
        [cfg.benchmark, cfg.asx_benchmark] + cfg.sector_etfs + regime_mod.INDEX_SYMBOLS))
    frames = marketdata.download_ohlcv(symbols + aux, period=cfg.history_period,
                                       quotes=quotes)
    log.info("Downloaded %d/%d frames", len(frames), len(symbols) + len(aux))

    # ---- 3. regime ----------------------------------------------------
    try:
        reg = regime_mod.assess({s: frames.get(s) for s in regime_mod.INDEX_SYMBOLS})
    except Exception as e:                               # noqa: BLE001
        log.warning("Regime assessment failed (%s); using neutral", e)
        reg = regime_mod.default()
    log.info("Regime: %s | strictness x%.2f", reg["summary"], reg["strictness"])

    # ---- 4. relative strength + sector leadership ---------------------
    benches = {"US": frames.get(cfg.benchmark), "ASX": frames.get(cfg.asx_benchmark)}
    tradables = {s: frames[s] for s in symbols if s in frames}
    rs = strength.rate_universe(tradables, benches, markets)
    sector_ranks = strength.rank_sectors(
        {e: frames[e] for e in cfg.sector_etfs if e in frames}, frames.get(cfg.benchmark))
    log.info("Ranked %d sector ETFs", len(sector_ranks))

    meta = _fetch_meta(symbols)

    def sector_of(sym):
        return strength.sector_score((meta.get(sym) or {}).get("sector"), sector_ranks)

    ctx = {"cfg": cfg, "regime": reg, "rs": rs, "sector_of": sector_of}

    # ---- 5. analyse ----------------------------------------------------
    rows, skipped = [], []
    for sym in symbols:
        df = frames.get(sym)
        good, why = marketdata.validate(df, cfg.min_bars)
        if not good:
            skipped.append((sym, why))
            continue
        stale = marketdata.freshness(df, session)
        if stale > 4:
            skipped.append((sym, f"stale data ({stale}d)"))
            continue
        try:
            row = analysis.analyse(sym, df, marketdata.to_weekly(df),
                                   marketdata.to_monthly(df), ctx)
        except Exception as e:                           # noqa: BLE001
            log.debug("%s: analysis failed (%s)", sym, e)
            skipped.append((sym, f"analysis error: {e}"))
            continue
        row.update(meta.get(sym, {}))
        row.setdefault("exchange", markets.get(sym))
        rows.append(row)

    log.info("Analysed %d symbols (%d skipped)", len(rows), len(skipped))
    if skipped:
        log.debug("Skipped: %s", skipped[:15])

    if not rows:
        notify.send_message(f"*CMT Breakout Screen — {session}*\n"
                            f"No analysable symbols today.", cfg)
        return 0

    rows.sort(key=lambda r: (GRADE_ORDER.get(r["grade"], 9), -r["score"]))

    # ---- 6. persist + alert --------------------------------------------
    df_out = pd.DataFrame(rows)
    for c in CSV_COLUMNS:
        if c not in df_out.columns:
            df_out[c] = None
    df_out[CSV_COLUMNS].to_csv(CSV_PATH, index=False)

    counts = df_out["grade"].value_counts().to_dict()
    log.info("Grades: %s", counts)
    _log_table(rows)

    msgs = notify.build_messages(session, reg, len(cands), rows, cfg)
    for m in msgs:
        notify.send_message(m, cfg)
    if any(r["grade"] in ("A", "B", "W") for r in rows):
        notify.send_document(CSV_PATH, f"CMT institutional screen — {session}", cfg)

    log.info("Done in %.1fs", (datetime.now() - started).total_seconds())
    return 0


def _log_table(rows, n=25):
    cols = ["symbol", "grade", "score", "stage_name", "pattern", "base_weeks",
            "rs_rating", "vol_ratio", "rr", "entry", "stop", "target1",
            "disqualifiers"]
    df = pd.DataFrame(rows)[: n]
    avail = [c for c in cols if c in df.columns]
    if len(df):
        log.info("Top setups:\n%s", df[avail].to_string(index=False))


def _fetch_meta(symbols, max_workers=8):
    """Company / sector / industry labels. Best-effort: never blocks the run."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf

    out = {}

    def one(sym):
        try:
            info = yf.Ticker(sym).get_info()
            return sym, {
                "company": info.get("shortName") or info.get("longName") or sym,
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "exchange": info.get("fullExchangeName") or info.get("exchange"),
            }
        except Exception:                                # noqa: BLE001
            return sym, {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(one, s) for s in symbols]
        for f in as_completed(futs):
            try:
                sym, d = f.result()
                if d:
                    out[sym] = d
            except Exception:                            # noqa: BLE001
                continue
    return out
