"""Generate ONE source item for two (or more) channels and show the posts side by
side -- the test for channel-level voice: the same story on two channels must be
visibly different posts.

    python scripts/voice_compare.py                         # latest fresh news item, Wall Street vs Alpha Edge
    python scripts/voice_compare.py --item 2184411 --channels crypto_wall_street alpha_edge_crypto
    python scripts/voice_compare.py --show-prompt

Profiles are read straight from config/channel_voices.yaml, NOT from the database,
so a voice can be reviewed before it is seeded into production. Nothing is written
to the database except the usual llm_usage metering row per call, and nothing is
sent to Telegram. Costs two or three DeepSeek calls (about $0.001).
"""

import argparse
import os
import re
import sys
import textwrap
from itertools import zip_longest

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import channel_voice, llm_usage  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.write_post import generate_post_with_report  # noqa: E402

COL = 58
_WORD_RE = re.compile(r"\w+")


def _latest_fresh_news(conn) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id AS raw_item_id, r.category, r.payload, r.collected_at, s.score
               FROM raw_items r JOIN scores s ON s.raw_item_id = r.id
               WHERE r.category = 'news' AND s.score >= 50 AND r.expired_at IS NULL
                 AND length(coalesce(r.payload->>'description', '')) >= 120
               ORDER BY r.id DESC LIMIT 1""")
        return cur.fetchone()


def _load_item(conn, raw_item_id: int) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id AS raw_item_id, r.category, r.payload, r.collected_at, s.score
               FROM raw_items r JOIN scores s ON s.raw_item_id = r.id WHERE r.id = %s""", (raw_item_id,))
        row = cur.fetchone()
    if not row:
        sys.exit(f"no scored raw_item with id {raw_item_id}")
    return row


def _display(post_html: str) -> list[str]:
    """The post as a reader sees it (tags stripped), wrapped to a column."""
    text = re.sub(r"<[^>]+>", "", post_html)
    out = []
    for para in text.split("\n"):
        out.extend(textwrap.wrap(para, COL) or [""])
    return out


def _shared_run(a: str, b: str, n: int = 8) -> bool:
    wa, wb = _WORD_RE.findall(a.lower()), _WORD_RE.findall(b.lower())
    grams = {tuple(wa[i:i + n]) for i in range(max(0, len(wa) - n + 1))}
    return any(tuple(wb[i:i + n]) in grams for i in range(max(0, len(wb) - n + 1)))


def main():
    load_dotenv()
    # Deliberate test traffic: kept out of the poller-loop anomaly alerts (pipeline/llm_usage.py).
    llm_usage.tag_session("voice_compare")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", type=int, help="raw_item id (default: latest fresh scored news item)")
    ap.add_argument("--channels", nargs="+", default=["crypto_wall_street", "alpha_edge_crypto"])
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()

    profiles = channel_voice.load_profiles_from_yaml()
    for ch in args.channels:
        if ch not in profiles:
            sys.exit(f"no voice profile for '{ch}' in config/channel_voices.yaml (have: {', '.join(profiles)})")

    conn = get_conn()
    try:
        item = _load_item(conn, args.item) if args.item else _latest_fresh_news(conn)
        category = item["category"]
        p = item["payload"]
        print(f"SOURCE  raw_item {item['raw_item_id']}  category={category}  score={item['score']}")
        print(f"        {p.get('title')}")
        print(f"        ({p.get('source')}; excerpt {len(p.get('description') or '')} chars)\n")

        results = {}
        for ch in args.channels:
            results[ch] = generate_post_with_report(conn, category, ch, item, profile=profiles[ch])
            if args.show_prompt:
                print(f"----- PROMPT for {ch} -----\n{results[ch]['prompt']}\n")

        cols = [_display(results[ch]["text"]) for ch in args.channels]
        heads = [f"{ch}  [{profiles[ch].name}]" for ch in args.channels]
        print(" | ".join(h[:COL].ljust(COL) for h in heads))
        print("-+-".join("-" * COL for _ in heads))
        for row in zip_longest(*cols, fillvalue=""):
            print(" | ".join(cell.ljust(COL) for cell in row))
        print()

        print(f"{'':28}" + "".join(f"{ch[:24]:26}" for ch in args.channels))
        for label, fn in (
            ("chars (post minus source/#)", lambda ch: f"{results[ch]['chars']}  (want {profiles[ch].length_min}-{profiles[ch].length_max})"),
            ("body paragraphs", lambda ch: str(len(profiles[ch].paragraphs))),
            ("hashtags", lambda ch: "on" if profiles[ch].hashtags else "off"),
            ("attempts (retries needed)", lambda ch: f"{results[ch]['attempts']}"),
        ):
            print(f"{label:28}" + "".join(f"{fn(ch):26}" for ch in args.channels))
        for ch in args.channels:
            for i, iss in enumerate(results[ch]["retry_issues"], 1):
                print(f"  {ch} attempt {i} was rejected for: {iss}")

        # "Visibly different" is judged mechanically too, not just by eye.
        print("\nDISTINCTNESS")
        titles = [re.sub(r"^\S+\s", "", _display(results[ch]["text"])[0]) for ch in args.channels]
        bodies = [" ".join(_display(results[ch]["text"])[1:]) for ch in args.channels]
        failures = []
        for i in range(len(args.channels)):
            for j in range(i + 1, len(args.channels)):
                a, b = args.channels[i], args.channels[j]
                checks = [
                    ("titles differ", titles[i].lower() != titles[j].lower()),
                    ("no 8-word run shared between the two bodies", not _shared_run(bodies[i], bodies[j])),
                    ("length differs by >= 50 chars", abs(results[a]["chars"] - results[b]["chars"]) >= 50),
                    ("paragraph structure differs", len(profiles[a].paragraphs) != len(profiles[b].paragraphs)
                     or profiles[a].hashtags != profiles[b].hashtags),
                ]
                ta, tb = set(_WORD_RE.findall(titles[i].lower())), set(_WORD_RE.findall(titles[j].lower()))
                print(f"  [info] {a} vs {b}: title word overlap {len(ta & tb) / max(1, len(ta | tb)):.0%}")
                for name, ok in checks:
                    print(f"  [{'PASS' if ok else 'FAIL'}] {a} vs {b}: {name}")
                    if not ok:
                        failures.append(name)
        sys.exit(1 if failures else 0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
