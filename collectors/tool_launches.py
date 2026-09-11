"""Tool/SaaS/AI launches collector (Phase 3, 2026-09-12) -- Hustle to Million.
Builder-audience channel, not crypto -- different voice, different notion of
"notable" from every other category so far.

Deliberate spec deviation, operator-approved: Product Hunt dropped entirely,
not parked as a "later" source. Their API requires an OAuth/developer token
from a registered account (can't create that myself), and their ToS
explicitly prohibits commercial use of the API -- these channels generate
revenue, so that's a real disqualifier, not something to reason around or
leave as a someday-maybe.

SOURCES, both verified live before building against them:
- Hacker News (Algolia API) -- free, keyless. `search_by_date?tags=show_hn`
  for community-self-tagged launches. HN's own tagging is real curation
  (unlike RemoteOK's tag system) -- verified the actual engagement
  distribution live rather than assuming a floor: 100 real Show HN posts
  from a 48h window had a MEDIAN of 2 points and 0 comments -- most Show HN
  posts get essentially no traction. MIN_HN_POINTS/MIN_HN_COMMENTS below are
  grounded in that real distribution, not guessed.
- GitHub Trending -- no official API (GitHub never shipped one); the
  unauthenticated search-API workaround is rate-limited to 10 req/min
  *shared across all unauthenticated GitHub API use from the same IP*,
  confirmed exhausted just from this session's own `gh` CLI use elsewhere
  -- too fragile to build on. Scraping github.com/trending directly instead
  (verified live, real HTML). Found the field that actually matters for
  "trending": each card exposes "N stars today" (velocity), distinct from
  total star count -- that's the signal used here, not raw popularity.

CROSS-SOURCE BONUS (operator-corrected, 2026-09-12): a launch appearing on
both HN and GitHub Trending the same day usually means real momentum, but
can also be one coordinated launch-day push -- the same shape of structural
false positive hit repeatedly on gems_security (is_mintable, Syrup, osETH,
Pendle: one real signal treated as strong evidence when it's actually weak
on its own). Kept MILD here (a modest score bump, not a strong multiplier)
rather than repeating that mistake a category later.

Noise floor, not a full relevance gate (operator-approved) -- both sources
are already meaningfully curated at the source (HN's own Show HN tagging,
GitHub's own trending algorithm), unlike RemoteOK's noisy tag system.

HOLD: every row this collector inserts is held=True unconditionally, same
as gems_security/news -- nothing notifies until the Hetzner poller is
verified (operator direction, ongoing through Phase 3).
"""

import logging
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "tool_launches"

HN_ALGOLIA_URL = "http://hn.algolia.com/api/v1/search_by_date"
GITHUB_TRENDING_URL = "https://github.com/trending"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_AGE_HOURS = 48

# Live-checked 2026-09-12 against a real 48h/100-post Show HN sample: median
# was 2 points, 0 comments -- most Show HN posts get essentially no
# traction. This floor (~18/100 real posts cleared it in the same sample)
# keeps genuine standouts, not just anything self-tagged "Show HN".
MIN_HN_POINTS = 5
MIN_HN_COMMENTS = 2

GITHUB_REPO_URL_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)", re.IGNORECASE)
# The actual bonus MAGNITUDE (deliberately mild -- see module docstring)
# lives in pipeline/score.py alongside the rest of the scoring logic; this
# collector only records the raw fact (also_trending_on_github /
# also_shown_on_hn below), same separation as every other category --
# collectors capture data, pipeline/score.py decides what it's worth.


def _extract_github_repo(url: str | None) -> str | None:
    if not url:
        return None
    m = GITHUB_REPO_URL_RE.search(url)
    return m.group(1).rstrip("/").lower() if m else None


def fetch_hn_show_hn() -> list[dict]:
    since = int(time.time()) - MAX_AGE_HOURS * 3600
    body = get_json(HN_ALGOLIA_URL, params={
        "tags": "show_hn",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": 200,
    })
    return body.get("hits", [])


def fetch_github_trending() -> list[dict]:
    resp = requests.get(GITHUB_TRENDING_URL, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    repos = []
    for article in soup.select("article.Box-row"):
        link = article.select_one("h2 a")
        if not link:
            continue
        full_name = (link.get("href") or "").strip("/").lower()
        if not full_name or "/" not in full_name:
            continue
        desc_el = article.select_one("p")
        lang_el = article.select_one("[itemprop=programmingLanguage]")
        stars_today_el = article.select_one("span.d-inline-block.float-sm-right")
        stars_today = 0
        if stars_today_el:
            m = re.search(r"([\d,]+)", stars_today_el.get_text())
            if m:
                stars_today = int(m.group(1).replace(",", ""))
        total_stars_el = article.select_one('a[href$="/stargazers"]')
        total_stars = 0
        if total_stars_el:
            m = re.search(r"([\d,]+)", total_stars_el.get_text())
            if m:
                total_stars = int(m.group(1).replace(",", ""))
        repos.append({
            "full_name": full_name,
            "description": desc_el.get_text(strip=True) if desc_el else "",
            "language": lang_el.get_text(strip=True) if lang_el else "",
            "stars_today": stars_today,
            "total_stars": total_stars,
        })
    return repos


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    inserted = 0
    try:
        with run_log(conn, "collect_tool_launches") as state:
            try:
                hn_hits = fetch_hn_show_hn()
            except Exception:
                logger.exception("tool_launches: failed to fetch HN Show HN")
                hn_hits = []
            try:
                github_repos = fetch_github_trending()
            except Exception:
                logger.exception("tool_launches: failed to fetch GitHub Trending")
                github_repos = []

            state["details"]["hn_fetched"] = len(hn_hits)
            state["details"]["github_fetched"] = len(github_repos)

            github_repo_names = {r["full_name"] for r in github_repos}

            items = []
            hn_filtered = 0
            for hit in hn_hits:
                points = hit.get("points") or 0
                comments = hit.get("num_comments") or 0
                if points < MIN_HN_POINTS and comments < MIN_HN_COMMENTS:
                    hn_filtered += 1
                    continue
                title = (hit.get("title") or "").removeprefix("Show HN: ").removeprefix("Show HN:").strip()
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                repo = _extract_github_repo(hit.get("url"))
                also_trending = repo in github_repo_names if repo else False

                payload = {
                    "title": title,
                    "topic_key": f"hn:{hit.get('objectID')}",
                    "source": "hackernews",
                    "url": url,
                    "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "points": points,
                    "comments": comments,
                    "created_at": datetime.fromtimestamp(hit.get("created_at_i", time.time()), tz=timezone.utc).isoformat(),
                    "also_trending_on_github": also_trending,
                }
                items.append((f"hn:{hit.get('objectID')}", payload))

            state["details"]["hn_filtered"] = hn_filtered

            hn_repo_names = set()
            for hit in hn_hits:
                r = _extract_github_repo(hit.get("url"))
                if r:
                    hn_repo_names.add(r)

            for repo in github_repos:
                if repo["stars_today"] <= 0:
                    continue
                also_on_hn = repo["full_name"] in hn_repo_names
                payload = {
                    "title": repo["full_name"],
                    "topic_key": f"github:{repo['full_name']}",
                    "source": "github_trending",
                    "url": f"https://github.com/{repo['full_name']}",
                    "description": repo["description"],
                    "language": repo["language"],
                    "stars_today": repo["stars_today"],
                    "total_stars": repo["total_stars"],
                    "also_shown_on_hn": also_on_hn,
                }
                items.append((f"github:{repo['full_name']}", payload))

            source_id = get_or_create_source(conn, CATEGORY, "hn_and_github_trending", "api+scrape",
                                              {"hn": HN_ALGOLIA_URL, "github": GITHUB_TRENDING_URL})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=True)
            state["details"]["candidates"] = len(items)
            state["details"]["inserted"] = inserted

            logger.info(
                "tool_launches: hn_fetched=%d hn_filtered=%d github_fetched=%d candidates=%d inserted=%d",
                len(hn_hits), hn_filtered, len(github_repos), len(items), inserted,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
