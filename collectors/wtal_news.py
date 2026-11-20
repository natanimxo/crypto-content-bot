"""We Talk A Lot (humor channel, operator 2026-11-20): the two POLLED categories of phase 1, weird_news and satire.

Every feed below was verified FROM RAILWAY before it was used (pipeline/probe_wtal.py, run_logs job `source_probe`, 2026-11-20 10:36 UTC): reachable, 200, shaped like a feed, newest item no more than a day old.
Dead or unusable from Railway and therefore NOT used: Fox odd news (400/404), News of the Weird (403/404), IFLScience (404), Betoota Advocate (404), The Hard Drive (connection refused), Al Hudood (404/SSL), WNN (no feed
items parse). Left out on purpose: Fark (its headlines are written by its users), Google News (the first Railway cycle showed it is a bare headline with a redirect link that cannot be opened for the article: the operator
2026-11-20 wants the source's own first paragraphs on every news card), and the German Postillon.

A CARD IS THE ARTICLE'S OPENING, NOT A HEADLINE (operator 2026-11-20, after the first three cards): "the source's own first paragraph or two, plus the outlet and date at the top (BBC, 20 Nov:)". So every item is
stored with:
  title       the source's headline, verbatim
  body        the article's own first body paragraph(s), verbatim (pipeline/article.py: the first paragraph whole, a second while the total stays within 900 characters; nothing cut, nothing rewritten)
  dateline    "CNA, 20 Nov:" -- the outlet's own name (its og:site_name) and the article's date (its own, else the feed's)
  body_source "page" (opened the article), "feed" (the feed carries the text), "none" (the source genuinely gave nothing more: a headline-only card, ranked lower by the scorer)
  weird_news  UPI Odd News (the feed's lede; upi.com answers Cloudflare 403 to scripts, so the page is not opened), Lemmy c/nottheonion (a LINK post: the article it links to is opened; a text post is not news and is skipped).
  satire      Eleven English satire feeds. Most carry the full article in the feed (content:encoded): its first paragraph(s) are used; a feed with nothing is tried at the article page; else headline-only.
A site that answers 403/404/5xx to the first request is not asked again in the same run (no hammering a Cloudflare wall; the page is simply not available and the card says only what the feed gave).

SAME STORY, ONE CARD: a headline whose significant words overlap an already-stored headline of the last three days by 60% or more is the same story and is not stored (UPI and Lemmy often carry the same item).
"""

import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv

from pipeline import article, verbatim
from pipeline.db import dict_cursor, get_conn
from pipeline.probe_wtal import WTAL_PROBES
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot)"}
MAX_AGE_HOURS = {"weird_news": 72.0, "satire": 96.0}   # a little wider than the scoring windows (48h / 72h): the scorer does the ranking, this only stops years-old items from being stored
SAME_STORY_OVERLAP = 0.6
MIN_TITLE_CHARS = 15
MAX_ENRICH_PER_RUN = 40     # article pages opened per run; items beyond it are NOT stored and come round again next cycle (never stored thin by accident)

_URLS = {p["id"]: p["url"] for p in WTAL_PROBES}

WEIRD_SOURCES = [
    # (source_key, display name, probe id)
    ("upi", "UPI", "w_upi_odd"),
    ("lemmy", "Lemmy c/nottheonion", "w_lemmy_nottheonion"),
]
LEMMY_SKIP_PREFIXES = ("META", "Reminder", "Rule", "Announcement", "Mod")   # community-management posts, not news

SATIRE_SOURCES = [
    ("onion", "The Onion", "s_onion"),
    ("clickhole", "ClickHole", "s_clickhole"),
    ("dailymash", "The Daily Mash", "s_dailymash"),
    ("newsthump", "NewsThump", "s_newsthump"),
    ("beaverton", "The Beaverton", "s_beaverton"),
    ("shovel", "The Shovel", "s_shovel"),
    ("chaser", "The Chaser", "s_chaser"),
    ("reductress", "Reductress", "s_reductress"),
    ("duffel", "Duffel Blog", "s_duffel"),
    ("hardtimes", "The Hard Times", "s_hardtimes"),
    ("babylonbee", "The Babylon Bee", "s_babylonbee"),
]
# Sites that answer a script with a Cloudflare wall (403 / 404 / 522 from a dev machine); their pages are not opened, their feeds' own text is used.
NO_PAGE_FETCH = {"upi.com", "thedailymash.co.uk"}

_WORD_RE = re.compile(r"[a-z0-9]{4,}")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def same_story(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= SAME_STORY_OVERLAP and min(len(wa), len(wb)) >= 3


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(url: str):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return feedparser.parse(r.content)


def _host(url: str) -> str:
    return re.sub(r"^(www|edition|m|amp)\.", "", (urlparse(url).netloc or "").lower())


class Fetcher:
    """Opens article pages for one run: each site is asked until it answers with an error, then not again; a site on NO_PAGE_FETCH is never asked."""

    def __init__(self):
        self.blocked = set(NO_PAGE_FETCH)
        self.stats: dict[str, int] = {}

    def get(self, url: str) -> dict:
        host = _host(url)
        if not url or host in self.blocked:
            self._count("skipped_blocked_site")
            return {"outlet": host, "date": None, "paragraphs": [], "status": "skipped"}
        res = article.fetch(url)
        self._count(res["status"])
        if res["status"].startswith("http_") or res["status"] == "error":
            self.blocked.add(host)
        return res

    def _count(self, key: str):
        self.stats[key] = self.stats.get(key, 0) + 1


def lemmy_external_url(entry) -> str | None:
    """A Lemmy link post carries the article it links to as a second link; a text post does not."""
    own = _host(entry.get("link") or "")
    for l in entry.get("links", []):
        href = l.get("href") or ""
        if href.startswith("http") and _host(href) != own:
            return href
    m = re.search(r'<br\s*/?>\s*<a href="(https?://[^"]+)"', entry.get("summary") or "")
    return m.group(1) if m and _host(m.group(1)) != own else None


def _payload(title, parts, outlet, key, url, when, body_source, extra=None):
    body = "\n\n".join(parts)
    p = {"title": title, "body": body, "body_source": body_source, "source_name": outlet, "source_key": key, "source_url": url,
         "published_at": when.isoformat(), "dateline": article.dateline(outlet, when)}
    p.update(extra or {})
    return p


def weird_entries(source_key: str, feed, now: datetime) -> list[dict]:
    """Fresh, plausible entries of a weird-news feed, BEFORE the article is opened: [{ident, title, when, entry}]."""
    out = []
    for e in feed.entries:
        title = verbatim.extract_text(e.get("title"))
        if source_key == "lemmy" and (title.startswith(LEMMY_SKIP_PREFIXES) or not lemmy_external_url(e)):
            continue
        if len(title) < MIN_TITLE_CHARS:
            continue
        when = _published(e)
        if not when or (now - when) > timedelta(hours=MAX_AGE_HOURS["weird_news"]):
            continue
        ident = e.get("id") or e.get("link")
        if ident:
            out.append({"ident": f"{source_key}:{ident}", "title": title, "when": when, "entry": e})
    return out


def weird_payload(source_key: str, display: str, item: dict, fetcher: Fetcher) -> dict:
    e, title, when = item["entry"], item["title"], item["when"]
    if source_key == "upi":
        lede = article.paragraphs_from_fragment(e.get("summary") or "") or article.paragraphs_from_fragment((e.get("content") or [{}])[0].get("value", ""))
        lede = [p for p in lede if p.strip() != title.strip()]
        page = fetcher.get(e.get("link") or "")
        parts = page["paragraphs"] or lede
        return _payload(title, parts, "UPI", "upi", e.get("link") or "", when, "page" if page["paragraphs"] else ("feed" if parts else "none"))
    url = lemmy_external_url(e)
    page = fetcher.get(url)
    outlet = page["outlet"] or _host(url)
    return _payload(title, page["paragraphs"], outlet, "lemmy", url, page["date"] or when, "page" if page["paragraphs"] else "none",
                    {"via": display, "lemmy_url": e.get("link") or ""})


def satire_entries(source_key: str, feed, now: datetime) -> list[dict]:
    out = []
    for e in feed.entries:
        title = verbatim.extract_text(e.get("title"))
        if len(title) < MIN_TITLE_CHARS or "\n" in title:
            continue
        when = _published(e)
        if not when or (now - when) > timedelta(hours=MAX_AGE_HOURS["satire"]):
            continue
        ident = e.get("id") or e.get("link")
        if ident:
            out.append({"ident": f"{source_key}:{ident}", "title": title, "when": when, "entry": e})
    return out


def satire_payload(source_key: str, display: str, item: dict, fetcher: Fetcher) -> dict:
    e, title, when = item["entry"], item["title"], item["when"]
    # the feed's FULL text first (content:encoded), then its summary; a summary that is only a standfirst or "The post X appeared first on" is rejected by article._pick (too short / boilerplate)
    fragments = [c.get("value", "") for c in (e.get("content") or [])] + [e.get("summary") or ""]
    parts = []
    for frag in fragments:
        parts = [p for p in article.paragraphs_from_fragment(frag) if p.strip() != title.strip() and not p.startswith("The post ")]
        if parts:
            break
    body_source = "feed" if parts else "none"
    if not parts:
        page = fetcher.get(e.get("link") or "")
        if page["paragraphs"]:
            parts, body_source = page["paragraphs"], "page"
    return _payload(title, parts, display, source_key, e.get("link") or "", when, body_source)


def _recent_titles(conn, category: str) -> list[str]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT payload->>'title' AS t FROM raw_items WHERE category = %s AND collected_at > now() - interval '3 days'", (category,))
        return [r["t"] for r in cur.fetchall() if r["t"]]


def _stored_ids(conn, category: str) -> set[str]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT external_id FROM raw_items WHERE category = %s AND collected_at > now() - interval '10 days'", (category,))
        return {r["external_id"] for r in cur.fetchall()}


def _dedupe(candidates: list[dict], existing_titles: list[str]) -> tuple[list[dict], int]:
    kept, seen, dropped = [], list(existing_titles), 0
    for c in candidates:
        t = c["payload"]["title"]
        if any(same_story(t, s) for s in seen):
            dropped += 1
            continue
        kept.append(c)
        seen.append(t)
    return kept, dropped


def _store(conn, category: str, candidates: list[dict]) -> tuple[int, int]:
    deduped, dropped = _dedupe(sorted(candidates, key=lambda c: c["payload"]["published_at"], reverse=True), _recent_titles(conn, category))
    by_source: dict[str, list] = {}
    for c in deduped:
        by_source.setdefault(c["payload"]["source_key"], []).append(c)
    inserted = 0
    for key, items in by_source.items():
        source_id = get_or_create_source(conn, category, f"wtal_{key}", "rss", {"feed": key})
        inserted += insert_raw_items_batch(conn, source_id, category, [(i["external_id"], i["payload"]) for i in items])
    return inserted, dropped


def _collect(category: str, job: str, specs, entries_fn, payload_fn) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, job) as state:
            now = datetime.now(timezone.utc)
            stored = _stored_ids(conn, category)
            fetcher, per_source, failed, pending = Fetcher(), {}, [], []
            for key, display, probe_id in specs:
                try:
                    feed = fetch_feed(_URLS[probe_id])
                    if not feed.entries:
                        raise RuntimeError("feed parsed to no entries")
                    items = entries_fn(key, feed, now)
                    new = [i for i in items if i["ident"] not in stored]
                    per_source[key] = {"entries": len(feed.entries), "fresh": len(items), "new": len(new)}
                    pending.extend((key, display, i) for i in new)
                except Exception as exc:   # one dead feed never stops the others; all dead raises below
                    failed.append(key)
                    per_source[key] = {"error": str(exc)[:120]}
                    logger.warning("%s: %s failed: %s", job, key, exc)
                time.sleep(0.3)
            state["details"].update({"sources": per_source, "failed": failed})
            if len(failed) == len(specs):
                raise RuntimeError(f"{job}: every source failed")
            pending.sort(key=lambda t: t[2]["when"], reverse=True)
            deferred = max(0, len(pending) - MAX_ENRICH_PER_RUN)
            pending = pending[:MAX_ENRICH_PER_RUN]
            candidates = []
            for key, display, item in pending:   # sequential on purpose: one request at a time to each outlet
                try:
                    candidates.append({"external_id": item["ident"], "payload": payload_fn(key, display, item, fetcher)})
                except Exception:
                    logger.exception("%s: could not build %s", job, item["ident"])
            inserted, dropped = _store(conn, category, candidates)
            with_body = sum(1 for c in candidates if c["payload"].get("body"))
            state["details"].update({"candidates": len(candidates), "with_article_text": with_body, "headline_only": len(candidates) - with_body, "deferred_to_next_cycle": deferred,
                                     "same_story_dropped": dropped, "inserted": inserted, "article_fetch": fetcher.stats})
            logger.info("%s: candidates=%d with_text=%d same-story=%d inserted=%d failed=%s fetch=%s", job, len(candidates), with_body, dropped, inserted, failed, fetcher.stats)
        return inserted
    finally:
        conn.close()


def collect_weird_news() -> int:
    return _collect("weird_news", "collect_weird_news", WEIRD_SOURCES, weird_entries, lambda k, d, i, f: weird_payload(k, d, i, f))


def collect_satire() -> int:
    return _collect("satire", "collect_satire", SATIRE_SOURCES, satire_entries, lambda k, d, i, f: satire_payload(k, d, i, f))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect_weird_news()
    collect_satire()
    sys.exit(0)
