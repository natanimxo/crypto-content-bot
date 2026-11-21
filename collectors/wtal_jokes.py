"""We Talk A Lot: the DARK-JOKES library (operator 2026-11-20). Collected in bulk once, refreshed monthly, drawn from at random without repeats (pipeline/select_candidates.py LIBRARY_CATEGORIES).

DARK ONLY (operator 2026-11-20, after the first card was a clean dad joke under a dark header): "Pull only from JokeAPI's Dark category and dark-joke sources. Drop Pun, Misc, Programming and Christmas.
Before adding to the library, use DeepSeek for a yes/no 'is this genuinely dark humor' check and keep only the yeses."
  JokeAPI Dark   every English joke in the Dark category (71 on 2026-11-20), swept by id range, ALL flags kept (nsfw, religious, political, racist, sexist, explicit): the operator decides what to post, the card shows them.
  DeepSeek       a yes/no per joke: does the humor depend on death, violence, illness, disability, abuse, war, crime, tragedy or another grim or taboo subject? It is NOT asked whether the joke is acceptable.
                 A "yes" is stored as a normal library item (payload.dark_check = "yes"). A "no" is stored HELD (never drawn) with dark_check = "no", so the next monthly pass does not ask again. A joke the model
                 could not rule on (outage, malformed answer) is NOT stored and the pass is not marked complete, so the next cycle tries again: unknown is never coded as a yes.
CANDIDATES for volume (probes added 2026-11-21, read from Railway before any is built): the taivop/joke-dataset files on GitHub (Reddit r/Jokes scrape, 195K, with scores; Wocka; StupidStuff) and Hugging Face FunnyData.
NOT used: icanhazdadjoke (clean dad jokes) and JokeAPI's Pun / Misc / Programming / Spooky / Christmas, chucknorris.io (~9,700 entries of one meme).
  Lemmy          c/jokes (315 posts) and c/darkhumor (12) on lemmy.world, through Lemmy's API (verified from Railway, run_logs `source_probe` 2026-11-20 14:37 UTC: 200, 50 and 12 posts). A post is a joke when its title is the
                 setup and its body (if any) the delivery; EVERY one goes through the same DeepSeek check (c/jokes is mostly clean jokes: only the yeses join the library). Posts with Markdown or a link in them are skipped (the
                 words would not be the plain text of a joke), as are removed, mod and over-long posts. nsfw is kept as a flag.

The words are the source's own and are stored untouched (a JokeAPI two-part joke keeps its setup and delivery as two parts so the delivery can be shown as its own paragraph).
DEDUPE on normalized text (lowercase letters and digits only), also against what is already stored. REFRESH: a full pass runs when no successful `collect_jokes` full pass exists in the last 30 days.
"""

import json
import logging
import re
import sys
import time

import requests
from dotenv import load_dotenv

from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "jokes"
JOB = "collect_jokes"
REFRESH_DAYS = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot)"}
JOKEAPI = "https://v2.jokeapi.dev"
JOKEAPI_CATEGORIES = "Dark"     # the operator's call: Dark only
CHECK_BATCH = 12

CHECK_VERSION = 2     # 2 = dark AND a clear punchline (operator 2026-11-21, after the first 1,500 Reddit jokes came back 81% "yes" with rambling posts and weak gags among them); 1 = dark only

DARK_PROMPT = """You are sorting jokes for a dark-humor channel. For each numbered joke answer true ONLY if BOTH of these hold:
(1) it is genuinely dark humor: the joke's humor depends on death, suicide, violence, illness, disability, abuse, war, atrocity, crime, tragedy or another grim or taboo subject;
(2) it has a clear punchline: a setup that builds to one specific, identifiable twist or payoff line that makes the joke land, understandable on its own.
Answer false for clean puns and wordplay, innocent or merely edgy-sounding jokes, innuendo with nothing grim in it, and absurd or silly gags with nothing dark about them.
Also answer false for anything that is not a finished joke: rambling stories and anecdotes, personal confessions, rants, bare statements or opinions or insults with no twist, observations, lists, riddles with no answer, text that is cut off, and jokes whose payoff depends on context that is missing.
Do not judge whether a joke is acceptable or offensive; judge ONLY whether it is dark humor with a clear punchline.

Return ONLY a JSON object: {{"verdicts": [true, false, ...]}} with exactly {n} entries, in the order of the jokes.

{jokes}"""


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def jokeapi_payload(j: dict) -> dict | None:
    flags = j.get("flags") or {}
    base = {"title": "", "source_name": "JokeAPI", "source_key": "jokeapi", "source_url": "https://jokeapi.dev", "joke_category": j.get("category"), "flags": {k: bool(v) for k, v in flags.items()}}
    if j.get("type") == "twopart":
        setup, delivery = (j.get("setup") or "").strip(), (j.get("delivery") or "").strip()
        if not setup or not delivery:
            return None
        return {**base, "body_parts": [setup, delivery], "body": setup + "\n\n" + delivery}
    joke = (j.get("joke") or "").strip()
    if not joke:
        return None
    return {**base, "body": joke}


def fetch_jokeapi() -> list[tuple[str, dict]]:
    info = requests.get(f"{JOKEAPI}/info", headers=HEADERS, timeout=25).json()["jokes"]
    lo, hi = info["idRange"]["en"]
    out = []
    for a in range(lo, hi + 1, 10):
        r = requests.get(f"{JOKEAPI}/joke/{JOKEAPI_CATEGORIES}", params={"lang": "en", "idRange": f"{a}-{min(a + 9, hi)}", "amount": 10}, headers=HEADERS, timeout=25)
        if r.status_code == 400 and (r.json() or {}).get("code") == 106:      # "No matching joke found": this id range has no Dark joke (found on Railway 2026-11-21: it answers HTTP 400, not an empty list)
            time.sleep(0.55)
            continue
        r.raise_for_status()
        j = r.json()
        for x in (j.get("jokes") or ([j] if "id" in j else [])):
            p = jokeapi_payload(x)
            if p:
                out.append((f"jokeapi:{x['id']}", p))
        time.sleep(0.55)   # JokeAPI allows 120 requests a minute
    return out


LEMMY = "https://lemmy.world/api/v3/post/list"
LEMMY_COMMUNITIES = [("jokes", 10), ("darkhumor", 2)]     # (community, pages of 50 posts, newest first)
_MARKDOWN = re.compile(r"(:::|\*\*|__|\]\(|!\[|^\s*>|^\s*#|```|https?://|^\s*[-*] |~~|\|)", re.M)
MAX_JOKE_CHARS = 600


def lemmy_payload(post: dict, community: str) -> dict | None:
    """A Lemmy post as a joke: the title is the setup (or the whole joke), the body the delivery. None for removed / mod / Markdown / link / over-long posts."""
    p = post.get("post") or {}
    title, body = (p.get("name") or "").strip(), (p.get("body") or "").strip()
    if p.get("removed") or p.get("deleted") or p.get("featured_community") or p.get("featured_local") or not title:
        return None
    if re.match(r"(?i)^(meta|rules?|reminder|announcement|weekly|monthly)\b", title) or _MARKDOWN.search(title + "\n" + body) or len(title) + len(body) > MAX_JOKE_CHARS:
        return None
    parts = [title] + [x.strip() for x in re.split(r"\n\s*\n", body) if x.strip()]
    return {"title": "", "body_parts": parts, "body": "\n\n".join(parts), "source_name": f"Lemmy c/{community}", "source_key": f"lemmy_{community}", "source_url": p.get("ap_id") or "",
            "joke_category": f"c/{community}", "flags": {"nsfw": bool(p.get("nsfw"))}, "published_at": p.get("published")}


def fetch_lemmy(community: str, pages: int) -> list[tuple[str, dict]]:
    out = []
    for page in range(1, pages + 1):
        r = requests.get(LEMMY, params={"community_name": community, "sort": "New", "limit": 50, "page": page}, headers=HEADERS, timeout=25)
        r.raise_for_status()
        posts = r.json().get("posts") or []
        for post in posts:
            pl = lemmy_payload(post, community)
            if pl:
                out.append((f"lemmy:{post['post']['id']}", pl))
        if len(posts) < 50:
            break
        time.sleep(0.5)
    return out


# BULK: the taivop/joke-dataset r/Jokes scrape (operator 2026-11-21: "68 jokes is too small"). One static JSON file of Reddit's r/Jokes (title = setup, body = delivery, score = upvotes). It is user-written text
# credited to r/Jokes with a link to the post; the repo calls the data "for research purposes" -- the operator posts jokes as they are with the source credited, so this is flagged to him, not hidden.
# THE FILTER, cheapest first: score >= 5 (a joke people upvoted), at most 400 characters, plain text only, [removed] / [deleted] / EDIT: lines out, then a KEYWORD pre-filter for grim subjects (a 65,000-joke file is mostly
# not dark, and the model is asked about the ones that could be), then the same DeepSeek yes/no as every other source. A dark joke with none of the keywords is lost: that is the price of not asking the model 65,000 times.
REDDIT_JOKES_URL = "https://raw.githubusercontent.com/taivop/joke-dataset/master/reddit_jokes.json"
REDDIT_MIN_SCORE = 5
REDDIT_ENABLED = True       # ON 2026-11-21: Railway's source_probe (j_taivop_reddit, 08:35 UTC) reached the file (206 in 6 s)
REDDIT_MAX_PER_PASS = 1500  # new candidates sent to the model per pass, highest score first; a truncated pass is not 'full', so the next cycle carries on (a first pass stays inside the 25-minute step limit)
_GRIM = re.compile(r"\b(dead|death|dies|died|dying|die|kill\w*|murder\w*|suicid\w*|cancer|rap(?:e|ed|ist|ing)|holocaust|nazi\w*|hitler|auschwitz|cemetery|funeral|coffin|grave|corpse|abort\w*|orphan\w*|dementia|alzheimer\w*|aids|cripple\w*|wheelchair|paraly\w*|genocide|slave\w*|stillborn|miscarriage|overdose|hanged|hanging|lynch\w*|9/11|massacre|molest\w*|pedophil\w*|cannibal\w*|amputee|decapitat\w*|executed|electric chair|chemo\w*|hospice|widow\w*|gas chamber|ovens?|terminal|bury|buried|autopsy|morgue|hearse|noose|cult|school shoot\w*|bomb\w*|terror\w*)\b", re.I)
_EDIT_LINE = re.compile(r"(?im)^\s*(edit|update)\b")


def reddit_payload(x: dict) -> dict | None:
    title, body = (x.get("title") or "").strip(), (x.get("body") or "").strip()
    text = title + "\n" + body
    if not title or (x.get("score") or 0) < REDDIT_MIN_SCORE or len(title) + len(body) > MAX_JOKE_CHARS - 200:
        return None
    if body.lower() in ("[removed]", "[deleted]") or _EDIT_LINE.search(text) or _MARKDOWN.search(text) or "&amp;" in text or "&#" in text:
        return None
    if not _GRIM.search(text):
        return None
    parts = [title] + [p.strip() for p in re.split(r"\r?\n", body) if p.strip()]
    return {"title": "", "body_parts": parts, "body": "\n\n".join(parts), "source_name": "Reddit r/Jokes", "source_key": "reddit_jokes", "source_url": f"https://www.reddit.com/comments/{x.get('id')}",
            "joke_category": "r/Jokes", "flags": {"nsfw": bool(re.search(r"nsfw", title, re.I))}, "reddit_score": x.get("score")}


def fetch_reddit_jokes() -> list[tuple[str, dict]]:
    r = requests.get(REDDIT_JOKES_URL, headers=HEADERS, timeout=(20, 180))
    r.raise_for_status()
    out = []
    for x in sorted(r.json(), key=lambda j: -(j.get("score") or 0)):
        pl = reddit_payload(x)
        if pl:
            out.append((f"reddit:{x['id']}", pl))
    return out


def dark_verdicts(conn, texts: list[str]) -> list[bool | None]:
    """DeepSeek's yes/no per joke, in order. None = no usable answer for that joke (never treated as yes)."""
    from pipeline import llm, llm_usage, post_format
    out: list[bool | None] = []
    with llm_usage.tagged("wtal_jokes_bulk"):          # a bulk pass makes one call per group by design: not a runaway (pipeline/llm_usage.tagged)
        _check_batches(conn, texts, out, llm, post_format)
    return out


def _check_batches(conn, texts, out, llm, post_format):
    for i in range(0, len(texts), CHECK_BATCH):
        batch = texts[i:i + CHECK_BATCH]
        numbered = "\n\n".join(f"{n + 1}. {t}" for n, t in enumerate(batch))
        try:
            parsed = post_format.parse_llm_json(llm.generate_write(conn, CATEGORY, DARK_PROMPT.format(n=len(batch), jokes=numbered)))
            v = parsed.get("verdicts") if isinstance(parsed, dict) else None
            if not isinstance(v, list) or len(v) != len(batch) or not all(isinstance(x, bool) for x in v):
                raise ValueError(f"unusable verdicts: {str(parsed)[:120]}")
            out.extend(v)
        except Exception:
            logger.exception("jokes: the dark check could not rule on a batch of %d", len(batch))
            out.extend([None] * len(batch))


def source_names() -> list[str]:
    return ["jokeapi"] + [f"lemmy_{c}" for c, _ in LEMMY_COMMUNITIES] + (["reddit_jokes"] if REDDIT_ENABLED else [])


def due(conn) -> bool:
    """A full pass is due when none succeeded in the last 30 days, or when the last one did not cover every source now configured (a source added since: it runs on the next cycle, not next month)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT details FROM run_logs WHERE job_name = %s AND status = 'success' AND details->>'full_pass' = 'true' AND started_at > now() - make_interval(days => %s) ORDER BY started_at DESC LIMIT 1", (JOB, REFRESH_DAYS))
        row = cur.fetchone()
    if row is None:
        return True
    return sorted((row["details"] or {}).get("sources") or []) != sorted(source_names())


def collect(force: bool = False) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        if not force and not due(conn):
            return 0
        with run_log(conn, JOB) as state:
            sources = [("jokeapi", fetch_jokeapi)] + [(f"lemmy_{c}", (lambda c=c, n=n: fetch_lemmy(c, n))) for c, n in LEMMY_COMMUNITIES]
            if REDDIT_ENABLED:
                sources.append(("reddit_jokes", fetch_reddit_jokes))
            fetched_by, failed = {}, []
            for name, fn in sources:
                try:
                    fetched_by[name] = fn()
                except Exception as exc:       # one source down never stops the others; none fetched raises below
                    failed.append(name)
                    logger.warning("jokes: %s failed: %s", name, exc)
                    state["details"].setdefault("errors", {})[name] = str(exc)[:120]
            if not fetched_by:
                raise RuntimeError("jokes: every source failed")
            with dict_cursor(conn) as cur:
                cur.execute("SELECT payload->>'body' AS b FROM raw_items WHERE category = %s", (CATEGORY,))
                seen = {norm_key(r["b"]) for r in cur.fetchall() if r["b"]}
                cur.execute("SELECT external_id FROM raw_items WHERE category = %s", (CATEGORY,))
                known_ids = {r["external_id"] for r in cur.fetchall()}
            per_source, inserted_total, unknown_total = {}, 0, 0
            for name, fetched in fetched_by.items():
                fresh, dupes = [], 0
                for ext, payload in fetched:
                    if ext in known_ids:
                        continue
                    k = norm_key(payload["body"])
                    if k in seen:
                        dupes += 1
                        continue
                    seen.add(k)
                    payload["norm_key"] = k
                    fresh.append((ext, payload))
                truncated = 0
                if name == "reddit_jokes" and len(fresh) > REDDIT_MAX_PER_PASS:
                    truncated = len(fresh) - REDDIT_MAX_PER_PASS
                    fresh = fresh[:REDDIT_MAX_PER_PASS]
                verdicts = dark_verdicts(conn, [p["body"] for _, p in fresh])
                yes, no, unknown = [], [], 0
                for (ext, payload), v in zip(fresh, verdicts):
                    if v is None:
                        unknown += 1
                        continue
                    payload["dark_check"] = "yes" if v else "no"
                    payload["check_version"] = CHECK_VERSION
                    (yes if v else no).append((ext, payload))
                source_id = get_or_create_source(conn, CATEGORY, f"wtal_{name}", "api", {"library": name})
                inserted = insert_raw_items_batch(conn, source_id, CATEGORY, yes)
                insert_raw_items_batch(conn, source_id, CATEGORY, no, held=True)
                inserted_total += inserted
                unknown_total += unknown + truncated
                per_source[name] = {"fetched": len(fetched), "new_checked": len(fresh), "duplicates_skipped": dupes, "dark_yes": len(yes), "dark_no_held": len(no), "unknown_not_stored": unknown, "left_for_next_pass": truncated}
            state["details"].update({"full_pass": unknown_total == 0 and not failed, "sources": source_names(), "failed": failed, "by_source": per_source, "inserted": inserted_total})
            logger.info("jokes: %s failed=%s inserted=%d", per_source, failed, inserted_total)
        return inserted_total
    finally:
        conn.close()


def recheck_library(conn) -> dict:
    """ONE-OFF (2026-11-20): bring the library stored before the dark-only ruling into line. Everything that is not JokeAPI Dark (dad jokes, Pun, Misc, Programming, Spooky, Christmas) is HELD, never deleted
    (an item that was ever notified must stay on record); every JokeAPI Dark joke that has not been checked goes through the DeepSeek check, a "no" is held."""
    done = {"held_not_dark_source": 0, "checked": 0, "yes": 0, "no_held": 0, "unknown": 0}
    with dict_cursor(conn) as cur:
        cur.execute("""UPDATE raw_items SET held = TRUE WHERE category = %s AND held = FALSE
                       AND NOT (payload->>'source_key' = 'jokeapi' AND payload->>'joke_category' = 'Dark')""", (CATEGORY,))
        done["held_not_dark_source"] = cur.rowcount
        cur.execute("SELECT id, payload FROM raw_items WHERE category = %s AND payload->>'source_key' = 'jokeapi' AND payload->>'joke_category' = 'Dark' AND payload->>'dark_check' IS NULL ORDER BY id", (CATEGORY,))
        rows = cur.fetchall()
    verdicts = dark_verdicts(conn, [r["payload"]["body"] for r in rows])
    with dict_cursor(conn) as cur:
        for r, v in zip(rows, verdicts):
            if v is None:
                done["unknown"] += 1
                continue
            done["checked"] += 1
            done["yes" if v else "no_held"] += 1
            cur.execute("UPDATE raw_items SET payload = payload || %s::jsonb, held = %s WHERE id = %s", (json.dumps({"dark_check": "yes" if v else "no", "check_version": CHECK_VERSION}), not v, r["id"]))
    conn.commit()
    return done


def recheck_v2(conn, limit: int | None = None, chunk: int = 96) -> dict:
    """ONE-OFF (2026-11-21): put every stored joke that was judged under the old, dark-only prompt (no payload.check_version) through the current prompt (dark AND a clear punchline), without fetching any file.
    A joke that passes is live (held = FALSE), one that fails is HELD, never deleted; the old verdict is kept in payload.dark_check_v1. A joke already sent as a card is left alone (it stays on record as it was).
    A verdict the model could not give leaves the row untouched and counted, so a repeat run retries it: unknown is never coded as a yes. Committed per chunk, so an interrupted run resumes."""
    done = {"checked": 0, "kept_live": 0, "newly_live": 0, "newly_held": 0, "stays_held": 0, "unknown": 0, "skipped_already_sent": 0}
    with dict_cursor(conn) as cur:
        cur.execute("""SELECT r.id, r.held, r.payload FROM raw_items r
                       WHERE r.category = %s AND r.payload->>'dark_check' IN ('yes', 'no') AND r.payload->>'check_version' IS NULL
                         AND NOT EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))
                       ORDER BY r.id""", (CATEGORY,))
        rows = cur.fetchall()
        cur.execute("""SELECT COUNT(*) AS n FROM raw_items r WHERE r.category = %s AND r.payload->>'dark_check' IN ('yes', 'no') AND r.payload->>'check_version' IS NULL
                         AND EXISTS (SELECT 1 FROM notifications n WHERE r.id = ANY(n.candidate_raw_item_ids))""", (CATEGORY,))
        done["skipped_already_sent"] = cur.fetchone()["n"]
    if limit:
        rows = rows[:limit]
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        verdicts = dark_verdicts(conn, [r["payload"]["body"] for r in part])
        with dict_cursor(conn) as cur:
            for r, v in zip(part, verdicts):
                if v is None:
                    done["unknown"] += 1
                    continue
                done["checked"] += 1
                if v:
                    done["newly_live" if r["held"] else "kept_live"] += 1
                else:
                    done["newly_held" if not r["held"] else "stays_held"] += 1
                cur.execute("UPDATE raw_items SET payload = payload || %s::jsonb, held = %s WHERE id = %s",
                            (json.dumps({"dark_check": "yes" if v else "no", "check_version": CHECK_VERSION, "dark_check_v1": r["payload"].get("dark_check")}), not v, r["id"]))
        conn.commit()
        logger.info("jokes recheck_v2: %d/%d %s", min(i + chunk, len(rows)), len(rows), done)
    return done


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect(force="--force" in sys.argv)
    sys.exit(0)
