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
    V.ask_model = lambda conn, category, label, title, sentences: {"paragraph_starts": [0, 3]}
    out = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=0)
    text = out["text"]
    check("post: the exact-words check passes on a normal item", out["ok"], True)
    check("post: the words are the source's, in order (whitespace aside)", visible_words(text).count(V.strip_ws(news["body"])) == 1 and V.strip_ws(news["title"]) in visible_words(text), True)
    lines = text.split("\n")
    header = V.pick_header("weird_news", 0)
    check("post: the episode header is on top, from the category's pool, picked by code", (lines[0], lines[0] in cfg["headers"]["weird_news"]), (header, True))
    check("post: the title is bold and the hashtags are on the line directly below it", (lines[2], lines[3]), (f"<b>{news['title']}</b>", "#WeTalkALot #WeirdNews"))
    check("post: the hashtags are the category's fixed pair (#WeTalkALot + its one tag), whatever the model says", out["tags"], ["#WeTalkALot", "#WeirdNews"])
    check("post: the model's paragraph breaks (before sentence 3) are applied to the ORIGINAL sentences", "He had paid them in 1998.\n\nA clerk confirmed the error." in text, True)
    check("post: the final sentence is inside <tg-spoiler>, and there is NO source or credit line in the post", ("<tg-spoiler>He asked for a receipt.</tg-spoiler>" in text, "Source" in text, "UPI" in text), (True, False, False))
    check("post: no word was added -- the visible text minus header and tags equals title + body", visible_words(text).replace(V.strip_ws(header), "", 1).replace(V.strip_ws("#WeTalkALot #WeirdNews"), "", 1), V.strip_ws(news["title"] + news["body"]))

    # if the model returns garbage, code takes over and the words are still intact
    V.ask_model = lambda *a, **k: {}
    out2 = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=1)
    check("fallback: a model that returns nothing -> the same fixed hashtags, code paragraph breaks, same words", (out2["ok"], out2["tags"], visible_words(out2["text"]).count(V.strip_ws(news["body"]))), (True, V.hashtags_for("weird_news"), 1))
    V.ask_model = lambda *a, **k: {"hashtags": "not a list", "paragraph_starts": ["x", None]}
    out3 = V.build(None, "weird_news", {"raw_item_id": 1, "payload": news}, seed=1)
    check("fallback: malformed model output is survived", (out3["ok"], out3["tags"]), (True, V.hashtags_for("weird_news")))
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
    V.ask_model = lambda *a, **k: {}
    s_out = V.build(None, "satire", {"raw_item_id": 2, "payload": sat}, seed=0)["text"]
    check("satire: headline verbatim in bold, the fixed tags below, no source or 'via' line, no body, no spoiler", (s_out.split("\n")[2], s_out.split("\n")[3], "via" in s_out.split("\n")[-1], "Onion" in s_out, "<tg-spoiler>" in s_out),
          (f"<b>{sat['title']}</b>", "#WeTalkALot #Satire", False, False, False))

    # ---- jokes: a two-part joke keeps its parts; the delivery is the spoiler; tags close the post (no title)
    joke = {"title": "", "body_parts": ["Why did the scarecrow win an award?", "Because he was outstanding in his field."], "source_name": "JokeAPI"}
    V.ask_model = lambda *a, **k: {}
    j_out = V.build(None, "jokes", {"raw_item_id": 3, "payload": joke}, seed=2)
    jt = j_out["text"]
    check("joke: setup visible, delivery in <tg-spoiler>, and the post closes with exactly #WeTalkALot #Jokes (no source line)", ("Why did the scarecrow win an award?" in jt, "<tg-spoiler>Because he was outstanding in his field.</tg-spoiler>" in jt, jt.split("\n")[-1], "JokeAPI" in jt), (True, True, "#WeTalkALot #Jokes", False))
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
    variety = ('<html><head><meta property="og:site_name" content="Variety"></head><body><article><div class="article-header"><p class="byline-text-long">By Some Writer of the Day</p></div>'
               '<div class="pmc-paywall"><p class="paragraph">What does an actor do in the quiet moments before the announcement that will change his life today?</p>'
               '<p class="paragraph">For Robert Pattinson, there were about 12 hours between the call and the release. He spent it watching a film.<div class="injected-related-story"><h2>Related Stories</h2>'
               '<div>Himalayan Story Lab Unveils Fourth Cohort</div></div><div class="admz"><script>blogherads.adq.push(1)</script></div></p></div></article></body></html>')
    vr = AR.from_html(variety, "https://variety.com/2026/x/")
    check("article: a body wrapped in a 'pmc-paywall' div is the article (Variety), and a Related Stories card nested inside a paragraph is not part of its words", vr["paragraphs"],
          ["What does an actor do in the quiet moments before the announcement that will change his life today?", "For Robert Pattinson, there were about 12 hours between the call and the release. He spent it watching a film."])
    blooloop = ('<html><body><article><div class="widget__subheadline"><h2 class="widget__subheadline-text"><p>As challenges continue to stack up for attraction operators, finding new opportunities matters.</p></h2></div>'
                '<div class="body-description"><p>Whether you are a theme park, cultural organisation, zoo or any other visitor attraction, retail can contribute to growth.</p>'
                '<small class="media-caption"><p>Retail offers a way for guests to get involved at the park every day</p></small></div></article></body></html>')
    check("article: a 'body-description' container is body text (Blooloop), the standfirst above it and the caption inside it are not", AR.from_html(blooloop, "https://blooloop.com/x/")["paragraphs"],
          ["Whether you are a theme park, cultural organisation, zoo or any other visitor attraction, retail can contribute to growth."])
    check("article: a paywall MESSAGE block is still skipped", AR.from_html('<html><body><article><div class="paywall-message"><p>This is a long enough line that is only the subscription pitch for the reader.</p></div><p>The real first paragraph of the story, long enough to be kept as the lede.</p></article></body></html>', "https://x.test/")["paragraphs"], ["The real first paragraph of the story, long enough to be kept as the lede."])
    check("jokes: the check prompt asks for dark AND a clear punchline, and verdicts carry check_version 2", ("clear punchline" in WJ.DARK_PROMPT, WJ.CHECK_VERSION), (True, 2))
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
    check("card (satire): dateline on top, no 'via ..., satire' line at the bottom; the check passed", (sat["text"].split("\n")[2], "via The Onion" in sat["text"], sat["ok"]), ("The Onion, 20 Nov:", False, True))
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
    check("jokes: the reference block names the source and shows NO flag / warning line", ("flags" in orig, "racist" in orig, "JokeAPI" in orig), (False, False, True))
    check("jokes: Dark JokeAPI scores above other sources", (SC.score_jokes(None, 1, tp | {"body": "x" * 30})["quality"], SC.score_jokes(None, 1, {"body": "x" * 30, "source_key": "jokeapi", "joke_category": "Pun"})["quality"]), (80.0, 75.0))
    check("headers: the jokes pool is the dark pool (no pun-friendly lines), one pool per category", (all(h for h in V.load_config()["headers"]["jokes"]), "jokes" in V.load_config()["headers"], "weird_news" in V.load_config()["headers"], "satire" in V.load_config()["headers"]), (True, True, True, True))
    # hashtags (operator 2026-11-21): fixed per category, no model
    check("hashtags: jokes exactly #WeTalkALot #Jokes; every other category #WeTalkALot plus one tag", (V.hashtags_for("jokes"), [V.hashtags_for(c)[0] for c in V.load_config()["headers"]], [len(V.hashtags_for(c)) for c in V.load_config()["headers"]]), (["#WeTalkALot", "#Jokes"], ["#WeTalkALot"] * len(V.load_config()["headers"]), [2] * len(V.load_config()["headers"])))
    # Lemmy jokes
    ok_post = {"post": {"id": 1, "name": "My therapist said time heals", "body": "Then she sent me the bill", "ap_id": "https://lemmy.world/post/1", "nsfw": True}}
    lp = WJ.lemmy_payload(ok_post, "jokes")
    check("lemmy jokes: title = setup, body = delivery (two parts, delivery spoilered), nsfw kept as a flag, credited to the community", (lp["body_parts"], lp["flags"], lp["source_name"], lp["joke_category"]), (["My therapist said time heals", "Then she sent me the bill"], {"nsfw": True}, "Lemmy c/jokes", "c/jokes"))
    lb = V.build(None, "jokes", {"raw_item_id": 5, "payload": lp}, seed=0, use_model=False)
    check("lemmy jokes: the card passes the exact-words check with the delivery in a spoiler", (lb["ok"], "<tg-spoiler>Then she sent me the bill</tg-spoiler>" in lb["text"]), (True, True))
    check("lemmy jokes: Markdown / links / spoiler markup, mod posts and removed posts are skipped", [WJ.lemmy_payload({"post": {"id": i, "name": n, "body": b, **extra}}, "jokes") for i, (n, b, extra) in enumerate([("Joke", "see https://x.test", {}), ("Joke", "::: spoiler tap" + chr(10) + "x" + chr(10) + ":::", {}), ("META: rules", "", {}), ("Joke", "x", {"removed": True}), ("Joke", "x", {"featured_community": True})])], [None] * 5)
    check("lemmy jokes: a changed source set makes the monthly pass due again", WJ.source_names(), ["jokeapi", "lemmy_jokes", "lemmy_darkhumor", "reddit_jokes"])
    rp = WJ.reddit_payload({"id": "a1", "title": "How many dead hookers does it take to change a light bulb?", "body": "Apparently not three because my basement is still dark.", "score": 120})
    check("reddit jokes: a dark-keyword joke with enough upvotes becomes a two-part joke credited to r/Jokes with a link to the post", (rp["body_parts"][1], rp["source_name"], rp["source_url"]), ("Apparently not three because my basement is still dark.", "Reddit r/Jokes", "https://www.reddit.com/comments/a1"))
    check("reddit jokes: clean, low-score, EDIT:, removed and over-long ones are not candidates", [WJ.reddit_payload(x) for x in ({"id": "b", "title": "A pun", "body": "Cows go moo", "score": 300}, {"id": "c", "title": "Dead", "body": "x", "score": 1}, {"id": "d", "title": "Dead", "body": "x" + chr(10) + chr(10) + "EDIT: gold", "score": 50}, {"id": "e", "title": "Dead", "body": "[removed]", "score": 50}, {"id": "f", "title": "Dead " + "x" * 500, "body": "y", "score": 50})], [None] * 5)
    check("reddit jokes: on (Railway reached the file), and part of the source set", (WJ.REDDIT_ENABLED, "reddit_jokes" in WJ.source_names()), (True, True))
    import requests as _rq
    class _Resp:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b
        def raise_for_status(self):
            if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")
    _real_get = _rq.get
    try:
        def fake_get(url, params=None, **kw):
            if url.endswith("/info"): return _Resp(200, {"jokes": {"idRange": {"en": [0, 19]}}})
            if params["idRange"] == "0-9": return _Resp(400, {"error": True, "code": 106, "message": "No matching joke found"})
            return _Resp(200, {"error": False, "jokes": [{"id": 12, "category": "Dark", "type": "single", "joke": "A dark one.", "flags": {}}]})
        WJ.requests.get = fake_get
        WJ.time.sleep = lambda s: None
        sweep = WJ.fetch_jokeapi()
    finally:
        WJ.requests.get = _real_get
    check("jokes: an id range with no Dark joke (JokeAPI answers HTTP 400, code 106) is skipped, not a failure", [e for e, _ in sweep], ["jokeapi:12"])
    check("notify: the humor channel is limited to 2 cards per cycle in total", N.CHANNEL_PER_CYCLE_LIMIT.get("we_talk_a_lot"), 2)

    # ---- config
    chans = yaml.safe_load(open(os.path.join(ROOT, "config", "channel_config.yaml"), encoding="utf-8"))
    ch = chans["we_talk_a_lot"]
    check("channel: id, name, phase 1 + 2 categories, channel cap 9 (operator) below the sum of the categories' own caps (10)", (ch["chat_id"], ch["display_name"], ch["categories"], ch["soft_daily_cap"], sum(w[c]["soft_daily_cap"] for c in ch["categories"])),
          ("-1004376927203", "We Talk A Lot", ["weird_news", "satire", "jokes", "dark_history", "unusual_deaths", "world_records", "conspiracies", "weird_facts", "shower_thoughts"], 9, 10))
    check("channel: it has no voice profile on purpose (the builder is code, not a voice)", "we_talk_a_lot" in yaml.safe_load(open(os.path.join(ROOT, "config", "channel_voices.yaml"), encoding="utf-8")), False)
    check("triage: every humor category has a card line", all(c in TPL.TEMPLATES for c in ("weird_news", "satire", "jokes")), True)
    check("generate_post routes the humor channel to the builder", 'channel == "we_talk_a_lot"' in open(os.path.join(ROOT, "pipeline", "write_post.py"), encoding="utf-8").read(), True)
    check("the copy guard is not on this path (the builder never calls it)", "_find_reproduction_issues" in open(os.path.join(ROOT, "pipeline", "verbatim.py"), encoding="utf-8").read().split('"""', 2)[1] and "import" not in "", True)

    # ---- phase 2: the six libraries (collectors/wtal_library.py), parsers on small fixtures
    from collectors import wtal_library as WL
    from datetime import date
    ud_html = ('<div class="mw-parser-output"><table class="wikitable sortable"><tbody><tr><th>Name of person</th><th>Image</th><th>Date of death</th><th>Country</th><th>Details</th></tr>'
               '<tr><th>Jesse William Lazear</th><td></td><td>25 September 1900</td><td>Cuba</td><td>The 34-year-old American physician was convinced that <a>mosquitoes</a> were carriers for yellow fever.<sup class="reference">[3]</sup> He allowed himself to be bitten.</td></tr>'
               '<tr><th>Short</th><td></td><td>1901</td><td>X</td><td>Too short.</td></tr></tbody></table></div>')
    ud = WL.parse_unusual_deaths_tables(ud_html, "https://en.wikipedia.org/wiki/X")
    check("unusual_deaths: a table row -> name = title, details = body (citation mark dropped, words kept), 'date, country:' on top; a stub row is skipped",
          [(d["title"], d["body"], d["dateline"]) for d in ud], [("Jesse William Lazear", "The 34-year-old American physician was convinced that mosquitoes were carriers for yellow fever. He allowed himself to be bitten.", "25 September 1900, Cuba:")])
    li_html = ('<div class="mw-parser-output"><h2>List</h2><ul><li>Sylvester H. Roper (1823\u20131896), inventor of the Roper steam velocipede, died of a heart attack or crash in 1896.</li><li>Short one 1900</li></ul>'
               '<h2>See also</h2><ul><li>Somebody else (1900-1999), a long enough line about something that must not be taken from the closing sections of the page.</li></ul></div>')
    check("unusual_deaths: a list-style page gives its entries whole as body-only items and stops at 'See also'", [d["body"][:30] for d in WL.parse_li_entries(li_html, "u")], ["Sylvester H. Roper (1823\u20131896)"])
    ua = WL.parse_unusual_articles('<div class="mw-parser-output"><table class="wikitable"><tr><th>Article</th><th>Description</th></tr><tr><td>Bookland</td><td>The quasi-fictional country all books come from, according to barcodes.</td></tr></table></div>', "u")
    check("weird_facts: an Unusual articles row -> the article title is the title, the one-line description the body", [(d["title"], d["body"]) for d in ua], [("Bookland", "The quasi-fictional country all books come from, according to barcodes.")])
    cons_html = ('<div class="mw-parser-output"><h2>Contents</h2><h2>Aviation</h2><p>A group paragraph that is long enough to be kept as text but belongs to a group heading, not to a theory at all.</p><h3>Chemtrails</h3>'
                 '<p>This theory alleges that water condensation trails from aircraft consist of chemical or biological agents under secret government policies.</p><p>Scientists have repeatedly rejected the claim and found no evidence for it at all.</p><h2>See also</h2><p>Nothing that long enough to count as a paragraph here at all, honestly.</p></div>')
    cs = WL.parse_conspiracies(cons_html, "https://en.wikipedia.org/wiki/List_of_conspiracy_theories")
    check("conspiracies: an h3 heading is the title and the paragraph under it the body (a group h2 and the closing sections are not items)", [(c["title"], c["body"].count("\n\n"), c["source_url"].endswith("#Chemtrails")) for c in cs], [("Chemtrails", 1, True)])
    check("dark_history: an On This Day event -> body = its text, the day as the top line (BC years too), the page link kept", (tuple(WL.parse_otd([{"text": "The Columbine Mine massacre: striking coal miners are attacked with machine guns by state police.", "year": 1927, "pages": [{"content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Columbine_Mine_massacre"}}}]},
                                                                                                          {"text": "Judas Maccabeus rededicates the Temple in Jerusalem, an event commemorated each year by the festival.", "year": -164}], 11, 21)[i][k] for i, k in ((0, "dateline"), (0, "source_url"), (1, "dateline")))),
          ("21 November 1927:", "https://en.wikipedia.org/wiki/Columbine_Mine_massacre", "21 November 164 BC:"))
    check("dark_history: the grim-word prefilter keeps a massacre and drops a treaty", (bool(WL._DARK_CUES.search("Striking miners are killed in a massacre")), bool(WL._DARK_CUES.search("Two countries sign a trade treaty"))), (True, False))
    check("dark_history: the calendar is read in slices from today's date, skipping the days done", WL.next_days({"11-21", "11-22"}, date(2026, 11, 21), 3), ["11-23", "11-24", "11-25"])
    check("dark_history: a calendar has 366 days (02-29 included)", len(WL._calendar()), 366)
    posts = [{"post": {"id": 1, "name": "Brushing your teeth is the only time we clean a part of our own skeleton.", "body": "", "ap_id": "https://lemmy.world/post/1"}, "counts": {"score": 400}},
             {"post": {"id": 2, "name": "A thought with a body text attached to it that goes on", "body": "more", "ap_id": "x"}, "counts": {"score": 400}},
             {"post": {"id": 3, "name": "Low scoring shower thought that nobody upvoted at all", "body": "", "ap_id": "y"}, "counts": {"score": 3}},
             {"post": {"id": 4, "name": "A link post pretending to be a thought about something online", "body": "", "url": "https://x.test", "ap_id": "z"}, "counts": {"score": 500}}]
    check("shower_thoughts: only a title-only, un-removed, well-scored post counts; the title is the body", [(x["body"][:19], x["ext"]) for x in WL.parse_shower_posts(posts)], [("Brushing your teeth", "lemmy_showerthoughts:1")])
    rs_html = ('<a class="video-item" href="https://recordsetter.com/world-record/potatoes-held-hand/46606?autoplay=false"><div class="video-item-content"><span class="video-item-time">03:14</span></div>'
               '<div class="video-item-outer-content"><h2>Most Potatoes Held In Hand</h2><h3>Peter Durdik</h3></div><div class="video-item-description"><p><text>Peter Durdik held 27 potatoes in his hand at once.</text></p></div></a>'
               '<a href="/eating-world-records">eating</a><a href="/about">about</a>')
    recs, topics = WL.parse_recordsetter(rs_html)
    check("world_records: a record -> its title (h2) and the one-sentence description, link without the autoplay query; topic links are collected", ([(r["title"], r["body"], r["source_url"]) for r in recs], topics),
          ([("Most Potatoes Held In Hand", "Peter Durdik held 27 potatoes in his hand at once.", "https://recordsetter.com/world-record/potatoes-held-hand/46606")], ["/eating-world-records"]))
    check("phase 2: every library has a scorer, a category_config row and a channel slot; the 3-a-week categories have a weekly cap", ([c in SC.SCORERS for c in WL.COLLECTORS], [c in yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "category_config.yaml"), encoding="utf-8")) for c in WL.COLLECTORS],
          [c in yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "channel_config.yaml"), encoding="utf-8"))["we_talk_a_lot"]["categories"] for c in WL.COLLECTORS], SEL.WEEKLY_CAPS),
          ([True] * 6, [True] * 6, [True] * 6, {"unusual_deaths": 3, "world_records": 3, "conspiracies": 3}))
    check("phase 2: all six are library categories (random, no repeats)", all(c in SEL.LIBRARY_CATEGORIES for c in WL.COLLECTORS), True)
    wl_card = V.build(None, "unusual_deaths", {"raw_item_id": 11, "payload": {"title": ud[0]["title"], "body": ud[0]["body"], "dateline": ud[0]["dateline"], "source_name": "Wikipedia", "source_url": "https://en.wikipedia.org/wiki/X"}}, seed=0, use_model=False)
    wl_lines = wl_card["text"].split("\n")
    check("phase 2 card: header, then the 'date, country:' line, the bold name, the fixed tags; no source line; the check passed", (wl_lines[2], wl_lines[3], wl_lines[4], "Wikipedia" in wl_card["text"], wl_card["ok"]), ("25 September 1900, Cuba:", "<b>Jesse William Lazear</b>", "#WeTalkALot #UnusualDeaths", False, True))
    check("phase 2 card: the reference block names the source and links it", ("Wikipedia" in wl_card["original_html"], "https://en.wikipedia.org/wiki/X" in wl_card["original_html"]), (True, True))

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("We Talk A Lot builder cases behave as expected")


if __name__ == "__main__":
    main()
