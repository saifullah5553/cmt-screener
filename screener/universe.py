"""Candidate universe construction across every configured market.

Two discovery modes, chosen per market in `markets.py`:

  1. SCREENER  - Yahoo returns the day's movers for the region (US, AU, IN,
                 SA, KW, EG). Cheap and broad.
  2. STATIC    - Yahoo's screener returns nothing for the region (PK, AE, QA),
                 so a curated ticker list is scanned in full. Viable because a
                 few hundred symbols download in seconds; the quality filters
                 downstream are identical either way.

Historical note: the original code claimed ASX support but rejected any symbol
containing a dot — and every non-US Yahoo ticker is suffixed (.AX, .NS, .KA).
Exchange handling is now explicit per market.
"""
from __future__ import annotations

import logging

from . import markets as mk

log = logging.getLogger(__name__)

# Structures that are not single operating companies.
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
        return f"<{self.symbol} {self.market} {self.change_pct or 0:.1f}%>"


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _acceptable(sym, exch, price, chg, vol, market: mk.Market, cfg) -> bool:
    if not sym:
        return False
    e = (exch or "").lower()
    if any(t in e for t in EXCLUDE_TOKENS):
        return False
    if market.suffix:
        if not sym.upper().endswith(market.suffix):
            return False
    else:
        # US tickers carry no suffix; a dot means a foreign line.
        if "." in sym:
            return False
        if e and not any(t in e for t in market.exchange_tokens):
            return False
    if price is None or price < cfg.min_price:
        return False
    if chg is not None and chg < cfg.min_change_pct:
        return False
    # Liquidity is market-relative: a floor set for US dollars would erase
    # every PSX or EGX name. Applied later against local-currency turnover.
    if price and vol and price * vol < cfg.min_turnover_for(market.code):
        return False
    return True


def _collect(quotes, market: mk.Market, cfg, sink) -> int:
    kept = 0
    for q in quotes:
        sym = q.get("symbol")
        price = _num(q.get("regularMarketPrice"))
        chg = _num(q.get("regularMarketChangePercent"))
        vol = _num(q.get("regularMarketVolume"))
        exch = q.get("fullExchangeName") or q.get("exchange")
        if _acceptable(sym, exch, price, chg, vol, market, cfg):
            sink[sym] = Candidate(sym, market.code, exch, price, chg, vol, q)
            kept += 1
    return kept


def _discover_by_screener(market: mk.Market, cfg) -> dict:
    """Yahoo predefined/EquityQuery discovery for regions that support it."""
    import yfinance as yf
    found: dict[str, Candidate] = {}
    size = min(max(cfg.scan_limit * 3, 100), 250)

    # The US has a cleaner predefined screener than the generic region query.
    if market.code == "US":
        try:
            res = yf.screen("day_gainers", count=size)
            n = _collect(res.get("quotes", []) if isinstance(res, dict) else [],
                         market, cfg, found)
            log.info("%s day_gainers -> %d kept", market.code, n)
        except Exception as e:                           # noqa: BLE001
            log.warning("%s day_gainers failed: %s", market.code, e)
        if found:
            return found

    try:
        from yfinance import EquityQuery
        q = EquityQuery("and", [
            EquityQuery("gt", ["percentchange", cfg.min_change_pct]),
            EquityQuery("gt", ["dayvolume", 50_000]),
            EquityQuery("eq", ["region", market.region]),
        ])
        res = yf.screen(q, sortField="percentchange", sortAsc=False, size=size)
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        n = _collect(quotes, market, cfg, found)
        log.info("%s screener: %d raw -> %d kept", market.code, len(quotes), n)
    except Exception as e:                               # noqa: BLE001
        log.warning("%s screener failed: %s", market.code, e)
    return found


def _discover_static(market: mk.Market, cfg) -> dict:
    """Scan a curated list in full — used where Yahoo offers no discovery.

    No quote filtering is applied here: without a screener we have no reliable
    intraday change/volume, so every name is carried through and judged on its
    daily history by the same quality gates as everything else.
    """
    found = {sym: Candidate(sym, market.code, market.name, None, None, None, {})
             for sym in market.universe}
    log.info("%s static universe: %d symbols", market.code, len(found))
    return found


def build(cfg) -> list[Candidate]:
    """Assemble candidates for every enabled market."""
    all_found: dict[str, Candidate] = {}
    for market in mk.resolve(cfg.markets):
        try:
            found = (_discover_by_screener(market, cfg) if market.region
                     else _discover_static(market, cfg))
        except Exception as e:                           # noqa: BLE001
            log.warning("%s discovery failed entirely: %s", market.code, e)
            continue

        # Cap movers per market so one hot tape cannot crowd out the others.
        ranked = sorted(found.values(), key=lambda c: (c.change_pct or 0), reverse=True)
        limit = cfg.scan_limit if market.region else len(ranked)
        all_found.update({c.symbol: c for c in ranked[:limit]})

    return list(all_found.values())
