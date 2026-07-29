"""Market registry — one definition per exchange.

The engine (stage analysis, base detection, relative strength, risk) is
market-agnostic. Everything that genuinely differs between exchanges lives
here: ticker suffix, timezone, session hours, trading week, benchmark index and
how candidates are discovered.

TIMING RULE (the important one)
-------------------------------
The job runs once, at 09:15 Dubai = 05:15 UTC. At that moment some of these
markets are OPEN:

    ASX      00:00-06:00 UTC   open (closes 06:00)
    NSE/BSE  03:45-10:00 UTC   open
    PSX      04:30-10:30 UTC   open

An open market has no completed candle for today, only a partial one. We
therefore always analyse each market's **last fully completed session**, which
for an open market means yesterday. Partial bars are never analysed and never
repaired from a live quote — see marketdata.repair_last_bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Gulf exchanges trade Sunday-Thursday; the rest trade Monday-Friday.
MON_FRI = (5, 6)        # Saturday, Sunday closed
FRI_SAT = (4, 5)        # Friday, Saturday closed


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    tz: str
    open_t: time
    close_t: time
    weekend: tuple                 # weekday numbers that are NOT trading days
    suffix: str = ""               # Yahoo ticker suffix ("" for US)
    benchmark: str | None = None   # index for relative strength, None = cohort-only
    region: str | None = None      # Yahoo screener region code, None = no discovery
    universe: tuple = ()           # static tickers when discovery is unavailable
    exchange_tokens: tuple = ()    # substrings expected in Yahoo's exchange label
    currency: str = ""

    # -- session helpers -------------------------------------------------
    def now_local(self, now_utc: datetime | None = None) -> datetime:
        now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
        return now_utc.astimezone(ZoneInfo(self.tz))

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend

    def prev_trading_day(self, d: date):
        p = d - timedelta(days=1)
        for _ in range(12):
            if self.is_trading_day(p):
                return p
            p -= timedelta(days=1)
        return None

    def is_open(self, now_utc: datetime | None = None) -> bool:
        loc = self.now_local(now_utc)
        return (self.is_trading_day(loc.date())
                and self.open_t <= loc.time() < self.close_t)

    def last_completed_session(self, now_utc: datetime | None = None):
        """The most recent session that has FULLY closed.

        If we are mid-session (or before today's close) the answer is the
        previous trading day — never a partial candle.
        """
        loc = self.now_local(now_utc)
        d = loc.date()
        if self.is_trading_day(d) and loc.time() >= self.close_t:
            return d
        return self.prev_trading_day(d)


# --------------------------------------------------------------- registry
MARKETS: dict[str, Market] = {
    "US": Market(
        code="US", name="NYSE / NASDAQ", tz="America/New_York",
        open_t=time(9, 30), close_t=time(16, 0), weekend=MON_FRI,
        benchmark="SPY", region="us",
        exchange_tokens=("nasdaq", "nyse", "nms", "ngm", "ncm", "nyq", "ase",
                         "american", "amex"),
        currency="USD"),

    "AU": Market(
        code="AU", name="ASX", tz="Australia/Sydney",
        open_t=time(10, 0), close_t=time(16, 0), weekend=MON_FRI,
        suffix=".AX", benchmark="^AXJO", region="au",
        exchange_tokens=("asx", "australian"), currency="AUD"),

    "IN": Market(
        code="IN", name="NSE / BSE India", tz="Asia/Kolkata",
        open_t=time(9, 15), close_t=time(15, 30), weekend=MON_FRI,
        suffix=".NS", benchmark="^NSEI", region="in",
        exchange_tokens=("nse", "bse"), currency="INR"),

    # Yahoo's screener returns nothing for Pakistan, so the universe is static.
    # Every ticker below was verified to return >=200 usable daily bars AND at
    # least 20m PKR of average daily turnover. Names that look obvious but do
    # NOT work on Yahoo: ENGRO, FFBL, THALL, COLG, NESTLE, BWCL, AGL, PSEL.
    "PK": Market(
        code="PK", name="Pakistan Stock Exchange", tz="Asia/Karachi",
        open_t=time(9, 30), close_t=time(15, 30), weekend=MON_FRI,
        suffix=".KA", benchmark=None, region=None,
        exchange_tokens=("karachi", "pakistan"), currency="PKR",
        universe=(
            # large caps / index heavyweights
            "OGDC.KA", "HBL.KA", "LUCK.KA", "PPL.KA", "MCB.KA", "UBL.KA",
            "FFC.KA", "PSO.KA", "MARI.KA", "MEBL.KA", "BAHL.KA", "NBP.KA",
            "HUBC.KA", "DGKC.KA", "EFERT.KA", "POL.KA", "SYS.KA", "TRG.KA",
            "INDU.KA", "SEARL.KA", "AKBL.KA", "CHCC.KA", "KAPCO.KA", "NML.KA",
            # banks & financials
            "BOP.KA", "FABL.KA", "SNBL.KA", "JSBL.KA",
            # energy & refining
            "ATRL.KA", "NRL.KA", "SNGP.KA", "SSGC.KA", "APL.KA",
            # cement & construction
            "MLCF.KA", "FCCL.KA", "PIOC.KA", "KOHC.KA", "ACPL.KA",
            # chemicals & fertiliser
            "LOTCHEM.KA", "FATIMA.KA", "EPCL.KA", "ICI.KA", "AGP.KA",
            # steel & engineering
            "MUGHAL.KA", "ASTL.KA", "ISL.KA", "GHNI.KA", "GHGL.KA", "TGL.KA",
            # technology & telecom
            "NETSOL.KA", "AVN.KA", "PTC.KA", "WTL.KA", "OCTOPUS.KA", "AIRLINK.KA",
            # pharma & healthcare
            "GLAXO.KA", "HINOON.KA", "ABOT.KA", "SHFA.KA", "BIFO.KA",
            # textiles, autos & other
            "ILP.KA", "GATM.KA", "PAEL.KA", "TPLP.KA", "PACE.KA", "UNITY.KA",
            # autos & engineering
            "MTL.KA", "HCAR.KA", "SAZEW.KA", "GHNL.KA", "GTYR.KA", "AGTL.KA",
            # power & utilities
            "POWER.KA", "NPL.KA", "NCPL.KA", "KEL.KA",
            # insurance & investment
            "AICL.KA", "IGIHL.KA", "JSCL.KA", "BIPL.KA", "NICL.KA", "DAWH.KA",
            # consumer, textiles & mid-caps
            "PREMA.KA", "SRVI.KA", "TREET.KA", "IBLHL.KA", "NCL.KA",
            "KOSM.KA", "MSOT.KA", "ASC.KA",
        )),

    "SA": Market(
        code="SA", name="Saudi Tadawul", tz="Asia/Riyadh",
        open_t=time(10, 0), close_t=time(15, 0), weekend=FRI_SAT,
        suffix=".SR", benchmark="^TASI.SR", region="sa",
        exchange_tokens=("saudi", "tadawul"), currency="SAR"),

    "KW": Market(
        code="KW", name="Boursa Kuwait", tz="Asia/Kuwait",
        open_t=time(9, 0), close_t=time(12, 30), weekend=FRI_SAT,
        suffix=".KW", benchmark=None, region="kw",
        exchange_tokens=("kuwait",), currency="KWD"),

    "EG": Market(
        code="EG", name="EGX Egypt", tz="Africa/Cairo",
        open_t=time(10, 0), close_t=time(14, 30), weekend=FRI_SAT,
        suffix=".CA", benchmark=None, region="eg",
        exchange_tokens=("egx", "egypt", "cairo"), currency="EGP"),

    # Discovery returns nothing for UAE/Qatar -> static universes.
    # UAE list is the subset verified to return usable history (many Yahoo
    # .AE tickers, including FAB and ADCB, return nothing at all).
    "AE": Market(
        code="AE", name="DFM / ADX (UAE)", tz="Asia/Dubai",
        open_t=time(10, 0), close_t=time(15, 0), weekend=FRI_SAT,
        suffix=".AE", benchmark=None, region=None,
        exchange_tokens=("dubai", "abu dhabi", "uae"), currency="AED",
        universe=("EMAAR.AE", "EMIRATESNBD.AE", "DIB.AE", "DFM.AE",
                  "AIRARABIA.AE", "EMAARDEV.AE", "SALIK.AE")),

    "QA": Market(
        code="QA", name="Qatar Exchange", tz="Asia/Qatar",
        open_t=time(9, 30), close_t=time(13, 15), weekend=FRI_SAT,
        suffix=".QA", benchmark=None, region=None,
        exchange_tokens=("qatar", "doha"), currency="QAR",
        universe=("QNBK.QA", "IQCD.QA", "MARK.QA", "QIBK.QA", "CBQK.QA",
                  "ORDS.QA", "QEWS.QA", "QFLS.QA", "BRES.QA", "MPHC.QA")),
}

DEFAULT_MARKETS = ("US", "AU", "IN", "PK", "SA", "KW", "EG", "AE", "QA")


def get(code: str) -> Market | None:
    return MARKETS.get(str(code).strip().upper())


def resolve(codes) -> list[Market]:
    """Turn a list of codes (or 'ALL') into Market objects, preserving order."""
    if not codes or (len(codes) == 1 and str(codes[0]).strip().upper() == "ALL"):
        codes = DEFAULT_MARKETS
    out = []
    for c in codes:
        m = get(c)
        if m and m not in out:
            out.append(m)
    return out


def market_of(symbol: str) -> str:
    """Infer the market code from a Yahoo ticker suffix."""
    s = str(symbol).upper()
    for m in MARKETS.values():
        if m.suffix and s.endswith(m.suffix):
            return m.code
    return "US"
