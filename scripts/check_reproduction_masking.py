"""Does entity masking still catch real reproduction? (2026-11-14, operator condition.)

The reproduction guard now masks entities (figures, tickers/acronyms, proper nouns) before
looking for copied wording, because copyright protects expression, not facts. The risk is the
opposite failure: masking so much that a verbatim lift passes. This script tests that, offline
(no LLM calls, no Telegram), against the REAL source excerpts in the database:

  must FAIL (be flagged), both before and after masking:
    verbatim      the excerpt pasted as the draft
    half-lift     the first half of the excerpt verbatim + original commentary
    title-case    a Title-Cased source lifted verbatim (where "capitalised = entity" would
                  mask everything)
  evasion attempts, reported but NOT pass/fail (deliberate disguises a lazy model does not do;
  the old guard is beaten by a determined enough one too):
    swapped       the excerpt with every entity replaced by a different invented name
    sprinkled     a lift with an invented word inserted every 4th word
  must PASS (not be flagged) after masking -- these are accurate, fact-dense posts:
    facts-only    the excerpt's own entities and figures strung together in new words
    paraphrase    hand-written own-words summaries

    python scripts/check_reproduction_masking.py
"""

import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import entities as E  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.write_post import _find_reproduction_issues, _find_reproduction_issues_unmasked  # noqa: E402


def draft(text: str) -> dict:
    return {"title": "", "paragraphs": [text]}


def swap_entities(text: str) -> str:
    forms = sorted(E.entity_surface_forms(text), key=len, reverse=True)
    for i, f in enumerate(forms):
        text = re.sub(rf"(?<!\w){re.escape(f)}(?!\w)", f"Zorblax{i}", text, flags=re.IGNORECASE)
    return text


def sprinkled(text: str) -> str:
    """Insert an invented name after every 4th word of a verbatim lift."""
    words = text.split()
    out = []
    for i, w in enumerate(words, 1):
        out.append(w)
        if i % 4 == 0:
            out.append("Acme")
    return " ".join(out)


# Hand-built hard cases: entity-dense sources, where bridging matters most.
SYNTHETIC_SOURCES = [
    "Bitcoin ETFs lost $1.2 billion last week as Ether funds also bled, BlackRock's IBIT recorded "
    "outflows of $420 million while Fidelity's FBTC shed $300 million and Grayscale's GBTC lost $150 million.",
    "The SEC and CFTC jointly told Coinbase, Kraken and Binance on Tuesday that perpetual futures on "
    "tokenized equities fall under existing derivatives rules, according to people familiar with the matter.",
]
PARAPHRASES = [
    # (source, own-words summary) -- facts restated, wording different
    ("Bitcoin ETFs lost $1.2 billion last week as Ether funds also bled, according to CoinShares data.",
     "CoinShares data shows $1.2 billion left Bitcoin ETFs over the past week, with Ether funds seeing outflows too."),
    ("OpenAI launched a ChatGPT wallet that lets AI agents shop with ordinary cards, putting it in competition "
     "with crypto payment tools from Coinbase and Circle.",
     "OpenAI rolled out a card-based wallet for ChatGPT agents. It competes with Coinbase and Circle's crypto "
     "payment products for the agent-commerce market."),
]


def main():
    load_dotenv()
    conn = get_conn()
    with dict_cursor(conn) as cur:
        cur.execute("""SELECT r.id, r.payload->>'description' AS d FROM raw_items r
                       WHERE r.category IN ('news', 'macro_news') AND length(coalesce(r.payload->>'description', '')) >= 100
                       ORDER BY r.id DESC LIMIT 60""")
        rows = cur.fetchall()
    conn.close()
    excerpts = [(r["id"], r["d"]) for r in rows]
    sources = excerpts + [(f"synthetic{i}", s) for i, s in enumerate(SYNTHETIC_SOURCES)]
    print(f"{len(excerpts)} real excerpts (len {min(len(d) for _, d in excerpts)}-{max(len(d) for _, d in excerpts)}) "
          f"+ {len(SYNTHETIC_SOURCES)} entity-dense synthetic sources\n")

    must_fail = {
        "verbatim": lambda s: s,
        "half-lift": lambda s: s[: len(s) // 2] + " Separately, the broader market reaction has been muted so far.",
        "title-case": lambda s: s.title(),
    }
    evasion = {"swapped": swap_entities, "sprinkled": sprinkled}
    bad = 0
    print(f"{'must be FLAGGED':22}{'items':>6}{'old guard':>11}{'masked guard':>14}")
    for name, build in {**must_fail, **evasion}.items():
        if name == "swapped":
            print(f"\n{'evasion (info only)':22}{'items':>6}{'old guard':>11}{'masked guard':>14}")
        old = new = n = 0
        misses = []
        for sid, src in sources:
            # title-case is a property of the SOURCE: lift a Title-Cased source verbatim.
            if name == "title-case":
                src_used, d = src.title(), draft(src.title())
            else:
                src_used, d = src, draft(build(src))
            n += 1
            old += bool(_find_reproduction_issues_unmasked(d, src_used))
            hit = bool(_find_reproduction_issues(d, src_used))
            new += hit
            if not hit:
                misses.append(sid)
        print(f"{name:22}{n:6}{old:8}/{n:<3}{new:10}/{n:<3}" + (f"   MISSED: {misses[:6]}" if misses else ""))
        if name in must_fail:
            bad += len(misses)

    print(f"\n{'must PASS':22}{'items':>6}{'old guard':>11}{'masked guard':>14}   (flagged = bad)")
    facts_old = facts_new = n = 0
    flagged_after = []
    for sid, src in sources:
        forms = sorted(E.entity_surface_forms(src))
        words = ["Analysts note", " and ".join(forms[:6]) or "no figures", "were all in focus, with traders watching for follow-through"]
        d = draft(" ".join(words))
        n += 1
        facts_old += bool(_find_reproduction_issues_unmasked(d, src))
        f = bool(_find_reproduction_issues(d, src))
        facts_new += f
        if f:
            flagged_after.append(sid)
    print(f"{'facts-only':22}{n:6}{facts_old:8}/{n:<3}{facts_new:10}/{n:<3}")
    p_old = sum(bool(_find_reproduction_issues_unmasked(draft(p), s)) for s, p in PARAPHRASES)
    p_new = sum(bool(_find_reproduction_issues(draft(p), s)) for s, p in PARAPHRASES)
    print(f"{'paraphrase':22}{len(PARAPHRASES):6}{p_old:8}/{len(PARAPHRASES):<3}{p_new:10}/{len(PARAPHRASES):<3}")
    bad += facts_new + p_new

    print("\nEntity-rich LIFTS (entities masked, wording still copied) must still be flagged:")
    lifts = [
        (SYNTHETIC_SOURCES[1], "The SEC and CFTC told Coinbase, Kraken and Binance on Tuesday that perpetual futures on "
         "tokenized equities fall under derivatives rules already in place. Sources said."),
        (SYNTHETIC_SOURCES[0], "Bitcoin ETFs lost $1.2 billion last week, BlackRock's IBIT recorded outflows of $420 "
         "million, Fidelity's FBTC lost $300 million."),
    ]
    for src, text in lifts:
        flagged = bool(_find_reproduction_issues(draft(text), src))
        print(f"  masked guard flags it: {flagged}   (old guard: {bool(_find_reproduction_issues_unmasked(draft(text), src))})")
        bad += 0 if flagged else 1

    print("\nRESULT:", "OK -- every lift flagged, every fact-only/paraphrase draft passed" if bad == 0
          else f"PROBLEM -- {bad} case(s) wrong (see MISSED / flagged above)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
