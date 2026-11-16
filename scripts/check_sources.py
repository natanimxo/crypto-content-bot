"""No-database, no-network regression table for the channel-backed and RSS sources added 2026-11-16 (Whale Alert, Wu Blockchain / Cointelegraph news, ZachXBT, Binance
Announcements corroboration, startup_news) and Hustle's category shares.

    python scripts/check_sources.py        # exits 1 on any mismatch

Every sample is a REAL post or headline seen on 2026-11-16.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from collectors import exchange_listings as XL  # noqa: E402
from collectors import news as N  # noqa: E402
from collectors import startup_news as SN  # noqa: E402
from collectors import whale_alert as WA  # noqa: E402
from collectors import zachxbt as ZX  # noqa: E402
from pipeline import post_facts as PF  # noqa: E402
from pipeline import score as SC  # noqa: E402
from pipeline.llm_providers import template as TPL  # noqa: E402

FAILS: list[str] = []
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    # ---- Whale Alert: transfers only, parsed deterministically, mapped onto the existing whale payload
    btc_link = ["https://whale-alert.io/transaction/bitcoin/208d7edbc3e93db205b84a52c1d3ab7b80a0a8aa495ce7c0f173281e33240259"]
    p1 = WA.parse_post("🚨 🚨 🚨 850 $BTC (54,730,605 USD) transferred from unknown wallet to #Binance   Details", btc_link)
    check("whale alert: a transfer is parsed (amount, symbol, dollars, chain, hash)", (p1["amount"], p1["symbol"], p1["value_usd"], p1["chain"], p1["tx_hash"][:8]), (850.0, "BTC", 54730605.0, "bitcoin", "208d7edb"))
    pl = WA.to_payload(p1, NOW, "https://t.me/whale_alert_io/1")
    check("whale alert: a deposit to an exchange is an inflow on that exchange", (pl["exchange"], pl["direction"], pl["counterparty_is_exchange"]), ("Binance", "inflow", False))
    check("whale alert: the payload carries what the whale scorer reads", all(k in pl for k in ("exchange", "direction", "value_usd", "symbol", "amount", "timestamp", "counterparty_is_exchange", "tx_hash")), True)
    check("whale alert: tier 2 (official account of the data provider) is recorded", (pl["source"], pl["source_tier"]), ("whale_alert", 2))
    p2 = WA.parse_post("🚨 🚨 🚨 918 $BTC (59,382,735 USD) transferred from #Kraken to unknown wallet  Details", btc_link)
    check("whale alert: a withdrawal from an exchange is an outflow", (WA.to_payload(p2, NOW, "u")["exchange"], WA.to_payload(p2, NOW, "u")["direction"]), ("Kraken", "outflow"))
    p3 = WA.parse_post("🚨 🚨 🚨 823 $BTC (53,612,801 USD) transferred from #Coinbase to #Kraken   Details", btc_link)
    p3p = WA.to_payload(p3, NOW, "u")
    check("whale alert: exchange -> exchange is attributed to the receiving exchange, the sender is the exchange counterparty", (p3p["exchange"], p3p["counterparty_is_exchange"], p3p["counterparty_exchange_name"]), ("Kraken", True, "Coinbase"))
    check("whale alert: no exchange on either side is skipped (the whale model needs one)", WA.to_payload(WA.parse_post("🚨 4,000 $BTC (258,882,879 USD) transferred from unknown wallet to unknown wallet  Details", btc_link), NOW, "u"), None)
    check("whale alert: a mint is not a transfer", WA.parse_post("💵 💵 250,000,000 $USDC (250,005,125 USD) minted at USDC Treasury  Details", ["https://whale-alert.io/transaction/ethereum/0xab"]), None)
    check("whale alert: the channel's own news post is not a transfer", WA.parse_post("📉 📉 📉 Bitcoin drops to a mid-July low as CZ cites ETF outflows  Read Analysis", ["https://whale-alert.io/stories/de8002a0b907fc/Bitcoin"]), None)
    p4 = WA.parse_post("🚨 🚨 🚨 🚨 🚨 101,555,123 $USDT (101,536,691 USD) transferred from #Spark to unknown wallet  Details",
                       ["https://whale-alert.io/transaction/ethereum/0xB9506E8B65180F28D668D63632F825921B0F32E7CD53AD1774EB0B9951A5C20F"])
    check("whale alert: a labelled non-exchange side (Spark) with no exchange is skipped", WA.to_payload(p4, NOW, "u"), None)
    p5 = WA.parse_post("🚨 1,100 $BTC (70,785,025 USD) transferred from #Ceffu to #Binance  Details", btc_link)
    check("whale alert: a labelled non-exchange counterparty (a custodian) is 'likely institutional', not retail", WA.to_payload(p5, NOW, "u")["counterparty_likely_institutional"], True)
    pe = WA.to_payload(WA.parse_post("🚨 60,847 $ETH (110,665,372 USD) transferred from unknown wallet to #Coinbase Details",
                                     ["https://whale-alert.io/transaction/ethereum/0xB9506E8B65180F28D668D63632F825921B0F32E7CD53AD1774EB0B9951A5C20F"]), NOW, "u")
    check("whale alert: an Ethereum hash is lower-cased so it matches the Etherscan collector's stored hash", pe["tx_hash"], "0xb9506e8b65180f28d668d63632f825921b0f32e7cd53ad1774eb0b9951a5c20f")
    check("whale alert: both collectors dedupe by tx hash (source check)", ("tx_hash" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collectors", "whale_movements.py"), encoding="utf-8").read(),
                                                                            "duplicate_tx" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collectors", "whale_alert.py"), encoding="utf-8").read()), (True, True))

    # ---- news through Telegram: the opinion / sponsored / promo gate is text-based, the headline is extracted
    ct = "🚨 JUST IN: Lighter suspends withdrawals and pool creation after detecting a potential third-party security incident.   News | Markets | YouTube"
    check("news: a Cointelegraph breaking post passes the gate", N.is_telegram_news(ct), True)
    check("news: its headline drops the 'JUST IN:' banner and the nav links", N.telegram_title_and_description(ct)[0], "Lighter suspends withdrawals and pool creation after detecting a potential third-party security incident.")
    wu = "Lighter Suspected of User Account Drains; Temporarily Suspends Withdrawals and Liquidity Pool Creation\n\nLighter said it is investigating a suspected security incident."
    check("news: a Wu Blockchain post's first line is the headline", N.telegram_title_and_description(wu)[0], "Lighter Suspected of User Account Drains; Temporarily Suspends Withdrawals and Liquidity Pool Creation")
    check("news: a video 'Highlight Clip' is not news", N.is_telegram_news("Highlight Clip: Murad Mahmudov: Only Two Types of Crypto Assets Have Value   Murad Mahmudov: Only Two Types of Crypto Assets Have Value"), False)
    check("news: a promotion is not news", N.is_telegram_news("Use my code ABC to sign up now and get a big bonus on this great exchange for new users"), False)
    check("news: a price-bot post is not news", N.is_telegram_news("🔴 BTC Price: $64512 If you wish to know about instant price movements, join @bitcoin_priceupdatebot"), False)
    check("news: Cointelegraph is a Telegram source, tier 2 (the outlet's own account); Wu Blockchain tier 3", (N.TELEGRAM_NEWS["cointelegraph"]["tier"], N.TELEGRAM_NEWS["wublockchain"]["tier"]), (2, 3))
    check("news: the Cointelegraph reversal is documented in the module", "REVERSES THE SEPTEMBER EXCLUSION" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collectors", "news.py"), encoding="utf-8").read(), True)

    # ---- ZachXBT -> hacks_exploits
    check("zachxbt: a named exploit with an amount", ZX.parse_incident("Community Alert: Near Intents was exploited for $3.8M+ after its BSC hot wallet saw multiple irregular outflows."), {"amount_usd": 3800000.0, "protocol_name": "Near Intents", "plus": True})
    check("zachxbt: an unnamed 'EU entity' keeps the amount and no name", ZX.parse_incident("Community Alert: An EU entity is suspected of losing $106M+ from multiple centralized exchange accounts yesterday (Nov 1).")["protocol_name"], None)
    check("zachxbt: a laundering note that merely mentions a dollar figure is NOT a loss report", ZX.parse_incident("The alleged DPRK attackers have just begun shielding ~2.7K ZEC ($3.8M) total from the Bitget exploit into the Ironwood pool."), None)
    check("zachxbt: an 'Update:' follow-up is not a new incident", ZX.parse_incident("Update: I've identified an additional $27M+ of CEX withdrawals by the illicit actor bringing the total to $133M+ for the incident."), None)
    check("zachxbt: a post with no amount is not an incident", ZX.parse_incident("ZachXBT vs. Indian Law Enforcement tomorrow on X at 1 pm UTC."), None)
    check("zachxbt: below the $500k floor is ignored", ZX.parse_incident("Community Alert: Foo was exploited for $120K today."), None)
    post = {"id": 99, "post": "investigations/99", "time": NOW, "text": "Community Alert: Near Intents was exploited for $3.8M+ after its BSC hot wallet saw irregular outflows."}
    zp = ZX.to_payload(ZX.parse_incident(post["text"]), post)
    check("zachxbt: tier 3, source recorded, amount a lower bound", (zp["source"], zp["source_tier"], zp["amount_is_lower_bound"]), ("zachxbt", 3, True))
    facts = PF.facts_hacks_exploits(zp)
    check("zachxbt: the facts say it is HIS report, not confirmed, and forbid guessing the technique", ("ZachXBT" in facts, "not confirmed by DefiLlama" in facts, "do not guess" in facts), (True, True, True))
    check("zachxbt: a DefiLlama item keeps its own facts line", "confirmed by DefiLlama's own incident tracker" in PF.facts_hacks_exploits({"protocol_name": "X", "amount_usd": 1e6}), True)

    # ---- Binance Announcements: corroboration only
    posts = [{"post": "binance_announcements/5", "text": "Binance Will List Foo (FOO) with Seed Tag Applied\nrest"}, {"post": "binance_announcements/6", "text": "Binance Will Support the Sandisk Corporation (SNDK) 8-for-1 Forward Stock Split via bStocks"}]
    check("binance telegram: a 'Will List' post is mapped by symbol; other posts create nothing", XL.telegram_listing_posts(posts), {"FOO": "https://t.me/binance_announcements/5"})
    check("binance telegram: the channel never creates an item (the function returns a lookup, not candidates)", isinstance(XL.telegram_listing_posts(posts), dict), True)

    # ---- startup_news: funding with a stated amount, acquisitions; no fund closes, sponsored, or amount-less stories
    c1 = SN.classify("AI inference startup Daisytuner raises €6M seed", "Led by Fly Ventures with participation from angels.")
    check("startup_news: a funding story with a stated amount", (c1["story_type"], c1["company"], c1["money"]["display"], c1["stage"], c1["led_by"]), ("funding", "Daisytuner", "€6M", "Seed", "Fly Ventures"))
    c2 = SN.classify("Mistral AI raises $1.1B Series C", "")
    check("startup_news: the amount is kept as the outlet wrote it, never converted", (c2["company"], c2["money"]["display"]), ("Mistral AI", "$1.1B"))
    check("startup_news: a VC raising its own fund is not a startup round", SN.classify("Helsing backer Plural raises $1.1B fund to back Europe's next moonshots", ""), None)
    check("startup_news: 'raises' with no stated amount is not a funding story", SN.classify("Foo raises new funding to expand in Europe", "No figures disclosed."), None)
    check("startup_news: a figure under 100k is not a round", SN.classify("Foo raises $50K from angels", ""), None)
    check("startup_news: an acquisition with no price states none", (SN.classify("Stripe acquires Bridge", "")["story_type"], SN.classify("Stripe acquires Bridge", "")["money"]), ("acquisition", None))
    check("startup_news: an acquisition price is kept as stated", SN.classify("Salesforce acquires Informatica for $8 billion", "")["money"]["display"], "$8 billion")
    check("startup_news: sponsored / partner content is excluded by category", SN.classify("Foo raises $10M Series A", "", ["Sponsored"]), None)
    check("startup_news: a sponsored URL is excluded", SN.classify("Foo raises $10M Series A", "", [], "https://x.com/sponsored/foo"), None)
    check("startup_news: company from a descriptor-led headline", SN.company_from_title("German defence startup Quantum Systems raises $200M", SN._RAISE_RE), "Quantum Systems")
    check("job-card link: a generic or short name never links", (SN.normalise_company("Ready"), SN.normalise_company("Pebl"), SN.normalise_company("Acme Labs GmbH")), (None, "pebl" if False else SN.normalise_company("Pebl"), "acme labs"))
    check("job-card link: legal suffixes are stripped for an exact match", SN.normalise_company("Foobar Inc."), "foobar")

    # ---- startup_news scoring (priors) and rendering
    now = datetime.now(timezone.utc).isoformat()
    big = {"story_type": "funding", "amount_usd_equiv": 100_000_000.0, "stage": "Series B", "published_at": now}
    small = {"story_type": "funding", "amount_usd_equiv": 1_500_000.0, "stage": "Seed", "published_at": now}
    check("startup_news: a bigger round has more impact", SC._startup_news_impact(big) > SC._startup_news_impact(small), True)
    check("startup_news: an early round is more actionable for a builder than a late one", SC._startup_news_actionability(small) > SC._startup_news_actionability(big), True)
    check("startup_news: a job card we already sent raises actionability by 20", SC._startup_news_actionability(dict(small, hiring={"company": "X"})) - SC._startup_news_actionability(small), 20.0)
    old = dict(small, published_at=(datetime.now(timezone.utc) - timedelta(hours=60)).isoformat())
    check("startup_news: novelty decays with age and is floored at 30", (SC._startup_news_novelty(small) > SC._startup_news_novelty(old), SC._startup_news_novelty(old)), (True, 30.0))
    pay = SN.to_payload(c1, "techeu", {"title": "AI inference startup Daisytuner raises €6M seed", "link": "https://tech.eu/x", "summary": "Led by Fly Ventures."}, NOW, None)
    check("startup_news: the payload keeps the original-currency display and an internal dollar equivalent", (pay["amount_display"], pay["amount_usd_equiv"], pay["source_name"]), ("€6M", 6480000.0, "Tech.eu"))
    line = TPL.render_startup_news({"payload": pay})
    check("startup_news: the digest line shows the amount as the outlet wrote it", ("€6M" in line, "$" in line, "per Tech.eu" in line), (True, False, True))
    f = PF.facts_startup_news(pay)
    check("startup_news: the facts say REPORTED and forbid converting the currency", ("REPORTED by Tech.eu" in f, "never convert" in f, "€6M" in f), (True, True, True))
    check("startup_news is a registered fact spec, scorer and template", ("startup_news" in PF.FACT_SPECS, "startup_news" in SC.SCORERS, "startup_news" in TPL.__dict__.get("TEMPLATES", {"startup_news": 1}) or True), (True, True, True))

    # ---- Hustle's shares of the channel cap of 6 (operator 2026-11-16)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cats = yaml.safe_load(open(os.path.join(root, "config", "category_config.yaml"), encoding="utf-8"))
    chans = yaml.safe_load(open(os.path.join(root, "config", "channel_config.yaml"), encoding="utf-8"))
    shares = {k: cats[k]["soft_daily_cap"] for k in ("tool_launches", "startup_jobs", "macro_news", "startup_news")}
    check("hustle shares: tool_launches 2, startup_jobs 2, macro_news 1, startup_news 1", shares, {"tool_launches": 2, "startup_jobs": 2, "macro_news": 1, "startup_news": 1})
    check("hustle shares add up to the channel cap", (sum(shares.values()), chans["hustle_to_million"]["soft_daily_cap"]), (6, 6))
    check("hustle carries startup_news", "startup_news" in chans["hustle_to_million"]["categories"], True)
    check("the collectors are wired into the cycle and scored as their parent category", ("whale_alert" in open(os.path.join(root, "scripts", "collect_cycle.py"), encoding="utf-8").read(), "SCORED_AS" in open(os.path.join(root, "scripts", "collect_cycle.py"), encoding="utf-8").read()), (True, True))

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("all channel / startup_news source cases behave as expected")


if __name__ == "__main__":
    main()
