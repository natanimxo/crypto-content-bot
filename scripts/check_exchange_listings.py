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
from pipeline.llm_providers.template import render_exchange_listings  # noqa: E402
from pipeline.post_facts import listing_record_window  # noqa: E402
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
    thin = {"Binance": "2024-09", "OKX": "2024-11", "Bybit": "2025-03", "KuCoin": "2026-03"}   # KuCoin's record is the short one
    prior_none = {"exchange": "Bybit", "prior_listings": [], "trading_start": None, "record_since": thin}
    with_start = {"exchange": "KuCoin", "prior_listings": [], "trading_start": "2026-11-11T11:00:00+00:00", "record_since": thin}

    def issues(parsed, payload):
        return _find_listing_issues(parsed, payload)
    check("clean post with a prior listing",
          issues({"title": "OKX lists Canton (CC); Bybit listed Nov 7", "paragraphs": ["OKX listed Canton (CC). Bybit announced the token on Nov 7, so it was already listed on another tracked exchange."]}, prior_one), [])
    check("clean post with no prior listing",
          issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR Protocol (KOR) on Nov 2. No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none), [])
    check("price talk is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges since Mar 2026. The price could rally."]}, prior_none)), True)
    check("'debut' is rejected",
          bool(issues({"title": "Bybit lists KOR in a debut", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none)), True)
    check("'first' without 'tracked' is rejected",
          bool(issues({"title": "Bybit lists KOR first", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none)), True)
    check("'first among the tracked exchanges' is allowed",
          issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR, the first among the tracked exchanges since Mar 2026 to do so. No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none), [])
    check("a prior exchange left out is rejected",
          bool(issues({"title": "OKX lists Canton (CC)", "paragraphs": ["OKX listed Canton (CC) on Feb 12 and the token was already listed elsewhere among the tracked exchanges."]}, prior_one)), True)
    check("prior listing buried after the second sentence is rejected",
          bool(issues({"title": "OKX lists Canton (CC)", "paragraphs": ["OKX listed Canton (CC). The listing was announced on Feb 12. The announcement came after a long period. Bybit had announced it on Nov 7."]}, prior_one)), True)
    check("'no earlier listing' when the record shows one is rejected",
          bool(issues({"title": "OKX lists Canton (CC); Bybit listed Nov 7", "paragraphs": ["Bybit announced it on Nov 7, yet no earlier listing exists among the tracked exchanges."]}, prior_one)), True)
    check("'already listed' when the record shows none is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR, already listed on the tracked exchanges. No earlier listing appears among the tracked exchanges."]}, prior_none)), True)
    check("a trading time nobody stated is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR. No earlier listing appears among the other tracked exchanges since Mar 2026. Trading opens soon."]}, prior_none)), True)
    check("a stated trading time is fine (and the window is the OTHER exchanges' latest start: Mar 2025 for a KuCoin listing)",
          issues({"title": "KuCoin lists Matrix (MTX)", "paragraphs": ["KuCoin listed Matrix (MTX). No earlier listing appears among the other tracked exchanges since Mar 2025. Trading opens Nov 11 at 11:00 UTC."]}, with_start), [])

    # ---- THIN RECORD: "no earlier listing" must carry its window, so it can never read as "never listed anywhere"
    check("the record window for a Bybit listing is KuCoin's start", listing_record_window(prior_none), "Mar 2026")
    check("the record window for a KuCoin listing is Bybit's start (KuCoin's own is not 'another exchange')", listing_record_window(with_start), "Mar 2025")
    check("'no earlier listing' without the window is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR Protocol (KOR). No earlier listing appears among the other tracked exchanges."]}, prior_none)), True)
    check("the WRONG window is rejected (Mar 2025 quoted for a record that starts Mar 2026)",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR Protocol (KOR). No earlier listing appears among the other tracked exchanges since Mar 2025."]}, prior_none)), True)
    check("'first' with 'tracked' but no 'since' is rejected",
          bool(issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit is the first tracked exchange to list KOR. No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none)), True)
    check("with an earlier listing on record, 'the first tracked announcement' is just the record's order and is allowed",
          issues({"title": "OKX lists Canton (CC); Bybit listed Nov 7", "paragraphs": ["OKX listed Canton (CC). Bybit made the first tracked announcement on Nov 7, so the token was already listed on another tracked exchange."]}, prior_one), [])
    check("'not already listed' under a NO record is not a contradiction",
          issues({"title": "Bybit lists KOR", "paragraphs": ["Bybit listed KOR Protocol (KOR). The token is not already listed elsewhere: no earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none), [])
    for phrase in ("Bybit newly listed KOR.", "KOR is a new token on Bybit.", "Bybit is the only tracked exchange with KOR.", "KOR is a sole listing."):
        check(f"{phrase!r} is rejected", bool(issues({"title": "Bybit lists KOR", "paragraphs": [phrase + " No earlier listing appears among the other tracked exchanges since Mar 2026."]}, prior_none)), True)

    # ---- the digest line: position among the four, who already lists it, and the window when it is a 'first'
    def line(ex_key, ex_name, prior_exchanges):
        p = {"exchange": ex_name, "exchange_key": ex_key, "token_name": "Predict", "symbol": "PREDICT", "record_since": thin,
             "prior_listings": [{"exchange": n, "exchange_key": n.lower()} for n in prior_exchanges], "trading_start": None}
        return render_exchange_listings({"payload": p})
    first = line("binance", "Binance", [])
    fourth = line("kucoin", "KuCoin", ["Binance", "OKX", "Bybit"])
    check("a first listing reads as 1st with a green dot and its window", ("1st of 4" in first, first.startswith("\U0001F7E2"), "since Mar 2025" in first or "since Mar 2026" in first), (True, True, True))
    check("a fourth listing reads as 4th with a plain dot and names who already lists it", ("4th of 4" in fourth, fourth.startswith("⚪"), "already on Binance, OKX, Bybit" in fourth), (True, True, True))
    check("the two are visibly different at a glance", first[:1] != fourth[:1] and "1st" not in fourth, True)
    check("the same exchange's earlier announcement does not count as 'another exchange already lists it'",
          "2nd of 4" in line("okx", "OKX", ["Bybit", "OKX"]), True)

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all exchange_listings cases behave as expected")


if __name__ == "__main__":
    main()
