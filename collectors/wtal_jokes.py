"""We Talk A Lot: the jokes LIBRARY (operator 2026-11-20). Collected in bulk once, refreshed monthly, drawn from at random without repeats (pipeline/select_candidates.py LIBRARY_CATEGORIES).

Sources, verified from Railway (pipeline/probe_wtal.py, run_logs `source_probe` 2026-11-20 10:36 UTC):
  JokeAPI           every English joke in every category (Misc, Programming, Pun, Dark, Spooky, Christmas) with ALL flags kept (nsfw, religious, political, racist, sexist, explicit) -- the operator decides
                    what to post, the card shows the flags. Swept by id range (deterministic, 319 English jokes), not by random draws.
  icanhazdadjoke    the whole searchable set (744), paged.
NOT used: chucknorris.io (reachable, ~9,700 entries -- one meme would be nine of every ten draws), the Lemmy joke communities (4 items), Reddit (429 from Railway).

The words are the source's own and are stored untouched (a JokeAPI two-part joke keeps its setup and delivery as two parts so the delivery can be spoilered).
DEDUPE across sources on normalized text (lowercase letters and digits only), also against what is already stored. Each item keeps its source and, for JokeAPI, its category and flags.
REFRESH: a full pass runs when no successful `collect_jokes` full pass exists in the last 30 days; every other cycle is a no-op. A failed source does not fail the run unless nothing at all was fetched.
"""

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
DAD = "https://icanhazdadjoke.com/search"


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
        r = requests.get(f"{JOKEAPI}/joke/Any", params={"lang": "en", "idRange": f"{a}-{min(a + 9, hi)}", "amount": 10}, headers=HEADERS, timeout=25)
        r.raise_for_status()
        j = r.json()
        for x in (j.get("jokes") or ([j] if "id" in j else [])):
            p = jokeapi_payload(x)
            if p:
                out.append((f"jokeapi:{x['id']}", p))
        time.sleep(0.55)   # JokeAPI allows 120 requests a minute
    return out


def fetch_dad() -> list[tuple[str, dict]]:
    out, page = [], 1
    while True:
        r = requests.get(DAD, params={"limit": 30, "page": page}, headers={**HEADERS, "Accept": "application/json"}, timeout=25)
        r.raise_for_status()
        j = r.json()
        for x in j.get("results", []):
            text = (x.get("joke") or "").strip()
            if text:
                out.append((f"dad:{x['id']}", {"title": "", "body": text, "source_name": "icanhazdadjoke", "source_key": "dad", "source_url": f"https://icanhazdadjoke.com/j/{x['id']}", "joke_category": "Dad"}))
        if page >= int(j.get("total_pages") or 1):
            return out
        page += 1
        time.sleep(0.3)


def due(conn) -> bool:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT 1 FROM run_logs WHERE job_name = %s AND status = 'success' AND details->>'full_pass' = 'true' AND started_at > now() - make_interval(days => %s) LIMIT 1", (JOB, REFRESH_DAYS))
        return cur.fetchone() is None


def collect(force: bool = False) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        if not force and not due(conn):
            return 0
        with run_log(conn, JOB) as state:
            fetched, failed = {}, []
            for name, fn in (("jokeapi", fetch_jokeapi), ("dad", fetch_dad)):
                try:
                    fetched[name] = fn()
                except Exception as exc:
                    failed.append(name)
                    logger.warning("jokes: %s failed: %s", name, exc)
                    state["details"].setdefault("errors", {})[name] = str(exc)[:120]
            if not fetched:
                raise RuntimeError("jokes: every source failed")
            with dict_cursor(conn) as cur:
                cur.execute("SELECT payload->>'body' AS b FROM raw_items WHERE category = %s", (CATEGORY,))
                seen = {norm_key(r["b"]) for r in cur.fetchall() if r["b"]}
            inserted, dupes, by_source = 0, 0, {}
            for name, items in fetched.items():
                fresh = []
                for ext, payload in items:
                    k = norm_key(payload["body"])
                    if k in seen:
                        dupes += 1
                        continue
                    seen.add(k)
                    payload["norm_key"] = k
                    fresh.append((ext, payload))
                source_id = get_or_create_source(conn, CATEGORY, f"wtal_{name}", "api", {"library": name})
                n = insert_raw_items_batch(conn, source_id, CATEGORY, fresh)
                inserted += n
                by_source[name] = {"fetched": len(items), "inserted": n}
            state["details"].update({"full_pass": not failed, "failed": failed, "by_source": by_source, "duplicates_skipped": dupes, "inserted": inserted})
            logger.info("jokes: %s duplicates=%d inserted=%d failed=%s", by_source, dupes, inserted, failed)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect(force="--force" in sys.argv)
    sys.exit(0)
