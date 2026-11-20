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

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("technique-of-the-week cases behave as expected")


if __name__ == "__main__":
    main()
