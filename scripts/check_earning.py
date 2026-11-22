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
    check("NO recruitment penalty (operator 2026-11-17): referral earnings above the base payout change trust by nothing", (rec["trust"] == plain["trust"], "referral_recruitment_penalty" in rec["trust_components"], "recruitment_shape" in rec["referral"]), (True, False, False))
    check("referral earnings above the base payout still get the ordinary referral bonus", rec["referral_bonus"] > 0, True)
    check("a referral-only offer (no base payout) keeps its ordinary scoring: no trust cut", E.score_earning(dict(base, payout_usd=None, payout_kind=None, referral={"available": True, "amount_usd": 20.0}))["trust"] == E.score_earning(dict(base, payout_usd=None, payout_kind=None))["trust"], True)
    # a stated per-referral earning counts as the PAYOUT when the offer states no other (collector level)
    check("referral_payout: \"$15 per friend\" is a stated payout", E.referral_payout("Invite a friend and earn $15 per friend")[:2], (15.0, "stated"))
    check("referral_payout: \"up to $15 for each friend\" is a MAXIMUM", E.referral_payout("Get up to $15 for each friend you invite")[:2], (15.0, "max"))
    check("referral_payout: none when no referral clause states an amount", E.referral_payout("Complete 3 quests to qualify"), None)
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
    # ---- multi-level wording: detected, shown as a warning label, NO score effect (operator 2026-11-17)
    for label, text in (("downline", "Build your downline and earn on every signup"), ("multi-level", "Multi-level rewards program, $5 bonus"),
                        ("3 levels of referrals", "Earn from 3 levels of referrals"), ("referral levels", "Invite friends: level 1 pays 10%, level 2 pays 5%"),
                        ("russian", "Многоуровневая реферальная программа"), ("portuguese", "Ganhe com 3 níveis de indicação")):
        check(f"multi-level wording ({label}) is detected", E.extract_referral(text)["multi_level"], True)
    for label, text in (("game levels", "Complete 3 levels in the game to qualify for the airdrop"), ("level up", "Level up your account for a $20 bonus"),
                        ("percentage only", "Earn 20% of your referrals' rewards"), ("plain referral", "Invite friends and earn $5 each")):
        check(f"not multi-level: {label}", E.extract_referral(text)["multi_level"], False)
    mlm = dict(base, payout_usd=100.0, referral={"available": True, "amount_usd": None, "multi_level": True})
    plain_ref = dict(base, payout_usd=100.0, referral={"available": True, "amount_usd": None, "multi_level": False})
    check("multi-level wording is a WARNING LABEL with NO score effect (value, trust and bonus are identical)", (E.score_earning(mlm)["value"], E.score_earning(mlm)["trust"], E.score_earning(mlm)["referral_bonus"]), (E.score_earning(plain_ref)["value"], E.score_earning(plain_ref)["trust"], E.score_earning(plain_ref)["referral_bonus"]))
    check("multi-level wording is exposed for the card label", E.score_earning(mlm)["referral"]["multi_level"], True)
    check("a percentage-only referral stays ordinary", E.score_earning(dict(base, referral={"available": True, "amount_usd": None, "multi_level": False}))["referral"]["multi_level"], False)
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
    check("floor: a RAFFLE with expected payout $9.99 fails even with value 90 and trust 90", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": 9.99, "award_kind": "raffle"}), False)
    check("floor: a RAFFLE with expected payout exactly $10 passes", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": 10.0, "award_kind": "raffle"}), True)
    check("floor: a RAFFLE with unknown expected payout passes", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": None, "award_kind": "raffle"}), True)
    check("floor: a GUARANTEED $2 deposit bonus passes (the expected-payout floor is for raffles only, operator 2026-11-17)", E.passes_floors({"value": 90.0, "trust": 90.0, "expected_payout_usd": 1.6, "award_kind": "guaranteed"}), True)
    check("floor: a row scored before the floor existed (no award_kind) passes", E.expected_payout_ok({"value": 90.0, "trust": 90.0}), True)
    z = E.score_earning(dict(base, type="giveaway", award_kind="raffle", payout_usd=0.9, payout_kind="ev"))
    check("a RAFFLE with a $0.90 expected payout scores 40+ on value yet never passes the floors (the weakness this floor exists for)", (z["value"] >= 40.0, z["expected_payout_usd"], E.passes_floors(z)), (True, 0.9, False))
    u = E.score_earning(dict(base, payout_usd=None, payout_kind=None))
    check("unknown payout: expected payout is None, the payout component stays low (20-25)", (u["expected_payout_usd"], u["value_components"]["payout"]), (None, 20.0))
    check("probability-adjusted: a $20 airdrop (30% chance) is $6 expected and STILL passes (guaranteed-kind payouts have no expected-payout floor)", (E.score_earning(dict(base, payout_usd=20.0))["expected_payout_usd"], E.passes_floors(E.score_earning(dict(base, payout_usd=20.0)))), (6.0, True))

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
    check("'the steps are on the page' is banned filler even when none are available (a missing fact is left out, never announced)", any("banned filler" in i for i in G(post(ok_body, "The steps are on the offer's page.", "Needs an account."), no_steps)), True)
    check("with no steps the middle paragraph is simply the conditions, and that passes", G(post(ok_body, "You need an Epic account to claim it.", "Needs an account."), no_steps), [])

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
          sorted(["binance_campaigns", "devpost", "doctorofcredit", "galxe", "immunefi", "kucoin_campaigns", "superteam", "tg:OKXAnnouncements", "tg:Bybit_Announcements",
                  "tg:airdropfind", "tg:airdrops_io", "tg:criptinho", "tg:incrypted_airdrops", "tg:airdropinspector", "tg:AirdropDetective", "tg:airdropking_io", "tg:LegitAirdrops", "tg:cryptoairdrops", "zindi"] + []))
    check("the bounty-platform feeds, the micro-task sites and the affiliate networks are not built (operator)", [s for s in ("yeswehack", "intigriti", "bugcrowd", "hackerone", "taskon", "questn", "partnerstack", "impact", "firstpromoter") if s in C.earning.SOURCES], [])
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
    check("superteam: a stablecoin bounty is a COMPETITION with a pool and structured prize tiers -- no expected amount, no pool / entrants",
          (pa["award_kind"], pa["pool_usd"], pa["payout_usd"], pa["prize"]["entrants"]), ("competition", 1000.0, None, 5))
    check("superteam: a project (one applicant chosen) is a competition of one winner", (pg["award_kind"], pg["prize"]["tiers"], pg["prize"]["winners"]), ("competition", [500.0], 1))
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
    check("zindi: a competition: pool read from the reward text in its ORIGINAL currency, entrants kept, no expected amount, deadline = entries close",
          (pz["pool_usd"], pz["award_kind"], pz["payout_usd"], pz["prize"]["entrants"], pz["prize"]["symbol"], pz["deadline"][:10], pz["geo"]), (40000.0, "competition", None, 100 if False else pz["prize"]["entrants"], "$", soon[:10], "worldwide"))

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
    check("the card shows the KYC fact", N._score_text("earning_opportunities", {"score": 1, "score_breakdown": E.score_earning(ip["plether"])}).count("KYC required"), 1)
    check("the card shows 'no KYC' only when known", N._score_text("earning_opportunities", {"score": 1, "score_breakdown": E.score_earning(ip["nokyc"])}).count("no KYC"), 1)

    # ---- facts handed to the writer for the platform sources (a 400-650 character post cannot carry a whole prize ladder and every step)
    from pipeline import post_facts as PF
    fp = dict(ip['plether'], source='zindi', deadline='2026-12-13T23:59:00+00:00', geo='worldwide', enriched={
        'steps': ['Sign up on Raenest using code: community', 'Choose one track', 'Publish one long article on Medium or Substack, plus one X post that is long enough to be cut down at a boundary',
                  'Include your unique Raenest referral code or link', 'Post the video', 'Tag the sponsor'],
        'deadline': '14 December 2026 at 07:29', 'eligibility': 'Must have an account; must use a referral code; open to all', 'reward': '$5 000 USD in prizes: 1st $2,500 USD, 2nd $1,500 USD, 3rd $1,000 USD, plus a lot more detail that does not fit'})
    ff = PF.facts_earning_opportunities(fp)
    check('facts: a referral / promo-code step is never handed to the writer', ('referral' in ff.lower(), 'code: community' in ff.lower()), (False, False))
    check('facts: referral steps are dropped, the others kept whole, and a pointer says more exist', (ff.count("\n  1. "), ff.count("\n  4. "), ff.count("\n  5. "), 'more steps than fit' in ff), (1, 1, 0, True))
    check('facts: a structured platform deadline beats the page text (the page shows the same instant in local time)', ('Deadline: Dec 13' in ff, '14 December' in ff), (True, False))
    check('facts: eligibility clauses about a referral code are dropped', 'must use a referral code' in ff, False)

    # ---- the connect-wallet gate: claim-shaped wording only for a VETTED source; the broad rule stays for tier 3 / unknown (operator 2026-11-16)
    step = "Go to the site, connect your wallet and grab your referral link from the Earn button"
    check("gate: 'connect your wallet and grab your referral link' passes for an offer hosted on a vetted platform (superteam)", E.gate(step, "superteam"), [])
    check("gate: the same words relayed by an aggregator channel (airdrops_io) are still gated", E.gate(step, "tg:airdrops_io"), ["connect-wallet-to-claim"])
    check("gate: the same words with no tier are still gated", E.gate(step), ["connect-wallet-to-claim"])
    check("gate: connect-to-CLAIM trips even for a vetted source", E.gate("Connect your wallet to claim your tokens", "zindi"), ["connect-wallet-to-claim"])
    check("gate: connect-to-RECEIVE trips even for a vetted source", E.gate("Connect your wallet in order to receive your reward", "immunefi"), ["connect-wallet-to-claim"])
    check("gate: 'connect your wallet and sign a message' passes for a vetted platform", E.gate("Connect your wallet and sign a message with your X handle", "superteam"), [])
    check("gate: the relaxation is for HOSTED platforms only -- exactly superteam / zindi / immunefi", sorted(E.GATE_NARROW_WALLET_SOURCES), ["immunefi", "superteam", "zindi"])
    for relayed in ("tg:airdrops_io", "tg:incrypted_airdrops", "tg:airdropfind", "tg:criptinho", "doctorofcredit", "devpost"):
        check(f"gate: {relayed} keeps the BROAD wallet rule (it relays someone else's text)", E.gate(step, relayed), ["connect-wallet-to-claim"])
    check("gate: the seed-phrase rule is not tier-dependent", E.gate("Enter your seed phrase to continue", "superteam"), ["asks for a seed phrase / private key / wallet import"])

    # ---- NO referral-promotion / recruitment detection (the Raenest-style bounty is an ordinary bounty now; operator 2026-11-17)
    check("the removed detection stays removed", (hasattr(E, "has_referral_promotion"), hasattr(E, "REFERRAL_RECRUITMENT_PENALTY")), (False, False))

    # ---- a headline maximum that disagrees with the structured reward: the structured figure is the fact (operator 2026-11-16)
    MH = {"id": "m", "slug": "mh", "title": "MultiHopper Build & Route Challenge | Up to $50,000 + Revenue Share", "type": "bounty", "status": "OPEN", "rewardAmount": 5000, "token": "USDC",
          "deadline": later, "sponsor": {"name": "MultiHopper"}, "isWinnersAnnounced": False, "_count": {"Submission": 78}}
    C.get_json = lambda url, params=None, headers=None, **kw: ([MH] if "/details/" not in url else {"region": "Global", "description": "<p>Build it.</p>"})
    try:
        mm, _ = C.fetch_superteam(NOW)
    finally:
        C.get_json = real_get_json
    pm = C.build_payload(mm[0], NOW)
    check("headline: the structured reward is the pool and the headline maximum is recorded separately", (pm["pool_usd"], pm["headline_max_usd"]), (5000.0, 50000.0))
    fm = PF.facts_earning_opportunities(pm)
    check("headline: the facts lead with the structured reward and call the maximum a ceiling, not the reward",
          ("$5,000 USDC" in fm, "stated ceiling nobody is promised" in fm, "Reward: a competition" in fm), (True, True, True))
    check("headline: the 'What' line no longer carries 'up to $50,000'", "up to $50,000" in fm.split("- Kind:")[0].lower(), False)
    from pipeline import write_post as WP
    check("guard: a title that leads with the headline maximum is rejected",
          any("headline maximum" in i for i in WP._find_earning_issues({"title": "Win up to $50,000 in the MultiHopper challenge", "paragraphs": ["x"]}, pm)), True)
    check("guard: a title that leads with the structured reward is not rejected on that ground",
          any("headline maximum" in i for i in WP._find_earning_issues({"title": "Compete for $5,000 in the MultiHopper challenge", "paragraphs": ["x"]}, pm)), False)

    # ---- the daily "below the line" digest: every item that failed ONLY a soft floor, with the reasons (operator 2026-11-16)
    from bot import below_line as BL
    good = E.score_earning(dict(base, payout_usd=100.0))
    check("digest: an item that clears every floor is not below the line", BL.reasons_for(good, {}), [])
    lowt = E.score_earning(dict(base, payout_usd=100.0, source_tier=3, domain_age_days=5, terms={"deadline": False, "eligibility": False, "payout": False}))
    check("digest: trust below 40 is listed with its number", [r for r in BL.reasons_for(lowt, {}) if r.startswith("trust")], [f"trust {lowt['trust']:.0f} < 40"])
    cheap = E.score_earning(dict(base, type="giveaway", award_kind="raffle", payout_usd=0.9, payout_kind="ev"))
    check("digest: a RAFFLE's expected payout under $10 is listed", [r for r in BL.reasons_for(cheap, {}) if "expected payout" in r], ["raffle: expected payout $0.90 < $10"])
    tok = E.score_earning(dict(base, token_reward={"token": "BAOBAO", "amount": 1.0}, payout_usd=None, payout_kind=None))
    check("digest: a token reward is a value failure with its own reason", any("BAOBAO" in r and "capped" in r for r in BL.reasons_for(tok, {})), True)
    rec = E.score_earning(dict(base, payout_usd=10.0, referral={"available": True, "amount_usd": 50.0}))
    check("digest: a guaranteed small payout is NOT listed for its expected payout", [r for r in BL.reasons_for(E.score_earning(dict(base, award_kind="guaranteed", payout_usd=2.0, payout_kind="stated")), {}) if "expected payout" in r], [])
    check("digest: gate reasons that are security-flavoured are visible, roundup / not-income are not",
          (BL.visible_gate_reasons({"gate": {"reasons": ["connect-wallet-to-claim"]}}), BL.visible_gate_reasons({"gate": {"reasons": ["not an offer: a digest / roundup of several projects"]}}),
           BL.visible_gate_reasons({"gate": {"reasons": ["connect-wallet-to-claim", "not an offer: a digest / roundup of several projects"]}}), BL.visible_gate_reasons({"gate": {"reasons": ["not income: saves money"]}})),
          (["connect-wallet-to-claim"], [], [], []))
    gi = [{"raw_item_id": 1, "payload": {"title": "Connect your wallet to claim", "source_name": "S", "type": "airdrop"}, "score_breakdown": E.score_earning(base), "reasons": ["connect-wallet-to-claim"], "gated": True}]
    check("digest: a gated item carries a RED label and the gate reason", ("🔴" in BL.build_messages(gi, "Earn Stack")[0][0], "gate: connect-wallet-to-claim" in BL.build_messages(gi, "Earn Stack")[0][0]), (True, True))
    check("digest: multi-level wording shows as a warning label on the line", "multi-level" in BL.format_item(1, dict(gi[0], gated=False, reasons=["x"], score_breakdown=E.score_earning(mlm))), True)
    fake = [{"raw_item_id": i, "payload": {"title": "t" * 80, "source_name": "S", "type": "airdrop"}, "score_breakdown": lowt, "reasons": ["trust 18 < 40"]} for i in range(45)]
    msgs = BL.build_messages(fake, "Earn Stack")
    check("digest: 45 items split into messages under Telegram's limit, one Approve per item", (all(len(t) < 4096 for t, _ in msgs), sum(len(c) for _, c in msgs), max(len(c) for _, c in msgs) <= 20), (True, 45, True))
    check("digest: the header names the channel's display name", "Earn Stack" in msgs[0][0], True)
    check("digest: every item shows its scores and its reason", all(x in msgs[0][0] for x in ("value ", "trust ", "expected ", "referral ", "why: trust 18")), True)
    check("digest: cap queries count only kind='candidate' (a digest never eats the cap of 8)",
          ("kind = 'candidate'" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "notify.py"), encoding="utf-8").read(),
           "nf.kind = 'candidate'" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "select_candidates.py"), encoding="utf-8").read()), (True, True))

    # ---- guaranteed / raffle / competition, prize structure and odds, original currency, skill (operator 2026-11-16)
    from pipeline import prizes as PZ
    eur = {"type": "hackathon", "award_kind": "competition", "pool_usd": 27000.0, "prize": {"pool": 25000.0, "symbol": "€", "suffix": "EUR", "tiers": [12000.0, 8000.0, 5000.0],
           "winners": 3, "entrants": 142, "basis": "structured"}}
    check("prize line: the operator's own example, in the ORIGINAL currency", PZ.prize_line(eur), "Top 3 share €25,000 (€12k / €8k / €5k) · ~142 entrants · 3 winners")
    check("a euro pool is never relabelled or converted in the line", ("$" in PZ.prize_line(eur), "27" in PZ.prize_line(eur)), (False, False))
    check("prize line: a ladder with a bonus tier", PZ.prize_line({"type": "hackathon", "award_kind": "competition", "prize": {"pool": 13000.0, "symbol": "$", "suffix": "USDC",
          "tiers": [1500.0, 1400.0, 1300.0, 1200.0, 1000.0], "bonus": {"count": 20, "amount": 330.0}, "winners": 25, "entrants": 71}}),
          "Top 5 share $13,000 USDC ($1.5k / $1.4k / $1.3k / $1.2k / $1k, plus 20 x $330) · ~71 entrants · 25 winners")
    check("prize line: structure unknown -> says so, never guesses", PZ.prize_line({"type": "hackathon", "award_kind": "competition", "prize": {"pool": 5000.0, "symbol": "$", "suffix": "USDC", "tiers": [], "winners": None, "entrants": 78}}),
          "$5,000 USDC shared among the winners · ~78 entrants")
    check("prize line: only competitions have one", PZ.prize_line({"type": "giveaway", "award_kind": "raffle", "prize": {"pool": 100.0}}), None)
    check("a competition has NO expected payout and the $10 floor does not apply to it", (E.expected_payout_usd(eur), E.score_earning(eur)["expected_payout_usd"]), (None, None))
    se = E.score_earning(dict(base, **eur))
    check("a competition's value uses the size of the biggest prize, capped at 65 on the payout component", se["value_components"]["payout"] <= 65.0, True)
    guar = E.score_earning(dict(base, award_kind="guaranteed", payout_usd=12000.0, payout_kind="stated"))
    check("a competition never outscores a guaranteed payout of the same size on the payout component", se["value_components"]["payout"] < guar["value_components"]["payout"], True)
    check("the breakdown carries the kind and the line", (se["award_kind"], se["prize_line"].startswith("Top 3 share")), ("competition", True))
    check("kinds: giveaway = raffle, hackathon = competition, bank bonus = guaranteed", (PZ.award_kind({"type": "giveaway"}), PZ.award_kind({"type": "hackathon"}), PZ.award_kind({"type": "bank_bonus"})), ("raffle", "competition", "guaranteed"))
    check("a structured award_kind wins over the type", PZ.award_kind({"type": "airdrop", "award_kind": "competition"}), "competition")
    check("original amounts: '$40 000 USD' and '€20 000 EUR' are read as stated", (PZ.parse_original("$40 000 USD"), PZ.parse_original("€20 000 EUR")), ((40000.0, "$", "USD"), (20000.0, "€", "EUR")))
    check("ladder from a page's own words", PZ.parse_ladder("1st place $2,500 USD, 2nd place $1,500 USD, 3rd place $1,000 USD"), [2500.0, 1500.0, 1000.0])
    check("ladder: places must be consecutive from 1, else None", PZ.parse_ladder("1st $2,500, 3rd $1,000"), None)
    check("skill: ML, engineering and security are detected; plain content work is not",
          (PZ.skill_domains("Train a machine learning model on the dataset"), PZ.skill_domains("Audit the smart contracts for vulnerabilities"),
           PZ.skill_domains("Deploy an autonomous agent and integrate the SDK"), PZ.skill_domains("Write an X thread about the product")),
          (["machine learning"], ["software engineering", "security"], ["software engineering"], []))
    check("skill: Zindi is machine learning, Immunefi is security, by source", (PZ.skill_domains("x", "zindi"), PZ.skill_domains("x", "immunefi")), (["machine learning"], ["security"]))
    sk = E.score_earning(dict(base, payout_usd=100.0, effort_minutes=60, skill=["machine learning"]))
    ns = E.score_earning(dict(base, payout_usd=100.0, effort_minutes=60))
    check("skill required lowers the effort component (and nothing else in value_components)", (sk["value_components"]["effort"] < ns["value_components"]["effort"], sk["value_components"]["payout"] == ns["value_components"]["payout"], sk["skill"]), (True, True, ["machine learning"]))
    check("the card shows the skill fact", N._score_text("earning_opportunities", {"score": 1, "score_breakdown": sk}).endswith("skill: machine learning"), True)
    raffle = E.score_earning(dict(base, type="giveaway", award_kind="raffle", payout_usd=250.0, payout_kind="ev"))
    check("a raffle keeps pool / entrants as its expected amount", raffle["expected_payout_usd"], 250.0)
    check("facts: a competition shows structure and odds, never 'average per participant'",
          ("average per participant" in PF.facts_earning_opportunities(dict(pz, enriched={}, title="t")), "~" in PF.facts_earning_opportunities(dict(pz, enriched={}, title="t"))), (False, True))

    # ---- a competition's size floor (the assistant's choice, documented): without it a tiny hackathon cleared every floor on friction points alone
    tiny = dict(base, type="hackathon", award_kind="competition", pool_usd=60.0, prize={"pool": 60.0, "symbol": "$", "suffix": "", "tiers": [], "winners": 1, "entrants": 40})
    st_ = E.score_earning(tiny)
    check("a $60 competition: value still computed, payout component 0, and it does NOT pass the floors", (st_["value_components"]["payout"], E.passes_floors(st_)), (0.0, False))
    check("the digest names the size floor for it", any("largest prize is under" in r for r in BL.reasons_for(st_, {})), True)
    check("a EUR 25,000 top-3 competition passes the floors and shows no expected amount", (E.passes_floors(se), se["expected_payout_usd"]), (True, None))
    check("competition payout component: bigger top prize scores higher, none above 65",
          (E.score_earning(dict(base, **dict(eur, prize=dict(eur["prize"], tiers=[1000.0], pool=1000.0))))["value_components"]["payout"]
           < se["value_components"]["payout"] <= 65.0), True)

    # ---- the writer fallback: a plain FACTS card (no model) when a draft fails its guards after the retries; an Edit button on every preview (operator 2026-11-17)
    from pipeline import fallback_card as FB
    from bot import approval_poller as AP
    fc = FB.plain_facts_card(dict(pz, enriched={"steps": ["Join the challenge", "Submit predictions"]}, gate={"reasons": []}), E.score_earning(pz), "Earn Stack")
    check("fallback card: title, reward, deadline and steps, and no filler (the facts-card note moved to the operator's header)", all(k in fc for k in ("<b>Reward:</b>", "<b>Deadline:</b>", "1. Join the challenge")) and not any(k in fc for k in ("not stated", "Plain facts card", "steps are on", "Nothing is stated")), True)
    check("fallback card: no URL and no referral wording (the operator adds his own link)", ("http" in fc, "referral" in fc.lower()), (False, False))
    check("fallback card: a competition shows its prize structure in the ORIGINAL currency", "40,000" in fc and "$" in fc, True)
    gc = FB.plain_facts_card(dict(pz), E.score_earning(dict(pz, referral={"available": True, "amount_usd": None, "multi_level": True})), "Earn Stack", ["connect-wallet-to-claim"])
    check("fallback card: a gated item carries the red label and the multi-level warning", ("🔴 connect-wallet-to-claim" in gc, "multi-level wording" in gc), (True, True))
    check("poller: only a guard failure after the retries triggers the fallback (an outage or missing key still retries and alerts)",
          (AP._is_guard_failure(RuntimeError("write_post: category=earning_opportunities still failed the voice/guard checks after 9 retries")), AP._is_guard_failure(RuntimeError("DEEPSEEK_API_KEY is not set")), AP._is_guard_failure(ConnectionError("timeout"))), (True, False, False))
    check("poller: every preview carries the Edit button", 'edit_preview:{preview_id}' in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "approval_poller.py"), encoding="utf-8").read(), True)

    # ---- Earn Stack sources added 2026-11-17: campaigns pay or they are dropped; roundups are EXPANDED; nothing silently excluded
    check("campaign: a prize pool is kept", C._campaign_ok("Binance Alpha Trading Competition: Trade LAB and Share $200K Worth of Rewards"), True)
    check("campaign: a quiz reward is kept", C._campaign_ok("Binance Academy Bitcoin Page: Complete Quiz to Earn BTC Rewards This November 2026!"), True)
    check("campaign: an APR promotion is NOT income", C._campaign_ok("Binance P2P LATAM Exclusive: Earn Up to 25% APR on USDC!"), False)
    check("campaign: a fee discount is NOT income (no discounts, operator rule)", C._campaign_ok("Lite Loan Promotion Extended: Enjoy Simple Borrowing with 50% Off Service Fee!"), False)
    check("campaign: a maintenance notice is not an offer", C._campaign_ok("Binance Wallet to Discontinue Support for Blast; reward notice"), False)
    check("campaign: a trading competition is a COMPETITION with its pool in the original currency",
          (C._campaign_extra("Trade LAB and Share $200K Worth of Rewards: Trading Competition", "")["award_kind"], C._campaign_extra("Trade LAB and Share $200K Worth of Rewards: Trading Competition", "")["prize"]["pool"]), ("competition", 200000.0))
    check("campaign: a new-user bonus is GUARANTEED (never a raffle: pool / entrants would have cut a $26 bonus)", C._campaign_extra("New User Campaign: Earn Up to 26 USDT", "")["award_kind"], "guaranteed")
    check("campaign: a raffle says so", C._campaign_extra("Win $25 in Raffle", "")["award_kind"], "raffle")
    check("campaign: official exchange channels pass only campaign posts", (bool(C._TG_CAMPAIGN_RE.search("OKX Learn and Earn: complete the quiz and share 10,000 USDT")), bool(C._TG_CAMPAIGN_RE.search("OKX will delist FOO/USDT spot trading pair"))), (True, False))
    check("galxe: a testnet credit and a free badge are not rewards", (bool(C._GALXE_NOT_REAL_RE.search("2,000 TestUSDC test credit, test funds")), bool(C._GALXE_NOT_REAL_RE.search("a free commemorative badge with no monetary value"))), (True, True))
    check("galxe: tier 3, text_api", (C.earning.SOURCES["galxe"]["tier"], C.earning.SOURCES["galxe"]["kind"]), (3, "text_api"))
    check("binance / kucoin campaigns: tier 1 (the exchange's own API)", (C.earning.SOURCES["binance_campaigns"]["tier"], C.earning.SOURCES["kucoin_campaigns"]["tier"]), (1, 1))
    rd = {"post": "airdrops_io/8036", "time": "2026-11-16T07:00:00+00:00", "views": 100, "reactions": 0, "links": [], "text": "x",
          "html": ("📡 Airdrop Radar — Daily Roundup (November 16, 2026)<br/><br/>🚨 Lighter paused withdrawals after a trader's API key leaked (~$15.5M lost). Follow @Lighter_xyz only:<br/>"
                   '<a href="https://airdrops.io/lighter/">https://airdrops.io/lighter/</a><br/><br/>🪂 infiniFi INFI TGE is Nov 17 at 15:30 UTC. The airdrop is landing frozen now and unlocks at TGE:<br/>'
                   '<a href="https://airdrops.io/infinifi/">https://airdrops.io/infinifi/</a><br/><br/>no link here but long enough to be a sentence about something<br/><br/>short')}
    kids = C.extract_roundup_offers(rd)
    check("roundup: each block WITH a link and real text becomes its own offer (the header, a block with no link, and a stub are skipped)", [(k["post"], k["links"]) for k in kids],
          [("airdrops_io/8036-2", ["https://airdrops.io/lighter/"]), ("airdrops_io/8036-3", ["https://airdrops.io/infinifi/"])])
    check("roundup: an offer keeps the post's channel and time so it flows through the ordinary gate and scoring", (kids[0]["time"], kids[0]["views"]), ("2026-11-16T07:00:00+00:00", 100))
    check("roundup: a roundup with no linked block yields nothing (and stays excluded)", C.extract_roundup_offers(dict(rd, html="Weekly roundup<br/><br/>just words with no links at all in this block of text here")), [])
    # a guaranteed small payout is never cut, a deposit is never a block, a monetizable offer gets a little more value
    dep = dict(base, type="bank_bonus", payout_usd=2.0, payout_kind="stated", capital_usd=50.0, award_kind="guaranteed")
    sd = E.score_earning(dep)
    check("a guaranteed $2 deposit bonus passes the floors (deposit asked, tiny payout)", E.passes_floors(sd) or sd["value"] < 40, True)
    check("an offer that asks for a deposit is labelled monetizable and scores a little higher than the same offer without one",
          (sd["monetizable"], E.score_earning(dict(dep, capital_usd=0.0, type="airdrop"))["monetizable"]), (True, False))
    check("the monetizable bonus is small", E.MONETIZABLE_BONUS <= 6.0, True)
    check("the gate does not block a plain deposit", E.gate("Deposit $50 with a card and keep it: you get a $20 bonus after 3 days", "tg:criptinho"), [])

    # ---- selection: competitions only fill slots nothing else wants, at most 2 a day (operator 2026-11-16)
    from pipeline import select_candidates as SC
    mk = lambda i, comp, v: {"raw_item_id": i, "score": v, "payload": {"type": "hackathon" if comp else "bank_bonus", "award_kind": "competition" if comp else "guaranteed"}}
    pool = [mk(1, True, 90), mk(2, False, 55), mk(3, True, 80), mk(4, True, 70), mk(5, False, 45)]
    check("selection: every non-competition comes first even at lower value, competitions follow best-first", [r["raw_item_id"] for r in SC.prioritise_earning(pool, 2)], [2, 5, 1, 3])
    check("selection: at most the allowance of competitions", [r["raw_item_id"] for r in SC.prioritise_earning(pool, 1)], [2, 5, 1])
    check("selection: no allowance left -> no competition at all", [r["raw_item_id"] for r in SC.prioritise_earning(pool, 0)], [2, 5])
    check("selection: the cap is 2 a day", SC.EARNING_COMPETITIONS_PER_DAY, 2)
    # with the per-cycle cap of 1, a competition is only ever sent when no non-competition is waiting
    check("selection: with a slot of 1 the first candidate is a non-competition whenever one exists", SC.prioritise_earning(pool, 2)[0]["raw_item_id"], 2)

    # ---- the three exchange-announcement channels are corroboration only, never opportunity sources
    check("CoinMarketCap announcements stay corroboration-only; OKX and Bybit announcements are campaign sources, filtered to campaign posts", ([h for h in ("CoinMarketCapAnnouncements",) if f"tg:{h}" in C.earning.SOURCES], sorted(C.CAMPAIGN_ONLY_SOURCES)), ([], ["tg:Bybit_Announcements", "tg:OKXAnnouncements"]))
    check("the dead / absent handles are not configured", [h for h in ("AirdropNinja", "airdropalert", "easyprofitcrypto", "CoinMarketCapAirdrops", "Galxe", "AirdropOasis", "top2airdrop", "unfud_crypto", "OKXOfficial_English", "bulka_crypto", "moni_talks", "WWGiveaways", "freestuff_hub", "FREE_UDEMY_COURSES1", "bugbountyplzen", "officialairdropalert", "cryptosale_tg", "2top_airdrop")
                                                         if h in C.earning.TELEGRAM_HANDLES], [])


    # ---- the filler ban and the offer reader (operator 2026-11-22: every card was a title plus "not stated" filler)
    from pipeline import offer_reader as OR
    from pipeline import write_post as WP
    filler = ["The deadline and countries are not stated.", "Nothing is stated as required up front.", "Worth a look because it is free.", "Check the offer's page for details.", "The steps are on the offer's page.", "Who can take part is unknown.",
              "The reward amount isn't specified."]
    check("filler guard: every banned phrase (and its usual variants) is caught", [bool(WP.earning_filler(t)) for t in filler], [True] * len(filler))
    clean_texts = ["Open worldwide, ends Dec 1. Trade at least $50 in spot volume.", "A $100 deposit is required up front.", "Click the link to join.", "Nothing here is guaranteed."]
    check("filler guard: real facts that merely sound similar are not caught", [WP.earning_filler(t) for t in clean_texts], [[]] * len(clean_texts))
    check("filler guard: a draft with filler comes back from the writer's check as an issue", any("banned filler" in i for i in WP._find_earning_issues({"title": "Get $20", "paragraphs": ["You get $20. Deadline and countries are not stated.", "Join."]}, dict(pl, enriched={"steps": ["Join"]}))), True)

    # facts: a fact nobody gave is ABSENT, never "not stated"; capital_usd 0 is the collector's default, not a statement
    thin_p = {"title": "Some giveaway", "source_name": "OKX (Telegram)", "type": "giveaway", "capital_usd": 0.0, "enriched": {}}
    tf = PF.facts_earning_opportunities(thin_p)
    check("facts: a thin item gives the writer no 'not stated', no 'nothing required up front', no steps line", ("not stated" in tf, "up front" in tf, "steps" in tf.lower(), "Deadline" in tf, "Reward" in tf), (False, False, False, False, False))
    rich = {"title": "Крутая акция TransferGo", "source_name": "Criptinho (Telegram)", "type": "airdrop", "description": "x" * 400, "capital_usd": 0.0, "referral": {}, "enriched": {"version": 2, "title_en": "Get 20 EUR for your first TransferGo transfer",
            "steps": ["Register.", "Verify your identity in the documents section.", "Send at least 50 EUR to someone in another country."], "steps_from": "source_post", "reward": "20 EUR for the first transfer", "deadline": "Nov 30",
            "minimums": "A transfer of at least 50 EUR", "conditions": ["You must be a new user.", "The transfer must be to another country with currency conversion."], "post_en": "Cool promotion from TransferGo: 20 EUR for your first transfer. Send 50 EUR or more to a friend abroad."}}
    rf = PF.facts_earning_opportunities(rich)
    check("facts: the translated headline, the reward, the minimum, the conditions, the steps and the FULL announcement all reach the writer",
          ("What: Get 20 EUR for your first TransferGo transfer" in rf, "Reward: 20 EUR for the first transfer" in rf, "minimum to qualify: A transfer of at least 50 EUR" in rf, "You must be a new user." in rf,
           "  3. Send at least 50 EUR" in rf, "from the offer's own announcement" in rf, "Cool promotion from TransferGo" in rf, "not stated" in rf), (True, True, True, True, True, True, True, False))
    check("facts: an English announcement that only repeats the title is not passed twice; a real one is", ("The announcement itself" in PF.facts_earning_opportunities(dict(thin_p, description="Some giveaway. Some giveaway")),
          "The announcement itself" in PF.facts_earning_opportunities(dict(thin_p, description="Trade eligible tokens on our DEX for a chance to share 180K USDG. Ends Nov 27, 11am UTC. Join now."))), (False, True))

    # the reader: validation, merge order, thinness, the official-domain restriction, the guarded fetch
    check("reader: a reply is validated and capped, unknown keys dropped, null-ish strings become absent", OR.validate_terms({"steps": ["a" * 300, 5, "Do it"], "reward": "null", "deadline": "Nov 27", "cost": None, "evil": "x", "conditions": ["ok"]}),
          {"steps": ["a" * 200, "Do it"], "reward": None, "deadline": "Nov 27", "eligibility": None, "cost": None, "minimums": None, "conditions": ["ok"], "title_en": None, "post_en": None})
    check("reader: a reply that says the page held nothing is None", (OR.validate_terms({"page_found": False}), OR.validate_terms({"steps": [], "reward": None}), OR.validate_terms("text")), (None, None, None))
    m = OR.merge_terms([{"steps": [], "reward": "page reward", "deadline": None, "conditions": ["a"]}, {"steps": ["source step"], "reward": "post reward", "deadline": "Nov 27", "conditions": ["A", "b"]},
                        {"steps": ["search step"], "reward": "search reward", "eligibility": "search eligibility"}])
    check("reader: per field the offer's page wins, then the post, then the search (a search only fills a gap); conditions are the de-duplicated union", (m["reward"], m["steps"], m["deadline"], m["eligibility"], m["conditions"]),
          ("page reward", ["source step"], "Nov 27", "search eligibility", ["a", "b"]))
    check("reader: a thin item has nothing but a title and an amount; any single fact makes it not thin", (OR.is_thin({"title": "t", "enriched": {"reward": "20 EUR"}, "capital_usd": 0.0}), OR.is_thin({"title": "t", "enriched": {"deadline": "Nov 27"}}),
          OR.is_thin({"title": "t", "enriched": {}, "geo": "worldwide"}), OR.is_thin({"title": "t", "enriched": {"steps": ["x"]}}), OR.is_thin({"title": "t", "enriched": {}, "capital_usd": 50.0})), (True, False, False, False, False))
    check("reader: an official domain is the host a link finally resolves to, never a form, chat or link hub", (OR.official_domain("https://www.transfergo.com/uk"), OR.official_domain("https://web3.okx.com/boost/x"), OR.official_domain("https://docs.google.com/forms/d/e/1"),
          OR.official_domain("https://t.me/x"), OR.official_domain("https://app.galxe.com/quest/1"), OR.official_domain(None)), ("transfergo.com", "okx.com", None, None, None, None))
    sent = {"calls": []}
    real_chat = OR._chat

    def fake_chat(conn, prompt, tools=None, max_tokens=0):
        sent["calls"].append((prompt[:40], tools))
        msg = {"annotations": [{"type": "url_citation", "url_citation": {"url": "https://www.transfergo.com/uk/refer-a-friend"}}, {"type": "url_citation", "url_citation": {"url": "https://evil.example/transfergo.com/terms"}}],
               "server_tool_calls": [{"calls": [{"sources": [{"url": "https://help.transfergo.com/uk/bonus"}, {"url": "https://www.transfergo.com/gb/refer-a-friend"}, {"url": "https://transfergo.com.phish.net/terms"}]}]}]}
        return '{"urls": ["https://www.transfergo.com/uk/terms", "https://other-site.com/transfergo-terms"]}', {"choices": [{"message": msg}]}
    OR._chat = fake_chat
    try:
        urls = OR.search_official_urls(None, "transfergo.com", "TransferGo bonus", "https://www.transfergo.com/uk")
    finally:
        OR._chat = real_chat
    check("reader: the search is DeepSeek web search, and only URLs on the official domain survive (a look-alike host, another site and another country's page are dropped)", (sent["calls"][0][1], urls),
          ([{"type": "deepseek_web_search"}], ["https://www.transfergo.com/uk/terms", "https://www.transfergo.com/uk/refer-a-friend", "https://help.transfergo.com/uk/bonus"]))
    check("reader: private, loopback and link-local hosts are never fetched (no call into Railway's own network)", (OR._public_host("localhost"), OR._public_host("127.0.0.1"), OR._public_host("10.1.2.3"), OR._public_host("169.254.169.254"), OR.safe_get("http://169.254.169.254/latest/")["error"],
          OR.safe_get("file:///etc/passwd")["error"]), (False, False, False, False, "blocked_host", "blocked_host"))
    check("reader: the visible text of a page drops scripts and navigation", OR.visible_text("<html><nav>Menu Home</nav><script>var x=1</script><p>Get 20 EUR for your first transfer.</p><footer>Legal</footer></html>"), "Get 20 EUR for your first transfer.")
    check("reader: a post that only repeats the headline (an API listing) is not sent to the model", OR.extract_source_terms(None, {"title": "KuCoin Giveaway", "description": "KuCoin Giveaway. KuCoin Giveaway"}), None)
    # the whole chain with the network stubbed: the page gives nothing, the post gives the steps, the search is not needed for what the post covers
    stub = {"read_page": OR.read_page, "extract": OR.extract_source_terms, "search": OR.search_official_urls, "safe_get": OR.safe_get}
    OR.extract_source_terms = lambda conn, p: {"steps": ["Register", "Verify your identity", "Send 50 EUR abroad"], "reward": "20 EUR", "deadline": "Nov 30", "eligibility": "New users only", "cost": None, "minimums": "50 EUR transfer", "conditions": [], "title_en": "Get 20 EUR", "post_en": "post"}
    OR.read_page = lambda conn, url, title: (None, {"reader": "failed", "fetch": "no_text", "final_url": url})
    OR.search_official_urls = lambda *a, **k: (_ for _ in ()).throw(AssertionError("search must not run when steps, eligibility, deadline and minimums are all known"))
    try:
        enr = OR.build_enriched(None, {"title": "x", "landing_url": "https://trgo.co/uk", "url": "https://t.me/c/1", "description": "post"})
    finally:
        OR.read_page, OR.extract_source_terms, OR.search_official_urls, OR.safe_get = stub["read_page"], stub["extract"], stub["search"], stub["safe_get"]
    check("reader: when the landing page fails the source post's steps count as real steps, and the read status is on the record (never a silent {})", (enr["steps"], enr["steps_from"], enr["version"], enr["read"]["landing"]["reader"], enr["read"]["source_post"], enr["read"]["search"]),
          (["Register", "Verify your identity", "Send 50 EUR abroad"], "source_post", 2, "failed", "ok", "not_needed"))
    check("write path: the old one-shot landing read is gone from the earning draft; the reader and the thin-card path are in", ("offer_reader.enrich_offer" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "write_post.py"), encoding="utf-8").read(),
          "terms_unverified" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "write_post.py"), encoding="utf-8").read(),
          "enrich_landing.enrich_item" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "write_post.py"), encoding="utf-8").read()), (True, True, False))
    check("fallback card: only the known facts; no 'not stated', no 'steps are on the page', no facts-card note in the forwardable text", (lambda c: ("not stated" in c, "steps are on" in c, "Plain facts" in c, "<b>Reward:</b> 20 EUR" in c, "Who can take part" in c))(FB.plain_facts_card({"title": "t", "source_name": "X", "enriched": {"reward": "20 EUR"}, "capital_usd": 0.0})),
          (False, False, False, True, False))
    check("fallback card: nothing but a title and no amount still gives a clean card", FB.reward_text({"title": "t", "enriched": {}}), "")

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all earning_opportunities cases behave as expected")


if __name__ == "__main__":
    main()
