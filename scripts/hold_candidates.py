"""Operator tool: hold specific candidates back from notification, or release
them, via `raw_items.held` (db/schema.sql, 2026-09-11) -- lets an operator
pace a release ("send me a few first so I can gauge write quality before the
rest") in a way that survives a real collect/notify cycle. See
pipeline/select_candidates.py's module docstring for how `held` is checked.

Held items are otherwise ordinary candidates -- still scored, still eligible
to clear review_threshold -- they're just invisible to
select_candidates.get_new_candidates (and therefore never notified) until
explicitly released here.

Usage:
    python scripts/hold_candidates.py hold 20958 20946 20976
    python scripts/hold_candidates.py release 20958 20946 20976
    python scripts/hold_candidates.py list web3_jobs
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import dict_cursor, get_conn  # noqa: E402


def set_held(conn, raw_item_ids: list[int], held: bool) -> int:
    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE raw_items SET held = %s WHERE id = ANY(%s) RETURNING id",
            (held, raw_item_ids),
        )
        updated = [row["id"] for row in cur.fetchall()]
    conn.commit()
    missing = set(raw_item_ids) - set(updated)
    if missing:
        print(f"warning: no raw_item found for id(s) {sorted(missing)}")
    return len(updated)


def list_held(conn, category: str) -> list[dict]:
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id, r.payload->>'title' AS title, r.payload->>'company' AS company,
                      s.score
               FROM raw_items r
               LEFT JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
               WHERE r.category = %s AND r.held = TRUE
               ORDER BY s.score DESC NULLS LAST""",
            (category,),
        )
        return cur.fetchall()


def main():
    load_dotenv()
    if len(sys.argv) < 2 or sys.argv[1] not in ("hold", "release", "list"):
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    conn = get_conn()
    try:
        if action == "list":
            if len(sys.argv) != 3:
                print("usage: python scripts/hold_candidates.py list <category>")
                sys.exit(1)
            rows = list_held(conn, sys.argv[2])
            if not rows:
                print(f"no held candidates for category={sys.argv[2]}")
            for row in rows:
                label = row.get("title") or row.get("company") or ""
                print(f"{row['id']:6}  score={row['score']}  {label}")
        else:
            if len(sys.argv) < 3:
                print(f"usage: python scripts/hold_candidates.py {action} <raw_item_id> [more_ids...]")
                sys.exit(1)
            raw_item_ids = [int(x) for x in sys.argv[2:]]
            n = set_held(conn, raw_item_ids, held=(action == "hold"))
            print(f"{action}: updated {n}/{len(raw_item_ids)} raw_items")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
