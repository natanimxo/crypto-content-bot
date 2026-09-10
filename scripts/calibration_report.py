"""Real accept/reject calibration data, per category -- config/category_config.yaml's
own top-of-file note says score weights are "starting values... recalibrate
later against real accept/reject decisions, not to get them perfect now."
This is that data, already captured durably by `approvals.decision` (set the
moment the operator taps Approve/Edit/Reject in bot/approval_poller.py) --
nothing needed switching on to start capturing it, it was already there.
What was missing was a way to actually look at it, which is what this script is.

Every candidate that ever reached the operator has a permanent approvals row
(notification_id, raw_item_id, decision, decided_at) -- nothing in the
pipeline ever deletes raw_items/scores/approvals automatically, so this
history only grows. 'pending' means the operator hasn't acted yet (not a
decision); 'approved'/'edited' both mean the operator wanted it published
(edited = wanted it published, but with corrected text); 'rejected' means
they didn't.

Usage:
    python scripts/calibration_report.py                # every category
    python scripts/calibration_report.py web3_jobs       # one category
"""

import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import dict_cursor, get_conn  # noqa: E402

COMPONENTS = ["impact", "novelty", "credibility", "actionability"]
DECIDED = ("approved", "edited", "rejected")  # excludes 'pending' -- not a decision yet


def fetch_decisions(conn, category: str | None = None) -> list[dict]:
    query = """
        SELECT r.category, a.decision, s.score, s.score_breakdown
        FROM approvals a
        JOIN raw_items r ON r.id = a.raw_item_id
        LEFT JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
    """
    params: tuple = ()
    if category:
        query += " WHERE r.category = %s"
        params = (category,)
    with dict_cursor(conn) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def report(rows: list[dict]):
    by_category = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)

    if not by_category:
        print("No approval history yet for any category.")
        return

    for category in sorted(by_category):
        cat_rows = by_category[category]
        pending = [r for r in cat_rows if r["decision"] == "pending"]
        decided = [r for r in cat_rows if r["decision"] in DECIDED]

        print(f"\n=== {category} ===")
        print(f"total candidates ever notified: {len(cat_rows)}  "
              f"(pending: {len(pending)}, decided: {len(decided)})")

        if not decided:
            print("  not enough decided candidates yet to calibrate against.")
            continue

        for decision in ("approved", "edited", "rejected"):
            group = [r for r in decided if r["decision"] == decision]
            if not group:
                continue
            scored = [r for r in group if r["score_breakdown"]]
            print(f"  {decision}: {len(group)}", end="")
            if scored:
                avg_score = sum(float(r["score"]) for r in scored) / len(scored)
                avg_components = {
                    c: sum(float(r["score_breakdown"].get(c, 0)) for r in scored) / len(scored)
                    for c in COMPONENTS
                }
                comp_str = ", ".join(f"{c}={avg_components[c]:.1f}" for c in COMPONENTS)
                print(f"  |  avg score={avg_score:.1f}  |  avg components: {comp_str}")
            else:
                print("  (no score_breakdown on record for these)")

        accepted = [r for r in decided if r["decision"] in ("approved", "edited")]
        rejected = [r for r in decided if r["decision"] == "rejected"]
        if accepted and rejected:
            accept_rate = len(accepted) / len(decided) * 100
            print(f"  accept rate: {accept_rate:.0f}% ({len(accepted)}/{len(decided)})")


def main():
    load_dotenv()
    category = sys.argv[1] if len(sys.argv) > 1 else None
    conn = get_conn()
    try:
        rows = fetch_decisions(conn, category)
        report(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
