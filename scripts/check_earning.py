"""No-database, no-network regression table for earning_opportunities (Phase 5, 2026-11-15).

    python scripts/check_earning.py        # exits 1 on any mismatch

The gate, the extraction rules and the two scores are pure functions (pipeline/earning.py); this table pins the
behaviour the operator specified and the misfires found on real candidates the first day: an airdrop post that mentioned
"$20M raised" scored a $20 million payout; a deals-site price scored as a reward; a bank-style offer needing a
$39.99/month account looked free. Add a case whenever a real item is scored for a bad reason.
"""

import copy
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import earning as E  # noqa: E402

FAILS: list[str] = []
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    # ---- the gate: exclude seed phrases, connect-wallet-to-claim, upfront payment -- and only those
    for text, want in [
        ("Enter your seed phrase to verify your wallet and receive the airdrop", True),
        ("Import your wallet using your 12-word recovery phrase to claim", True),
        ("Share your private key to unlock the bonus", True),
        ("Connect your wallet to claim the airdrop", True),
        ("Claim your tokens now: connect wallet on the official dapp", True),
        ("WalletConnect login required to claim your reward", True),
        ("Pay a small gas fee to claim your 5 ETH", True),
        ("Activation fee required before you can withdraw", True),
        ("Send 0.1 ETH to receive 1 ETH back", True),
        ("Double your crypto in 24 hours", True),
        ("There is a registration fee of $5 to enter", True),
        ("Free games on Epic: connect your Epic account and press Get", False),
        ("Open a checking account, deposit $500 and earn a $200 bonus", False),
        ("Win a $500 gift card, no purchase necessary", False),
        ("Free shipping on the sample box", False),
        ("Sign in with Google to get 10,000 free credits", False),
        ("Register on the platform, complete KYC and get a 25 USDT reward", False),
    ]:
        check(f"gate {text[:55]!r}", bool(E.gate(text)), want)

    # ---- reward amount: what the reader RECEIVES, never a funding round, price or deposit
    for text, type_, want in [
        ("Get $250 bonus with a $250 contribution", "bank_bonus", 250.0),
        ("Win a $500 gift card", "giveaway", 500.0),
        ("Elysium raised $20M in a seed round, mainnet today", "airdrop", None),
        ("Airdrop worth up to $300 for early users", "airdrop", 300.0),
        ("Solar Battery 25kWh $5500 + Free EV Charge", "freebie", None),
        ("Deposit $2,500 and keep it for 90 days, earn $500", "bank_bonus", 500.0),
        ("TVL hit $120M after the launch", "airdrop", None),
        ("$10M airdrop pool for the community", "airdrop", None),     # a pool is not a per-person reward
        ("Free $1000 credit for your AI Agent", "credits", 1000.0),
    ]:
        check(f"reward {text[:50]!r}", E.extract_reward_usd(text, type_), want)

    # ---- capital the reader has to put in
    for text, type_, want in [
        ("Deposit $500 within 30 days", "bank_bonus", 500.0),
        ("Requires a $39.99 per month account", "cashback", 119.97),
        ("Fund with $25 to qualify", "bank_bonus", 25.0),
        ("Free to keep on Epic until Nov 21", "free_game", 0.0),
        ("Open an account and earn a bonus", "bank_bonus", None),     # not stated, normally needed: unknown, not zero
    ]:
        check(f"capital {text[:45]!r}", E.extract_capital_usd(text, type_), want)

    # ---- deadlines
    for text, want in [
        ("free to keep until Nov 21", (2026, 11, 21)),
        ("ends 11/21", (2026, 11, 21)),
        ("(11/14 Only)", (2026, 11, 14)),
        ("Submissions Oct 30 - Dec 04, 2026", (2026, 12, 4)),
        ("deadline 2026-12-01", (2026, 12, 1)),
        ("no date at all here", None),
    ]:
        got = E.extract_deadline(text, NOW)
        check(f"deadline {text!r}", (got.year, got.month, got.day) if got else None, want)

    # ---- geography: worldwide beats single-country
    for text, want in [
        ("Open worldwide, any country", "worldwide"),
        ("US only, residents must be 18+", "single"),
        ("Available to UK residents only", "single"),
        ("Giveaway for everyone", "unknown"),
    ]:
        check(f"geo {text!r}", E.geo_breadth(text), want)

    # ---- the "free" word must not override a source's own type
    check("airdrop channel post saying 'free' stays an airdrop", E.classify_type("3 early projects, free to join", "airdrop"), "airdrop")
    check("a hackathon is a hackathon", E.classify_type("Kiroween hackathon with a $100,000 prize pool", "freebie"), "hackathon")
    check("the same game announced by Epic and relayed by GamerPower dedupes",
          E.title_key("Knightica is free on the Epic Games Store", "free_game") == E.title_key("Knightica (Epic Games) Giveaway", "free_game"), True)

    # ---- referral: stripped from every link, never an input
    check("referral params stripped", E.normalize_url("https://www.Example.com/claim/?utm_source=x&ref=ABC123&id=7#top"), "https://example.com/claim?id=7")
    check("referral path stripped", E.normalize_url("https://make.fun/r/tvfbjxfd"), "https://make.fun")
    check("a referral link is detected (display only)", (E.is_referral_link("https://x.io/?ref=abc"), E.is_referral_link("https://x.io/claim")), (True, False))
    base = {"source": "tg:criptinho", "source_tier": 3, "type": "airdrop", "payout_usd": 100.0, "payout_kind": "stated", "capital_usd": 0.0,
            "effort_minutes": 30, "geo": "worldwide", "domain_age_days": 800, "landing_domain": "x.io",
            "terms": {"deadline": True, "eligibility": True, "payout": True}, "corroborators": [], "reputation": {"safe_browsing": "unchecked", "urlhaus": "unchecked"}}
    with_ref, without_ref = dict(base, referral_link=True), dict(base, referral_link=False)
    check("a referral program changes neither score", E.score_earning(with_ref), E.score_earning(without_ref))

    # ---- two scores, never blended
    a = E.score_earning(base)
    b = E.score_earning(dict(base, source_tier=1))
    check("raising the source tier moves trust only", (b["trust"] > a["trust"], b["value"] == a["value"]), (True, True))
    c = E.score_earning(dict(base, geo="single", effort_minutes=600))
    check("geography and effort move value only", (c["value"] < a["value"], c["trust"] == a["trust"]), (True, True))
    check("both numbers are always present", ("value" in a and "trust" in a), True)

    # ---- the cross-link: payout-for-nothing costs TRUST (and is exempt for an official source)
    scam = dict(base, payout_usd=1000.0, payout_kind="stated", effort_minutes=5, source_tier=3, domain_age_days=4)
    legit_official = dict(scam, source_tier=1, domain_age_days=11000)
    s_scam, s_off = E.score_earning(scam), E.score_earning(legit_official)
    check("$1000 for 5 minutes from an unvetted 4-day-old domain: trust collapses", s_scam["trust"] < 20, True)
    check("...and it still looks valuable (that is the trap)", s_scam["value"] >= 60, True)
    check("the same offer from an official source is not penalised", s_off["trust_components"]["payout_for_nothing_penalty"], 0.0)
    check("an expected-value hackathon pool is not 'payout for nothing'",
          E.payout_for_nothing_penalty(dict(base, type="hackathon", payout_usd=20.0, payout_kind="ev", effort_minutes=600)), 0.0)
    check("a real bonus with real effort is not penalised",
          E.payout_for_nothing_penalty(dict(base, type="bank_bonus", payout_usd=450.0, capital_usd=500.0, effort_minutes=120, source_tier=2)), 0.0)

    # ---- trust is facts: each fact does what the operator listed
    t0 = E.trust_components(dict(base, domain_age_days=None))["domain_age"]
    check("domain age: old > unknown > young", (E.trust_components(dict(base, domain_age_days=800))["domain_age"] > t0 > E.trust_components(dict(base, domain_age_days=5))["domain_age"]), True)
    check("two independent corroborating sources outweigh none", E.trust_components(dict(base, corroborators=["a", "b"]))["corroboration"], 20.0)
    check("stated terms add trust", E.trust_components(dict(base, terms={"deadline": False, "eligibility": False, "payout": False}))["terms_stated"], 0.0)
    check("a token contract we could not check is never coded as clean",
          E.trust_components(dict(base, goplus={"status": "unchecked"}))["reputation"] < E.trust_components(dict(base, goplus={"status": "none"}))["reputation"], True)

    # ---- unknown is never coded as high
    unk = E.score_earning(dict(base, payout_usd=None, payout_kind=None, capital_usd=None))
    check("an unknown payout and unknown capital cannot score as a high-value item", unk["value"] < 70, True)

    # ---- the floors: both must pass; the trust gate does not rank
    check("floors: both pass", E.passes_floors({"value": 40.0, "trust": 40.0}), True)
    check("floors: high value, low trust fails", E.passes_floors({"value": 95.0, "trust": 39.9}), False)
    check("floors: high trust, low value fails", E.passes_floors({"value": 39.9, "trust": 95.0}), False)

    # ---- the post guard: actionable means no invented steps, no invented amounts, the deadline stated, no referral / URL
    from pipeline.write_post import _find_earning_issues as G
    pl = {"title": "Knightica is free on the Epic Games Store", "type": "free_game", "source_name": "Epic Games Store (official)", "payout_usd": 14.99,
          "payout_kind": "worth", "capital_usd": 0.0, "geo": "worldwide", "deadline": "2026-11-19T16:00:00+00:00", "prereqs": [],
          "enriched": {"steps": ["Open the store page.", "Sign in to an Epic account.", "Press Get, then Place Order (the total is zero)."]}}

    def post(*paras, title="Get Knightica free until Nov 19"):
        return {"title": title, "paragraphs": list(paras)}
    ok_body = "Knightica is worth $15 and free until Nov 19."
    ok_steps = "1. Open the store page.\n2. Sign in to an Epic account.\n3. Press Get, then Place Order."
    check("a grounded post passes", G(post(ok_body, ok_steps, "Needs an Epic account."), pl), [])
    check("an invented step is rejected", bool(G(post(ok_body, "1. Open the store page.\n2. Download the launcher and restart your PC.", "Needs an account."), pl)), True)
    check("an amount that is not in the facts is rejected", bool(G(post("Knightica is worth $60 and free until Nov 19.", ok_steps, "Needs an account."), pl)), True)
    check("a missing deadline is rejected", bool(G(post("Knightica is free to keep.", ok_steps, "Needs an account.", title="Get Knightica free"), pl)), True)
    check("a URL is rejected", bool(G(post(ok_body, ok_steps, "More at https://store.epicgames.com/en-US/p/knightica"), pl)), True)
    check("referral language is rejected", bool(G(post(ok_body, ok_steps, "Use my referral code for extra rewards."), pl)), True)
    check("'the facts' leaking into the post is rejected", bool(G(post(ok_body, ok_steps, "The facts do not list the limits."), pl)), True)
    check("steps run together on one line are rejected", bool(G(post(ok_body, "1. Open the store page. 2. Sign in. 3. Press Get.", "Needs an account."), pl)), True)
    no_steps = dict(pl, enriched={})
    check("numbered steps are rejected when none are available", bool(G(post(ok_body, ok_steps, "Needs an account."), no_steps)), True)
    check("'the steps are on the page' is fine when none are available", G(post(ok_body, "The steps are on the offer's page.", "Needs an account."), no_steps), [])

    # ---- multilingual terms (Russian / Portuguese): a detector gap, not an absence of terms (0 of 34 Russian items scored any)
    for text, want in [
        ("Раздача завершится до 21 ноября, успейте", (2026, 11, 21)),
        ("Дедлайн: 30.11.2026", (2026, 11, 30)),
        ("Регистрация открыта по 5 декабря", (2026, 12, 5)),
        ("Prazo: até 21 de novembro de 2026", (2026, 11, 21)),
        ("A campanha termina dia 3 de dezembro", (2026, 12, 3)),
        ("Válido até 15/12", (2026, 12, 15)),
        ("Просто новости о проекте без дат", None),
    ]:
        got = E.extract_deadline(text, NOW)
        check(f"deadline (ru/pt) {text[:40]!r}", (got.year, got.month, got.day) if got else None, want)
    for text, want in [
        ("Доступно только для жителей ЕС, нужна верификация", True),
        ("Необходимо пройти KYC", True),
        ("Disponível para residentes do Brasil", True),
        ("Requisito: conta verificada", True),
        ("Jumper запустили агрегатор перпов", False),
    ]:
        check(f"eligibility (ru/pt) {text[:40]!r}", E.has_eligibility(text), want)
    for text, want in [
        ("Разделяем 1 000 000 токенов KNTQ между участниками", True),
        ("Забираем приветственные бонусы до $15 000", True),
        ("Ganhe 50 USDT de prêmio no sorteio", True),
        ("Пул 100000 $ на всех", True),
        ("Новости рынка за неделю", False),
    ]:
        check(f"payout stated (ru/pt) {text[:40]!r}", E.payout_stated(text), want)
    check("a Russian reward amount is read", E.extract_reward_usd("Разделяем награду в $4 000 за качественный контент", "airdrop"), 4000.0)
    check("a Russian funding round is not a reward", E.extract_reward_usd("Проект привлек $20M в раунде", "airdrop"), None)

    # ---- WHAT KIND OF AMOUNT: the payout-for-nothing penalty must fire only on what one reader receives (or a stated maximum).
    # Same shape of bug as "Разделяем $70 000", other wordings, three languages. (amount, kind) per phrasing:
    for text, want in [
        # pools / totals -- never a per-person payout
        ("A $50,000 prize pool for the community", (50000.0, "pool")),
        ("Total rewards of $10,000 for early users", (10000.0, "pool")),
        ("$100,000 in prizes for the top builders", (100000.0, "pool")),
        ("$20,000 shared among the top traders", (20000.0, "pool")),
        ("$5,000 distributed to all participants", (5000.0, "pool")),
        ("$8,000 up for grabs this week", (8000.0, "pool")),
        ("Giving away $2,000 among our followers", (2000.0, "pool")),
        ("Разделяем $70 000 на TrueNorth и торгуем", (70000.0, "pool")),
        ("Призовой фонд $30 000 для участников", (30000.0, "pool")),
        ("Общий пул наград $100 000", (100000.0, "pool")),
        ("Разыгрываем $5 000 между всеми участниками", (5000.0, "pool")),
        ("Награды на сумму $10 000", (10000.0, "pool")),
        ("Prêmio total de $10.000 para a comunidade", (10000.0, "pool")),
        ("Pool de $5.000 dividido entre os participantes", (5000.0, "pool")),
        ("Sorteio de $1.000 entre os seguidores", (1000.0, "pool")),
        # an amount tied to N winners -- per person or total cannot be told
        ("Reward: $5 for 160 winners", (5.0, "unclear")),
        ("Win $1,000 -- 3 winners", (1000.0, "unclear")),
        ("Награда $500 для 10 победителей", (500.0, "unclear")),
        ("Ganhe $200 -- 5 vencedores", (200.0, "unclear")),
        # a stated maximum -- deliberately treated as the payout (BACKLOG)
        ("Up to $15,000 welcome bonus", (15000.0, "max")),
        ("Бонусы до $15 000 на WEEX", (15000.0, "max")),
        ("Bônus de até $300", (300.0, "max")),
        # what one reader receives
        ("Win a $500 gift card", (500.0, "stated")),
        ("Get a $250 bonus", (250.0, "stated")),
        ("Free $1000 credit for your AI Agent", (1000.0, "stated")),
        ("Каждому участнику по $50", (50.0, "stated")),
        ("$20 each for the first 100 users", (20.0, "stated")),
        ("Ganhe $30 por pessoa indicada", (30.0, "stated")),
        # a per-person cue wins over a pool word
        ("$10 per winner from a $1,000 pool", (1000.0, "pool")),   # both present: the larger (pool) is recorded -- penalty-safe
        # no reward cue at all
        ("Proekt raised $20M", (None, None)),
    ]:
        check(f"reward kind {text[:46]!r}", E.extract_reward(text, "airdrop"), want)

    # ---- the penalty only ever fires on 'stated' / 'max' text claims -- never a pool, an unclear amount, a structured worth, an EV
    big = dict(base, payout_usd=70000.0, effort_minutes=5, source_tier=3, domain_age_days=800)
    for kind, want in [("stated", 50.0), ("max", 50.0), ("pool", 0.0), ("unclear", 0.0), ("worth", 0.0), ("ev", 0.0), (None, 0.0)]:
        check(f"penalty on payout_kind={kind!r}", E.payout_for_nothing_penalty(dict(big, payout_kind=kind)), want)
    check("a figure that is a PRICE (not a reward) means 'for nothing' is not demonstrated: no penalty",
          E.payout_for_nothing_penalty(dict(big, payout_kind="stated", payout_usd=3500.0, other_amount_max=5500.0, type="freebie")), 0.0)
    check("a bare big claim with no other figure is still penalised",
          E.payout_for_nothing_penalty(dict(big, payout_kind="stated", payout_usd=500.0, other_amount_max=0.0)), 30.0)
    check("a misread pool can no longer zero trust", E.score_earning(dict(big, payout_kind="pool", payout_usd=None, pool_usd=70000.0))["trust"] >= 40, True)

    # ---- roundups are not offers (operator ruling): excluded, but a single offer that says 'weekly' / 'daily' is not a roundup
    for title, want in [
        ("📡 Airdrop Radar — Daily Roundup (November 5, 2026)", True),
        ("🛰 AIRDROP WEEKLY RADAR · Oct 31 → Nov 6", True),
        ("🪂 18 Confirmed November Airdrops", True),
        ("📆 3 November Campaigns u didn't hear", True),
        ("3 Early Projects on our radar 👀", True),
        ("Отжимка по дропам за 06.11 - 09.11", True),
        ("Где фармить объемы прямо сейчас: подборка PerpDEX", True),
        ("Обзор криптобиржи WEEX: разбираем функционал", True),
        ("Resumo semanal de airdrops", True),
        ("Weekly giveaway: win a $100 gift card", False),
        ("Daily check-in reward: 10 points", False),
        ("Elysium Mainnet Today", False),
        ("🎙 $4,000 in $MNT for creators", False),
        ("Axis Update Season 2 starts TODAY", False),
        ("Новости Metric", False),
        ("Free Steam game until Nov 21", False),
    ]:
        check(f"roundup {title[:44]!r}", E.is_roundup(title), want)

    # ---- aggregator landing: follow airdrops.io's page

    # ---- WHOIS fallback for the ccTLDs RDAP does not cover (.io / .me): parse only here, the lookup is covered live
    from collectors import earning_opportunities as W
    check("whois creation date parses", str(W.parse_whois_created("Domain Name: ARC.IO\nCreation Date: 2013-02-28T00:00:00Z\nRegistry Expiry Date: 2027-02-28")), "2013-02-28")
    check("whois without a creation date is unknown", W.parse_whois_created("No match for domain"), None)

    # ---- aggregator landing: follow airdrops.io's page to the PROJECT's site, never score the aggregator's own domain
    from collectors import earning_opportunities as AG

    class _Page:
        def __init__(self, status, text="", url=""):
            self.status_code, self.text, self.url = status, text, url
    real_get, real_head = AG.requests.get, AG.requests.head
    try:
        AG.requests.get = lambda *a, **k: _Page(200, 'x<li>Website: <a href="/visit/abc123/" target="_blank">go</a></li>')
        AG.requests.head = lambda *a, **k: _Page(200, "", "https://elysium.kinetiq.xyz/")
        check("airdrops.io page resolves to the project site", AG.resolve_project_site("https://airdrops.io/elysium"), "https://elysium.kinetiq.xyz/")
        AG.requests.head = lambda *a, **k: _Page(200, "", "https://airdrops.io/somewhere/")
        check("a redirect that stays on airdrops.io is NOT accepted", AG.resolve_project_site("https://airdrops.io/elysium"), None)
        AG.requests.get = lambda *a, **k: _Page(200, "<html>no website list here</html>")
        check("no Website link -> unknown, not the aggregator", AG.resolve_project_site("https://airdrops.io/elysium"), None)
        AG.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        check("a fetch failure -> unknown", AG.resolve_project_site("https://airdrops.io/elysium"), None)
        check("a non-aggregator link is not touched", AG.resolve_project_site("https://example.com/claim"), None)
    finally:
        AG.requests.get, AG.requests.head = real_get, real_head

    # ---- Safe Browsing / URLhaus: mocked (no keys yet). The invariant that matters: a check that could not run is never "clean".
    from collectors import earning_opportunities as RC

    class _Resp:
        def __init__(self, status, data):
            self.status_code, self._d = status, data

        def json(self):
            return self._d
    real_post, real_env = RC.requests.post, dict(os.environ)
    try:
        os.environ.pop("GOOGLE_SAFE_BROWSING_API_KEY", None)
        os.environ.pop("URLHAUS_AUTH_KEY", None)
        RC.requests.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no key -> no call"))
        r = RC.reputation_checks(["https://a.example/x"])
        check("no keys: both unchecked, no network call", r["https://a.example/x"], {"safe_browsing": "unchecked", "urlhaus": "unchecked"})
        os.environ["GOOGLE_SAFE_BROWSING_API_KEY"], os.environ["URLHAUS_AUTH_KEY"] = "k1", "k2"

        def fake(url, **kw):
            if "safebrowsing" in url:
                return _Resp(200, {"matches": [{"threat": {"url": "https://bad.example/x"}}]})
            host = kw["data"]["host"]
            return _Resp(200, {"query_status": "ok" if host == "bad.example" else "no_results"})
        RC.requests.post = fake
        r = RC.reputation_checks(["https://bad.example/x", "https://good.example/y"])
        check("flagged by both", r["https://bad.example/x"], {"safe_browsing": "flagged", "urlhaus": "flagged"})
        check("clean by both", r["https://good.example/y"], {"safe_browsing": "clean", "urlhaus": "clean"})
        RC.requests.post = lambda *a, **k: _Resp(500, {})
        check("a 500 is unchecked, never clean", RC.reputation_checks(["https://good.example/y"])["https://good.example/y"], {"safe_browsing": "unchecked", "urlhaus": "unchecked"})
        RC.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout"))
        check("an exception is unchecked, never clean", RC.reputation_checks(["https://good.example/y"])["https://good.example/y"], {"safe_browsing": "unchecked", "urlhaus": "unchecked"})
    finally:
        RC.requests.post = real_post
        os.environ.clear()
        os.environ.update(real_env)

    # ---- Telegram: the t.me/s/ preview parses (id, time, text, outbound links, views, reaction total)
    from collectors import earning_opportunities as C
    page = ('<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="chan/12">'
            '<div class="tgme_widget_message_text js-message_text" dir="auto">Win a $500 gift card <a href="https://example.com/claim?ref=abc">link</a>'
            '<br/>Ends Nov 21</div><div class="tgme_widget_message_reactions js-message_reactions"><span class="tgme_reaction"><i class="emoji"><b>x</b></i>21</span>'
            '<span class="tgme_reaction"><i class="emoji"><b>y</b></i>6</span></div><div class="tgme_widget_message_footer">'
            '<span class="tgme_widget_message_views">2.23K</span><a class="tgme_widget_message_date" href="https://t.me/chan/12">'
            '<time datetime="2026-11-15T16:06:00+00:00" class="time">16:06</time></a></div></div></div>')
    msgs = C.parse_telegram_preview(page, "chan")
    check("telegram preview parses one message", len(msgs), 1)
    if msgs:
        m = msgs[0]
        check("telegram message fields", (m["post"], m["views"], m["reactions"], m["links"], "Ends Nov 21" in m["text"]),
              ("chan/12", 2230, 27, ["https://example.com/claim?ref=abc"], True))

    # ---- staleness: a channel silent for 30 days is flagged, one active yesterday is not (6 of the 20 researched handles were dead)
    from datetime import timedelta
    check("silent 92 days is stale", C.is_stale(NOW - timedelta(days=92), NOW, C.TELEGRAM_STALE_DAYS), True)
    check("silent exactly 30 days is stale", C.is_stale(NOW - timedelta(days=30), NOW, C.TELEGRAM_STALE_DAYS), True)
    check("active yesterday is not stale", C.is_stale(NOW - timedelta(days=1), NOW, C.TELEGRAM_STALE_DAYS), False)
    check("a source with no limit is never stale", C.is_stale(NOW - timedelta(days=500), NOW, None), False)

    # ---- the three exchange-announcement channels are corroboration only, never opportunity sources
    check("exchange channels are not candidate sources", [h for h in C.earning.CORROBORATION_ONLY_TELEGRAM if f"tg:{h}" in C.earning.SOURCES], [])
    check("the dead / absent handles are not configured", [h for h in ("WWGiveaways", "freestuff_hub", "FREE_UDEMY_COURSES1", "bugbountyplzen", "officialairdropalert", "cryptosale_tg", "2top_airdrop")
                                                         if h in C.earning.TELEGRAM_HANDLES], [])

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all earning_opportunities cases behave as expected")


if __name__ == "__main__":
    main()
