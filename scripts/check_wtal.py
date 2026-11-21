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

    # ---- collectors (offline: fake feeds and a fake page fetcher)
    import feedparser
    from collectors import wtal_jokes as WJ, wtal_news as WN
    from pipeline import article as AR
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    def rss(items):
        body = "".join(f"<item><title>{t}</title><link>{l}</link><guid>g{i}</guid><pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate><description><![CDATA[{s}]]></description></item>" for i, (t, d, s, l) in enumerate(items))
        return feedparser.parse(f"<?xml version='1.0'?><rss version='2.0'><channel><title>f</title>{body}</channel></rss>".encode())
    hrs = lambda h: now - timedelta(hours=h)
    class FakeFetcher:
        def __init__(self, pages): self.pages, self.asked = pages, []
        def get(self, url):
            self.asked.append(url)
            return self.pages.get(url, {"outlet": "", "date": None, "paragraphs": [], "status": "http_403"})
    PAGE = "<html><head><meta property='og:site_name' content='CNA'><meta property='article:published_time' content='2026-11-20T03:00:00+00:00'></head><body><nav><p>Menu item that is long enough to be a real paragraph of text</p></nav><article><div class='content-detail__description'><p>Standfirst that restates the whole story in one line for the front page.</p></div><div class='category-news tag-a post-1 status-publish'><p>By Staff Writer</p><p>SINGAPORE: A woman sued her driver after he braked for a chicken, but lost the case in court on Thursday.</p><p>The judge said the incident was caused by a chicken crossing the road and was part of life.</p><p>A third paragraph that must not be included in the card at all.</p></div></article></body></html>"
    pg = AR.from_html(PAGE, "https://www.channelnewsasia.com/x")
    check("article: outlet from og:site_name, the article's own date, standfirst / byline / nav skipped, the first body paragraphs verbatim (a third is not added)", (pg["outlet"], pg["date"].day, pg["paragraphs"]), ("CNA", 20, ["SINGAPORE: A woman sued her driver after he braked for a chicken, but lost the case in court on Thursday.", "The judge said the incident was caused by a chicken crossing the road and was part of life."]))
    check("article: dateline is 'Outlet, 20 Nov:' (no date -> 'Outlet:')", (AR.dateline("BBC", datetime(2026, 11, 20, tzinfo=timezone.utc)), AR.dateline("BBC", None)), ("BBC, 20 Nov:", "BBC:"))
    check("article: a page with no usable paragraph says so (paywall / bot wall) instead of inventing one", AR.from_html("<html><body><p>Short.</p><p>Please enable cookies to continue reading this article on our website today.</p></body></html>", "https://x.test/")["paragraphs"], [])
    check("article: the second paragraph is dropped when it would push the total past 900 characters", len(AR._pick(["a" * 700, "b" * 400])), 1)
    check("article: a feed fragment gives its own first paragraphs, 'The post X appeared first on' lines are not the lede", AR.paragraphs_from_fragment("<p>The opening paragraph of the satire piece, written in full by the outlet.</p><p>The post <a href='x'>T</a> appeared first on <a>Y</a>.</p>")[0].startswith("The opening paragraph"), True)

    lem_feed = rss([("META: Banning Townflex", hrs(1), "x", "https://lemmy.world/post/1"),
                    ("Woman loses suit against driver over incident caused by chicken crossing road", hrs(2), 'submitted by <a href="https://lemmy.zip/u/a">a</a> to <a href="https://lemmy.world/c/nottheonion">n</a><br />39 points<br /><a href="https://www.channelnewsasia.com/sg/chicken">https://www.channelnewsasia.com/sg/chicken</a>', "https://lemmy.world/post/2"),
                    ("A text post that links to no article at all, only talks", hrs(2), "submitted by a to n<p>just words</p>", "https://lemmy.world/post/3"),
                    ("Old odd story from long ago that nobody posts now", hrs(300), '<br /><a href="https://x.test/old">x</a>', "https://lemmy.world/post/4")])
    ents = WN.weird_entries("lemmy", lem_feed, now)
    check("weird news: a Lemmy post linking to an image, video or social post is not an article", [WN._is_media(u) for u in ("https://thelemmy.club/pictrs/image/69b8.webp", "https://i.redd.it/abc.jpeg", "https://www.youtube.com/watch?v=1", "https://www.cnn.com/2026/11/13/us/story")], [True, True, True, False])
    check("weird news: Lemmy keeps only fresh LINK posts (META, text posts and stale ones are skipped)", [e["title"][:20] for e in ents], ["Woman loses suit aga"])
    ff = FakeFetcher({"https://www.channelnewsasia.com/sg/chicken": {"outlet": "CNA", "date": None, "paragraphs": ["SINGAPORE: A woman sued her driver.", "The judge dismissed it."], "status": "ok"}})
    wp = WN.weird_payload("lemmy", "Lemmy c/nottheonion", ents[0], ff)
    check("weird news: the article is opened, its paragraphs become the body, the dateline is the outlet and day", (wp["body"], wp["dateline"], wp["body_source"], wp["source_url"]), ("SINGAPORE: A woman sued her driver.\n\nThe judge dismissed it.", "CNA, 20 Nov:", "page", "https://www.channelnewsasia.com/sg/chicken"))
    ff2 = FakeFetcher({})
    wp2 = WN.weird_payload("lemmy", "Lemmy c/nottheonion", ents[0], ff2)
    check("weird news: an article that cannot be opened is a headline-only card, flagged body_source=none, outlet from the link's domain", (wp2["body"], wp2["body_source"], wp2["dateline"]), ("", "none", "channelnewsasia.com, 20 Nov:"))
    upi_feed = rss([("Pepper pileup closes two lanes of busy Florida road", hrs(3), '<img src="x.jpg" /> Authorities in Florida said two lanes of a busy roadway were temporarily closed when a truck lost 19 pallets of peppers.', "https://www.upi.com/Odd_News/a")])
    up = WN.weird_payload("upi", "UPI", WN.weird_entries("upi", upi_feed, now)[0], FakeFetcher({}))
    check("weird news: UPI's lede comes from the feed when its page cannot be opened (image tag removed)", (up["body"], up["body_source"], up["dateline"]), ("Authorities in Florida said two lanes of a busy roadway were temporarily closed when a truck lost 19 pallets of peppers.", "feed", "UPI, 20 Nov:"))
    sat_feed = rss([("Area Man Passionately Hates Thing He Has Never Seen", hrs(10), "<p>WASHINGTON—An area man announced Thursday that he passionately hates a thing he has never seen, in a statement of some length.</p><p>Second.</p>", "https://theonion.com/a"),
                    ("Satire headline with nothing underneath it at all", hrs(5), "", "https://reductress.com/b"), ("Short", hrs(1), "", "https://x.test/c")])
    sents = WN.satire_entries("onion", sat_feed, now)
    check("satire: stubs dropped, fresh headlines kept", [s["title"][:10] for s in sents], ["Area Man P", "Satire hea"])
    sp = WN.satire_payload("onion", "The Onion", sents[0], FakeFetcher({}))
    check("satire: the feed's own opening paragraph is the body, the dateline names the outlet and day", (sp["body"][:20], sp["dateline"], sp["body_source"]), ("WASHINGTON—An area m", "The Onion, 20 Nov:", "feed"))
    sp0 = WN.satire_payload("reductress", "Reductress", sents[1], FakeFetcher({}))
    check("satire: nothing in the feed and the page will not open -> headline-only, flagged", (sp0["body"], sp0["body_source"]), ("", "none"))
    check("fetcher: a site that answered an error is not asked again in the same run; Cloudflare-walled sites are never asked", (WN.Fetcher().blocked >= {"upi.com", "thedailymash.co.uk"},), (True,))
    check("same story: the same item worded by two sources collapses; unrelated ones do not", (WN.same_story("Florida man rides alligator to work", "Man in Florida rides an alligator to his work"), WN.same_story("Florida man rides alligator to work", "Squirrel elected mayor of tiny town")), (True, False))
    kept, dropped = WN._dedupe([{"payload": {"title": "Squirrel elected mayor of tiny town"}}, {"payload": {"title": "Tiny town elects squirrel as mayor"}}, {"payload": {"title": "Unrelated headline about cheese boats"}}], [])
    check("same story: dedupe keeps one of a pair, counts the drop, and honours already-stored titles", (len(kept), dropped, len(WN._dedupe([{"payload": {"title": "Squirrel elected mayor of tiny town"}}], ["Tiny town elects squirrel mayor"])[0])), (2, 1, 0))
    # the card
    full = V.build(None, "weird_news", {"raw_item_id": 9, "payload": wp}, seed=0, use_model=False)
    lines = full["text"].split("\n")
    check("card (news): header, then 'CNA, 20 Nov:', then the bold title, then the hashtags; no bottom 'Source:' line; the check passed", (lines[2], lines[3].startswith("<b>Woman loses"), lines[4].startswith("#"), "Source:" in full["text"], full["ok"]), ("CNA, 20 Nov:", True, True, False, True))
    check("card (news): the article's own words follow, the last sentence in a spoiler", ("SINGAPORE: A woman sued her driver." in full["text"], "<tg-spoiler>The judge dismissed it.</tg-spoiler>" in full["text"]), (True, True))
    sat = V.build(None, "satire", {"raw_item_id": 10, "payload": sp}, seed=0, use_model=False)
    check("card (satire): dateline on top AND the 'via The Onion, satire' label at the bottom; the check passed", (sat["text"].split("\n")[2], sat["text"].endswith("via The Onion, satire"), sat["ok"]), ("The Onion, 20 Nov:", True, True))
    check("scoring: a card with an article opening outranks the same headline alone", (SC.score_weird_news(None, 1, wp | {"published_at": hrs(2).isoformat()})["quality"] > SC.score_weird_news(None, 1, wp2 | {"published_at": hrs(2).isoformat()})["quality"], SC.score_satire(None, 1, sp | {"published_at": hrs(2).isoformat()})["quality"] > SC.score_satire(None, 1, sp0 | {"published_at": hrs(2).isoformat()})["quality"]), (True, True))
    # dark jokes
    tp = WJ.jokeapi_payload({"id": 1, "category": "Dark", "type": "twopart", "setup": "Why?", "delivery": "Because.", "flags": {"racist": True, "nsfw": False}})
    check("jokes: JokeAPI two-part keeps its parts, category and all flags", (tp["body_parts"], tp["joke_category"], tp["flags"]["racist"], tp["source_name"]), (["Why?", "Because."], "Dark", True, "JokeAPI"))
    check("jokes: an empty delivery is not a joke", WJ.jokeapi_payload({"id": 2, "type": "twopart", "setup": "Why?", "delivery": "", "flags": {}}), None)
    check("jokes: dedupe key ignores case, punctuation and spacing", WJ.norm_key("Why did the chicken -- cross?") == WJ.norm_key("why did the CHICKEN cross"), True)
    check("jokes: only the Dark category is fetched", WJ.JOKEAPI_CATEGORIES, "Dark")
    import pipeline.llm as LLM
    real = LLM.generate_write
    try:
        LLM.generate_write = lambda conn, cat, prompt: '{"verdicts": [true, false, true]}'
        ok3 = WJ.dark_verdicts(None, ["a", "b", "c"])
        LLM.generate_write = lambda conn, cat, prompt: '{"verdicts": [true]}'
        short = WJ.dark_verdicts(None, ["a", "b", "c"])
        LLM.generate_write = lambda conn, cat, prompt: "I cannot help with that"
        junk = WJ.dark_verdicts(None, ["a", "b"])
        def boom(*a, **k): raise RuntimeError("model down")
        LLM.generate_write = boom
        down = WJ.dark_verdicts(None, ["a"])
    finally:
        LLM.generate_write = real
    check("jokes: the DeepSeek check keeps yes / no per joke in order", ok3, [True, False, True])
    check("jokes: a wrong-length, unparseable or failed answer is UNKNOWN (None), never a yes", (short, junk, down), ([None, None, None], [None, None], [None]))
    orig = V.original_block(tp)
    check("jokes: the card's original block shows the category and the flags that are set", ("Dark | flags: racist" in orig, "JokeAPI" in orig), (True, True))
    check("jokes: Dark JokeAPI scores above other sources", (SC.score_jokes(None, 1, tp | {"body": "x" * 30})["quality"], SC.score_jokes(None, 1, {"body": "x" * 30, "source_key": "jokeapi", "joke_category": "Pun"})["quality"]), (80.0, 75.0))
    check("headers: the jokes pool is the dark pool (no pun-friendly lines), one pool per category", (all(h for h in V.load_config()["headers"]["jokes"]), "jokes" in V.load_config()["headers"], "weird_news" in V.load_config()["headers"], "satire" in V.load_config()["headers"]), (True, True, True, True))
    # hashtags (operator 2026-11-21): a tag whose every word is generic is out; one specific word makes it acceptable
    check("hashtags: genre-only tags are rejected", [V.is_generic_tag(t) for t in ("#EdgyJokes", "#DarkHumorJokes", "#OffensiveComedy", "#BlackComedy", "#WeirdNews", "#darkhumorjokes", "#Satire")], [True] * 7)
    check("hashtags: a tag with a specific subject word is kept", [V.is_generic_tag(t) for t in ("#DarkCatholicHumor", "#AcnePunchline", "#ChickenCrossing", "#SingaporeSuit", "#PriestJokes", "#AbsurdButTrue")], [False] * 6)
    check("hashtags: the model's generic ones are dropped, its specific ones kept", V.valid_hashtags(["#EdgyJokes", "#DarkHumorJokes", "#PriestJokes", "#AcnePunchline", "#Extra"]), ["#PriestJokes", "#AcnePunchline", "#Extra"])
    # Lemmy jokes
    ok_post = {"post": {"id": 1, "name": "My therapist said time heals", "body": "Then she sent me the bill", "ap_id": "https://lemmy.world/post/1", "nsfw": True}}
    lp = WJ.lemmy_payload(ok_post, "jokes")
    check("lemmy jokes: title = setup, body = delivery (two parts, delivery spoilered), nsfw kept as a flag, credited to the community", (lp["body_parts"], lp["flags"], lp["source_name"], lp["joke_category"]), (["My therapist said time heals", "Then she sent me the bill"], {"nsfw": True}, "Lemmy c/jokes", "c/jokes"))
    lb = V.build(None, "jokes", {"raw_item_id": 5, "payload": lp}, seed=0, use_model=False)
    check("lemmy jokes: the card passes the exact-words check with the delivery in a spoiler", (lb["ok"], "<tg-spoiler>Then she sent me the bill</tg-spoiler>" in lb["text"]), (True, True))
    check("lemmy jokes: Markdown / links / spoiler markup, mod posts and removed posts are skipped", [WJ.lemmy_payload({"post": {"id": i, "name": n, "body": b, **extra}}, "jokes") for i, (n, b, extra) in enumerate([("Joke", "see https://x.test", {}), ("Joke", "::: spoiler tap" + chr(10) + "x" + chr(10) + ":::", {}), ("META: rules", "", {}), ("Joke", "x", {"removed": True}), ("Joke", "x", {"featured_community": True})])], [None] * 5)
    check("lemmy jokes: a changed source set makes the monthly pass due again", WJ.source_names(), ["jokeapi", "lemmy_jokes", "lemmy_darkhumor"])
    check("notify: the humor channel is limited to 2 cards per cycle in total", N.CHANNEL_PER_CYCLE_LIMIT.get("we_talk_a_lot"), 2)

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
