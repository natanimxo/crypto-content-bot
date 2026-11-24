"""No-database, no-network regression table for Crypto Notebook's weekly "technique of the week" explainer (2026-11-20) and the Rekt.news source.

    python scripts/check_technique.py        # exits 1 on any mismatch

The incidents below are real DefiLlama tracker rows (names, dates, amounts, labels) as stored on 2026-11-20.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from collectors import technique_explainer as T  # noqa: E402
from pipeline import post_facts as PF  # noqa: E402
from pipeline import score as SC  # noqa: E402
from pipeline import select_candidates as SEL  # noqa: E402
from pipeline import write_post as WP  # noqa: E402
from pipeline.llm_providers import template as TPL  # noqa: E402

FAILS: list[str] = []
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def inc(i, name, when, usd, cls="Token & Share Accounting", tech="Arithmetic Error", chain="Ethereum", ret=None):
    return {"raw_item_id": i, "name": name, "when": datetime.fromisoformat(when).replace(tzinfo=timezone.utc), "amount_usd": float(usd), "classification": cls,
            "technique": tech, "chain": chain, "returned_usd": ret}


# a small, realistic stand-in for the stored tracker: one type with depth, one with too few incidents, one that is old, one with unnamed targets
TSA = [inc(1, "Euler V1", "2023-03-13", 197e6, tech="Donation Attack", ret=240e6), inc(2, "Cetus CLMM", "2025-05-22", 223e6, chain="Sui"),
       inc(3, "K3 Capital", "2026-11-18", 3.42e6, tech="Incorrect Share Accounting")] + [inc(10 + n, f"Proto{n}", f"2024-0{1 + n % 9}-10", 1e6 + n * 1e5, tech="Infinite Mint") for n in range(8)]
FEW = [inc(100 + n, f"Few{n}", "2026-05-01", 2e6, cls="Governance") for n in range(4)]
OLD = [inc(200 + n, f"Old{n}", "2019-05-01", 2e6, cls="Reentrancy") for n in range(12)]
UNNAMED = [inc(300 + n, "Unknown", "2026-05-01", 2e6, cls="Rugpull") for n in range(12)]
ALL = TSA + FEW + OLD + UNNAMED


def main():
    # ---- which type, which examples
    check("only a type with 10+ incidents, 3 named examples and a recent incident is eligible", T.pick_type(ALL, {}, NOW), "Token & Share Accounting")
    check("an old-only type is not eligible (no incident in 2 years)", T.pick_type(OLD, {}, NOW), None)
    check("a type whose targets are all 'Unknown' has nothing to cite and is not eligible", T.pick_type(UNNAMED, {}, NOW), None)
    two = TSA + [inc(400 + n, f"Br{n}", "2026-09-0" + str(1 + n % 8), 5e6, cls="Bridge & Cross-Chain") for n in range(11)]
    check("the type covered longest ago (or never) comes first", T.pick_type(two, {"Token & Share Accounting": NOW}, NOW), "Bridge & Cross-Chain")
    check("a type never covered beats one covered", T.pick_type(two, {"Bridge & Cross-Chain": NOW}, NOW), "Token & Share Accounting")
    ex = T.pick_examples(TSA)
    check("examples: the largest, the most recent, and a different technique label, oldest first", [e["name"] for e in ex], ["Euler V1", "Cetus CLMM", "K3 Capital"])
    check("examples never include an unnamed target", [e["name"] for e in T.pick_examples(UNNAMED)], [])
    p = T.build_payload("Token & Share Accounting", TSA, NOW, T.iso_week(NOW))
    check("payload: week, count, first year, topic key", (p["week"], p["incident_count"], p["first_year"], p["topic_key"]), ("2026-W47", 11, 2023, "technique:token & share accounting"))
    check("payload: the tracker's last-12-months window", p["last12_count"], 1)
    check("the item is one per ISO week (week id in the external id)", T.iso_week(datetime(2026, 12, 28, tzinfo=timezone.utc)), "2026-W53")

    # ---- the facts the writer sees
    facts = PF.facts_technique_explainer(p)
    check("facts: the tracker's scale, the examples as given, and the 500k floor stated", all(s in facts for s in ("11, recorded from 2023", "Euler V1 -- March 2023", "$197 million (exactly $197,000,000)", "$500,000 or more")), True)
    check("facts: a recovery larger than the loss (Euler: $240M back on $197M) prints NO figure", ("$240 million" in facts, "state no recovery figure" in facts), (False, True))
    check("facts: money formatting", (PF.usd_words(1.5e9), PF.usd_words(283e6), PF.usd_words(3.42e6), PF.usd_words(720000)), ("$1.5 billion", "$283 million", "$3.4 million", "$720,000"))

    # ---- the guard: every figure, count and year must come from the facts; two examples must be named
    def issues(title, *paras):
        return WP._find_explainer_issues({"title": title, "paragraphs": list(paras)}, p)
    good = ("How does broken accounting drain a protocol?",
            "Token and share accounting is how a protocol tracks who owns what. The tracker records 11 incidents since 2023, about $432 million in all.",
            "Euler V1 lost $197 million in March 2023 (Donation Attack). Cetus CLMM lost about $223 million in May 2025 (Arithmetic Error).",
            "Read the audit reports and check who can change balances.")
    check("guard: a post that uses only the facts passes", issues(*good), [])
    check("guard: an invented dollar figure is refused", len(issues(good[0], good[1], "Euler V1 lost $350 million in March 2023. Cetus CLMM lost $223 million.", good[3])) >= 1, True)
    check("guard: a rounded figure within 6% is allowed ($223 million -> about $225 million)", issues(good[0], good[1], "Euler V1 lost $197 million in March 2023. Cetus CLMM lost about $225 million in May 2025.", good[3]), [])
    check("guard: an invented incident count is refused", any("count" in i for i in issues(good[0], "The tracker records 250 incidents of this kind.", good[2], good[3])), True)
    check("guard: a year that is not in the facts is refused", any("year 2019" in i for i in issues(good[0], good[1], "Euler V1 lost $197 million in 2019. Cetus CLMM lost $223 million.", good[3])), True)
    check("guard: a post that names fewer than two examples is refused", any("at least two" in i for i in issues(good[0], good[1], "Euler V1 lost $197 million in March 2023.", good[3])), True)
    check("guard: the $500,000 floor may be mentioned", issues(good[0], "The tracker covers incidents of $500,000 or more: 11 since 2023.", good[2], good[3]), [])
    check("guard: attacker speculation is refused by the shared speculation guard", len(WP._find_speculation_issues({"title": "t", "paragraphs": ["It was allegedly an inside job."]})) >= 1, True)

    # ---- scoring: every eligible type clears the review threshold of 50, even the weakest
    weakest = dict(p, incident_count=10, total_lost_usd=136e6, examples=p["examples"])
    for label, payload in (("the weakest real type (Market Manipulation: 10 incidents, $136M)", weakest), ("the strongest (Key Compromise: 138 incidents, $15B)", dict(p, incident_count=138, total_lost_usd=15e9))):
        b = {"impact": SC._technique_impact(payload), "novelty": 70.0, "credibility": 100.0, "actionability": 80.0}      # novelty 70 = the floor of the scorer's formula
        total = 0.35 * b["impact"] + 0.25 * b["novelty"] + 0.25 * b["credibility"] + 0.15 * b["actionability"]
        check(f"score: {label} clears 50 even at the novelty floor", total >= 50, True)
    check("score: a registered scorer with the four weighted components", ("technique_explainer" in SC.SCORERS, set(yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "category_config.yaml"), encoding="utf-8"))["technique_explainer"]["score_weights"])), (True, {"impact", "novelty", "credibility", "actionability"}))

    # ---- wiring
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chans = yaml.safe_load(open(os.path.join(root, "config", "channel_config.yaml"), encoding="utf-8"))
    cats = yaml.safe_load(open(os.path.join(root, "config", "category_config.yaml"), encoding="utf-8"))
    check("wiring: Crypto Notebook carries the explainer and its channel cap is the sum of its three category caps", ("technique_explainer" in chans["crypto_notebook"]["categories"], chans["crypto_notebook"]["soft_daily_cap"], sum(cats[c]["soft_daily_cap"] for c in chans["crypto_notebook"]["categories"])), (True, 9, 9))
    check("wiring: a FactSpec, a post computer, a triage template, a freshness window of 7 days, no age bonus", ("technique_explainer" in PF.FACT_SPECS, "technique_explainer" in WP.POST_COMPUTERS, "technique_explainer" in TPL.TEMPLATES, SEL.FRESHNESS_MAX_AGE_HOURS.get("technique_explainer"), "technique_explainer" in SEL.NO_AGE_BONUS_CATEGORIES), (True, True, True, 168.0, True))
    check("wiring: the explainer is speculation- and figure-checked", ("technique_explainer" in WP.SPECULATION_CHECKED_CATEGORIES, "technique_explainer" in WP.EXPLAINER_CHECKED_CATEGORIES), (True, True))
    check("wiring: the collector is in the cycle", "technique_explainer" in open(os.path.join(root, "scripts", "collect_cycle.py"), encoding="utf-8").read(), True)
    line = TPL.TEMPLATES["technique_explainer"]({"payload": p})
    check("triage line names the type, the scale and the examples", all(s in line for s in ("Token & Share Accounting", "11 incidents", "Euler V1")), True)

    # ---- Rekt.news post-mortems (markup copied from the live homepage, 2026-11-20)
    from collectors import rekt as RK

    def art(slug, title, date, tags, excerpt):
        tag_html = "<span> - </span>".join(f'<span><a href="/?tag={t.replace(" ", "+")}">{t}</a></span>' for t in tags)
        return (f'<article class="post "><header class="post-header"><h5 class="post-title"><a href="/{slug}"> <!-- -->{title}</a></h5><hr/><span class="post-meta"><time>{date}</time><p>{tag_html}</p></span><hr/></header>'
                f'<section class="post-excerpt"><p>{excerpt}</p><p class="post-excerpt-more"><a href="/{slug}">MORE</a></p></section></article>')
    kava = art("kava-lend-rekt", "Kava Lend - Rekt", "Wednesday, November 18, 2026", ["Kava Lend", "Rekt"],
               "A dormant BUSD market remained exploitable as Kava�s lending protocol was being wound down. An accounting flaw inflated a few thousand BUSD into a 1.25 billion BUSD claim, worth about $3.76 million at pre-attack prices.")
    binance = art("binance-rekt", "Binance - Rekt", "Friday, November 6, 2026", ["Binance", "Operational Security", "Rekt"],
                  "A fake job interview led to Binance�s signing service, and $6.43 billion left across 11 chains. About $2.85 billion was frozen or intercepted.")
    essay = art("is-the-internet-rekt", "Is the Internet Rekt?", "Tuesday, November 3, 2026", ["Operational Security", "Artificial Intelligence", "Cybersecurity"], "Real exploits. Stolen secrets.")
    arts = RK.parse_articles("<html><body><main>" + kava + binance + essay + "</main></body></html>")
    check("rekt: every post block is parsed (title, date, tags, excerpt, url)", (len(arts), arts[0]["title"], arts[0]["date"].date().isoformat(), arts[0]["tags"], arts[0]["url"]),
          (3, "Kava Lend - Rekt", "2026-11-18", ["Kava Lend", "Rekt"], "https://www.rekt.news/kava-lend-rekt"))
    check("rekt: only post-mortems (tag 'Rekt' or a '- Rekt' title) are incidents; an essay is not", [RK.is_postmortem(a) for a in arts], [True, True, False])
    check("rekt: the protocol name comes from the title", [RK.protocol_name(a) for a in arts[:2]], ["Kava Lend", "Binance"])
    check("rekt: the amount is the first million/billion figure in the excerpt", (RK.first_amount(arts[0]["excerpt"]), RK.first_amount(arts[1]["excerpt"]), RK.first_amount("No figure here.")), (3760000.0, 6430000000.0, None))
    check("rekt: a page that is not the homepage parses to nothing (the collector then RAISES, never a quiet empty run)", RK.parse_articles("<html>Just a moment...</html>"), [])
    ex = [{"raw_item_id": 11, "name": "Kava Lend", "when": datetime(2026, 11, 14, tzinfo=timezone.utc)}, {"raw_item_id": 12, "name": "Kava Lend", "when": datetime(2025, 1, 5, tzinfo=timezone.utc)},
          {"raw_item_id": 13, "name": "Binance CEX", "when": datetime(2019, 5, 7, tzinfo=timezone.utc)}, {"raw_item_id": 14, "name": "Initia", "when": datetime(2026, 10, 23, tzinfo=timezone.utc)}]
    check("rekt: matched to the tracker's incident of the SAME name within 45 days before the post (not the 2025 one)", (RK.match_sibling("Kava Lend", arts[0]["date"], ex) or {}).get("raw_item_id"), 11)
    check("rekt: a 2019 incident of a similar name is not the same event", RK.match_sibling("Binance", arts[1]["date"], ex), None)
    check("rekt: a name that is only a short fragment does not match by substring", RK.match_sibling("Ini", datetime(2026, 10, 28, tzinfo=timezone.utc), ex), None)
    tracker = {"amount_usd": 3.7e6, "classification": "Token & Share Accounting", "technique": "Incorrect Share Accounting", "chain": "Kava", "chains": ["Kava"], "returned_funds": None, "defillama_id": "1", "protocol_tvl_usd": 1.2e7, "protocol_category": "Lending"}
    pl = RK.to_payload(arts[0], {"raw_item_id": 11}, tracker)
    check("rekt: payload is a hacks item with the tracker's facts, Rekt's excerpt, tier 2 and the earlier card's id", (pl["source"], pl["source_tier"], pl["amount_usd"], pl["classification"], pl["topic_key"], pl["followup_of_raw_item_id"], pl["description"] == pl["claim_text"]), ("rekt", 2, 3.7e6, "Token & Share Accounting", "kava lend", 11, True))
    pl2 = RK.to_payload(arts[1], None, None)
    check("rekt: unmatched -> amount from the excerpt, no tracker facts, no follow-up id", (pl2["amount_usd"], pl2["classification"], pl2["followup_of_raw_item_id"]), (6430000000.0, None, None))
    check("rekt: the scorer accepts both payloads (unknown tracker data is neutral, never an error)", (isinstance(SC._hacks_impact(pl), float), isinstance(SC._hacks_impact(pl2), float), SC._hacks_impact(dict(pl2, amount_usd=None)) >= 0), (True, True, True))
    facts_m, facts_u = PF.facts_hacks_exploits(pl), PF.facts_hacks_exploits(pl2)
    check("rekt facts: Rekt's account is attributed and the tracker's fields appear when matched", all(s in facts_m for s in ("Rekt.news", "attribute it to Rekt", "Token & Share Accounting", "Incorrect Share Accounting")), True)
    check("rekt facts: an incident the tracker lacks says so and forbids a classification, technique or chain", "not in DefiLlama's tracker -- do not state a classification" in facts_u, True)
    check("rekt triage line: a follow-up says it follows the earlier card; an unmatched one says it is not in the tracker",
          (TPL.TEMPLATES["hacks_exploits"]({"payload": pl}).endswith("follows the earlier card for this incident"), "not in DefiLlama's tracker" in TPL.TEMPLATES["hacks_exploits"]({"payload": pl2})), (True, True))
    check("rekt wiring: in the cycle, scored as hacks_exploits", all(s in open(os.path.join(root, "scripts", "collect_cycle.py"), encoding="utf-8").read() for s in ('"rekt": rekt.collect', '"rekt": "hacks_exploits"')), True)

    # ---- finished drafts on Crypto Notebook (draft-first channels): written before the card, stored on the approval, reused on Approve; a failed draft is NOT lost
    from bot import approval_poller as AP
    from bot import notify as N

    check("draft-first: only We Talk A Lot (Crypto Notebook went back to short cards, post written after the tap, 2026-11-24)", N.DRAFT_FIRST_CHANNELS, {"we_talk_a_lot"})
    card, kb = N.render_draft_card("Crypto Notebook", "🧭 TECHNIQUE OF THE WEEK", "score 83.7/100", "🧭 <b>How does it work?</b>\n\nBody.", 42)
    check("draft card: the draft is shown unescaped under a header that names the channel", ("Finished draft for Crypto Notebook" in card, "<b>How does it work?</b>" in card, "&lt;" in card), (True, True, False))
    check("draft card: Approve / Edit / Skip use the existing callbacks (no new handler)", [(b["text"], b["callback_data"]) for b in kb], [("✅ Approve", "approve:42"), ("✏️ Edit", "edit:42"), ("❌ Skip", "reject:42")])

    class _Cur:
        def __init__(self, log):
            self.log, self._id = log, 0
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, sql, params=None):
            self.log.append((" ".join(sql.split()), params))
        def fetchone(self):
            return {"id": 900 + len(self.log)}

    class _Conn:
        def __init__(self):
            self.log, self.commits = [], 0
        def cursor(self, **kw):
            return _Cur(self.log)
        def commit(self):
            self.commits += 1

    sent = []
    orig = (N._try_draft, N.send_message, N.load_category_config, N.get_channel_display_name)
    drafts = {1: "🧭 <b>A short draft</b>\n\nText.", 2: None, 3: "x" * 5000}
    N._try_draft = lambda conn, category, channel, row: drafts[row["raw_item_id"]]
    N.send_message = lambda chat, text, reply_markup=None, **kw: (sent.append((text, reply_markup)) or {"message_id": 7000 + len(sent)})
    N.load_category_config = lambda conn, category: {"label": "🧭 TECHNIQUE OF THE WEEK"}
    N.get_channel_display_name = lambda conn, channel: "Crypto Notebook"
    os.environ["TELEGRAM_OPERATOR_CHAT_ID"] = "1"
    try:
        fc = _Conn()
        cands = [("technique_explainer", {"raw_item_id": i, "payload": {}, "score": 80.0, "score_breakdown": {}}) for i in (1, 2, 3)]
        rest, last_id = N._send_draft_cards(fc, "crypto_notebook", cands)
    finally:
        N._try_draft, N.send_message, N.load_category_config, N.get_channel_display_name = orig
    check("draft-first: only the candidate with a usable draft gets a draft card; a failed draft and an over-long one go out as ordinary cards (nothing lost)", ([r[1]["raw_item_id"] for r in rest], len(sent)), ([2, 3], 1))
    inserts = [p for sql, p in fc.log if sql.startswith("INSERT INTO approvals")]
    check("draft-first: the approval row stores the draft text and starts pending", (len(inserts), inserts[0][1], inserts[0][2]), (1, 1, drafts[1]))
    check("draft-first: persisted after the send, notification id returned", (fc.commits, last_id is not None), (1, True))

    calls = []
    op = (AP.generate_post, AP._send_preview_and_store, AP.load_category_config, AP.generate_post_with_note)
    AP.generate_post = lambda *a, **k: (calls.append("model") or "FROM THE MODEL")
    AP.generate_post_with_note = lambda *a, **k: (calls.append("model") or "FROM THE MODEL", None)
    AP._send_preview_and_store = lambda conn, approval_id, channel, category, score, model, text, header_note=None: calls.append(("preview", text))
    AP.load_category_config = lambda conn, category: {"write_model": "m"}
    try:
        base = {"category": "technique_explainer", "channel": "crypto_notebook", "raw_item_id": 5, "payload": {}, "collected_at": NOW, "score": 80.0, "score_breakdown": {}, "approval_id": 9}
        AP._generate_and_preview(None, dict(base, draft_text="THE DRAFT"))
        AP._generate_and_preview(None, dict(base, draft_text=None))
    finally:
        AP.generate_post, AP._send_preview_and_store, AP.load_category_config, AP.generate_post_with_note = op
    check("approve: a stored draft is sent as is with NO model call; without one the post is written as before", calls, [("preview", "THE DRAFT"), "model", ("preview", "FROM THE MODEL")])
    check("schema: the draft column exists", "ADD COLUMN IF NOT EXISTS draft_text" in open(os.path.join(root, "db", "schema.sql"), encoding="utf-8").read(), True)
    schema = open(os.path.join(root, "db", "schema.sql"), encoding="utf-8").read()
    check("schema: the RLS block is still the LAST statement (new columns above it)", schema.rstrip().endswith("$$;") and schema.index("draft_text") < schema.index("ENABLE ROW LEVEL SECURITY"), True)

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("technique-of-the-week cases behave as expected")


if __name__ == "__main__":
    main()
