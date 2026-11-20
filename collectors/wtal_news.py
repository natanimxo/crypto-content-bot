"""We Talk A Lot (humor channel, operator 2026-11-20): the two POLLED categories of phase 1, weird_news and satire.

Every source below was verified FROM RAILWAY before it was used (pipeline/probe_wtal.py, run_logs job `source_probe`, 2026-11-20 10:36 UTC): reachable, 200, shaped like a feed, newest item no more than a day old.
Dead or unusable from Railway and therefore NOT used: Fox odd news (400/404), News of the Weird (403/404), IFLScience (404), Betoota Advocate (404), The Hard Drive (connection refused), Al Hudood (404/SSL), WNN (no feed
items parse). Left out on purpose: Fark (its headlines are written by its users, not by a news source), Google News' generic "weird" query (it returns ordinary news that happens to contain the word), and the German Postillon.

THE TEXT IS THE SOURCE'S, NOT OURS. A collector's job here is to store the item's words exactly as the source printed them (HTML removed, entities decoded, whitespace collapsed -- pipeline.verbatim.extract_text) together
with where they came from; pipeline/verbatim.py later formats them and proves the formatted card still has exactly those words.
  weird_news  UPI Odd News (title + summary), Lemmy c/nottheonion (title; the post is a link to the real article), Google News "florida man" (the headline; the publisher is the source and is stripped from the headline text
              only because Google appends " - Publisher" to it).
  satire      Eleven English satire feeds, headline only (title verbatim, no body; the card says "via <source>, satire").

SAME STORY, ONE CARD: a headline whose significant words overlap an already-stored headline of the last three days by 60% or more is the same story and is not stored (UPI and Lemmy often carry the same item).
"""

import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dotenv import load_dotenv

from pipeline import verbatim
from pipeline.db import dict_cursor, get_conn
from pipeline.probe_wtal import WTAL_PROBES
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot)"}
MAX_AGE_HOURS = {"weird_news": 72.0, "satire": 96.0}   # a little wider than the scoring windows (48h / 72h): the scorer does the ranking, this only stops years-old items from being stored
SAME_STORY_OVERLAP = 0.6
MIN_TITLE_CHARS = 15

_URLS = {p["id"]: p["url"] for p in WTAL_PROBES}

WEIRD_SOURCES = [
    # (source_key, display name, probe id, uses the summary as the body)
    ("upi", "UPI", "w_upi_odd", True),
    ("lemmy", "Lemmy c/nottheonion", "w_lemmy_nottheonion", False),
    ("gnews", "Google News", "w_gnews_florida_man", False),
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


def weird_items(source_key: str, display: str, feed, use_summary: bool, now: datetime) -> list[dict]:
    out = []
    for e in feed.entries:
        title = verbatim.extract_text(e.get("title"))
        publisher = display
        if source_key == "gnews":
            pub = (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None
            if pub and title.endswith(" - " + pub):
                title, publisher = title[: -len(" - " + pub)].rstrip(), pub
            elif " - " in title and len(title.rsplit(" - ", 1)[1]) <= 40:
                title, publisher = (s.strip() for s in title.rsplit(" - ", 1))
        if source_key == "lemmy" and title.startswith(LEMMY_SKIP_PREFIXES):
            continue
        if len(title) < MIN_TITLE_CHARS:
            continue
        when = _published(e)
        if not when or (now - when) > timedelta(hours=MAX_AGE_HOURS["weird_news"]):
            continue
        body = verbatim.extract_text(e.get("summary")) if use_summary else ""
        if body.strip() == title.strip():
            body = ""
        ident = e.get("id") or e.get("link")
        if not ident:
            continue
        out.append({"external_id": f"{source_key}:{ident}", "payload": {
            "title": title, "body": body, "source_name": publisher if source_key == "gnews" else display, "source_key": source_key,
            "source_url": e.get("link") or "", "published_at": when.isoformat()}})
    return out


def satire_items(source_key: str, display: str, feed, now: datetime) -> list[dict]:
    out = []
    for e in feed.entries:
        title = verbatim.extract_text(e.get("title"))
        if len(title) < MIN_TITLE_CHARS or "\n" in title:
            continue
        when = _published(e)
        if not when or (now - when) > timedelta(hours=MAX_AGE_HOURS["satire"]):
            continue
        ident = e.get("id") or e.get("link")
        if not ident:
            continue
        out.append({"external_id": f"{source_key}:{ident}", "payload": {
            "title": title, "body": "", "source_name": display, "source_key": source_key, "source_url": e.get("link") or "", "published_at": when.isoformat()}})
    return out


def _recent_titles(conn, category: str) -> list[str]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT payload->>'title' AS t FROM raw_items WHERE category = %s AND collected_at > now() - interval '3 days'", (category,))
        return [r["t"] for r in cur.fetchall() if r["t"]]


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


def _store(conn, category: str, job_source: str, candidates: list[dict]) -> tuple[int, int]:
    deduped, dropped = _dedupe(sorted(candidates, key=lambda c: c["payload"]["published_at"], reverse=True), _recent_titles(conn, category))
    by_source: dict[str, list] = {}
    for c in deduped:
        by_source.setdefault(c["payload"]["source_key"], []).append(c)
    inserted = 0
    for key, items in by_source.items():
        source_id = get_or_create_source(conn, category, f"wtal_{key}", "rss", {"feed": key})
        inserted += insert_raw_items_batch(conn, source_id, category, [(i["external_id"], i["payload"]) for i in items])
    return inserted, dropped


def _collect(category: str, job: str, build) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, job) as state:
            now = datetime.now(timezone.utc)
            candidates, per_source, failed = [], {}, []
            for spec in build:
                key = spec[0]
                try:
                    feed = fetch_feed(_URLS[spec[2]])
                    if not feed.entries:
                        raise RuntimeError("feed parsed to no entries")
                    items = weird_items(*spec[:2], feed, spec[3], now) if category == "weird_news" else satire_items(*spec[:2], feed, now)
                    per_source[key] = {"entries": len(feed.entries), "fresh": len(items)}
                    candidates.extend(items)
                except Exception as exc:   # one dead feed never stops the others; all dead raises below
                    failed.append(key)
                    per_source[key] = {"error": str(exc)[:120]}
                    logger.warning("%s: %s failed: %s", job, key, exc)
                time.sleep(0.3)
            state["details"].update({"sources": per_source, "failed": failed})
            if len(failed) == len(build):
                raise RuntimeError(f"{job}: every source failed")
            inserted, dropped = _store(conn, category, job, candidates)
            state["details"].update({"candidates": len(candidates), "same_story_dropped": dropped, "inserted": inserted})
            logger.info("%s: candidates=%d same-story=%d inserted=%d failed=%s", job, len(candidates), dropped, inserted, failed)
        return inserted
    finally:
        conn.close()


def collect_weird_news() -> int:
    return _collect("weird_news", "collect_weird_news", WEIRD_SOURCES)


def collect_satire() -> int:
    return _collect("satire", "collect_satire", SATIRE_SOURCES)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect_weird_news()
    collect_satire()
    sys.exit(0)
