"""No-database, no-network regression table for the exchange_listings category (Phase 4, 2026-11-15).

    python scripts/check_exchange_listings.py        # exits 1 on any mismatch

Covers the parts where a quiet mistake is easy and expensive: the STRICT title filter (a perpetual or a
tokenized stock must never become a listing card), token identity across exchanges (Binance calls Gensyn
"AIGENSYN", OKX calls it "AI"), the prior-listing computation (a re-announcement is not a second listing; ordering
is by announcement time), the scorer, and the output guard (no price talk, no contradictory prior-listing claim,
the prior-listing fact in the title or first two sentences). Titles below are real, from the 2026-11-15 history.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import exchange_listings as L  # noqa: E402
from pipeline.score import SCORERS  # noqa: E402,F401  (import registers the scorers)
from pipeline.score import score_exchange_listings  # noqa: E402
from pipeline.write_post import _find_listing_issues  # noqa: E402

FAILS: list[str] = []


def check(label: str, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def syms(exchange: str, title: str) -> list[str]:
    return [t["symbol"] for t in L.parse_listing(exchange, title)]


def main():
    # ---- strict title filter: real spot listings in, everything else out
    for ex, title, want in [
        ("binance", "Binance Will List Predict (PREDICT) with Seed Tag Applied", ["PREDICT"]),
        ("binance", "Binance Will List Genius Terminal (GENIUS) and OpenGradient (OPG) with Seed Tag Applied", ["GENIUS", "OPG"]),
        ("binance", "Binance Will List RWUSD and Introduce RWUSD Zero Trading Fee Promotion", ["RWUSD"]),
        ("binance", "Binance Will List Ripple USD (RLUSD) and Introduce RLUSD Zero Trading Fee Promotion", ["RLUSD"]),
        ("binance", "Binance Will List 牛来 (牛来) with Seed Tag Applied", ["牛来"]),
        ("binance", "Binance Futures Will Launch USDⓈ-Margined HYPEUSDC and TRXUSDC Perpetual Contracts (2026-11-16)", []),
        ("binance", "Binance Will Add 5 bStocks Tokenized Securities as Collateral Asset - 2026-10-28", []),
        ("binance", "Binance Exchange Adds Applovin Corp (APPB), Bending Spoons (BSPB)", []),
        ("binance", "Introducing Opinion (OPN) on Binance Launchpool! Farm OPN by Locking BNB, USDC, U, and USD1", []),
        ("binance", "Tether Gold (XAUt) Listing Will Be Postponed", []),
        ("binance", "Notice on New Trading Pairs & Trading Bots Services on Binance Spot", []),
        ("okx", "OKX to list CT/USDT (Concrete) for spot trading", ["CT"]),
        ("okx", "OKX to list MEGA (MegaETH) for spot trading", ["MEGA"]),
        ("okx", "OKX to list USDS for spot trading", ["USDS"]),
        ("okx", "OKX to list SEI (Sei), 2Z (DoubleZero) for spot trading", ["SEI", "2Z"]),
        ("okx", "OKX will launch AERO/USDC for spot trading", []),
        ("okx", "OKX to list Unified Tokenized Stocks xCEG, xCVNA and more for spot trading", []),
        ("okx", "OKX to list ONDSUSD and DDOGUSD Equity X-Perps", []),
        ("okx", "OKX to list TradFi perpetual futures for XLV, EIDO and EWZS", []),
        ("okx", "OKX completed VELODROME token migration", []),
        ("bybit", "Bybit to List KOR Protocol (KOR) on Spot", ["KOR"]),
        ("bybit", "Bybit to list Based (BASED) on Spot", ["BASED"]),
        ("bybit", "Bybit to List   ARCIUM (ARX) on Spot", ["ARX"]),
        ("bybit", "New listing: OROCHIUSDT Perpetual Contract in Innovation Zone, with up to 50x leverage", []),
        ("bybit", "New listing: Listing of FNUSDT on Bybit TradFi Perpetual on Nov 6, 2026, 11:00AM UTC", []),
        ("bybit", "[Selected regions only] New listing: GRVT/USDT | Grab a share of the 2,500,000 GRVT prize pool", []),
        ("kucoin", "World Premiere: Matrix (MTX) Listed on KuCoin", ["MTX"]),
        ("kucoin", "HODLer Airdrops: Kor Protocol (KOR) World Premiere Listing on KuCoin", ["KOR"]),
        ("kucoin", "Ritual (RITUAL) Listed on KuCoin", ["RITUAL"]),
        ("kucoin", "KuCoin Futures Will List Multiple Stock Index Perpetual Contracts (2026-11-13)", []),
        ("kucoin", "KuCoin Spot to List Tokenized Stocks: AMZNX, COINX, INTCX, METAX and NVDAX", []),
    ]:
        check(f"parse {ex}: {title[:60]!r}", syms(ex, title), want)
    check("kucoin world-premiere flag", [t["flags"] for t in L.parse_listing("kucoin", "World Premiere: Matrix (MTX) Listed on KuCoin")],
          [["World Premiere (KuCoin's own label)"]])
    check("binance seed-tag flag", L.parse_listing("binance", "Binance Will List Predict (PREDICT) with Seed Tag Applied")[0]["flags"], ["Seed Tag applied"])

    # ---- token identity across exchanges
    def tok(name, sym):
        return {"token_name": name, "symbol": sym}
    for a, b, want in [
        (tok("Gensyn", "AIGENSYN"), tok("Gensyn", "AI"), True),            # different symbols, same name
        (tok("Fabric Protocol", "ROBO"), tok("Fabric Protocol", "ROBO"), True),
        (tok("Kor Protocol", "KOR"), tok("KOR Protocol", "KOR"), True),
        (tok("Chip", "CHIP"), tok("USD.AI", "CHIP"), True),                 # same 4-letter symbol, different display names
        (tok("Canton Network", "CC"), tok("Canton", "CC"), True),
        (tok("Pons", "PONS"), tok("Pons Family", "PONS"), True),
        (tok("Aerodrome", "AERO"), tok("Aerodrome", "AERO"), True),
        (tok("Some Token", "AI"), tok("Gensyn", "AI"), False),              # 2-letter symbol alone is not enough when both have names
        (tok("Sonic", "S"), tok("Sonic SVM", "SONIC"), False),
        (tok("Meteora", "MET"), tok("Metis", "METIS"), False),
        (tok("USDS", "USDS"), tok("USDS", "USDS"), True),                   # name == symbol carries no name information; the 4-letter symbol matches
    ]:
        check(f"same_token {a} vs {b}", L.same_token(a, b), want)

    # ---- prior listings: ordering, per-exchange, re-announcements
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def cand(ex, name, sym, days, ext=None):
        ts = t0 + timedelta(days=days)
        return {"external_id": ext or f"{ex}:{sym}:{days}", "exchange_key": ex, "exchange": L.DISPLAY[ex], "token_name": name,
                "symbol": sym, "ts_ms": int(ts.timestamp() * 1000), "published_at": ts.isoformat(), "flags": []}
    batch = [
        cand("bybit", "KOR Protocol", "KOR", 1),
        cand("kucoin", "Kor Protocol", "KOR", 2),
        cand("bybit", "KOR Protocol", "KOR", 3),          # Bybit announcing the SAME token again two days later
        cand("okx", "Kor Protocol", "KOR", 40),           # 40 days on: a real third listing
        cand("binance", "Predict", "PREDICT", 5),
    ]
    L.attach_prior_listings(batch, [])
    check("first listing has no prior", batch[0]["prior_count"], 0)
    check("second exchange sees the first", [p["exchange"] for p in batch[1]["prior_listings"]], ["Bybit"])
    check("same-exchange repeat within 30d is a duplicate", batch[2]["duplicate"], True)
    check("a genuine new exchange is not a duplicate", batch[3]["duplicate"], False)
    check("later listing sees one line per exchange", [p["exchange"] for p in batch[3]["prior_listings"]], ["Bybit", "KuCoin"])
    check("unrelated token has no prior", batch[4]["prior_count"], 0)
    # the database's own rows count as history
    known = [dict(cand("binance", "Gensyn", "AIGENSYN", 0, "binance:x:aigensyn"))]
    c = [cand("okx", "Gensyn", "AI", 1)]
    L.attach_prior_listings(c, known)
    check("prior found from the database", c[0]["prior_count"], 1)

    # ---- trading start
    check("kucoin trading start", L._kucoin_trading_start("Trading: 11:00 on November 11, 2026 (UTC)"), "2026-11-11T11:00:00+00:00")
    check("kucoin no time", L._kucoin_trading_start("KuCoin Futures Will List Multiple Stock Index Perpetual Contracts (2026-11-13)"), None)

    # ---- scorer: tier x prior listings x trading start
    now = datetime.now(timezone.utc)

    def score(ex, prior, start=True, age_h=0.0):
        p = {"exchange_key": ex, "prior_count": prior, "published_at": (now - timedelta(hours=age_h)).isoformat(),
             "trading_start": (now + timedelta(hours=2)).isoformat() if start else None}
        b = score_exchange_listings(None, 1, p)
        return round(0.35 * b["impact"] + 0.35 * b["novelty"] + 0.10 * b["credibility"] + 0.20 * b["actionability"], 1)
    check("binance first listing, trading ahead", score("binance", 0), 100.0)
    check("kucoin first listing, trading ahead", score("kucoin", 0), 84.2)
    check("a first listing on a lower tier outranks a repeat on a higher one", score("kucoin", 0) > score("okx", 2), True)
    check("novelty decays with age", score("okx", 0, age_h=20) < score("okx", 0, age_h=1), True)
    check("kucoin with 3 prior listings and no stated start sits at the threshold", score("kucoin", 3, start=False) <= 51.0, True)

    # ---- output guard
    prior_one = {"prior_listings": [{"exchange": "Bybit"}], "trading_start": None}
    prior_none = {"prior_listings": [], "trading_start": None}
    with_start = {"prior_listings": [], "trading_start": "2026-11-11T11:00:00+00:00"}

    def issues(parsed, payload):
        return _find_listing_issues(parsed, payload)
    check("clean post with a prior listing",
          issues({"title": "OKX lists Canton (CC); Bybit listed Nov 7", "paragraphs": ["OKX listed Canton (CC). Bybit announced the token on Nov 7, so it was already listed on another tracked exchange."]}, prior_one), [])
    check("clean post with no prior listing",
          issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR Protocol (KOR) on Nov 2. No earlier listing appears among the other tracked exchanges."]}, prior_none), [])
    check("price talk is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges. The price could rally."]}, prior_none)), True)
    check("'debut' is rejected",
          bool(issues({"title": "Bybit lists KOR in a debut", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges."]}, prior_none)), True)
    check("'first' without 'tracked' is rejected",
          bool(issues({"title": "Bybit lists KOR first", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges."]}, prior_none)), True)
    check("'first among the tracked exchanges' is allowed",
          issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR, the first among the tracked exchanges to do so. No earlier listing appears among the other tracked exchanges."]}, prior_none), [])
    check("a prior exchange left out is rejected",
          bool(issues({"title": "OKX lists Canton (CC)", "paragraphs": ["OKX listed Canton (CC) on Feb 12 and the token was already listed elsewhere among the tracked exchanges."]}, prior_one)), True)
    check("prior listing buried after the second sentence is rejected",
          bool(issues({"title": "OKX lists Canton (CC)", "paragraphs": ["OKX listed Canton (CC). The listing was announced on Feb 12. The announcement came after a long period. Bybit had announced it on Nov 7."]}, prior_one)), True)
    check("'no earlier listing' when the record shows one is rejected",
          bool(issues({"title": "OKX lists Canton (CC); Bybit listed Nov 7", "paragraphs": ["Bybit announced it on Nov 7, yet no earlier listing exists among the tracked exchanges."]}, prior_one)), True)
    check("'already listed' when the record shows none is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR, already listed on the tracked exchanges. No earlier listing appears among the tracked exchanges."]}, prior_none)), True)
    check("a trading time nobody stated is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges. Trading opens soon."]}, prior_none)), True)
    check("a stated trading time is fine",
          issues({"title": "KuCoin lists Matrix (MTX)", "paragraphs": ["KuCoin listed Matrix (MTX). No earlier listing appears among the other tracked exchanges. Trading opens Nov 11 at 11:00 UTC."]}, with_start), [])

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all exchange_listings cases behave as expected")


if __name__ == "__main__":
    main()
