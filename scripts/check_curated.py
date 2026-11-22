"""No-database, no-network regression table for the editor-curated sources of We Talk A Lot (collectors/wtal_curated.py, operator 2026-11-21).

    python scripts/check_curated.py        # exits 1 on any mismatch

Pins: which paragraphs make a card (whole, never cut, within the card size; a promo / truncated / mid-thought text is left out), which category a piece is routed to (the source's own tags and sections, a title keyword
overriding), the Darwin Awards entry parser (only "Winner ... Confirmed True"), the Telegram archive cleaning (the channel's own signature link is not a promotion), the feed reader's progress (older pages continue, a
throttled page is not marked seen, a page is atomic), and that a library category runs BOTH its legs and raises only after both have finished.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from collectors import wtal_curated as C  # noqa: E402
from collectors import wtal_library as L  # noqa: E402
from pipeline import article  # noqa: E402

FAILS: list[str] = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


P1 = "In the village of Bellewstown, about 15 miles north of Dublin, Ireland, they still talk about what Barney Curley did back in 1975. It all happened during a horse race."
P2 = "The race in question occurred on 26 June 1975. Barney Curley, our protagonist, had spent months preparing a gamble that nobody in the village suspected."
P3 = "As the spectators placed their wagers and settled in around the edge of the track, Curley tugged his hat down tight and waited for the horses to be called."


def rss(items):
    """A WordPress-style feed: items = [(title, link, [tags], html)]"""
    body = ""
    for t, link, tags, html in items:
        cats = "".join(f"<category>{x}</category>" for x in tags)
        body += f"<item><title>{t}</title><link>{link}</link>{cats}<description><![CDATA[{html}]]></description><content:encoded><![CDATA[{html}]]></content:encoded></item>"
    return f'<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>x</title>{body}</channel></rss>'


class Resp:
    def __init__(self, status=200, content=b"", text=None):
        self.status_code, self.content = status, content
        self.text = text if text is not None else content.decode("utf-8", "replace")
        self.encoding, self.apparent_encoding, self.headers = "utf-8", "utf-8", {}


def main():
    # ---- which paragraphs make the card
    check("pick: the first paragraph whole and the second while it fits; nothing is cut", C.pick_paragraphs([P1, P2, P3], "A title"), ([P1, P2], None))
    long_first = "x" * 880 + ". " + "y" * 30 + "."
    check("pick: a first paragraph too long for the card size is 'first_paragraph_too_long', never shortened", C.pick_paragraphs([long_first], "A title")[1], "first_paragraph_too_long")
    one_sentence = "A single sentence that is long enough to count as a paragraph on its own right here."
    check("pick: a one-sentence first paragraph takes the second AND a third while the text is still under two sentences and they fit", C.pick_paragraphs([one_sentence, "Short follow up that is also quite long enough to be kept as one paragraph here.", P3], "T")[0][0], one_sentence)
    check("pick: a print notice and a note about the site are not the story", C.pick_paragraphs(["© 2025 All Rights Reserved. Do not distribute or repurpose this work without written permission.", "Printed from https://www.damninteresting.com/x/ and a long tail to pass the length floor.", P1, P2], "T")[0], [P1, P2])
    check("pick: a sponsor note in the opening is left out (unknown is never coded as clear)", C.pick_paragraphs(["Just before we get into today's topic, a shout out to the sponsor of this episode, thank you very much indeed.", P2], "T"), ([], "promo"))
    promo_cases = {"story": ["The show began as a podcast in 2009, and by the time its host died, the podcast had been downloaded more than four million times worldwide.", P2], "sponsor in a long story paragraph": [("The race sponsor pulled out in 1998. " + "x" * 420 + "."), P2],
                   "subscribe": ["Subscribe to our newsletter for the best strange facts every single week, delivered straight to your inbox.", P2], "patreon": ["Support us on Patreon to help keep this blog going for another year.", P2],
                   "merch": ["Check out our merch store for shirts, mugs and more strange things right now!", P2], "use code": ["Use code STRANGE at checkout for ten percent off your first order today.", P2]}
    check("promo: only an actual promo line is left out; a story that merely mentions a podcast or a sponsor stays", {k: C.pick_paragraphs(v, "T")[1] for k, v in promo_cases.items()},
          {"story": None, "sponsor in a long story paragraph": None, "subscribe": None, "patreon": "promo", "merch": "promo", "use code": "promo"})
    check("pick: a feed excerpt cut with [...] is truncated, not a card", C.pick_paragraphs(["Yet the Parisians refused to surrender until the whole city, after many weeks of siege and hunger, finally […]"], "T"), ([], "truncated"))
    check("pick: a paragraph that hands over to a quote or a list ('The author reports:') is not the end of a thought", C.pick_paragraphs(["Some detectives specialize in one thing only. Detectives whose specialty is detecting kissing might benefit from the method described in this study:"], "T"), ([], "ends_mid_thought"))
    check("pick: nothing long enough is 'no_paragraphs'", C.pick_paragraphs(["Short.", "Also short."], "T"), ([], "no_paragraphs"))

    # ---- photo captions are the picture's words, not the story's
    frag = ('<div class="wp-caption alignnone"><img src="x.jpg"/><p class="wp-caption-text"><span class="credit">Harvey Mills/National Trust Images</span><span class="caption-body">Little is known about the person.</span></p></div>'
            f'<p class="dropcaps">{P1}</p><p>{P2}</p>')
    check("fragment: the caption and its credit are not paragraphs of the story", article.fragment_paragraphs(frag), [P1, P2])

    # ---- routing
    r = C.route_item
    check("route Futility Closet: Death -> unusual deaths, Crime -> dark history, Oddities -> weird facts, a puzzle or a quotation is not routed", (r("futility_closet", "t", {"death"}, ""), r("futility_closet", "t", {"crime"}, ""), r("futility_closet", "t", {"oddities", "science & math"}, ""),
          r("futility_closet", "t", {"puzzles"}, ""), r("futility_closet", "t", {"history", "quotations"}, "")), ("unusual_deaths", "dark_history", "weird_facts", None, None))
    check("route Damn Interesting: Tragedy / War -> dark history, other stories -> weird facts, an announcement or a podcast is not routed", (r("damn_interesting", "t", {"articles", "tragedy"}, ""), r("damn_interesting", "t", {"articles", "whimsy"}, ""),
          r("damn_interesting", "t", {"announcements", "future"}, ""), r("damn_interesting", "t", {"podcast", "history"}, "")), ("dark_history", "weird_facts", None, None))
    check("route All That's Interesting: macabre -> dark history, weird -> weird facts, plain history or news is not routed, a gallery or a video is not routed", (r("all_thats_interesting", "t", {"history", "macabre"}, ""), r("all_thats_interesting", "t", {"weird", "history"}, ""),
          r("all_thats_interesting", "t", {"history", "news"}, ""), r("all_thats_interesting", "t", {"macabre", "galleries"}, ""), r("all_thats_interesting", "t", {"sad", "history"}, "")), ("dark_history", "weird_facts", None, None, "dark_history"))
    check("route: Today I Found Out and Improbable Research -> weird facts; Atlas Obscura places -> weird facts", (r("today_i_found_out", "t", {"history"}, ""), r("improbable_research", "t", set(), ""), r("atlas_obscura_places", "t", {"museums"}, "")), ("weird_facts",) * 3)
    check("route Atlas Obscura articles: death / haunted -> dark history, oddities / legends -> weird facts, a podcast is not routed", (r("atlas_obscura_articles", "t", {"ghosts", "history"}, ""), r("atlas_obscura_articles", "t", {"oddities"}, ""), r("atlas_obscura_articles", "t", {"podcast", "ghosts"}, ""), r("atlas_obscura_articles", "t", {"history"}, "")),
          ("dark_history", "weird_facts", None, None))
    check("route Mental Floss by the section of its URL: /crime/ -> dark history, /history/ -> weird facts, entertainment / maps / rankings are not routed", (r("mental_floss", "t", set(), "https://www.mentalfloss.com/crime/x"), r("mental_floss", "t", set(), "https://www.mentalfloss.com/history/law/x"),
          r("mental_floss", "t", set(), "https://www.mentalfloss.com/entertainment/movies/x"), r("mental_floss", "t", set(), "https://www.mentalfloss.com/geography/maps/x")), ("dark_history", "weird_facts", None, None))
    check("route: a title or tag that says conspiracy / world record / a strange death overrides the source's own routing", (r("today_i_found_out", "The Strangest Conspiracy Theories", set(), ""), r("all_thats_interesting", "x", {"conspiracies", "history"}, ""),
          r("today_i_found_out", "10 Weird World Records", {"x"}, ""), r("all_thats_interesting", "The Strange Death Of Albert Dekker", {"history"}, ""), r("today_i_found_out", "10 Bizarre Deaths of Roman Emperors", {"history"}, "")),
          ("conspiracies", "conspiracies", "world_records", "unusual_deaths", "unusual_deaths"))
    check("route: 'Record-Breaking Pilot' is not a world record (only 'world record' / Guinness are)", r("all_thats_interesting", "The Record-Breaking Pilot Who First Exceeded The Speed Of Sound", {"weird"}, ""), "weird_facts")

    # ---- Darwin Awards
    page = ('<html><body><article><h2><!-- story_title -->Modern Armor<div class="smalltext">2006 Darwin Award Winner<BR>Confirmed True by Darwin</div></h2><!-- story_text_begin -->'
            f'<A href="/x"><IMG src="i.jpg"></a><p>(26 August 2006, Leicester, England) {P1}\n\n<p>{P2}\n<P>{P3}<P><FONT size="-1"><A href="/slush/x.html">MEDIA REFERENCES</A></FONT><P>Reader Comments: "Shanks for nothing."</article></body></html>')
    d = C.parse_darwin(page, "https://darwinawards.com/darwin/darwin2006-10.html")
    check("darwin: the title, the status line and the story paragraphs (the media references and the reader comments are not part of the story)", (d["title"], d["status"], len(d["paragraphs"]), "Shanks" in " ".join(d["paragraphs"]), d["paragraphs"][0].startswith("(26 August 2006, Leicester, England) In the village")), ("Modern Armor", "2006 Darwin Award Winner Confirmed True by Darwin", 3, False, True))
    check("darwin: every entry is used (the status line is read but no longer a filter)", (hasattr(C, "darwin_is_fact"), C.DARWIN_SCOPE), (False, "all"))
    check("darwin: the index lists the year pages, a year page its entries (de-duplicated, sorted, absolute)", (C.darwin_years('<a href="darwin2020.html">x</a><a href="/darwin/darwin2018.html">y</a><a href="darwin2020.html">'),
          C.darwin_entry_urls('<a href="https://DarwinAwards.com/darwin/darwin2012-02.html">a</a><a href="darwin2012-01.html">b</a><a href="darwin2012-01.html">c</a><a href="darwin2011-03.html">d</a>', 2012)),
          ([2020, 2018], ["https://darwinawards.com/darwin/darwin2012-01.html", "https://darwinawards.com/darwin/darwin2012-02.html"]))

    # ---- Telegram archive
    check("telegram: the channel's own signature line and a hashtag line are cut from the end", C.clean_telegram("Black widows kill members of their families.\nStatistically they start at 25.\n\n Horror History\n"), "Black widows kill members of their families.\nStatistically they start at 25.")
    own = {"post": "mistic_history/1260", "text": f"{P1}\n\n{P2}\n\n Horror History", "links": ["https://t.me/mistic_history"], "time": "2025-04-06T00:00:00+00:00", "views": 1, "reactions": 0, "html": ""}
    it, why = C.telegram_item("mistic_history", own, "dark_history")
    check("telegram: a post whose only link is the channel's own signature is a card; title empty, source = the post's own link", (why, it["title"], it["source_url"], it["ext"], it["body"].endswith(P2)), (None, "", "https://t.me/mistic_history/1260", "tg:mistic_history/1260", True))
    check("telegram: a post that links elsewhere (an invite, another channel) or sells something is dropped", (C.telegram_item("mistic_history", {**own, "links": ["https://t.me/+W0OPxSoqJDVjYjVk"]}, "dark_history")[1], C.telegram_item("mistic_history", {**own, "text": "‼️ This channel contains the best money making loopholes you didn't know about! " + P1}, "dark_history")[1]),
          ("promo_or_link", "promo_or_link"))
    check("telegram: a one-line photo caption is not a card (no paragraph long enough)", C.telegram_item("mistic_history", {**own, "text": "Beautiful and deadly at the same time\n\nHorror History"}, "dark_history")[1], "no_paragraphs")

    # ---- the feed reader: older pages continue, a page is atomic, a throttled page is retried, known items are not read again
    pages = {
        "https://x.test/feed/": rss([("First story", "https://x.test/a/", ["Tragedy"], f"<p>{P1}</p><p>{P2}</p>"), ("An announcement", "https://x.test/b/", ["Announcements"], f"<p>{P1}</p>")]),
        "https://x.test/feed/?paged=2": rss([("Older story", "https://x.test/c/", ["Whimsy"], f"<p>{P2}</p><p>{P3}</p>")]),
        "https://x.test/feed/?paged=3": rss([]),
    }
    calls = []

    def fake_get(url, tries=3, timeout=30):
        calls.append(url)
        if url in pages:
            return Resp(200, pages[url].encode())
        return Resp(404, b"")
    real_get, real_sleep = C.http_get, C.time.sleep
    C.http_get, C.time.sleep = fake_get, lambda s: None
    try:
        src = {"name": "X", "url": "https://x.test/feed/", "paged": True, "pass_pages": 5, "max_page": 50, "body": "feed", "route": C._r_damn_interesting}
        C.FEEDS["x_test"] = src
        items, st, prog = C.read_feed_source("x_test", src, {}, set(), C.time.monotonic() + 100)
        check("feed: page 1 is read, then the older pages from page 2 until the empty one; the announcement is not routed", ([i["title"] for i in items], [i["category"] for i in items], st["unrouted"], prog["done"], prog["next_page"] >= 3),
              (["First story", "Older story"], ["dark_history", "weird_facts"], 1, True, True))
        check("feed: an item's source_url and ext id are stable and unique per link", (items[0]["source_url"], items[0]["ext"].startswith("x_test:"), items[0]["ext"] != items[1]["ext"]), ("https://x.test/a/", True, True))
        calls.clear()
        items2, st2, prog2 = C.read_feed_source("x_test", src, prog, {i["ext"] for i in items}, C.time.monotonic() + 100)
        check("feed: a finished archive is read from page 1 only, and what is already stored is not taken again", (items2, calls, st2["already_known"]), ([], ["https://x.test/feed/"], 1))

        # fetch mode: an excerpt-only feed -> the article page; a 429 is retried next cycle (not 'seen'), a good page is seen
        pages_f = {"https://y.test/feed/": rss([("Story one", "https://y.test/1/", ["Tragedy"], "<p>Excerpt only [&#8230;]</p>"), ("Story two", "https://y.test/2/", ["Tragedy"], "<p>Excerpt only [&#8230;]</p>")])}
        art = {"https://y.test/1/": Resp(200, text=f"<html><body><article><p>{P1}</p><p>{P2}</p></article></body></html>"), "https://y.test/2/": Resp(429, text="slow down")}

        def fake_get2(url, tries=3, timeout=30):
            if url in pages_f:
                return Resp(200, pages_f[url].encode())
            return art.get(url, Resp(404, text=""))
        C.http_get = fake_get2
        srcf = {"name": "Y", "url": "https://y.test/feed/", "paged": False, "body": "fetch", "fetch_cap": 10, "route": C._r_damn_interesting}
        C.FEEDS["y_test"] = srcf
        itf, stf, progf = C.read_feed_source("y_test", srcf, {}, set(), C.time.monotonic() + 100)
        check("feed (fetch): the article page's own paragraphs are the card; a 429 is counted, leaves the item out and is NOT remembered as seen (it is tried again next cycle)", ([i["title"] for i in itf], stf["page_status"], [e for e in progf["seen"]] == [itf[0]["ext"]]),
              (["Story one"], {"ok": 1, "http_429": 1}, True))
        check("feed (fetch): the card text is the page's paragraphs in order", itf[0]["body"], P1 + "\n\n" + P2)
    finally:
        C.http_get, C.time.sleep = real_get, real_sleep
        C.FEEDS.pop("x_test", None)
        C.FEEDS.pop("y_test", None)


    # ---- Strange Company, Skeptoid, the two list articles and the safety filter (operator 2026-11-22; Listverse dropped)
    check("Listverse is no longer a source", ("listverse" in C.FEEDS, hasattr(C, "_r_listverse")), (False, False))
    check("route Strange Company: deaths -> unusual deaths, murder -> dark history, ghosts / UFOs / poltergeists / monsters / fairies / folklore / 'just plain weird' -> weird facts; only clippings and link dumps are not routed",
          (r("strange_company", "A Death in Yosemite", {"evelyn rosemann", "mysterious deaths"}, ""), r("strange_company", "x", {"poisonings", "murder"}, ""), r("strange_company", "x", {"just plain weird"}, ""), r("strange_company", "x", {"ghosts", "mysterious deaths"}, ""),
           r("strange_company", "x", {"ghosts"}, ""), r("strange_company", "x", {"ufos"}, ""), r("strange_company", "x", {"poltergeists"}, ""), r("strange_company", "x", {"monsters"}, ""), r("strange_company", "x", {"fairies"}, ""), r("strange_company", "x", {"folklore"}, ""),
           r("strange_company", "x", {"murder"}, ""), r("strange_company", "x", {"newspaper clippings", "mysterious deaths"}, ""), r("strange_company", "Weekend Link Dump", {"weekend link dump"}, ""), r("strange_company", "x", {"wales"}, "")),
          ("unusual_deaths", "unusual_deaths", "weird_facts", "unusual_deaths", "weird_facts", "weird_facts", "weird_facts", "weird_facts", "weird_facts", "weird_facts", "dark_history", None, None, None))
    check("feed paging: Blogger pages with start-index (50 a page), WordPress with ?paged", (C.feed_page_url(C.FEEDS["strange_company"], 1), C.feed_page_url(C.FEEDS["strange_company"], 3), C.feed_page_url(C.FEEDS["futility_closet"], 2)),
          ("https://strangeco.blogspot.com/feeds/posts/default?max-results=50&start-index=1", "https://strangeco.blogspot.com/feeds/posts/default?max-results=50&start-index=101", "https://www.futilitycloset.com/feed/?paged=2"))
    check("pick: a content-note paragraph ('A word of warning: ...') is not the story's opening", C.pick_paragraphs(["A word of warning: this post discusses a mysterious fatality that is pretty dark, even for this blog and its readers.", P1, P2], "T")[0], [P1, P2])
    check("no topic filter: there is no safety / topic filter left in the curated collector (operator 2026-11-22)", [hasattr(C, n) for n in ("UNSAFE", "unsafe_topic")], [False, False])
    list_html = ('<html><body><article><h2>24. The Killing Joke</h2><p>' + P1 + '</p><p>' + P2 + '</p><h2>23. Falling</h2><p>' + P3 + '</p><h2>1. Again</h2><p>' + P1 + '</p><h3>More from the site</h3><p>' + P2 + '</p></article></body></html>')
    ents = C.parse_list_article(list_html, "https://x.test/list", 2)
    check("list article: one item per NUMBERED h2 (the number is not part of the title), its paragraphs up to the next heading, and no more than max_entries", ([(e["title"], len(e["paragraphs"])) for e in ents], len(C.parse_list_article(list_html, "u", 10))), ([("The Killing Joke", 2), ("Falling", 1)], 3))
    check("list article: HistorySnob reads BOTH of its lists (20 entries), Factinate its 24", (C.LIST_ARTICLES["historysnob_true_conspiracies"]["max_entries"], C.LIST_ARTICLES["historysnob_true_conspiracies"]["category"], C.LIST_ARTICLES["factinate_ironic_deaths"]["max_entries"], C.LIST_ARTICLES["factinate_ironic_deaths"]["category"]), (20, "conspiracies", 24, "unusual_deaths"))
    real_get2, real_paras = C.http_get, C.page_paragraphs_of
    C.http_get = lambda url, tries=3, timeout=30: Resp(200, text='<a href="/episodes/1013">Medbeds</a><a href="/episodes/1013"></a><a href="/episodes/46">Support Your Local Reptoid</a><a href="/episodes/46"></a><a href="/other/9">x</a>')
    C.page_paragraphs_of = lambda url, selector=None: ([P1, P2], "ok") if url.endswith("/46") else ([], "http_429")
    C.time.sleep = lambda s: None
    try:
        isrc = dict(C.INDEX_SOURCES["skeptoid_conspiracies"], fetch_cap=5)
        iit, ist, ipr = C.read_index_source("skeptoid_conspiracies", isrc, {}, set(), C.time.monotonic() + 100)
    finally:
        C.http_get, C.page_paragraphs_of, C.time.sleep = real_get2, real_paras, real_sleep
    check("index source: every listed page is read newest first; a throttled one is NOT remembered (retried next cycle), a read one is", ([i["title"] for i in iit], ist["listed"], ist["page_status"], len(ipr["seen"])), (["Support Your Local Reptoid"], 2, {"http_429": 1, "ok": 1}, 1))

    # ---- the structural filters: the same rules the library uses, with the curated floor of two sentences
    check("rules: a curated item under two full sentences is dropped for EVERY category, a list page too", (L.rule_reason("dark_history", "T", "One long enough sentence only, with nothing after it at all.", C.MIN_SENTENCES), L.rule_reason("weird_facts", "List of odd things", P1 + " " + P2, 2),
          L.rule_reason("dark_history", "T", P1, C.MIN_SENTENCES)), ("under 2 full sentences", "list, index or category page", None))
    check("rules: without an override the category's own floor applies (a one-sentence dark-history EVENT from the old source is still fine)", L.rule_reason("dark_history", "", "1692 - A one sentence event that is long enough to be a card on its own."), None)

    # ---- storing: structural filters only, grouped per category and source, marked curated
    stored_calls = []
    real_store = L._store
    L._store = lambda conn, category, label, key, items, **kw: stored_calls.append((category, key, label, len(items), kw)) or {"stored_live": len(items), "rule_dropped": {"under 2 full sentences": 1}, "duplicates_skipped": 2}

    class Conn:
        def commit(self):
            pass
    try:
        stats = {}
        out = C.store_all(Conn(), [("a", "A", {"category": "dark_history"}), ("a", "A", {"category": "weird_facts"}), ("b", "B", {"category": "dark_history"}), ("a", "A", {"category": "dark_history"})], stats)
    finally:
        L._store = real_store
    check("store: one call per (category, source), never a model check, floor of two sentences, rows marked curated", ([(c[0], c[1], c[3]) for c in stored_calls], {(c[4]["audience"], c[4]["min_sentences"], c[4]["curated"]) for c in stored_calls}),
          ([("dark_history", "a", 2), ("dark_history", "b", 1), ("weird_facts", "a", 1)], {(False, 2, True)}))
    check("store: the per-category totals and the per-source numbers are reported", (out, stats["a"]["stored"], stats["a"]["rule_dropped"]), ({"dark_history": 3, "weird_facts": 1}, {"dark_history": 2, "weird_facts": 1}, {"under 2 full sentences": 2}))

    # ---- a failed pass is not run again for the next category; both legs of a category run
    runs = []
    C._PASS = None
    real_run = C.run_pass
    C.run_pass = lambda budget=0: runs.append(1) or (_ for _ in ()).throw(RuntimeError("every source failed"))
    errs = []
    try:
        for cat in ("dark_history", "weird_facts", "unusual_deaths"):
            try:
                C.collect_for(cat)
            except RuntimeError as exc:
                errs.append(str(exc))
    finally:
        C.run_pass, C._PASS = real_run, None
    check("pass: a failing pass runs ONCE and every category asked afterwards gets the same error (a 14-minute failure is not repeated five times)", (len(runs), errs), (1, ["every source failed"] * 3))
    import collect_cycle as CC
    order = []
    real_cf = CC.wtal_curated.collect_for
    CC.wtal_curated.collect_for = lambda cat: order.append(("curated", cat)) or 5
    try:
        leg_ok = CC._with_curated("dark_history", lambda: order.append(("old", "dark_history")) or 7)()
        try:
            CC._with_curated("weird_facts", lambda: (_ for _ in ()).throw(ValueError("wikipedia 429")))()
            raised = None
        except ValueError as exc:
            raised = str(exc)
    finally:
        CC.wtal_curated.collect_for = real_cf
    check("cycle: a category runs its older leg AND the curated leg (total of both); one leg failing is raised only after the other has run", (leg_ok, order, raised), (12, [("old", "dark_history"), ("curated", "dark_history"), ("curated", "weird_facts")], "wikipedia 429"))

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("curated sources behave as expected")


if __name__ == "__main__":
    main()
