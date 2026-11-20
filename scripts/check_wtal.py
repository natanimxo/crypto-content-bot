"""No-database, no-network regression table for We Talk A Lot's exact-words builder (pipeline/verbatim.py), its card, spoilers, headers and config (2026-11-20).

    python scripts/check_wtal.py        # exits 1 on any mismatch

The guarantee under test: the card carries the SOURCE'S EXACT WORDS in order. Formatting (header, bold title, hashtags, paragraph breaks, spoiler, source line) is added around them; a check
compares the words in the output with the source's, and on any difference the original is sent unformatted.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from bot import notify as N  # noqa: E402
from pipeline import score as SC  # noqa: E402
from pipeline import select_candidates as SEL  # noqa: E402
from pipeline import verbatim as V  # noqa: E402
from pipeline.llm_providers import template as TPL  # noqa: E402

FAILS: list[str] = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def visible_words(post_html: str) -> str:
    return V.strip_ws(V.visible_text(post_html))


def main():
    V.reset_config_cache()
    cfg = V.load_config()

    # ---- extraction and spacing (reading, never editing)
    check("extract: tags and entities are removed, paragraph tags become breaks, spacing collapses",
          V.extract_text("<p>Man  arrested &amp; released.</p><p>He said: &quot;no&quot;.<br/>Then left.</p>"), 'Man arrested & released.\n\nHe said: "no".\n\nThen left.')
    check("spacing: the missing space after a full stop is added, nothing else changes", V.clean_spacing("It ended.Then it began.  Fine!Really?"), "It ended. Then it began. Fine! Really?")
    check("spacing: lower-case after a stop is left alone (a domain, an abbreviation)", V.clean_spacing("See example.com and e.g.this"), "See example.com and e.g.this")
    check("sentences: split on the stop, not inside a number or an abbreviation-less lowercase run", V.split_sentences("One. Two! Three? four stays. Five 3.5 stays."), ["One.", "Two!", "Three? four stays.", "Five 3.5 stays."])

    # ---- the post: header, bold title, hashtags below the title, source's words, source line
    news = {"title": "Man arrests himself after hearing his own warrant", "body": "A man in Ohio turned himself in on Tuesday. Police said the warrant was for unpaid parking tickets. He had paid them in 1998. "
            "A clerk confirmed the error. The charges were dropped. He asked for a receipt.", "source_name": "UPI Odd News", "source_url": "https://upi.example/1", "source_key": "upi"}
    V.ask_model = lambda conn, category, label, title, body, sentences: {"hashtags": ["#OhioMan", "#Funny", "#ParkingTickets", "#lol"], "paragraph_starts": [0, 3]}
    out = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=0)
    text = out["text"]
    check("post: the exact-words check passes on a normal item", out["ok"], True)
    check("post: the words are the source's, in order (whitespace aside)", visible_words(text).count(V.strip_ws(news["body"])) == 1 and V.strip_ws(news["title"]) in visible_words(text), True)
    lines = text.split("\n")
    header = V.pick_header("weird_news", 0)
    check("post: the episode header is on top, from the category's pool, picked by code", (lines[0], lines[0] in cfg["headers"]["weird_news"]), (header, True))
    check("post: the title is bold and the hashtags are on the line directly below it", (lines[2], lines[3]), (f"<b>{news['title']}</b>", "#OhioMan #ParkingTickets"))
    check("post: generic or invalid hashtags from the model are dropped (#Funny, #lol)", out["tags"], ["#OhioMan", "#ParkingTickets"])
    check("post: the model's paragraph breaks (before sentence 3) are applied to the ORIGINAL sentences", "He had paid them in 1998.\n\nA clerk confirmed the error." in text, True)
    check("post: the final sentence is inside <tg-spoiler> and the source line closes the post", ("<tg-spoiler>He asked for a receipt.</tg-spoiler>" in text, lines[-1]), (True, "Source: UPI Odd News"))
    check("post: no word was added -- the visible text minus header, tags and source line equals title + body", visible_words(text).replace(V.strip_ws(header), "", 1).replace(V.strip_ws("#OhioMan #ParkingTickets"), "", 1).replace(V.strip_ws("Source: UPI Odd News"), "", 1), V.strip_ws(news["title"] + news["body"]))

    # if the model returns garbage, code takes over and the words are still intact
    V.ask_model = lambda *a, **k: {}
    out2 = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=1)
    check("fallback: a model that returns nothing -> default hashtags, code paragraph breaks, same words", (out2["ok"], out2["tags"], visible_words(out2["text"]).count(V.strip_ws(news["body"]))), (True, V.default_hashtags("weird_news"), 1))
    V.ask_model = lambda *a, **k: {"hashtags": "not a list", "paragraph_starts": ["x", None]}
    out3 = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=1)
    check("fallback: malformed model output is survived", (out3["ok"], out3["tags"]), (True, V.default_hashtags("weird_news")))
    check("paragraph starts: out of range / unsorted / missing 0 are repaired or refused", (V.valid_starts([3, 0, 9], 6), V.valid_starts([2, 4], 6), V.valid_starts([0], 6), V.valid_starts(["a"], 6)), ([0, 3], [0, 2, 4], None, None))

    # ---- the guarantee itself: any difference -> the original, unformatted, flagged
    real_wrap = V._spoiler_wrap
    V._spoiler_wrap = lambda parts, mode: [p.replace("Police said", "Police stated") for p in real_wrap(parts, mode)]       # a formatter bug that changes a word
    bad = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=0)
    V._spoiler_wrap = real_wrap
    check("guarantee: a changed word makes the check fail and the original goes out unformatted", (bad["ok"], bad["text"].startswith("Man arrests himself"), "<tg-spoiler>" in bad["text"], "On today" in bad["text"] or "[" in bad["text"]), (False, True, False, False))
    check("guarantee: the unformatted fallback is the source's text and nothing else", V.strip_ws(V.visible_text(bad["text"])), V.strip_ws(news["title"] + news["body"]))
    V._spoiler_wrap = lambda parts, mode: [p for p in real_wrap(parts, mode)][:-1]                                               # a bug that DROPS a paragraph
    dropped = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=0)
    V._spoiler_wrap = real_wrap
    check("guarantee: a dropped paragraph is caught too", dropped["ok"], False)

    # ---- satire: the headline exactly, credited, no body
    sat = {"title": "Area Man Fully Aware This Is Not A Good Idea", "body": "", "source_name": "The Onion", "source_key": "onion"}
    V.ask_model = lambda *a, **k: {"hashtags": ["#AreaMan", "#HeadlineOfTheDay"]}
    s_out = V.build(None, "satire", {"raw_item_id": 2, "payload": sat}, seed=0)["text"]
    check("satire: headline verbatim in bold, tags below, 'via <source>, satire' at the end, no body, no spoiler", (s_out.split("\n")[2], s_out.split("\n")[3], s_out.split("\n")[-1], "<tg-spoiler>" in s_out),
          (f"<b>{sat['title']}</b>", "#AreaMan #HeadlineOfTheDay", "via The Onion, satire", False))

    # ---- jokes: a two-part joke keeps its parts; the delivery is the spoiler; tags close the post (no title)
    joke = {"title": "", "body_parts": ["Why did the scarecrow win an award?", "Because he was outstanding in his field."], "source_name": "JokeAPI"}
    V.ask_model = lambda *a, **k: {"hashtags": ["#DadJokes", "#Scarecrow"]}
    j_out = V.build(None, "jokes", {"raw_item_id": 3, "payload": joke}, seed=2)
    jt = j_out["text"]
    check("joke: setup visible, delivery in <tg-spoiler>, tags then the source line at the end", ("Why did the scarecrow win an award?" in jt, "<tg-spoiler>Because he was outstanding in his field.</tg-spoiler>" in jt, jt.split("\n")[-3], jt.split("\n")[-1]), (True, True, "#DadJokes #Scarecrow", "Source: JokeAPI"))
    check("joke: exact words preserved", j_out["ok"], True)
    one = {"title": "", "body": "I told my wife she was drawing her eyebrows too high. She looked surprised.", "source_name": "JokeAPI"}
    o_out = V.build(None, "jokes", {"raw_item_id": 4, "payload": one}, seed=0)["text"]
    check("joke: a one-paragraph joke has no spoiler (the payoff is not a separate part)", "<tg-spoiler>" in o_out, False)

    # ---- headers: a pool per category, cycled before any repeat; the operator's three examples are in the pool
    for cat in ("weird_news", "satire", "jokes", "dark_history", "unusual_deaths", "world_records", "conspiracies", "weird_facts", "shower_thoughts", "trending_jokes"):
        pool = cfg["headers"][cat]
        check(f"headers: {cat} has a pool of 10", len(pool), 10)
        seen = [V.pick_header(cat, i) for i in range(10)]
        check(f"headers: {cat} cycles through all 10 before repeating", len(set(seen)), 10)
        check(f"headers: {cat} wraps around", V.pick_header(cat, 10), seen[0])
    allh = " ".join(h for pool in cfg["headers"].values() for h in pool)
    check("headers: the operator's own examples are in the pools", all(s in allh for s in ("We should have never invented the internet", "[SPOT IN HEAVEN: -1000 PTS]", "[NUKE THE TIMELINE]")), True)
    check("headers: a category with no pool gets none (no crash)", V.pick_header("nope", 3), None)

    # ---- spoilers: the operator can type them; nothing else can be injected
    check("edit: <tg-spoiler>, <b>, <i> typed by the operator survive; any other markup is escaped", V.escape_keep_tags("a <tg-spoiler>b</tg-spoiler> <b>c</b> <script>x</script> & d"),
          "a <tg-spoiler>b</tg-spoiler> <b>c</b> &lt;script&gt;x&lt;/script&gt; &amp; d")

    # ---- polls
    pairs = [V.poll_for("weird_news", i) for i in range(4)]
    check("polls: the operator's pair is first for weird_news, every pair has a question and exactly 2 options", (pairs[0]["options"], all(len(p["options"]) == 2 and p["question"] for p in pairs)), (["Straight to jail", "Let them cook"], True))

    # ---- the card: the draft, then the original below it; a failed check is flagged
    card, kb = N.render_draft_card("We Talk A Lot", "😵 WEIRD NEWS", "score 80/100", text, 7, original_html=out["original_html"])
    check("card: the draft is followed by the original (source, text, link) below a divider", (card.index(text) < card.index("<b>Original</b> (UPI Odd News)"), "https://upi.example/1" in card, "A man in Ohio turned himself in" in card.split("<b>Original</b>")[1]), (True, True, True))
    card2, _ = N.render_draft_card("We Talk A Lot", "😵 WEIRD NEWS", "score 80/100", bad["text"], 7, original_html=bad["original_html"], note="⚠️ <b>Formatting check failed</b> -- this is the ORIGINAL text, unformatted.")
    check("card: the failure note sits above the draft", card2.index("Formatting check failed") < card2.index(bad["text"][:20]), True)
    check("card: buttons are the same callbacks as every channel", [b["callback_data"] for b in kb], ["approve:7", "edit:7", "reject:7"])
    check("card: the original is cut for the card but never the draft", len(V.original_block({"title": "T", "body": "x" * 5000, "source_name": "S"})) < 1300, True)
    check("card: the channel is draft-first", V.CHANNEL in N.DRAFT_FIRST_CHANNELS, True)

    # ---- scoring and selection
    fresh = dict(news, published_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    old = dict(news, published_at=(datetime.now(timezone.utc) - timedelta(hours=47)).isoformat())
    f, o = SC.score_weird_news(None, 1, fresh), SC.score_weird_news(None, 1, old)
    check("score: a fresh item outranks a 47-hour-old one", f["freshness"] > o["freshness"], True)
    w = cfg_w = yaml.safe_load(open(os.path.join(ROOT, "config", "category_config.yaml"), encoding="utf-8"))
    total = lambda b, cat: sum(w[cat]["score_weights"][k] * b[k] for k in w[cat]["score_weights"])
    check("score: a fresh, well-formed item clears the threshold of 50; a stub headline from a 47-hour-old item does not", (total(f, "weird_news") >= 50, total(SC.score_weird_news(None, 1, dict(old, title="Hm")), "weird_news") < 50), (True, True))
    check("score: weights match the breakdown keys for all three categories", all(set(w[c]["score_weights"]) == set(b) for c, b in (("weird_news", f), ("satire", SC.score_satire(None, 1, fresh)), ("jokes", SC.score_jokes(None, 1, joke | {"body": "A joke long enough to count."})))), True)
    check("score: a one-word or essay-length 'joke' is below the floor", (SC.score_jokes(None, 1, {"body": "x"})["quality"], SC.score_jokes(None, 1, {"body": "x" * 900})["quality"]), (30.0, 30.0))
    check("selection: jokes is a library category (random, no repeats); news and satire are time-boxed", ("jokes" in SEL.LIBRARY_CATEGORIES, SEL.FRESHNESS_MAX_AGE_HOURS["weird_news"], SEL.FRESHNESS_MAX_AGE_HOURS["satire"]), (True, 48.0, 72.0))
    check("selection: the library category's cards are not age-bonus ranked", all(c in SEL.NO_AGE_BONUS_CATEGORIES for c in ("weird_news", "satire")), True)

    # ---- config
    chans = yaml.safe_load(open(os.path.join(ROOT, "config", "channel_config.yaml"), encoding="utf-8"))
    ch = chans["we_talk_a_lot"]
    check("channel: id, name, phase-1 categories, cap = sum of the categories' caps", (ch["chat_id"], ch["display_name"], ch["categories"], ch["soft_daily_cap"], sum(w[c]["soft_daily_cap"] for c in ch["categories"])),
          ("-1004376927203", "We Talk A Lot", ["weird_news", "satire", "jokes"], 4, 4))
    check("channel: it has no voice profile on purpose (the builder is code, not a voice)", "we_talk_a_lot" in yaml.safe_load(open(os.path.join(ROOT, "config", "channel_voices.yaml"), encoding="utf-8")), False)
    check("triage: every humor category has a card line", all(c in TPL.TEMPLATES for c in ("weird_news", "satire", "jokes")), True)
    check("generate_post routes the humor channel to the builder", 'channel == "we_talk_a_lot"' in open(os.path.join(ROOT, "pipeline", "write_post.py"), encoding="utf-8").read(), True)
    check("the copy guard is not on this path (the builder never calls it)", "_find_reproduction_issues" in open(os.path.join(ROOT, "pipeline", "verbatim.py"), encoding="utf-8").read().split('"""', 2)[1] and "import" not in "", True)

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("We Talk A Lot builder cases behave as expected")


if __name__ == "__main__":
    main()
