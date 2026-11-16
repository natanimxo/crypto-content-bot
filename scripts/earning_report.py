"""What the earning_opportunities gate and scores are doing to real candidates.

    python scripts/earning_report.py              # per source / per type, and the "worth it but unproven" quadrant
    python scripts/earning_report.py --unproven   # list those items (value >= 40, trust < 40), best value first

Read-only. The operator judges "is this real" by hand; this shows which items the trust floor (40) is holding back and
why, so the floor or the corroboration rule can be argued from data, not from the description.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import dict_cursor, get_conn  # noqa: E402


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--unproven", action="store_true", help="list items with value >= 40 and trust < 40")
    ap.add_argument("--drafts", action="store_true", help="how many attempts the card drafts needed (is the 400-650 band too tight?)")
    ap.add_argument("--days", type=int, default=7, help="window for --drafts")
    args = ap.parse_args()
    conn = get_conn()
    with dict_cursor(conn) as cur:
        if args.drafts:
            cur.execute("""SELECT details->>'source' AS src, (details->>'ok')::boolean AS ok, (details->>'attempts')::int AS attempts
                           FROM run_logs WHERE job_name = 'earning_draft' AND started_at > now() - (%s || ' days')::interval""", (args.days,))
            rows = cur.fetchall()
            ok = [r["attempts"] for r in rows if r["ok"]]
            print(f"earning card drafts in the last {args.days} days: {len(rows)} ({len(rows) - len(ok)} gave up)")
            if ok:
                print(f"  attempts when it worked: mean {sum(ok) / len(ok):.1f}, 1-3 attempts: {sum(1 for a in ok if a <= 3)}, 4+ attempts: {sum(1 for a in ok if a >= 4)} "
                      f"({100 * sum(1 for a in ok if a >= 4) / len(ok):.0f}%)")
            by = {}
            for r in rows:
                by.setdefault(r["src"], []).append(r)
            for src, rs in sorted(by.items(), key=lambda kv: str(kv[0])):
                good = [r["attempts"] for r in rs if r["ok"]]
                print(f"  {str(src):24} n={len(rs):3} failed={len(rs) - len(good):2} mean attempts={(sum(good) / len(good)) if good else 0:.1f}")
            print("  READ: if most cards need 4+ attempts (or any source keeps giving up) the 400-650 band is too tight -- widen it rather than keep retrying.")
            return
        if args.unproven:
            cur.execute("""SELECT left(r.payload->>'title', 70) AS title, r.payload->>'source' AS source, r.payload->>'type' AS type,
                                  s.score AS value, (s.score_breakdown->>'trust')::float AS trust, r.payload->>'domain_age_days' AS age,
                                  jsonb_array_length(coalesce(r.payload->'corroborators', '[]'::jsonb)) AS corroborators
                           FROM raw_items r JOIN scores s ON s.raw_item_id = r.id
                           WHERE r.category = 'earning_opportunities' AND NOT r.held AND s.score >= 40
                             AND (s.score_breakdown->>'trust')::float < 40
                           ORDER BY s.score DESC LIMIT 60""")
            for r in cur.fetchall():
                print(f"value {float(r['value']):5.1f} trust {r['trust']:5.1f} | {r['type']:10} {r['source']:22} age={r['age']} corro={r['corroborators']} | {r['title']}")
            return
        cur.execute("""SELECT r.payload->>'source' AS src, count(*) AS n, count(*) FILTER (WHERE r.held) AS held,
                              count(*) FILTER (WHERE NOT r.held AND (s.score_breakdown->>'trust')::float >= 40 AND s.score >= 40 AND (s.score_breakdown->>'expected_payout_usd' IS NULL OR (s.score_breakdown->>'expected_payout_usd')::float >= 10)) AS eligible,
                              count(*) FILTER (WHERE (s.score_breakdown->>'trust')::float < 40) AS trust_low,
                              round(avg((s.score_breakdown->>'trust')::float)::numeric, 1) AS avg_trust, round(avg(s.score)::numeric, 1) AS avg_value
                       FROM raw_items r JOIN scores s ON s.raw_item_id = r.id WHERE r.category = 'earning_opportunities' GROUP BY 1 ORDER BY 1""")
        print(f"{'source':24} {'n':>4} {'held':>4} {'eligible':>8} {'trust<40':>8} {'avg trust':>9} {'avg value':>9}")
        for r in cur.fetchall():
            print(f"{r['src']:24} {r['n']:4} {r['held']:4} {r['eligible']:8} {r['trust_low']:8} {r['avg_trust']:9} {r['avg_value']:9}")
        cur.execute("""SELECT r.payload->>'type' AS type, count(*) AS n,
                              count(*) FILTER (WHERE NOT r.held AND (s.score_breakdown->>'trust')::float >= 40 AND s.score >= 40 AND (s.score_breakdown->>'expected_payout_usd' IS NULL OR (s.score_breakdown->>'expected_payout_usd')::float >= 10)) AS eligible
                       FROM raw_items r JOIN scores s ON s.raw_item_id = r.id WHERE r.category = 'earning_opportunities' GROUP BY 1 ORDER BY 2 DESC""")
        print("\nby type (stored -> eligible):", ", ".join(f"{r['type']} {r['n']}->{r['eligible']}" for r in cur.fetchall()))


if __name__ == "__main__":
    main()
