"""We Talk A Lot, PHASE 2 libraries that are NOT editor-curated (operator 2026-11-21): world records from RecordSetter and shower thoughts from Lemmy c/showerthoughts. Wikipedia is no longer a source of anything
here: dark history, unusual deaths, weird facts, world records (a second leg) and conspiracies come from collectors/wtal_curated.py. This module still owns the shared machinery: the HTTP helper, the junk rules
(list / index pages, too few sentences, a question back to the reader), the DeepSeek yes/no, `_store` (also used by the curated collector) and the one-off recheck.

Every card is the source's EXACT words (pipeline/verbatim.py); nothing here rewrites anything.

  category        source (endpoint verified from Railway: run_logs `source_probe`)                       what one item is
  shower_thoughts Lemmy c/showerthoughts (lemmy.world feed verified; the same API as c/jokes)              a title-only post; DeepSeek yes/no: a genuine shower thought, not about the platform
  world_records   RecordSetter category pages (recordsetter.com: the homepage verified from Railway)      a record: title (h2) and the one-sentence description under it

Each collector keeps its own run_logs job (collect_<category>): a full pass runs when none succeeded in REFRESH_DAYS, or when the source set changed. A joke-style yes/no (DeepSeek) that could not rule on an item stores
nothing and leaves the pass open: unknown is never coded as yes.
"""

import copy
import hashlib
import json
import logging
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

REFRESH_DAYS = 30
HEADERS = {"User-Agent": "WeTalkALotCollector/1.0 (research use; python-requests)"}      # descriptive and generic: Wikimedia throttles anonymous browser-looking clients harder
LEMMY_API = "https://lemmy.world/api/v3/post/list"
RS_BASE = "https://recordsetter.com"
CHECK_BATCH = 30
SHOWER_PAGES = 12             # Lemmy pages of 50, best first
SHOWER_MIN_SCORE = 100
RS_MAX_PAGES = 220

# ------------------------------------------------------------------------------------------------ the model's yes/no (never a rewrite)
SHOWER_PROMPT = """You are sorting posts from a "shower thoughts" community. For each numbered post answer true ONLY if it is a genuine shower thought: a short, witty or thought-provoking observation about life, language, habits or the world that stands on its own and reads well as a single line.
Answer false for posts about Reddit, Lemmy, the fediverse, moderators, votes or the platform itself, questions asking for answers, announcements, rants, political slogans, complaints, plain facts with no twist, and anything that needs context to make sense.

Return ONLY a JSON object: {{"verdicts": [true, false, ...]}} with exactly {n} entries, in the order of the posts.

{items}"""

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


# ------------------------------------------------------------------------------------------------ parsers (pure functions, unit-tested)
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


# ------------------------------------------------------------------------------------------------ junk filters (operator 2026-11-21): rules, then a yes/no that never touches a word
_LIST_PAGE = re.compile(r"^\s*(?:lists? of|lists of|index of|category:|outline of|timeline of|glossary of)\b", re.I)
# the shortest text a card of this category may be, in full sentences (title excluded). Only where one-sentence items are junk: weird facts, conspiracies, unusual deaths. A dark-history event, a world record and a
# shower thought ARE one sentence by nature, so the rule is not applied to them (it would empty those libraries); jokes are not touched at all.
MIN_SENTENCES: dict[str, int] = {}          # the curated collector passes its own floor (2 sentences) explicitly
FILTERED_CATEGORIES = ["world_records", "shower_thoughts"]

AUDIENCE_PROMPT = """You are filtering items for a channel of dark, weird and funny things. For each numbered item answer true ONLY if a general audience would find it genuinely weird, dark, or funny, so that they would send it to a friend.
Answer false for dry, academic or encyclopedic facts, definitions, list or index entries, fragments that need context to make sense, questions put to the reader, ordinary trivia or news with no twist, and anything merely mildly interesting.
Judge the item exactly as written; you are only a filter and never suggest changes.

Return ONLY a JSON object: {{"verdicts": [true, false, ...]}} with exactly {n} entries, in the order of the items.

{items}"""


AUDIENCE_PROMPTS: dict[str, str] = {}      # a category-specific question (the dark-history one went with the Wikipedia events it was written for)


def _sentences(text: str) -> list[str]:
    from pipeline import verbatim
    out = []
    for para in str(text or "").split("\n\n"):
        out.extend(x for x in verbatim.split_sentences(para) if len(x.split()) >= 2)
    return out


def rule_reason(category: str, title: str, body: str, min_sentences: int | None = None) -> str | None:
    """Why this item is junk by RULE (None = it passes): a list / index / category page, too few full sentences for its category (`min_sentences` overrides the category's own floor: the curated sources
    use 2 for every category), or mostly a question put back to the reader."""
    if _LIST_PAGE.match(title or "") or _LIST_PAGE.match(body or ""):
        return "list, index or category page"
    sents = _sentences(body)
    floor = min_sentences if min_sentences is not None else MIN_SENTENCES.get(category, 1)
    if len(sents) < floor:
        return f"under {floor} full sentences"
    if sents and sum(1 for x in sents if x.rstrip().endswith("?")) * 2 >= len(sents):
        return "mostly a question back to the reader"
    return None


def audience_text(title: str, body: str) -> str:
    return f"{title}: {body}" if title else body


def audience_verdicts(conn, category: str, texts: list[str]) -> list[bool | None]:
    """DeepSeek's 'would a general audience find this genuinely weird, dark or funny?' per item. A FILTER: it only answers yes or no. None = no usable answer (never treated as yes)."""
    return yes_no(conn, category, AUDIENCE_PROMPTS.get(category, AUDIENCE_PROMPT), texts)


# ------------------------------------------------------------------------------------------------ storing
def _payload(item: dict, category: str, source_name: str, source_key: str) -> dict:
    p = {"title": item.get("title") or "", "body": item.get("body") or "", "source_name": source_name, "source_key": source_key, "source_url": item.get("source_url") or "", "library": category}
    if item.get("dateline"):
        p["dateline"] = item["dateline"]
    for k in ("year", "page", "score", "tags"):
        if item.get(k) is not None:
            p[k] = item[k]
    return p


def _store(conn, category: str, source_label: str, source_key: str, items: list[dict], judge=None, limit: int | None = None, audience: bool = True, min_sentences: int | None = None, curated: bool = False) -> dict:
    """Dedupe against what is stored, optionally ask the model (yes -> live, no -> held with the verdict, unknown -> nothing stored), insert. Returns the per-source counts.
    `curated` (operator 2026-11-21: editor-picked sources are already weird, dark or funny): structural filters only (dedupe, list pages, too short), never a model check; the rows are marked so that a later
    recheck_filters pass leaves them alone."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT payload->>'title' AS t, payload->>'body' AS b FROM raw_items WHERE category = %s", (category,))
        seen = {norm_key((r["t"] or "") + (r["b"] or "")) for r in cur.fetchall()}
        cur.execute("SELECT external_id FROM raw_items WHERE category = %s", (category,))
        known = {r["external_id"] for r in cur.fetchall()}
    fresh, dupes, rule_dropped = [], 0, {}
    for it in items:
        ext = it.get("ext") or ext_id(source_key, it.get("page") or "", it.get("title") or "", it.get("body") or "")
        k = norm_key((it.get("title") or "") + (it.get("body") or ""))
        if ext in known or k in seen:
            dupes += 1
            continue
        seen.add(k)
        why = rule_reason(category, it.get("title") or "", it.get("body") or "", min_sentences)
        if why:
            rule_dropped[why] = rule_dropped.get(why, 0) + 1          # junk by rule: not stored (nothing to ask the model about)
            continue
        pl = _payload(it, category, source_label, source_key)
        pl["norm_key"] = k
        if curated:
            pl["curated"] = True
            pl["audience_check"] = "skipped_curated"
        fresh.append((ext, pl))
    left = 0
    if limit and len(fresh) > limit:
        left, fresh = len(fresh) - limit, fresh[:limit]
    yes, no, unknown = [], [], 0
    first = judge([p["body"] for _, p in fresh]) if judge is not None else [True] * len(fresh)            # the category's own yes/no (dark? a real shower thought?) when it has one
    second = [None] * len(fresh)
    if audience:                                                                                         # then the audience check on everything that passed it
        ask = [i for i, v in enumerate(first) if v is True]
        got = audience_verdicts(conn, category, [audience_text(fresh[i][1]["title"], fresh[i][1]["body"]) for i in ask])
        for i, v in zip(ask, got):
            second[i] = v
    for n, ((ext, pl), v1) in enumerate(zip(fresh, first)):
        v2 = second[n] if audience else True
        if v1 is None or (v1 is True and v2 is None):
            unknown += 1                                                                                 # unknown is never coded as a yes: nothing stored, the pass stays open
            continue
        if v1 is True and v2 is True:
            pl["check"] = "yes"
            yes.append((ext, pl))
        else:
            pl["check"] = "no" if v1 is False else "audience_no"
            no.append((ext, pl))
    source_id = get_or_create_source(conn, category, f"wtal_{source_key}", "api", {"library": category})
    inserted = insert_raw_items_batch(conn, source_id, category, yes)
    if no:
        insert_raw_items_batch(conn, source_id, category, no, held=True)
    return {"fetched": len(items), "rule_dropped": rule_dropped, "new_checked": len(fresh), "duplicates_skipped": dupes, "stored_live": inserted, "no_held": len(no), "unknown_not_stored": unknown, "left_for_next_pass": left}


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


# ------------------------------------------------------------------------------------------------ the two collectors
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


def recheck_filters(conn, categories: list[str] | None = None, chunk: int = 90, passes: int = 3) -> dict:
    """ONE-OFF (2026-11-21): apply the junk rules and the audience yes/no to every LIVE item already stored in the phase-2 libraries, without fetching anything. A failing item is HELD (never deleted) with the
    reason on its payload (`filter_dropped` for a rule, `audience_check: no` for the model); a passing one is marked `audience_check: yes`. An item already sent as a card is left alone. A verdict the model could not
    give leaves the item unmarked and a repeat pass retries it; after `passes` passes whatever is still unknown is HELD (unknown is never coded as a yes). Resumable: marked items are skipped."""
    import json
    out = {}
    for cat in categories or FILTERED_CATEGORIES:
        res = {"live_before": 0, "dropped_by_rule": {}, "audience_no": 0, "audience_yes": 0, "unknown_held": 0, "sent_left_alone": 0}
        with dict_cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM raw_items r WHERE r.category = %s AND r.held = FALSE AND NOT EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))", (cat,))
            res["live_before"] = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM raw_items r WHERE r.category = %s AND r.held = FALSE AND EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))", (cat,))
            res["sent_left_alone"] = cur.fetchone()["n"]
        for _ in range(passes):
            with dict_cursor(conn) as cur:
                cur.execute("""SELECT r.id, r.payload FROM raw_items r WHERE r.category = %s AND r.held = FALSE AND r.payload->>'audience_check' IS NULL AND r.payload->>'filter_dropped' IS NULL
                               AND NOT EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids)) ORDER BY r.id""", (cat,))
                rows = cur.fetchall()
            if not rows:
                break
            unknown_left = 0
            for i in range(0, len(rows), chunk):
                part = rows[i:i + chunk]
                keep = []
                with dict_cursor(conn) as cur:
                    for r in part:
                        pl = r["payload"]
                        why = rule_reason(cat, pl.get("title") or "", pl.get("body") or "")
                        if why:
                            cur.execute("UPDATE raw_items SET held = TRUE, payload = payload || %s::jsonb WHERE id = %s", (json.dumps({"filter_dropped": why}), r["id"]))
                            res["dropped_by_rule"][why] = res["dropped_by_rule"].get(why, 0) + 1
                        else:
                            keep.append(r)
                verdicts = audience_verdicts(conn, cat, [audience_text(r["payload"].get("title") or "", r["payload"].get("body") or "") for r in keep])
                with dict_cursor(conn) as cur:
                    for r, v in zip(keep, verdicts):
                        if v is None:
                            unknown_left += 1
                            continue
                        cur.execute("UPDATE raw_items SET held = %s, payload = payload || %s::jsonb WHERE id = %s", (not v, json.dumps({"audience_check": "yes" if v else "no"}), r["id"]))
                        res["audience_yes" if v else "audience_no"] += 1
                conn.commit()
                logger.info("recheck_filters %s: %d/%d %s", cat, min(i + chunk, len(rows)), len(rows), res)
            if not unknown_left:
                break
        with dict_cursor(conn) as cur:                                       # whatever still has no verdict after the passes is held, never coded as a yes
            cur.execute("""UPDATE raw_items r SET held = TRUE, payload = payload || '{"audience_check": "unknown"}'::jsonb WHERE r.category = %s AND r.held = FALSE AND r.payload->>'audience_check' IS NULL
                           AND r.payload->>'filter_dropped' IS NULL AND NOT EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))""", (cat,))
            res["unknown_held"] = cur.rowcount
            cur.execute("SELECT COUNT(*) AS n FROM raw_items r WHERE r.category = %s AND r.held = FALSE AND NOT EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))", (cat,))
            res["live_after"] = cur.fetchone()["n"]
        conn.commit()
        out[cat] = res
    return out


COLLECTORS = {"world_records": collect_world_records, "shower_thoughts": collect_shower_thoughts}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    which = [a for a in sys.argv[1:] if not a.startswith("--")] or list(COLLECTORS)
    for c in which:
        print(c, COLLECTORS[c](force="--force" in sys.argv))
    sys.exit(0)
