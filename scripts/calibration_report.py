"""Real accept/reject calibration data, per category -- config/category_config.yaml's
own top-of-file note says score weights are "starting values... recalibrate
later against real accept/reject decisions, not to get them perfect now."
This is that data, already captured durably by `approvals.decision` (set the
moment the operator taps Approve/Edit/Reject in bot/approval_poller.py).

REWRITTEN 2026-09-20 around a real finding: the operator approves or ignores,
almost never taps Reject (first live pull: 50 decisions across 8 categories,
seven of them with ZERO rejections, while 81 of 105 undecided cards were more
than 2 days old). The original report treated 'pending' as "no data yet" and
so discarded the single most useful signal being generated -- what the
operator leaves alone. Every notified candidate is now classified as:

  accepted   approved / edited -- the operator wanted it published.
  rejected   an explicit Reject tap.
  ignored    still 'pending' after IGNORE_AFTER_DAYS (default 3, --days N).
             An implicit negative: a card old enough that the operator has
             plainly had the chance and passed. The cutoff is a judgment, so
             the report prints how long the operator actually takes to act
             (observed decision latency) to sanity-check it.
  (excluded) not a signal either way, listed but never counted:
             - too recent: pending but younger than the cutoff (not reached yet)
             - expired: retired without ever being judged (e.g. the cards left
               on Crypto Notebook when it was removed) -- deliberately NOT
               'rejected', which would poison exactly these statistics.

Headline numbers per category:
  implicit accept rate   accepted / (accepted + rejected + ignored)
  acted-on rate by score band   the same, split into score terciles
  AUC   the chance a randomly chosen accepted card out-scored a randomly
        chosen negative one (0.5 = the score carries no information about the
        operator's judgment, 1.0 = perfect). Compact and threshold-free.

DIGEST-POSITION CAVEAT (built in, printed with every report, because it is a
real confound and not a footnote): within a digest, cards are listed highest
score first, so "the operator acts on high scores" and "the operator reads
from the top and runs out of time" are the SAME observation -- score and
position cannot be separated by this data alone, and an ignored card may be
one the operator never scrolled to rather than one they judged. The report
therefore also shows the acted-on rate for the TOP vs LOWER half of each
digest, and how many accepted cards came from the lower half: picks from deep
in a digest are the only evidence here of content-driven choice rather than
reading order. The only thing that would fully separate the two is a
deliberately shuffled digest order, which this script does not do.

Every candidate that ever reached the operator has a permanent approvals row --
nothing in the pipeline deletes raw_items/scores/approvals automatically.

Usage:
    python scripts/calibration_report.py                    # every category
    python scripts/calibration_report.py web3_jobs          # one category
    python scripts/calibration_report.py --days 5           # ignore cutoff, days
    python scripts/calibration_report.py news --days 2
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import dict_cursor, get_conn  # noqa: E402

COMPONENTS = ["impact", "novelty", "credibility", "actionability"]
IGNORE_AFTER_DAYS = 3.0

# Score-tracking verdict thresholds (on AUC). Deliberately conservative -- with
# the sample sizes this project has, "clearly not tracking" needs to mean it.
AUC_TRACKING = 0.65
AUC_NOT_TRACKING = 0.55
MIN_PER_SIDE = 5      # below this on either side, no verdict at all
THIN_BELOW = 20       # accepted + negatives below this: verdict is tagged thin
MIN_FOR_BANDS = 9     # need at least 3 per tercile to say anything about bands

POSITION_CAVEAT = (
    "digest-position caveat: digests list cards highest score first, so 'acts on high\n"
    "  scores' and 'reads from the top and runs out of time' are the same observation --\n"
    "  this data cannot separate them, and an ignored card may simply never have been\n"
    "  reached. Picks from the LOWER half of a digest are the only evidence of\n"
    "  content-driven choice; the top-vs-lower split below shows how much of that exists."
)


def fetch_rows(conn, category: str | None = None) -> list[dict]:
    query = """
        SELECT r.id AS raw_item_id, r.category, a.decision, a.decided_at,
               s.score, s.score_breakdown, n.id AS notification_id, n.sent_at,
               RANK() OVER (PARTITION BY n.id, r.category ORDER BY s.score DESC) AS digest_rank,
               COUNT(*) OVER (PARTITION BY n.id, r.category) AS digest_size
        FROM approvals a
        JOIN raw_items r ON r.id = a.raw_item_id
        JOIN notifications n ON n.id = a.notification_id
        LEFT JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
    """
    params: tuple = ()
    if category:
        query += " WHERE r.category = %s"
        params = (category,)
    with dict_cursor(conn) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def classify(row: dict, now: datetime, ignore_after_days: float) -> str:
    """accepted | rejected | ignored | too_recent | expired"""
    d = row["decision"]
    if d in ("approved", "edited"):
        return "accepted"
    if d == "rejected":
        return "rejected"
    if d == "expired":
        return "expired"
    # pending
    age_days = (now - row["sent_at"]).total_seconds() / 86400
    return "ignored" if age_days >= ignore_after_days else "too_recent"


def auc(positives: list[float], negatives: list[float]) -> float | None:
    """P(random positive outscores random negative), ties count half."""
    if not positives or not negatives:
        return None
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def percentile(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def avg(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")


def print_group(label: str, group: list[dict]) -> None:
    scored = [r for r in group if r["score"] is not None]
    line = f"  {label:<9} {len(group):>3}"
    if scored:
        comps = {
            c: avg([float((r["score_breakdown"] or {}).get(c, 0)) for r in scored if r["score_breakdown"]])
            for c in COMPONENTS
        }
        comp_str = ", ".join(f"{c}={comps[c]:.1f}" for c in COMPONENTS if comps[c] == comps[c])
        line += f"  | avg score={avg([float(r['score']) for r in scored]):5.1f}"
        if comp_str:
            line += f"  | {comp_str}"
    print(line)


def verdict(pos: list[float], neg: list[float]) -> str:
    if len(pos) < MIN_PER_SIDE or len(neg) < MIN_PER_SIDE:
        return (f"no verdict -- need >={MIN_PER_SIDE} accepted AND >={MIN_PER_SIDE} negatives "
                f"(have {len(pos)} / {len(neg)})")
    a = auc(pos, neg)
    thin = " [THIN sample -- treat as a lead, not a finding]" if len(pos) + len(neg) < THIN_BELOW else ""
    if a >= AUC_TRACKING:
        return f"AUC={a:.2f}: score TRACKS your judgment{thin}"
    if a <= AUC_NOT_TRACKING:
        return f"AUC={a:.2f}: *** FLAG -- score is NOT tracking your judgment ***{thin}"
    return f"AUC={a:.2f}: weak/unclear -- score only loosely tracks your judgment{thin}"


def report(rows: list[dict], ignore_after_days: float) -> None:
    if not rows:
        print("No approval history yet for any category.")
        return
    now = datetime.now(timezone.utc)
    print(f"Ignore cutoff: pending >= {ignore_after_days:g} days counts as a negative "
          f"(change with --days N).")

    latencies = sorted(
        (r["decided_at"] - r["sent_at"]).total_seconds() / 86400
        for r in rows
        if r["decision"] in ("approved", "edited", "rejected") and r["decided_at"] and r["sent_at"]
    )
    if latencies:
        print(f"Observed time-to-decision across all {len(latencies)} decisions: "
              f"median {percentile(latencies, .5):.1f}d, 75th pct {percentile(latencies, .75):.1f}d, "
              f"90th pct {percentile(latencies, .9):.1f}d -- a cutoff well below the 75th "
              f"percentile would mislabel slow-but-real decisions as ignored.")
    print(POSITION_CAVEAT)

    by_category = defaultdict(list)
    for row in rows:
        row["cls"] = classify(row, now, ignore_after_days)
        by_category[row["category"]].append(row)

    for category in sorted(by_category):
        cat_rows = by_category[category]
        g = {k: [r for r in cat_rows if r["cls"] == k]
             for k in ("accepted", "rejected", "ignored", "too_recent", "expired")}
        resolved = g["accepted"] + g["rejected"] + g["ignored"]
        negatives = g["rejected"] + g["ignored"]

        print(f"\n=== {category} ===")
        print(f"notified: {len(cat_rows)}  =  accepted {len(g['accepted'])}, rejected {len(g['rejected'])}, "
              f"ignored {len(g['ignored'])}  |  excluded: too recent {len(g['too_recent'])}, "
              f"expired {len(g['expired'])}")

        if not resolved:
            print("  nothing resolved yet.")
            continue

        print_group("accepted", g["accepted"])
        print_group("rejected", g["rejected"])
        print_group("ignored", g["ignored"])

        implicit = len(g["accepted"]) / len(resolved) * 100
        line = f"  implicit accept rate: {implicit:.0f}% ({len(g['accepted'])}/{len(resolved)})"
        explicit_judged = len(g["accepted"]) + len(g["rejected"])
        if g["rejected"]:
            line += f"   |   explicit-only (ignores dropped): {len(g['accepted']) / explicit_judged * 100:.0f}%"
        else:
            line += "   |   explicit-only rate not meaningful: zero Reject taps in this category"
        print(line)

        scored_pos = [float(r["score"]) for r in g["accepted"] if r["score"] is not None]
        scored_neg = [float(r["score"]) for r in negatives if r["score"] is not None]
        print(f"  {verdict(scored_pos, scored_neg)}")

        # acted-on rate by score band (terciles of the resolved set)
        scored_resolved = sorted((r for r in resolved if r["score"] is not None), key=lambda r: float(r["score"]))
        if len(scored_resolved) >= MIN_FOR_BANDS:
            n = len(scored_resolved)
            bands = [scored_resolved[: n // 3], scored_resolved[n // 3: 2 * n // 3], scored_resolved[2 * n // 3:]]
            parts = []
            for name, b in zip(("low", "mid", "high"), bands):
                acc = sum(1 for r in b if r["cls"] == "accepted")
                parts.append(f"{name} {float(b[0]['score']):.0f}-{float(b[-1]['score']):.0f}: {acc}/{len(b)} accepted")
            print("  by score band:  " + "  |  ".join(parts))
        else:
            print(f"  by score band:  too few resolved cards ({len(scored_resolved)}) for terciles")

        # digest position: top half vs lower half, restricted to digests with >=2 cards
        multi = [r for r in resolved if r["digest_size"] >= 2]
        if multi:
            def is_top(r):
                return r["digest_rank"] <= (r["digest_size"] + 1) // 2

            top = [r for r in multi if is_top(r)]
            low = [r for r in multi if not is_top(r)]
            acc_top = sum(1 for r in top if r["cls"] == "accepted")
            acc_low = sum(1 for r in low if r["cls"] == "accepted")
            print(f"  by digest position:  top half {acc_top}/{len(top)} accepted  |  "
                  f"lower half {acc_low}/{len(low)} accepted   "
                  f"({acc_low} of {len(g['accepted'])} accepted cards came from the lower half of a "
                  f"multi-card digest)")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("category", nargs="?", help="limit to one category")
    parser.add_argument("--days", type=float, default=IGNORE_AFTER_DAYS,
                        help=f"pending cards older than this count as ignored (default {IGNORE_AFTER_DAYS:g})")
    args = parser.parse_args()
    conn = get_conn()
    try:
        report(fetch_rows(conn, args.category), args.days)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
