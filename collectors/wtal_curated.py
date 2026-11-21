"""We Talk A Lot: EDITOR-CURATED sources for the five library categories that used to read Wikipedia (operator 2026-11-21: "these are already picked for being weird, dark or funny, so no DeepSeek funny/weird
check on them"). Structural filters only: dedupe, list / index pages, too short (under two full sentences), and a card-sized text (title + body at most CARD_CHARS, the length the library scorer accepts).

Every card is the source's EXACT words (pipeline/verbatim.py); this module only decides WHICH of the source's own paragraphs make the card (the first one or two, whole, never cut) and WHICH category a piece belongs to.

  source                          what is read                                             where it goes (tags / sections the source itself prints; a title keyword can override, see GLOBAL_RULES)
  Futility Closet                 feed (full text), paged back                             Death -> unusual deaths; Crime -> dark history; Oddities / Hoaxes / History -> weird facts; puzzles, quotations, poems ... are not routed
  Damn Interesting                feed excerpt -> the article page's opening paragraphs    Tragedy / Disaster / War / Crime -> dark history; every other story tag -> weird facts; announcements and podcasts are not routed
  All That's Interesting          feed (full text), paged back                             macabre / crime / serial killers / scary ... -> dark history; weird / wtf / baffling / curiosities ... -> weird facts; plain history and news are not routed
  Today I Found Out               feed (full text), paged back                             weird facts
  Listverse                       feed excerpt -> the article page's opening paragraphs    History / Crime / Creepy -> dark history; Weird Stuff / Mysteries / Humans / Our World -> weird facts
  Atlas Obscura                   Places feed (full text) and Articles feed (full text)    places -> weird facts; articles by tag (death / haunted / ghosts ... -> dark history; oddities / legends ... -> weird facts)
  Mental Floss                    feed one-liner -> the article page's opening paragraphs  /crime/ -> dark history; /history/ -> weird facts; its entertainment, maps and rankings are not routed
  Improbable Research             feed excerpt -> the article page's opening paragraphs    weird facts
  Darwin Awards                   the archive: year pages -> entry pages                   unusual deaths; only "Winner ... Confirmed True by Darwin" entries (an unconfirmed one is not a fact, so it is not used)
  Telegram @mistic_history        t.me/s paged back with ?before=                          dark history (a posts-with-links / invite / promo is dropped, the channel's own "Horror History" signature line is cut)
  NOT USED, on record             Snopes "freakish fatalities" tag: no article list in the page and an empty tag feed. Ranker: HTTP 401 from Railway. Telegram @SHOCKING_FACTSS: the fact is in the picture, the text is a caption.

Backfill: a feed is read from page 1 every cycle (new posts) and the older pages continue where the last cycle stopped (progress in the run_logs row `collect_curated`), PASS_PAGES pages a cycle, until the archive ends. An article page is
fetched only for an item that is routed AND not stored / seen before. One pass per process (several categories share it); a source that fails is recorded and the others carry on.
"""

import html as htmllib
import logging
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from collectors import wtal_library as L
from collectors.earning_opportunities import parse_telegram_preview
from pipeline import article
from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log

logger = logging.getLogger(__name__)

CARD_CHARS = 900                 # title + body: what pipeline/score.py's library scorer accepts (25..900)
MIN_SENTENCES = 2                # "too short": under two full sentences (every curated category)
SLEEP = 0.7                      # between requests
BUDGET_SECONDS = 14 * 60         # the whole pass; a source not reached is picked up by the next cycle
SEEN_KEEP = 600
HEADERS = {"User-Agent": article.HEADERS["User-Agent"], "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
DARWIN = "https://darwinawards.com"
SKIP = "skip"

# a promo / sponsor note is not the source's story: an item whose opening paragraphs carry one is left out (unknown is never coded as clear)
PROMO = re.compile(r"\b(sponsor(?:ed|s)?|patreon|merch|t-?shirt|shout[- ]out|subscribe|newsletter|podcast|promo code|use code|affiliate|click here|follow us)\b", re.I)
NOISE = re.compile(r"^(?:©|copyright\b|printed from\b|editor.s note\b)", re.I)          # a print-only notice or a note about the site, not the story (Damn Interesting prints both above every article)
TRUNCATED = re.compile(r"\[\s*(?:…|\.\.\.)\s*\]|read more|continue reading|appeared first on", re.I)
HARD_SKIP_TAGS = {"announcements", "podcast", "podcasts", "videos", "galleries", "quizzes"}

# a title / tag keyword wins over the source's own routing (a piece that says it is about a conspiracy goes to conspiracies wherever it was published)
GLOBAL_RULES = [
    ("conspiracies", re.compile(r"conspirac", re.I)),
    ("world_records", re.compile(r"world record|guinness", re.I)),
    ("unusual_deaths", re.compile(r"(?:bizarre|strange|weird|unusual|freak(?:ish)?|odd|ironic|absurd|gruesome|stupid|silly)\s+(?:deaths?|ways? to die|accidents?)|freak accident|darwin awards?\b", re.I)),
]


def _r_futility(title, tags, link):
    if tags & {"puzzles", "quotations", "poems", "language", "literature"}:
        return SKIP
    if "death" in tags:
        return "unusual_deaths"
    if "crime" in tags:
        return "dark_history"
    if tags & {"oddities", "hoaxes", "history"}:
        return "weird_facts"
    return SKIP


def _r_damn_interesting(title, tags, link):
    if tags & {"tragedy", "disaster", "war", "crime"}:
        return "dark_history"
    return "weird_facts" if tags - {"articles"} else SKIP


def _r_ati(title, tags, link):
    if tags & {"macabre", "crime", "serial killers", "scary", "mafia", "nazis", "holocaust", "natural disasters", "revenge"} or {"sad", "history"} <= tags:
        return "dark_history"
    if tags & {"weird", "wtf", "baffling", "curiosities", "gross", "counterintuitive", "weird news", "funny"}:
        return "weird_facts"
    return SKIP


def _r_listverse(title, tags, link):
    if tags & {"history", "crime", "creepy"}:
        return "dark_history"
    if tags & {"weird stuff", "mysteries", "humans", "our world"}:
        return "weird_facts"
    return SKIP


def _r_atlas_articles(title, tags, link):
    if tags & {"death", "haunted", "ghosts", "ghost stories", "scary stories", "eerie feeling"}:
        return "dark_history"
    if tags & {"oddities", "architectural oddities", "mystery", "folklore", "legends", "folktales"}:
        return "weird_facts"
    return SKIP


def _r_mental_floss(title, tags, link):
    section = urlsplit(link).path.strip("/").split("/")[0]
    return {"crime": "dark_history", "history": "weird_facts"}.get(section, SKIP)


FEEDS = {
    "futility_closet": {"name": "Futility Closet", "url": "https://www.futilitycloset.com/feed/", "paged": True, "pass_pages": 20, "max_page": 400, "body": "feed", "route": _r_futility},
    "damn_interesting": {"name": "Damn Interesting", "url": "https://www.damninteresting.com/feed", "paged": True, "pass_pages": 6, "max_page": 80, "body": "fetch", "fetch_cap": 60, "route": _r_damn_interesting},
    "all_thats_interesting": {"name": "All That's Interesting", "url": "https://allthatsinteresting.com/feed", "paged": True, "pass_pages": 10, "max_page": 60, "body": "feed", "route": _r_ati},
    "today_i_found_out": {"name": "Today I Found Out", "url": "https://www.todayifoundout.com/index.php/feed/", "paged": True, "pass_pages": 6, "max_page": 120, "body": "feed", "route": lambda *a: "weird_facts"},
    "listverse": {"name": "Listverse", "url": "https://listverse.com/feed/", "paged": True, "pass_pages": 6, "max_page": 120, "body": "fetch", "fetch_cap": 60, "selector": "#articlecontentonly", "route": _r_listverse},
    "atlas_obscura_places": {"name": "Atlas Obscura", "url": "https://www.atlasobscura.com/feeds/latest", "paged": False, "body": "feed", "route": lambda *a: "weird_facts"},
    "atlas_obscura_articles": {"name": "Atlas Obscura", "url": "https://www.atlasobscura.com/feeds/articles", "paged": False, "body": "feed", "route": _r_atlas_articles},
    "mental_floss": {"name": "Mental Floss", "url": "https://www.mentalfloss.com/feed", "paged": False, "body": "fetch", "fetch_cap": 40, "selector": "div.article-content", "route": _r_mental_floss},
    "improbable_research": {"name": "Improbable Research", "url": "https://www.improbable.com/feed/", "paged": False, "body": "fetch", "fetch_cap": 10, "route": lambda *a: "weird_facts"},
}
DARWIN_ENTRIES_PER_PASS = 80
DARWIN_YEARS_PER_PASS = 8
TELEGRAM = {"mistic_history": {"name": "Horror History (Telegram @mistic_history)", "category": "dark_history", "pass_pages": 20}}
TG_AD = re.compile(r"t\.me/\+|joinchat|money[- ]making|free training|loophole|earn(?:ing)? (?:money|\$)|passive income|crypto|airdrop", re.I)
TG_SIGNATURE = re.compile(r"^\s*(?:horror history|join and share.*|@\w+|(?:#\w+\s*)+)\s*$", re.I)


# ------------------------------------------------------------------------------------------------ small helpers
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(text or "").replace("\xa0", " ")).strip()


def strip_tracking(url: str) -> str:
    p = urlsplit(url or "")
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def http_get(url: str, tries: int = 3, timeout: int = 30):
    """One GET with a retry on a dropped connection or a 429 / 503 (Improbable Research and Wikimedia-style throttles answer 429 to bursts)."""
    last = None
    for n in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            last = str(exc)[:80]
            time.sleep(2 * (n + 1))
            continue
        if r.status_code in (429, 503) and n < tries - 1:
            last = f"http_{r.status_code}"
            time.sleep(6 * (n + 1))
            continue
        return r
    raise RuntimeError(f"{url[:90]}: {last}")


def pick_paragraphs(paragraphs: list[str], title: str) -> tuple[list[str], str | None]:
    """The card's paragraphs: the first one whole, a second while the card stays within CARD_CHARS, a third only while the text still has fewer than two sentences. (paragraphs, why-not): the reason is one of
    no_paragraphs / promo / truncated / first_paragraph_too_long / ends_mid_thought. Nothing is cut or reworded."""
    good = [p for p in article.good_paragraphs(paragraphs) if not NOISE.match(p)]
    if not good:
        return [], "no_paragraphs"
    if any(PROMO.search(p) for p in good[:2]):
        return [], "promo"
    if any(TRUNCATED.search(p) for p in good[:2]):
        return [], "truncated"
    budget = CARD_CHARS - len(title or "") - 2
    if len(good[0]) > budget:
        return [], "first_paragraph_too_long"
    out = [good[0]]
    for i, p in enumerate(good[1:3]):
        if i == 1 and len(L._sentences("\n\n".join(out))) >= MIN_SENTENCES:
            break
        if sum(len(x) + 2 for x in out) + len(p) <= budget and not TRUNCATED.search(p):
            out.append(p)
    while out and out[-1].rstrip().endswith(":"):          # a paragraph that hands over to something the card does not carry (a quote, a list): it is not the end of a thought
        out.pop()
    return (out, None) if out else ([], "ends_mid_thought")


def global_route(title: str, tags: set[str]) -> str | None:
    text = f"{title} {' '.join(sorted(tags))}"
    for category, rx in GLOBAL_RULES:
        if rx.search(text):
            return category
    return None


def route_item(src_key: str, title: str, tags: set[str], link: str) -> str | None:
    """The category an item goes to, or None (not routed: left out, never guessed)."""
    if tags & HARD_SKIP_TAGS:
        return None
    mine = FEEDS[src_key]["route"](title, tags, link)
    return global_route(title, tags) or (None if mine == SKIP else mine)


# ------------------------------------------------------------------------------------------------ feeds
def feed_page_url(src: dict, n: int) -> str:
    return src["url"] if n == 1 else f"{src['url']}{'&' if '?' in src['url'] else '?'}paged={n}"


def entry_fragment(e) -> str:
    cont = e.get("content") or []
    return (cont[0].get("value") if cont else "") or e.get("summary") or e.get("description") or ""


def entry_tags(e) -> set[str]:
    return {clean(t.get("term")).lower() for t in (e.get("tags") or []) if t.get("term")}


def page_paragraphs_of(url: str, selector: str | None = None) -> tuple[list[str], str]:
    """(paragraphs, status) of an article page fetched from here: 'ok', 'http_403', 'error' ... A page that gives no paragraph is a status, never an exception."""
    try:
        r = http_get(url, tries=2, timeout=25)
    except Exception as exc:
        logger.info("curated: %s could not be fetched: %s", url, exc)
        return [], "error"
    if r.status_code != 200:
        return [], f"http_{r.status_code}"
    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else (r.apparent_encoding or "utf-8")
    paras = None
    if selector:                                      # the source's own article container (Listverse's author box and Mental Floss's sidebars sit outside it)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()
        root = soup.select_one(selector)
        if root is not None:
            paras = [article._p_text(p) for p in root.find_all("p") if not article._skippable(p) and not article._is_caption(p)]
    if paras is None:
        paras = article.page_paragraphs(r.text)
    return paras, "ok" if article.good_paragraphs(paras) else "no_paragraphs"


def read_feed_source(key: str, src: dict, prog: dict, known: set[str], deadline: float) -> tuple[list[dict], dict, dict]:
    """One feed source for one pass -> (items with a category, stats, new progress). Page 1 is always read; older pages continue from `next_page`."""
    st = {"pages_read": 0, "entries": 0, "unrouted": 0, "already_known": 0, "opened": 0, "page_status": {}, "left_out": {}, "items": 0}
    prog = {"next_page": prog.get("next_page", 2), "done": bool(prog.get("done")), "seen": list(prog.get("seen") or [])}
    seen = set(prog["seen"])
    items: list[dict] = []
    pages = [1]
    if src["paged"] and prog["next_page"] > src["max_page"]:
        prog["done"] = True
    if src["paged"] and not prog["done"]:
        pages += list(range(prog["next_page"], min(prog["next_page"] + src["pass_pages"], src["max_page"] + 1)))
    fetches = 0
    for n in pages:
        if time.monotonic() > deadline:
            st["budget_hit"] = True
            break
        r = http_get(feed_page_url(src, n))
        time.sleep(SLEEP)
        if r.status_code != 200:
            if n > 1:
                prog["done"] = True                       # past the last page (WordPress answers 404 there)
                st["page_status"][str(r.status_code)] = st["page_status"].get(str(r.status_code), 0) + 1
                break
            raise RuntimeError(f"{key}: feed page 1 answered HTTP {r.status_code}")
        entries = feedparser.parse(r.content).entries
        st["pages_read"] += 1
        if not entries:
            if n > 1:
                prog["done"] = True
            break
        routed = []
        for e in entries:
            st["entries"] += 1
            title, link, tags = clean(e.get("title")), strip_tracking(e.get("link") or ""), entry_tags(e)
            category = route_item(key, title, tags, link)
            if not category or not link:
                st["unrouted"] += 1
                continue
            ext = L.ext_id(key, link)
            if ext in known or ext in seen:
                st["already_known"] += 1
                continue
            routed.append((e, title, link, tags, category, ext))
        need_fetch = sum(1 for _ in routed) if src["body"] == "fetch" else 0
        if need_fetch and fetches + need_fetch > src.get("fetch_cap", 60) and n > 1:
            prog["next_page"] = n                          # the page is atomic: it is read again next cycle, from where the cap stopped it
            st["fetch_cap_hit"] = True
            break
        for e, title, link, tags, category, ext in routed:
            if src["body"] == "fetch":
                paras, status = page_paragraphs_of(link, src.get("selector"))
                fetches += 1
                st["page_status"][status] = st["page_status"].get(status, 0) + 1
                st["opened"] += 1 if status == "ok" else 0
                time.sleep(SLEEP)
            else:
                paras = article.fragment_paragraphs(entry_fragment(e))
            if src["body"] != "fetch" or status in ("ok", "no_paragraphs", "http_404", "http_410"):
                seen.add(ext)                         # a throttle (429), a timeout or a 5xx is not an answer about the page: it is tried again next cycle
            picked, why = pick_paragraphs(paras, title)
            if why:
                st["left_out"][why] = st["left_out"].get(why, 0) + 1
                continue
            items.append({"category": category, "title": title, "body": "\n\n".join(picked), "source_url": link, "ext": ext, "tags": sorted(tags)[:8]})
        if n >= 2 and n + 1 > prog["next_page"]:
            prog["next_page"] = n + 1
    st["items"] = len(items)
    prog["seen"] = sorted(seen)[-SEEN_KEEP:] if len(seen) > SEEN_KEEP else sorted(seen)
    return items, st, prog


# ------------------------------------------------------------------------------------------------ Darwin Awards
def darwin_years(index_html: str) -> list[int]:
    return sorted({int(y) for y in re.findall(r"darwin(\d{4})\.html", index_html, re.I)}, reverse=True)


def darwin_entry_urls(year_html: str, year: int) -> list[str]:
    names = sorted({m.lower() for m in re.findall(rf"(darwin{year}-\d+\.html)", year_html, re.I)})
    return [f"{DARWIN}/darwin/{n}" for n in names]


def parse_darwin(page_html: str, url: str) -> dict | None:
    """(title, status, paragraphs) of one entry page, or None when it has no story. The status line says 'Winner' / 'Honorable Mention' and 'Confirmed True' / 'Unconfirmed' / 'Urban Legend'."""
    soup = BeautifulSoup(page_html, "html.parser")
    h2 = soup.find("h2")
    if not h2:
        return None
    div = h2.find("div")
    status = clean(div.get_text(" ")) if div else ""
    if div:
        div.extract()
    title = clean(h2.get_text(" "))
    i = page_html.find("story_text_begin")
    if i < 0:
        return None
    seg = page_html[i:]
    cuts = [j for j in (seg.find(x) for x in ("MEDIA REFERENCES", "Reader Comments", "begin ranking block", "REFERENCES")) if j > 0]
    seg = seg[:min(cuts)] if cuts else seg
    paras = [clean(BeautifulSoup(part, "html.parser").get_text(" ")) for part in re.split(r"<p\b[^>]*>", seg, flags=re.I)[1:]]
    return {"title": title, "status": status, "paragraphs": [p for p in paras if p], "url": url}


def darwin_is_fact(status: str) -> bool:
    s = status.lower()
    return "winner" in s and "confirmed true" in s


def read_darwin(prog: dict, known: set[str], deadline: float) -> tuple[list[dict], dict, dict]:
    st = {"years_listed": 0, "entries_read": 0, "not_a_confirmed_winner": 0, "left_out": {}, "page_status": {}, "items": 0}
    prog = {"years_todo": prog.get("years_todo"), "entries_todo": list(prog.get("entries_todo") or []), "done": bool(prog.get("done"))}
    if prog["done"]:
        return [], {**st, "done": True}, prog
    if prog["years_todo"] is None:
        r = http_get(f"{DARWIN}/darwin/")
        if r.status_code != 200:
            raise RuntimeError(f"darwin index answered HTTP {r.status_code}")
        prog["years_todo"] = darwin_years(r.text)
        time.sleep(SLEEP)
    while prog["years_todo"] and len(prog["entries_todo"]) < DARWIN_ENTRIES_PER_PASS and st["years_listed"] < DARWIN_YEARS_PER_PASS and time.monotonic() < deadline:
        year = prog["years_todo"].pop(0)
        r = http_get(f"{DARWIN}/darwin/darwin{year}.html")
        time.sleep(SLEEP)
        st["years_listed"] += 1
        if r.status_code == 200:
            prog["entries_todo"].extend(u for u in darwin_entry_urls(r.text, year) if L.ext_id("darwin_awards", u) not in known)
    items = []
    for _ in range(DARWIN_ENTRIES_PER_PASS):
        if not prog["entries_todo"] or time.monotonic() > deadline:
            break
        url = prog["entries_todo"].pop(0)
        try:
            r = http_get(url, tries=2)
        except Exception:
            st["page_status"]["error"] = st["page_status"].get("error", 0) + 1
            continue
        time.sleep(SLEEP)
        st["page_status"][str(r.status_code)] = st["page_status"].get(str(r.status_code), 0) + 1
        if r.status_code != 200:
            continue
        r.encoding = r.apparent_encoding or "utf-8"
        e = parse_darwin(r.text, url)
        st["entries_read"] += 1
        if not e or not darwin_is_fact(e["status"]):
            st["not_a_confirmed_winner"] += 1
            continue
        picked, why = pick_paragraphs(e["paragraphs"], e["title"])
        if why:
            st["left_out"][why] = st["left_out"].get(why, 0) + 1
            continue
        items.append({"category": "unusual_deaths", "title": e["title"], "body": "\n\n".join(picked), "source_url": url, "ext": L.ext_id("darwin_awards", url)})
    prog["done"] = not prog["years_todo"] and not prog["entries_todo"]
    st["items"], st["entries_left"] = len(items), len(prog["entries_todo"])
    return items, st, prog


# ------------------------------------------------------------------------------------------------ Telegram archive
def clean_telegram(text: str) -> str:
    lines = [ln.rstrip() for ln in (text or "").split("\n")]
    while lines and (not lines[-1].strip() or TG_SIGNATURE.match(lines[-1])):
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def telegram_item(handle: str, post: dict, category: str) -> tuple[dict | None, str | None]:
    pid = post["post"].split("/")[-1]
    raw = post.get("text") or ""
    others = [x for x in (post.get("links") or []) if not re.match(rf"https?://t\.me/{re.escape(handle)}(?:[/?#]|$)", x, re.I)]       # the channel's own signature is a link to itself: that is not a promotion
    if others or TG_AD.search(raw):
        return None, "promo_or_link"
    text = clean_telegram(raw)
    paras = [clean(p) for p in re.split(r"\n\s*\n", text) if clean(p)]
    picked, why = pick_paragraphs(paras, "")
    if why:
        return None, why
    return {"category": category, "title": "", "body": "\n\n".join(picked), "source_url": f"https://t.me/{handle}/{pid}", "ext": f"tg:{handle}/{pid}"}, None


def read_telegram(handle: str, src: dict, prog: dict, known: set[str], deadline: float) -> tuple[list[dict], dict, dict]:
    """The latest page (new posts) every pass, then the older pages from where the last pass stopped (`before` = the lowest post id seen), PASS pages a cycle, until the channel's first post."""
    st = {"pages_read": 0, "posts": 0, "left_out": {}, "items": 0}
    prog = {"before": prog.get("before"), "done": bool(prog.get("done"))}
    items: list[dict] = []

    def page(before):
        r = http_get(f"https://t.me/s/{handle}" + (f"?before={before}" if before else ""))
        time.sleep(SLEEP)
        if r.status_code != 200:
            raise RuntimeError(f"t.me/s/{handle} answered HTTP {r.status_code}")
        posts = parse_telegram_preview(r.text, handle)
        st["pages_read"] += 1
        for post in posts:
            st["posts"] += 1
            it, why = telegram_item(handle, post, src["category"])
            if why:
                st["left_out"][why] = st["left_out"].get(why, 0) + 1
            elif it and it["ext"] not in known:
                items.append(it)
        return [int(x["post"].split("/")[-1]) for x in posts]

    ids = page(None)
    if prog["before"] is None and ids:
        prog["before"] = min(ids)
    for _ in range(src["pass_pages"] if not prog["done"] else 0):
        if time.monotonic() > deadline:
            st["budget_hit"] = True
            break
        if not prog["before"] or prog["before"] <= 1:
            prog["done"] = True
            break
        ids = page(prog["before"])
        if not ids:
            prog["done"] = True
            break
        prog["before"] = min(ids)
    st["items"] = len(items)
    return items, st, prog


# ------------------------------------------------------------------------------------------------ the pass
def _known_ext_ids(conn) -> set[str]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT external_id FROM raw_items WHERE payload->>'curated' = 'true'")
        return {r["external_id"] for r in cur.fetchall()}


def _load_progress(conn) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT details FROM run_logs WHERE job_name = 'collect_curated' AND status = 'success' ORDER BY started_at DESC LIMIT 1")
        row = cur.fetchone()
    return ((row or {}).get("details") or {}).get("progress") or {}


def fetch_all(progress: dict, known: set[str], budget: float = BUDGET_SECONDS) -> tuple[list[tuple[str, str, dict]], dict, dict]:
    """Network only (no database connection is open): every source in turn. Returns ([(source_key, source_name, item)], stats per source, new progress). A failing source is a stat, never an exception."""
    deadline = time.monotonic() + budget
    out, stats, new = [], {}, {"feeds": {}, "darwin": {}, "telegram": {}}
    for key, src in FEEDS.items():
        try:
            items, st, pr = read_feed_source(key, src, (progress.get("feeds") or {}).get(key) or {}, known, deadline)
            new["feeds"][key] = pr
            stats[key] = st
            out.extend((key, src["name"], it) for it in items)
        except Exception as exc:
            logger.exception("curated: %s failed", key)
            stats[key] = {"error": str(exc)[:160]}
            new["feeds"][key] = (progress.get("feeds") or {}).get(key) or {}
    try:
        items, st, pr = read_darwin(progress.get("darwin") or {}, known, deadline)
        new["darwin"] = pr
        stats["darwin_awards"] = st
        out.extend(("darwin_awards", "Darwin Awards", it) for it in items)
    except Exception as exc:
        logger.exception("curated: darwin failed")
        stats["darwin_awards"] = {"error": str(exc)[:160]}
        new["darwin"] = progress.get("darwin") or {}
    for handle, src in TELEGRAM.items():
        try:
            items, st, pr = read_telegram(handle, src, (progress.get("telegram") or {}).get(handle) or {}, known, deadline)
            new["telegram"][handle] = pr
            stats[f"telegram_{handle}"] = st
            out.extend((f"telegram_{handle}", src["name"], it) for it in items)
        except Exception as exc:
            logger.exception("curated: telegram %s failed", handle)
            stats[f"telegram_{handle}"] = {"error": str(exc)[:160]}
            new["telegram"][handle] = (progress.get("telegram") or {}).get(handle) or {}
    return out, stats, new


def store_all(conn, fetched: list[tuple[str, str, dict]], stats: dict) -> dict[str, int]:
    """Structural filters and insert, one group per (category, source). Returns {category: rows stored live}."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for key, name, it in fetched:
        groups.setdefault((it["category"], key, name), []).append(it)
    stored: dict[str, int] = {}
    for (category, key, name), items in sorted(groups.items()):
        counts = L._store(conn, category, name, key, items, audience=False, min_sentences=MIN_SENTENCES, curated=True)
        conn.commit()
        stored[category] = stored.get(category, 0) + counts["stored_live"]
        st = stats.setdefault(key, {})
        st.setdefault("stored", {})[category] = counts["stored_live"]
        for reason, n in counts["rule_dropped"].items():
            st.setdefault("rule_dropped", {})[reason] = st.get("rule_dropped", {}).get(reason, 0) + n
        st["duplicates_skipped"] = st.get("duplicates_skipped", 0) + counts["duplicates_skipped"]
    return stored


_PASS: dict | None = None


def run_pass(budget: float = BUDGET_SECONDS) -> dict:
    """The whole pass once per process, logged as run_logs `collect_curated` (its details carry the per-source stats and the progress the next cycle continues from)."""
    load_dotenv()
    conn = get_conn()
    try:
        progress, known = _load_progress(conn), _known_ext_ids(conn)
    finally:
        conn.close()
    fetched, stats, new_progress = fetch_all(progress, known, budget)
    conn = get_conn()
    try:
        with run_log(conn, "collect_curated") as state:
            if stats and all("error" in s for s in stats.values()):
                raise RuntimeError(f"curated: every source failed ({str({k: v['error'] for k, v in stats.items()})[:300]})")
            stored = store_all(conn, fetched, stats)
            state["details"].update({"by_source": stats, "stored": stored, "progress": new_progress, "fetched_items": len(fetched)})
            logger.info("collect_curated: fetched=%d stored=%s", len(fetched), stored)
        return {"stored": stored, "by_source": stats}
    finally:
        conn.close()


def ensure_pass() -> dict:
    """The pass once per process. A pass that failed is not run again for the next category (that would repeat a 14-minute failure five times): every category asked afterwards gets the same error."""
    global _PASS
    if _PASS is None:
        try:
            _PASS = run_pass()
        except Exception as exc:
            _PASS = {"error": exc}
            raise
    if "error" in _PASS:
        raise _PASS["error"]
    return _PASS


def collect_for(category: str) -> int:
    """The curated rows stored live for `category` by this process's pass (the pass itself runs once, on the first category asked)."""
    return ensure_pass()["stored"].get(category, 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = ensure_pass()
    print(res["stored"])
