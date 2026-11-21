"""We Talk A Lot, PHASE 2 (operator 2026-11-21): six LIBRARY categories, collected in bulk and drawn from at random without repeats (pipeline/select_candidates.py LIBRARY_CATEGORIES), like the jokes.

Every card is the source's EXACT words (pipeline/verbatim.py); nothing here rewrites anything. A parser reads what the page prints (tags removed, whitespace collapsed) and nothing else.

  category        source (endpoint verified from Railway: run_logs `source_probe`)                       what one item is
  unusual_deaths  Wikipedia "List of unusual deaths in <period>" (7 pages: antiquity ... 21st century)   a table row: name (title), details (body), "date, country:" line on top
                  + "List of inventors killed by their own invention"                                      a list entry, whole
  weird_facts     Wikipedia "Wikipedia:Unusual articles" (104 sections)                                    a table row: article title (title), the page's one-line description (body)
  conspiracies    Wikipedia "List of conspiracy theories" (102 sections)                                   a section: heading (title), its first paragraph or two (body)
  dark_history    Wikipedia "On this day" events (api.wikimedia.org feed/v1, 200 from Railway)             one event ("1692 - ..."), the day as the top line; DeepSeek yes/no: dark or bizarre
  shower_thoughts Lemmy c/showerthoughts (lemmy.world feed verified; the same API as c/jokes)              a title-only post; DeepSeek yes/no: a genuine shower thought, not about the platform
  world_records   RecordSetter category pages (recordsetter.com: the homepage verified from Railway)      a record: title (h2) and the one-sentence description under it

Wikipedia text is CC BY-SA; the credit is NOT in the post (operator 2026-11-21: he adds sources himself): the reference block under the draft names the source and links it.

Each collector keeps its own run_logs job (collect_<category>): a full pass runs when none succeeded in REFRESH_DAYS, or when the source set changed. dark_history works through the calendar in slices (DAYS_PER_PASS
days a cycle, each day ~600 KB) and remembers the days done in its run_logs details. A joke-style yes/no (DeepSeek) that could not rule on an item stores nothing and leaves the pass open: unknown is never coded as yes.
"""

import copy
import hashlib
import json
import logging
import re
import sys
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

REFRESH_DAYS = 30
HEADERS = {"User-Agent": "WeTalkALotCollector/1.0 (research use; python-requests)"}      # descriptive and generic: Wikimedia throttles anonymous browser-looking clients harder
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_URL = "https://en.wikipedia.org/wiki/"
# the host Railway verified first; the same Wikimedia feed is also served at en.wikipedia.org (the developer machine cannot resolve api.wikimedia.org)
OTD_URLS = ["https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{mm}/{dd}", "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{mm}/{dd}"]
LEMMY_API = "https://lemmy.world/api/v3/post/list"
RS_BASE = "https://recordsetter.com"
CHECK_BATCH = 30
DAYS_PER_PASS = 40            # dark_history: calendar days read per cycle
SHOWER_PAGES = 12             # Lemmy pages of 50, best first
SHOWER_MIN_SCORE = 100
RS_MAX_PAGES = 220

UD_PAGES = ["List of unusual deaths in antiquity", "List of unusual deaths in the Middle Ages", "List of unusual deaths in the Renaissance", "List of unusual deaths in the early modern period",
            "List of unusual deaths in the 19th century", "List of unusual deaths in the 20th century", "List of unusual deaths in the 21st century"]
UD_LIST_PAGES = ["List of inventors killed by their own invention"]
UNUSUAL_ARTICLES_PAGE = "Wikipedia:Unusual articles"
CONSPIRACY_PAGE = "List of conspiracy theories"
_END_SECTIONS = ("see also", "notes", "references", "further reading", "external links", "bibliography", "footnotes", "sources")

# ------------------------------------------------------------------------------------------------ the model's yes/no (never a rewrite)
DARK_HISTORY_PROMPT = """You are sorting historical events for a dark-humor channel about dark and bizarre history. For each numbered event answer true ONLY if it is dark or bizarre history that a dark-humor audience would find striking: a massacre, atrocity, execution, assassination, torture, an epidemic or famine, a disaster with deaths, a strange, absurd or ironic event, a bizarre death or an odd historical episode.
Answer false for ordinary politics and diplomacy, treaties, elections, openings and foundings, births, scientific or technical milestones, sports results, awards and anything with nothing grim or odd in it.
Do not judge whether an event is acceptable; judge ONLY whether it is dark or bizarre.

Return ONLY a JSON object: {{"verdicts": [true, false, ...]}} with exactly {n} entries, in the order of the events.

{items}"""

SHOWER_PROMPT = """You are sorting posts from a "shower thoughts" community. For each numbered post answer true ONLY if it is a genuine shower thought: a short, witty or thought-provoking observation about life, language, habits or the world that stands on its own and reads well as a single line.
Answer false for posts about Reddit, Lemmy, the fediverse, moderators, votes or the platform itself, questions asking for answers, announcements, rants, political slogans, complaints, plain facts with no twist, and anything that needs context to make sense.

Return ONLY a JSON object: {{"verdicts": [true, false, ...]}} with exactly {n} entries, in the order of the posts.

{items}"""

_DARK_CUES = re.compile(
    r"\b(kill|kills|killed|killing|die|dies|died|dead|death|deaths|deadly|murder|murdered|massacre|massacres|execut|assassinat|slain|slaughter|behead|hanged|hangs|burn|burned|burnt|burns|fire|explo|bomb|disaster|crash|crashes|sink|sinks|sank|drown|shipwreck|plague|epidemic|pandemic|"
    r"famine|starv|poison|tortur|riot|riots|rebel|revolt|siege|genocide|holocaust|shoot|shot|stab|hostage|kidnap|suicide|collapse|earthquake|tsunami|flood|eruption|erupts|hurricane|tornado|cholera|smallpox|witch|cannibal|lynch|prison|imprison|exile|banish|curse|ghost|strange|bizarre|"
    r"mysterious|vanish|disappear|duel|dancing|riot|mutiny|coup|invasion|invades|war|battle|wounded|injur|victims|hostages|terror|attack|explosion|massacred|crucif|stoned|burned|buried|cult|apocalypse|doomsday|madness|insane|mad)", re.I)


def http_get(url: str, *, params=None, timeout: int = 60, tries: int = 4):
    """GET with a polite retry: HTTP 429 (Wikimedia / RecordSetter throttle bursts) waits for Retry-After (at most 60 s) or 5 / 15 / 30 s, then tries again; 5xx and timeouts are retried the same way."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                try:
                    wait = min(60.0, float(r.headers.get("Retry-After", "")))
                except ValueError:
                    wait = (5, 15, 30, 45)[min(attempt, 3)]
                last = requests.HTTPError(f"HTTP {r.status_code} for {url[:80]}", response=r)
                if attempt < tries - 1:
                    time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep((5, 15, 30, 45)[min(attempt, 3)])
    raise last


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def ext_id(prefix: str, *parts: str) -> str:
    return f"{prefix}:" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:14]


# ------------------------------------------------------------------------------------------------ reading HTML (not editing)
_DROP = "sup.reference, sup.noprint, .mw-editsection, style, script, figure, .hatnote, .noprint, .navbar, .mw-empty-elt"


def cell_text(el) -> str:
    """The text a reader sees in an element: citation marks, edit links, styles and figures dropped, line breaks and nested blocks become spaces, whitespace collapsed. Never rewrites a word."""
    el = copy.copy(el)
    for x in el.select(_DROP):
        x.decompose()
    for br in el.find_all("br"):
        br.replace_with(" ")
    for blk in el.find_all(["p", "li", "div", "dd", "dt"]):
        blk.append(" ")
    t = el.get_text("").replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", t).strip()


def wiki_html(page: str) -> tuple[str, str]:
    """(html, url) of a Wikipedia page through the parse API (the endpoint family Railway's source_probe verified), redirects followed."""
    r = http_get(WIKI_API, params={"action": "parse", "page": page, "prop": "text", "format": "json", "formatversion": 2, "redirects": 1}, timeout=90)
    d = r.json()
    if "parse" not in d:
        raise RuntimeError(f"wikipedia: no parse for {page!r}: {str(d)[:100]}")
    return d["parse"]["text"], WIKI_URL + d["parse"]["title"].replace(" ", "_")


def _root(html: str):
    s = BeautifulSoup(html or "", "html.parser")
    return s.select_one("div.mw-parser-output") or s


# ------------------------------------------------------------------------------------------------ parsers (pure functions, unit-tested)
def parse_unusual_deaths_tables(html: str, page_url: str) -> list[dict]:
    """Every wikitable row of a "List of unusual deaths" page: name, date of death, country, details."""
    out = []
    for table in _root(html).select("table.wikitable"):
        rows = table.find_all("tr")
        if not rows:
            continue
        heads = [cell_text(c).lower() for c in rows[0].find_all(["th", "td"])]
        if not any("detail" in h or "description" in h or "cause" in h for h in heads):
            continue
        idx = {"name": 0}
        for i, h in enumerate(heads):
            if "date" in h:
                idx["date"] = i
            elif "country" in h:
                idx["country"] = i
            elif "detail" in h or "description" in h:
                idx["details"] = i
        if "details" not in idx:
            continue
        for tr in rows[1:]:
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) != len(heads):
                continue
            name, details = cell_text(cells[idx["name"]]), cell_text(cells[idx["details"]])
            date_t = cell_text(cells[idx["date"]]) if "date" in idx else ""
            country = cell_text(cells[idx["country"]]) if "country" in idx else ""
            if not name or len(details) < 40 or len(details) > 800:
                continue
            line = ", ".join(x for x in (date_t, country) if x)
            out.append({"title": name, "body": details, "dateline": (line + ":") if line else "", "source_url": page_url})
    return out


def parse_li_entries(html: str, page_url: str) -> list[dict]:
    """The list entries of a list-style page ("List of inventors killed by their own invention"): each <li> before the "See also" / notes sections, whole, as a body-only item."""
    out = []
    for el in _root(html).find_all(["h2", "ul"]):
        if el.name == "h2":
            if cell_text(el).lower().startswith(_END_SECTIONS):
                break
            continue
        if el.find_parent(["table", "div"], class_=re.compile(r"navbox|toc|sidebar|mw-heading|reflist|refbegin")) or el.get("class") and "gallery" in " ".join(el.get("class")):
            continue
        for li in el.find_all("li", recursive=False):
            t = cell_text(li)
            if 60 <= len(t) <= 700 and re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", t):
                out.append({"title": "", "body": t, "dateline": "", "source_url": page_url})
    return out


def parse_unusual_articles(html: str, page_url: str) -> list[dict]:
    """Wikipedia:Unusual articles: wikitable rows of two cells, the article's title and a one-line description."""
    out = []
    for table in _root(html).select("table.wikitable"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) != 2:
                continue
            title, desc = cell_text(tds[0]), cell_text(tds[1])
            if title and 40 <= len(desc) <= 700:
                out.append({"title": title, "body": desc, "dateline": "", "source_url": WIKI_URL + title.replace(" ", "_")})
    return out


def parse_conspiracies(html: str, page_url: str) -> list[dict]:
    """List of conspiracy theories: a heading and the paragraphs under it. The first paragraph whole, the second added while the pair stays within 700 characters."""
    out, heading, paras = [], None, []

    def flush():
        if heading and paras:
            body = paras[0]
            if len(paras) > 1 and len(body) + len(paras[1]) <= 700:
                body = body + "\n\n" + paras[1]
            if 60 <= len(paras[0]) <= 700:
                out.append({"title": heading, "body": body, "dateline": "", "source_url": page_url + "#" + heading.replace(" ", "_")})

    for el in _root(html).find_all(["h2", "h3", "h4", "p"]):
        if el.name in ("h2", "h3", "h4"):
            flush()
            t = cell_text(el)
            # an h2 is a GROUP ("Government, politics, and conflict"), not a theory: only h3 / h4 headings are items; the closing sections and the table of contents are not
            heading, paras = (None if el.name == "h2" or t.lower().startswith(_END_SECTIONS + ("contents",)) else t), []
        elif heading and not el.find_parent(["table", "figure", "blockquote", "div"], class_=re.compile(r"navbox|sidebar|hatnote|reflist|thumb|infobox")):
            t = cell_text(el)
            if len(t) >= 40:
                paras.append(t)
    flush()
    return out


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def otd_dateline(mm: int, dd: int, year) -> str:
    y = int(year)
    return f"{dd} {MONTHS[mm - 1]} {abs(y)} BC:" if y < 0 else f"{dd} {MONTHS[mm - 1]} {y}:"


def parse_otd(events: list[dict], mm: int, dd: int) -> list[dict]:
    out = []
    for e in events or []:
        text = (e.get("text") or "").strip()
        if not text or e.get("year") is None or len(text) < 40 or len(text) > 600:
            continue
        pages = e.get("pages") or []
        url = ""
        if pages:
            url = (((pages[0].get("content_urls") or {}).get("desktop") or {}).get("page")) or ""
        out.append({"title": "", "body": text, "dateline": otd_dateline(mm, dd, e["year"]), "source_url": url or f"{WIKI_URL}{MONTHS[mm - 1]}_{dd}", "year": e["year"]})
    return out


def parse_shower_posts(posts: list[dict]) -> list[dict]:
    out = []
    for p in posts or []:
        post, counts = p.get("post") or {}, p.get("counts") or {}
        name = (post.get("name") or "").strip()
        if post.get("removed") or post.get("deleted") or (post.get("body") or "").strip() or post.get("url") or not name:
            continue
        if not 20 <= len(name) <= 300 or re.search(r"\]\(|https?://|^\s*[#>*-]\s|\n", name):
            continue
        if int(counts.get("score") or 0) < SHOWER_MIN_SCORE:
            continue
        out.append({"title": "", "body": name, "dateline": "", "source_url": post.get("ap_id") or "", "score": int(counts.get("score") or 0), "ext": f"lemmy_showerthoughts:{post.get('id')}"})
    return out


_RS_BOILERPLATE = re.compile(r"WARNING|submissions|minors|#\w|RecordSetter", re.I)


def parse_recordsetter(html: str) -> tuple[list[dict], list[str]]:
    """(records, topic links) from a RecordSetter category page: each <a class="video-item"> holds the record's title (h2) and a one-sentence description."""
    s = BeautifulSoup(html or "", "html.parser")
    out = []
    for a in s.select("a.video-item"):
        h2, desc = a.find("h2"), a.select_one(".video-item-description")
        title, body = cell_text(h2) if h2 else "", cell_text(desc) if desc else ""
        href = (a.get("href") or "").split("?")[0]
        # RecordSetter's own boilerplate rides inside some descriptions ("WARNING: ... can be dangerous", "We will not accept submissions ... from minors", "#RecordSetterBook01"): not the record's words, so
        # such a record is left out rather than printed with a site notice inside it
        if _RS_BOILERPLATE.search(body):
            continue
        if title and 20 <= len(body) <= 400 and "/world-record/" in href:
            out.append({"title": title, "body": body, "dateline": "", "source_url": href})
    topics = []
    for a in s.find_all("a", href=True):
        h = a["href"]
        if re.fullmatch(r"/[a-z0-9\-']+-world-records", h) and h not in topics:
            topics.append(h)
    return out, topics


# ------------------------------------------------------------------------------------------------ fetchers
def fetch_wiki_items(pages: list[str], parser) -> tuple[list[dict], dict]:
    items, errors = [], {}
    for page in pages:
        try:
            html, url = wiki_html(page)
            got = parser(html, url)
            for g in got:
                g["page"] = page
            items.extend(got)
            time.sleep(2.0)
        except Exception as exc:                # one page down never stops the others
            errors[page] = str(exc)[:100]
            logger.warning("library: %s failed: %s", page, exc)
    return items, errors


def fetch_lemmy_showerthoughts(pages: int = SHOWER_PAGES) -> list[dict]:
    posts = []
    for pg in range(1, pages + 1):
        r = http_get(LEMMY_API, params={"community_name": "showerthoughts", "sort": "TopAll", "limit": 50, "page": pg}, timeout=40)
        got = r.json().get("posts") or []
        posts.extend(got)
        if len(got) < 50:
            break
        time.sleep(0.4)
    return parse_shower_posts(posts)


def fetch_recordsetter(max_pages: int = RS_MAX_PAGES) -> tuple[list[dict], dict]:
    """The homepage's category pages, then each category's topic pages (16 records a page)."""
    seen_pages, items, errors = set(), [], {}
    seen_records = set()
    first = http_get(RS_BASE + "/world-records", timeout=60)
    _, top = parse_recordsetter(first.text)
    queue = list(top[:14])
    while queue and len(seen_pages) < max_pages:
        path = queue.pop(0)
        if path in seen_pages:
            continue
        seen_pages.add(path)
        try:
            r = http_get(RS_BASE + path, timeout=45, tries=2)
            recs, topics = parse_recordsetter(r.text)
        except Exception as exc:
            errors[path] = str(exc)[:80]
            continue
        for rec in recs:
            if rec["source_url"] not in seen_records:
                seen_records.add(rec["source_url"])
                items.append(rec)
        if len(seen_pages) <= 14:                     # only the main categories feed the queue (their topics), not every topic's own neighbours
            queue.extend(t for t in topics if t not in seen_pages and t not in queue)
        time.sleep(0.3)
    return items, errors


def fetch_otd_day(mm: int, dd: int) -> list[dict]:
    last = None
    for tpl in OTD_URLS:
        try:
            r = http_get(tpl.format(mm=f"{mm:02d}", dd=f"{dd:02d}"), timeout=90)
            return parse_otd(r.json().get("events") or [], mm, dd)
        except Exception as exc:
            last = exc
    raise last


# ------------------------------------------------------------------------------------------------ the model
def yes_no(conn, category: str, prompt: str, texts: list[str]) -> list[bool | None]:
    """DeepSeek's yes/no per item, in order. None = no usable answer (never treated as yes)."""
    from pipeline import llm, llm_usage, post_format
    out: list[bool | None] = []
    for i in range(0, len(texts), CHECK_BATCH):
        batch = texts[i:i + CHECK_BATCH]
        numbered = "\n\n".join(f"{n + 1}. {t}" for n, t in enumerate(batch))
        try:
            with llm_usage.tagged("wtal_library_bulk"):          # one call per group by design: not a runaway (pipeline/llm_usage.tagged)
                raw = llm.generate_write(conn, category, prompt.format(n=len(batch), items=numbered))
            parsed = post_format.parse_llm_json(raw)
            v = parsed.get("verdicts") if isinstance(parsed, dict) else None
            if not isinstance(v, list) or len(v) != len(batch) or not all(isinstance(x, bool) for x in v):
                raise ValueError(f"unusable verdicts: {str(parsed)[:120]}")
            out.extend(v)
        except Exception:
            logger.exception("library: the yes/no check could not rule on a batch of %d (%s)", len(batch), category)
            out.extend([None] * len(batch))
    return out


# ------------------------------------------------------------------------------------------------ storing
def _payload(item: dict, category: str, source_name: str, source_key: str) -> dict:
    p = {"title": item.get("title") or "", "body": item.get("body") or "", "source_name": source_name, "source_key": source_key, "source_url": item.get("source_url") or "", "library": category}
    if item.get("dateline"):
        p["dateline"] = item["dateline"]
    for k in ("year", "page", "score"):
        if item.get(k) is not None:
            p[k] = item[k]
    return p


def _store(conn, category: str, source_label: str, source_key: str, items: list[dict], judge=None, limit: int | None = None) -> dict:
    """Dedupe against what is stored, optionally ask the model (yes -> live, no -> held with the verdict, unknown -> nothing stored), insert. Returns the per-source counts."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT payload->>'title' AS t, payload->>'body' AS b FROM raw_items WHERE category = %s", (category,))
        seen = {norm_key((r["t"] or "") + (r["b"] or "")) for r in cur.fetchall()}
        cur.execute("SELECT external_id FROM raw_items WHERE category = %s", (category,))
        known = {r["external_id"] for r in cur.fetchall()}
    fresh, dupes = [], 0
    for it in items:
        ext = it.get("ext") or ext_id(source_key, it.get("page") or "", it.get("title") or "", it.get("body") or "")
        k = norm_key((it.get("title") or "") + (it.get("body") or ""))
        if ext in known or k in seen:
            dupes += 1
            continue
        seen.add(k)
        pl = _payload(it, category, source_label, source_key)
        pl["norm_key"] = k
        fresh.append((ext, pl))
    left = 0
    if limit and len(fresh) > limit:
        left, fresh = len(fresh) - limit, fresh[:limit]
    yes, no, unknown = list(fresh), [], 0
    if judge is not None:
        verdicts = judge([p["body"] for _, p in fresh])
        yes = []
        for (ext, pl), v in zip(fresh, verdicts):
            if v is None:
                unknown += 1
                continue
            pl["check"] = "yes" if v else "no"
            (yes if v else no).append((ext, pl))
    source_id = get_or_create_source(conn, category, f"wtal_{source_key}", "api", {"library": category})
    inserted = insert_raw_items_batch(conn, source_id, category, yes)
    if no:
        insert_raw_items_batch(conn, source_id, category, no, held=True)
    return {"fetched": len(items), "new_checked": len(fresh), "duplicates_skipped": dupes, "stored_live": inserted, "no_held": len(no), "unknown_not_stored": unknown, "left_for_next_pass": left}


def _due(conn, job: str, sources: list[str]) -> bool:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT details FROM run_logs WHERE job_name = %s AND status = 'success' AND details->>'full_pass' = 'true' AND started_at > now() - make_interval(days => %s) ORDER BY started_at DESC LIMIT 1", (job, REFRESH_DAYS))
        row = cur.fetchone()
    return row is None or sorted((row["details"] or {}).get("sources") or []) != sorted(sources)


def _run(category: str, sources: list[str], fetch, store, force: bool = False) -> int:
    """The shared shell. No database connection is held while pages download (a cloud Postgres closes an idle connection, which killed the first local run): check whether a pass is due, close, `fetch()` (network
    only), reconnect, log under collect_<category> and `store(conn, state, fetched)`. A fetch failure is still logged as a failed run, never a silent empty one."""
    load_dotenv()
    job = f"collect_{category}"
    conn = get_conn()
    try:
        if not force and not _due(conn, job, sources):
            return 0
    finally:
        conn.close()
    fetched, fetch_error = None, None
    try:
        fetched = fetch()
    except Exception as exc:
        fetch_error = exc
    conn = get_conn()
    try:
        with run_log(conn, job) as state:
            if fetch_error:
                raise fetch_error
            inserted, full, extra = store(conn, state, fetched)
            state["details"].update({"full_pass": bool(full), "sources": sources, "inserted": inserted, **extra})
            logger.info("%s: inserted=%d full=%s %s", job, inserted, full, extra)
        return inserted
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------ the six collectors
def _wiki_collect(category: str, pages: list[str], parser, source_key: str, force: bool = False) -> int:
    def fetch():
        items, errors = fetch_wiki_items(pages, parser)
        if not items:
            raise RuntimeError(f"{category}: nothing parsed ({errors})")
        return items, errors

    def store(conn, state, fetched):
        items, errors = fetched
        counts = _store(conn, category, "Wikipedia", source_key, items)
        return counts["stored_live"], not errors, {"by_source": {source_key: counts}, "page_errors": errors}
    return _run(category, pages, fetch, store, force)


def collect_unusual_deaths(force: bool = False) -> int:
    def fetch():
        t, e1 = fetch_wiki_items(UD_PAGES, parse_unusual_deaths_tables)
        li, e2 = fetch_wiki_items(UD_LIST_PAGES, parse_li_entries)
        if not (t or li):
            raise RuntimeError(f"unusual_deaths: nothing parsed ({ {**e1, **e2} })")
        return t + li, {**e1, **e2}

    def store(conn, state, fetched):
        items, errors = fetched
        counts = _store(conn, "unusual_deaths", "Wikipedia", "wikipedia_unusual_deaths", items)
        return counts["stored_live"], not errors, {"by_source": {"wikipedia_unusual_deaths": counts}, "page_errors": errors}
    return _run("unusual_deaths", UD_PAGES + UD_LIST_PAGES, fetch, store, force)


def collect_weird_facts(force: bool = False) -> int:
    return _wiki_collect("weird_facts", [UNUSUAL_ARTICLES_PAGE], parse_unusual_articles, "wikipedia_unusual_articles", force)


def collect_conspiracies(force: bool = False) -> int:
    return _wiki_collect("conspiracies", [CONSPIRACY_PAGE], parse_conspiracies, "wikipedia_conspiracies", force)


def collect_world_records(force: bool = False) -> int:
    def fetch():
        items, errors = fetch_recordsetter()
        if not items:
            raise RuntimeError(f"world_records: nothing parsed ({str(errors)[:200]})")
        return items, errors

    def store(conn, state, fetched):
        items, errors = fetched
        counts = _store(conn, "world_records", "RecordSetter", "recordsetter", items)
        return counts["stored_live"], len(errors) <= 3, {"by_source": {"recordsetter": counts}, "page_errors": dict(list(errors.items())[:10])}
    return _run("world_records", ["recordsetter"], fetch, store, force)


def collect_shower_thoughts(force: bool = False) -> int:
    def fetch():
        items = fetch_lemmy_showerthoughts()
        if not items:
            raise RuntimeError("shower_thoughts: nothing parsed")
        return items

    def store(conn, state, items):
        counts = _store(conn, "shower_thoughts", "Lemmy c/showerthoughts", "lemmy_showerthoughts", items, judge=lambda texts: yes_no(conn, "shower_thoughts", SHOWER_PROMPT, texts))
        return counts["stored_live"], counts["unknown_not_stored"] == 0, {"by_source": {"lemmy_showerthoughts": counts}}
    return _run("shower_thoughts", ["lemmy_showerthoughts"], fetch, store, force)


def _calendar() -> list[str]:
    d, out = date(2024, 1, 1), []                       # a leap year: includes 02-29
    while d.year == 2024:
        out.append(d.strftime("%m-%d"))
        d += timedelta(days=1)
    return out


def next_days(done: set[str], today: date, n: int) -> list[str]:
    """The next `n` calendar days (MM-DD) not read yet, starting from today's date so the first slice is the nearest days."""
    cal = _calendar()
    start = cal.index(today.strftime("%m-%d")) if today.strftime("%m-%d") in cal else 0
    ordered = cal[start:] + cal[:start]
    return [d for d in ordered if d not in done][:n]


def _otd_done(conn) -> set[str]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT details FROM run_logs WHERE job_name = 'collect_dark_history' AND status = 'success' ORDER BY started_at DESC LIMIT 1")
        row = cur.fetchone()
    return set(((row or {}).get("details") or {}).get("days_done") or [])


def collect_dark_history(force: bool = False, days: int = DAYS_PER_PASS, prefilter: bool = True) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        done = _otd_done(conn)
    finally:
        conn.close()
    if len(done) >= 366 and not force:
        return 0                                         # the calendar is complete; On This Day does not change from month to month
    todo = next_days(done, date.today(), days)
    if not todo:
        return 0
    items, errors, read = [], {}, []
    for md in todo:                                      # network only: no database connection is open while the days download
        mm, dd = int(md[:2]), int(md[3:])
        try:
            items.extend(fetch_otd_day(mm, dd))
            read.append(md)
        except Exception as exc:
            errors[md] = str(exc)[:80]
            logger.warning("dark_history: %s failed: %s", md, exc)
        time.sleep(1.5)
    conn = get_conn()
    try:
        with run_log(conn, "collect_dark_history") as state:
            if not read:
                raise RuntimeError(f"dark_history: no day could be read ({str(errors)[:300]})")
            cand = [i for i in items if _DARK_CUES.search(i["body"])] if prefilter else items
            counts = _store(conn, "dark_history", "Wikipedia", "wikipedia_onthisday", cand, judge=lambda texts: yes_no(conn, "dark_history", DARK_HISTORY_PROMPT, texts))
            all_done = sorted(done | set(read))
            counts["events_read"] = len(items)
            counts["passed_prefilter"] = len(cand)
            state["details"].update({"full_pass": len(all_done) >= 366 and counts["unknown_not_stored"] == 0, "sources": ["wikipedia_onthisday"], "inserted": counts["stored_live"], "days_done": all_done,
                                     "days_read_this_pass": read, "day_errors": errors, "by_source": {"wikipedia_onthisday": counts}})
            logger.info("collect_dark_history: days=%d %s", len(read), counts)
        return counts["stored_live"]
    finally:
        conn.close()


COLLECTORS = {
    "dark_history": collect_dark_history, "unusual_deaths": collect_unusual_deaths, "world_records": collect_world_records,
    "conspiracies": collect_conspiracies, "weird_facts": collect_weird_facts, "shower_thoughts": collect_shower_thoughts,
}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    which = [a for a in sys.argv[1:] if not a.startswith("--")] or list(COLLECTORS)
    for c in which:
        print(c, COLLECTORS[c](force="--force" in sys.argv))
    sys.exit(0)
