"""Candidate universe construction across US (NYSE/NASDAQ/AMEX) and ASX.

NOTE ON A GAP FOUND IN REVIEW: the previous code claimed ASX coverage but could
never deliver it — `keep()` rejected any symbol containing a dot, and every ASX
ticker on Yahoo is suffixed `.AX` (e.g. BHP.AX). ASX was therefore silently
100% filtered out. Exchange handling is now explicit per market.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

US_EXCHANGE_TOKENS = ("nasdaq", "nyse", "nms", "ngm", "ncm", "nyq", "ase",
                      "american", "amex")
# Yahoo exchange labels seen for ASX listings.
ASX_EXCHANGE_TOKENS = ("asx", "australian")

# Structures that are not single operating companies; we screen equities only.
EXCLUDE_TOKENS = ("arca", "cboe", "otc", "pink")


class Candidate:
    __slots__ = ("symbol", "market", "exchange", "price", "change_pct", "volume", "quote")

    def __init__(self, symbol, market, exchange, price, change_pct, volume, quote=None):
        self.symbol = symbol
        self.market = market
        self.exchange = exchange or ""
        self.price = price
        self.change_pct = change_pct
        self.volume = volume
        self.quote = quote or {}

    def __repr__(self):
        return f"<{self.symbol} {self.market} {self.change_pct:.1f}%>"


def _passes_basic(sym, exch, price, chg, vol, market, cfg):
    if not sym:
        return False
    e = (exch or "").lower()
    if any(t in e for t in EXCLUDE_TOKENS):
        return False
    if market == "US":
        # US symbols are dot-free apart from class shares (BF.B) which Yahoo
        # writes as BF-B; anything else with a dot is a foreign line.
        if "." in sym:
            return False
        if e and not any(t in e for t in US_EXCHANGE_TOKENS):
            return False
    else:  # ASX
        if not sym.upper().endswith(".AX"):
            return False
    if price is None or price < cfg.min_price:
        return False
    if chg is not None and chg < cfg.min_change_pct:
        return False
    if price and vol and price * vol < cfg.min_dollar_vol:
        return False
    return True


def _collect(quotes, market, cfg, sink):
    kept = 0
    for q in quotes:
        sym = q.get("symbol")
        exch = q.get("fullExchangeName") or q.get("exchange")
        price = q.get("regularMarketPrice")
        chg = q.get("regularMarketChangePercent")
        vol = q.get("regularMarketVolume")
        try:
            price = float(price) if price is not None else None
            chg = float(chg) if chg is not None else None
            vol = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            continue
        if _passes_basic(sym, exch, price, chg, vol, market, cfg):
            sink[sym] = Candidate(sym, market, exch, price, chg, vol, q)
            kept += 1
    return kept


def fetch_us(cfg):
    """US movers via the Yahoo predefined screener, with an EquityQuery fallback."""
    import yfinance as yf
    found: dict[str, Candidate] = {}
    size = min(max(cfg.scan_limit * 3, 100), 250)

    try:
        res = yf.screen("day_gainers", count=size)
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        n = _collect(quotes, "US", cfg, found)
        log.info("US day_gainers: %d raw -> %d kept", len(quotes), n)
    except Exception as e:                               # noqa: BLE001
        log.warning("US day_gainers failed: %s", e)

    if not found:
        try:
            from yfinance import EquityQuery
            q = EquityQuery("and", [
                EquityQuery("gt", ["percentchange", cfg.min_change_pct]),
                EquityQuery("gt", ["dayvolume", 200_000]),
                EquityQuery("eq", ["region", "us"]),
            ])
            res = yf.screen(q, sortField="percentchange", sortAsc=False, size=size)
            n = _collect(res.get("quotes", []) if isinstance(res, dict) else [],
                         "US", cfg, found)
            log.info("US EquityQuery fallback -> %d kept", n)
        except Exception as e:                           # noqa: BLE001
            log.warning("US EquityQuery fallback failed: %s", e)
    return found


def fetch_asx(cfg):
    """ASX movers via EquityQuery region=au (the predefined screener is US-only)."""
    import yfinance as yf
    found: dict[str, Candidate] = {}
    size = min(max(cfg.scan_limit * 2, 100), 250)
    try:
        from yfinance import EquityQuery
        q = EquityQuery("and", [
            EquityQuery("gt", ["percentchange", cfg.min_change_pct]),
            EquityQuery("gt", ["dayvolume", 100_000]),
            EquityQuery("eq", ["region", "au"]),
        ])
        res = yf.screen(q, sortField="percentchange", sortAsc=False, size=size)
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        n = _collect(quotes, "ASX", cfg, found)
        log.info("ASX EquityQuery: %d raw -> %d kept", len(quotes), n)
    except Exception as e:                               # noqa: BLE001
        log.warning("ASX screen failed (continuing US-only): %s", e)
    return found


def build(cfg):
    """Return the ranked candidate list across all enabled markets."""
    found: dict[str, Candidate] = {}
    if cfg.include_us:
        found.update(fetch_us(cfg))
    if cfg.include_asx:
        found.update(fetch_asx(cfg))

    ranked = sorted(found.values(), key=lambda c: (c.change_pct or 0), reverse=True)
    return ranked[: cfg.scan_limit]
