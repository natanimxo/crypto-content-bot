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
    # ---- referral counts in VALUE (operator override 2026-11-16): capped, never overrides the rest, and a recruitment shape is a WARNING
    nr = E.score_earning(base)
    wr = E.score_earning(dict(base, referral={"available": True, "amount_usd": None}))
    wbig = E.score_earning(dict(base, referral={"available": True, "amount_usd": 50.0}))
    check("a referral program raises value (not trust)", (wr["value"] > nr["value"], wr["trust"] == nr["trust"]), (True, True))
    check("a stated per-referral amount is worth at least as much as an unstated one", wbig["value"] >= wr["value"], True)
    check("the referral bonus is capped at 12 points", max(E.score_earning(dict(base, referral={"available": True, "amount_usd": 90.0}))["referral_bonus"], wr["referral_bonus"]) <= E.REFERRAL_VALUE_MAX, True)
    weak = dict(base, payout_usd=5.0, capital_usd=300.0, effort_minutes=200, geo="single", referral={"available": True, "amount_usd": None})
    check("a mediocre offer WITH a referral program does not outrank a strong one without",
          E.score_earning(weak)["value"] < E.score_earning(base)["value"], True)
    check("a referral code on the source's own link is NOT availability (an inference): reported, never scored",
          (E.referral_info(dict(base, referral_link=True))["available"], E.referral_info(dict(base, referral_link=True))["link_code"],
           E.score_earning(dict(base, referral_link=True)) == dict(nr, referral=E.referral_info(dict(base, referral_link=True)))), (False, True, True))
    check("russian 'за каждого друга' is a referral clause", E.extract_referral("‼ ЗАБИРАЕМ ДО 15$ ЗА КАЖДОГО ДРУГА ОТ FOLK AI ‼"), {"available": True, "amount_usd": 15.0, "multi_level": False})
    check("no referral -> no bonus", (nr["referral_bonus"], nr["referral"]["available"]), (0.0, False))
    rec = E.score_earning(dict(base, payout_usd=10.0, referral={"available": True, "amount_usd": 50.0}))
    plain = E.score_earning(dict(base, payout_usd=10.0))
    check("referral earnings above the base payout: no value bonus, trust cut by the recruitment penalty",
          (rec["referral_bonus"], rec["trust_components"]["referral_recruitment_penalty"], rec["trust"] < plain["trust"], rec["referral"]["recruitment_shape"]), (0.0, 30.0, True, True))
    check("referral earnings BELOW the base payout are not recruitment-shaped", E.score_earning(dict(base, payout_usd=100.0, referral={"available": True, "amount_usd": 10.0}))["referral"]["recruitment_shape"], False)
    check("a referral-only offer (no base payout, a stated referral amount) is recruitment-shaped",
          E.score_earning(dict(base, payout_usd=None, payout_kind=None, referral={"available": True, "amount_usd": 20.0}))["referral"]["recruitment_shape"], True)
    # ---- extraction: the referral clause is split from the base text so a referral bonus is never read as the offer's payout
    x = "Sign up and get a $50 bonus. Invite friends and earn $25 for each referral."
    check("referral amount read from its clause", E.extract_referral(x), {"available": True, "amount_usd": 25.0, "multi_level": False})
    check("the payout is read from the text without the referral clause", E.extract_reward(E.split_referral(x)[0], "bank_bonus"), (50.0, "stated"))
    check("same sentence: 'get $50 and refer a friend for $25'", E.extract_referral("Get a $50 bonus and refer a friend for $25"), {"available": True, "amount_usd": 25.0, "multi_level": False})
    check("same sentence: base kept", E.extract_reward(E.split_referral("Get a $50 bonus and refer a friend for $25")[0], "bank_bonus"), (50.0, "stated"))
    check("no program in the text", E.extract_referral("Complete 3 quests to qualify for the airdrop"), {"available": False, "amount_usd": None, "multi_level": False})
    check("'no referral needed' is not a program", E.extract_referral("Free to join, no referral needed")["available"], False)
    check("the poster's own code is not a program", E.extract_referral("Use my referral code ABC for a bonus")["available"], False)
    check("a percentage program: available, no amount", E.extract_referral("Earn 20% of your referrals' rewards"), {"available": True, "amount_usd": None, "multi_level": False})
    check("russian", E.extract_referral("Бонус $30. За каждого приглашённого друга +$5"), {"available": True, "amount_usd": 5.0, "multi_level": False})
    check("'50$ and 50$ per friend': the sign AFTER the number is read, and the base is kept (equal amounts are not recruitment-shaped)",
          (E.extract_referral("‼ ЗАБИРАЕМ 50$ И 50$ ЗА ДРУГА ОТ PumpFun ‼"), E.extract_reward(E.split_referral("‼ ЗАБИРАЕМ 50$ И 50$ ЗА ДРУГА ОТ PumpFun ‼")[0], "airdrop")),
          ({"available": True, "amount_usd": 50.0, "multi_level": False}, (50.0, "stated")))
    check("'$25 per friend' after 'and' splits from the base", E.split_referral("Get $50 and $25 per friend you refer"), ("Get $50", ["$25 per friend you refer"]))
    # ---- multi-level wording: recruitment-shaped ON ITS OWN, whatever the amounts (operator 2026-11-16); no official-source exemption
    for label, text in (("downline", "Build your downline and earn on every signup"), ("multi-level", "Multi-level rewards program, $5 bonus"),
                        ("3 levels of referrals", "Earn from 3 levels of referrals"), ("referral levels", "Invite friends: level 1 pays 10%, level 2 pays 5%"),
                        ("russian", "Многоуровневая реферальная программа"), ("portuguese", "Ganhe com 3 níveis de indicação")):
        check(f"multi-level wording ({label}) is recruitment-shaped", E.extract_referral(text)["multi_level"], True)
    for label, text in (("game levels", "Complete 3 levels in the game to qualify for the airdrop"), ("level up", "Level up your account for a $20 bonus"),
                        ("percentage only", "Earn 20% of your referrals' rewards"), ("plain referral", "Invite friends and earn $5 each")):
        check(f"not multi-level: {label}", E.extract_referral(text)["multi_level"], False)
    mlm = dict(base, payout_usd=100.0, referral={"available": True, "amount_usd": None, "multi_level": True})
    sm = E.score_earning(mlm)
    check("multi-level wording: recruitment-shaped even with a small referral amount, no bonus, trust cut", (sm["referral"]["recruitment_shape"], sm["referral_bonus"], sm["trust_components"]["referral_recruitment_penalty"]), (True, 0.0, 30.0))
    check("no official-source exemption for the recruitment penalty", E.score_earning(dict(mlm, source_tier=1))["trust_components"]["referral_recruitment_penalty"], 30.0)
    check("a percentage-only referral stays ordinary", E.score_earning(dict(base, referral={"available": True, "amount_usd": None, "multi_level": False}))["referral"]["recruitment_shape"], False)
    check("french 'indique ... tiers' (indicates ... third parties) is not a referral or multi-level",
          E.extract_referral("la fiche indique qu'aucune donnée n'est partagée avec des tiers"), {"available": False, "amount_usd": None, "multi_level": False})
    # ---- "50$" (sign after the number) in capital too
    check("capital: '500$' deposit read", E.extract_capital_usd("Deposit 500$ and get a bonus", "bank_bonus"), 500.0)
    check("capital: russian deposit word", E.extract_capital_usd("Нужен депозит 100$ для участия", "airdrop"), 100.0)
    check("capital: airdrop with no figure stays 0", E.extract_capital_usd("Do the quests", "airdrop"), 0.0)
    check("portuguese", E.extract_referral("Ganhe R$ 50 de bônus. Indique um amigo e ganhe R$ 20")["available"], True)

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
    # ---- the THIRD hard floor: expected payout >= $10; unknown passes (operator 2026-11-16)
    check("floor: expected payout $9.99 fails even with value 90 and trust 90", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": 9.99}), False)
    check("floor: expected payout exactly $10 passes", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": 10.0}), True)
    check("floor: unknown expected payout passes", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": None}), True)
    check("floor: unknown expected payout is not absent either (a row scored before the floor existed)", E.expected_payout_ok({"value": 90.0, "trust": 90.0}), True)
    z = E.score_earning(dict(base, payout_usd=0.9, payout_kind="ev"))
    check("a $0.90 expected payout scores 40+ on value yet never passes the floors (the weakness this floor exists for)", (z["value"] >= 40.0, z["expected_payout_usd"], E.passes_floors(z)), (True, 0.9, False))
    u = E.score_earning(dict(base, payout_usd=None, payout_kind=None))
    check("unknown payout: expected payout is None, the payout component stays low (20-25)", (u["expected_payout_usd"], u["value_components"]["payout"]), (None, 20.0))
    check("probability-adjusted: a $20 airdrop (30% chance) is $6 expected and fails", (E.score_earning(dict(base, payout_usd=20.0))["expected_payout_usd"], E.passes_floors(E.score_earning(dict(base, payout_usd=20.0)))), (6.0, False))

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

    # ---- SCOPE (operator ruling): money INTO the pocket, not money SAVED. Deals / discounts / free games have no collectors at all.
    removed = {"epic_free_games", "gamerpower", "ozbargain", "mydealz", "dealabs", "tg:EpicSteamGames", "tg:FreeGamesNews", "tg:r_FreeGamesOnSteam"}
    check("removed sources are gone from the registry", sorted(removed & set(C.earning.SOURCES)), [])
    check("removed sources have no collector functions", [n for n in ("fetch_epic", "fetch_gamerpower") if hasattr(C, n)], [])
    check("only Doctor of Credit is left as an RSS feed", sorted(C.RSS_FEEDS), ["doctorofcredit"])
    check("the registry is exactly the income sources", sorted(C.earning.SOURCES),
          ["devpost", "doctorofcredit", "immunefi", "superteam", "tg:airdropfind", "tg:airdrops_io", "tg:criptinho", "tg:incrypted_airdrops", "zindi"])
    check("Galxe and the bounty-platform feeds are not built (operator)", [s for s in ("galxe", "yeswehack", "intigriti", "bugcrowd", "hackerone", "kucoin", "binance") if s in C.earning.SOURCES], [])
    for t, gated in [("free_game", True), ("freebie", True), ("credits", True), ("airdrop", False), ("hackathon", False),
                     ("bank_bonus", False), ("bounty", False), ("giveaway", False), ("cashback", False)]:
        check(f"not-income gate for type {t}", bool(C.earning.not_income_reason(t)), gated)

    # ---- platforms that pay for work: Superteam Earn, Zindi, Immunefi audit competitions (operator 2026-11-16). Network mocked.
    from datetime import timedelta
    C.CALL_DELAY = 0
    soon, later, past = (NOW + timedelta(days=9)).isoformat(), (NOW + timedelta(days=30)).isoformat(), (NOW - timedelta(days=2)).isoformat()
    ST = [
        {"id": "a", "slug": "global-usdc", "title": "Write a thread", "type": "bounty", "status": "OPEN", "rewardAmount": 1000, "token": "USDC", "deadline": soon,
         "sponsor": {"name": "Raenest"}, "isWinnersAnnounced": False, "_count": {"Submission": 5}},
        {"id": "b", "slug": "baobao", "title": "A narrative of buying BAOBAO", "type": "bounty", "status": "OPEN", "rewardAmount": 689655, "token": "BAOBAO", "deadline": soon,
         "sponsor": {"name": "BAOBAO"}, "isWinnersAnnounced": False},
        {"id": "c", "slug": "ukraine", "title": "Write about workshops", "type": "bounty", "status": "OPEN", "rewardAmount": 400, "token": "USDG", "deadline": soon,
         "sponsor": {"name": "Superteam Ukraine"}, "isWinnersAnnounced": False},
        {"id": "d", "slug": "closed", "title": "x", "type": "bounty", "status": "CLOSED", "rewardAmount": 10, "token": "USDC", "deadline": soon, "sponsor": {"name": "s"}},
        {"id": "e", "slug": "decided", "title": "x", "type": "bounty", "status": "OPEN", "rewardAmount": 10, "token": "USDC", "deadline": soon, "sponsor": {"name": "s"}, "isWinnersAnnounced": True},
        {"id": "f", "slug": "late", "title": "x", "type": "bounty", "status": "OPEN", "rewardAmount": 10, "token": "USDC", "deadline": past, "sponsor": {"name": "s"}},
        {"id": "g", "slug": "job", "title": "Design a landing page", "type": "project", "status": "OPEN", "rewardAmount": 500, "token": "USDC", "deadline": later, "sponsor": {"name": "Acme"}},
    ]
    DETAIL = {"global-usdc": {"region": "GLOBAL", "description": "<p>Do the work</p>"}, "ukraine": {"region": None}, "baobao": {"region": "GLOBAL"}, "job": {"region": "INDIA"}}

    def fake_json(url, params=None, headers=None, **kw):
        if "/details/" in url:
            return DETAIL[url.rsplit("/", 1)[1]]
        return ST
    real_get_json = C.get_json
    C.get_json = fake_json
    try:
        st_raws, _ = C.fetch_superteam(NOW)
    finally:
        C.get_json = real_get_json
    by = {r["ext_id"]: r for r in st_raws}
    check("superteam: only open, undecided, future listings", sorted(by), ["a", "b", "c", "g"])
    pa, pb, pc, pg = (C.build_payload(by[k], NOW) for k in ("a", "b", "c", "g"))
    check("superteam: region GLOBAL from the detail endpoint -> worldwide", pa["geo"], "worldwide")
    check("superteam: a chapter sponsor (Superteam Ukraine) with no region is NOT global", pc["geo"], "single")
    check("superteam: a detail region other than GLOBAL is single", pg["geo"], "single")
    check("superteam: a stablecoin competition is a pool with an expected value, never a per-person payout",
          (pa["payout_kind"], pa["pool_usd"], pa["payout_usd"]), ("ev", 1000.0, 100.0))
    check("superteam: a fixed-pay project's amount is stated", (pg["payout_kind"], pg["payout_usd"]), ("stated", 500.0))
    check("superteam: a project-token reward is NOT priced (no payout, no pool) and is flagged", (pb["payout_usd"], pb["pool_usd"], pb["token_reward"]["token"]), (None, None, "BAOBAO"))
    sa, sb, sc_ = E.score_earning(pa), E.score_earning(pb), E.score_earning(pc)
    check("a token-denominated reward caps VALUE at 30 (below the 40 floor: it never reaches the operator)", (sb["value"] <= 30.0, E.passes_floors(sb)), (True, False))
    check("the cap records what the value would have been", sb["value_capped_by"]["value_uncapped"] > sb["value"], True)
    check("a stablecoin reward is not capped", "value_capped_by" in sa, False)
    check("a chapter-scoped bounty scores lower on geography than a global one", sc_["value_components"]["geo"] < sa["value_components"]["geo"], True)
    check("stablecoins: USDC / USDG yes, BAOBAO / SOL / NOS no", [E.is_stablecoin(t) for t in ("USDC", "usdg", "BAOBAO", "SOL", "NOS")], [True, True, False, False, False])

    ZI = [
        {"id": "genai", "title": "GenAI for Good", "open": True, "reward_type": "prize", "reward": "$40 000 USD", "participations_count": 400, "organization": {"name": "Acme"},
         "start_time": past, "end_time": later, "entries_close_at": soon, "is_access_restricted": False},
        {"id": "knowledge", "title": "Learn", "open": True, "reward_type": "knowledge", "reward": "Knowledge", "end_time": later},
        {"id": "closed", "title": "Over", "open": False, "reward_type": "prize", "reward": "$1 000 USD", "end_time": later},
        {"id": "secret", "title": "Private", "open": True, "reward_type": "prize", "reward": "$1 000 USD", "end_time": later, "secret_code_required": True},
    ]
    C.get_json = lambda url, params=None, headers=None, **kw: ZI
    try:
        z_raws, _ = C.fetch_zindi(NOW)
    finally:
        C.get_json = real_get_json
    check("zindi: only an open, public, prize-paying competition", [r["ext_id"] for r in z_raws], ["genai"])
    pz = C.build_payload(z_raws[0], NOW)
    check("zindi: pool read from the reward text, expected value = pool / entrants, deadline = entries close",
          (pz["pool_usd"], pz["payout_kind"], pz["payout_usd"], pz["deadline"][:10], pz["geo"]), (40000.0, "ev", 100.0, soon[:10], "worldwide"))

    IM = [
        {"project": "Audit Competition | Plether", "slug": "plether", "maxBounty": 80000, "rewardsToken": "USDC", "launchDate": past, "endDate": soon, "kyc": True},
        {"project": "Standing bounty", "slug": "standing", "maxBounty": 500000, "rewardsToken": "USDC", "launchDate": past, "endDate": None, "kyc": False},
        {"project": "Finished comp", "slug": "done", "maxBounty": 70000, "rewardsToken": "USDC", "launchDate": past, "endDate": past, "kyc": False},
        {"project": "Open comp, no KYC", "slug": "nokyc", "maxBounty": 20000, "rewardsToken": "USDC", "launchDate": past, "endDate": later, "kyc": False},
        {"project": "Paid in a token", "slug": "tok", "maxBounty": 90000, "rewardsToken": "ABC", "launchDate": past, "endDate": later, "kyc": False},
    ]
    C.get_json = lambda url, params=None, headers=None, **kw: IM
    try:
        i_raws, _ = C.fetch_immunefi(NOW)
    finally:
        C.get_json = real_get_json
    check("immunefi: ONLY time-boxed competitions with a future end (never a standing program, never a finished one)", sorted(r["ext_id"].split(":")[0] for r in i_raws), ["nokyc", "plether", "tok"])
    ip = {r["ext_id"].split(":")[0]: C.build_payload(r, NOW) for r in i_raws}
    check("immunefi: KYC required is a fact (prereq + card), no KYC is a known fact too", (E.score_earning(ip["plether"])["kyc"], E.score_earning(ip["nokyc"])["kyc"]), (True, False))
    check("immunefi: the prize is a pool, not a payout", (ip["plether"]["pool_usd"], ip["plether"]["payout_usd"]), (80000.0, None))
    check("immunefi: a competition paid in a non-stablecoin token is capped like any token reward", E.score_earning(ip["tok"])["value"] <= 30.0, True)
    check("KYC unknown stays unknown (never 'no KYC')", E.score_earning(dict(base))["kyc"], None)
    from bot import notify as N
    check("the card shows the KYC fact", N._score_text("earning_opportunities", {"score": 1, "score_breakdown": E.score_earning(ip["plether"])}).endswith("KYC required"), True)
    check("the card shows 'no KYC' only when known", N._score_text("earning_opportunities", {"score": 1, "score_breakdown": E.score_earning(ip["nokyc"])}).endswith("no KYC"), True)

    # ---- facts handed to the writer for the platform sources (a 400-650 character post cannot carry a whole prize ladder and every step)
    from pipeline import post_facts as PF
    fp = dict(ip['plether'], source='zindi', deadline='2026-12-13T23:59:00+00:00', geo='worldwide', enriched={
        'steps': ['Sign up on Raenest using code: community', 'Choose one track', 'Publish one long article on Medium or Substack, plus one X post that is long enough to be cut down at a boundary',
                  'Include your unique Raenest referral code or link', 'Post the video', 'Tag the sponsor'],
        'deadline': '14 December 2026 at 07:29', 'eligibility': 'Must have an account; must use a referral code; open to all', 'reward': '$5 000 USD in prizes: 1st $2,500 USD, 2nd $1,500 USD, 3rd $1,000 USD, plus a lot more detail that does not fit'})
    ff = PF.facts_earning_opportunities(fp)
    check('facts: a referral / promo-code step is never handed to the writer', ('referral' in ff.lower(), 'code: community' in ff.lower()), (False, False))
    check('facts: at most 3 steps, then a pointer to the page', (ff.count("\n  1. "), ff.count("\n  4. "), 'further steps' in ff), (1, 0, True))
    check('facts: a structured platform deadline beats the page text (the page shows the same instant in local time)', ('Deadline: Dec 13' in ff, '14 December' in ff), (True, False))
    check('facts: eligibility clauses about a referral code are dropped', 'must use a referral code' in ff, False)

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
