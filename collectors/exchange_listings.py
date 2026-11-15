"""Exchange listings collector (Phase 4, 2026-11-15) -- new SPOT token listings announced by four
exchanges, with the one fact a reader cannot get from the announcement itself: whether the token is
ALREADY listed on another tracked exchange, from our own record of every listing the four have announced.

Operator-approved scope (2026-11-15):
  * Crypto Wall Street only (a market-moving event, written in the market-desk voice).
  * Binance, OKX, Bybit, KuCoin -- spot token listings only. Perpetuals, futures, margin, tokenized
    stocks, equity perps, collateral additions, new trading PAIRS for a token an exchange already lists,
    delistings and migrations are all excluded by a deliberately STRICT title filter; if it is too strict
    it shows up as zero volume and gets loosened, not the other way round.
  * Upbit and Coinbase are deliberately NOT here yet (Coinbase's blog is 403 to scripts, Upbit's API
    failed certificate verification from the dev machine -- retry Upbit from Railway once these four have
    a track record; Korean listings move prices harder than most).
  * No price, no market data. A stale number reads as a call and nothing here makes one safe.

Sources (all keyless public endpoints, live-checked 2026-11-15):
  Binance  www.binance.com/bapi/composite/v1/public/cms/article/list/query?catalogId=48  (New Cryptocurrency Listing)
  OKX      www.okx.com/api/v5/support/announcements?annType=announcements-new-listings
  Bybit    api.bybit.com/v5/announcements/index?type=new_crypto
  KuCoin   api.kucoin.com/api/v3/announcements?annType=new-listings (startTime reaches back a year)

THE PRIOR-LISTING FACT. Every stored listing carries `prior_listings`: earlier announcements, on ANY tracked
exchange, of the same token, computed against everything in the database plus the batch being inserted, ordered
by announcement time -- so a first run over a year of history gets it right, and later runs only ever add
items that are newer than what they compare against. Identity is the normalised token NAME, or the SYMBOL when
it is at least 3 characters (or when a side has no name): Binance calls Gensyn "AIGENSYN" and OKX calls it "AI",
so symbol alone is not enough, and a 2-letter symbol alone is not safe. It is a match on our record, never a claim
about the token's whole history: `record_since` says how far back each exchange's record goes (KuCoin's
announcement API only reaches back about a year), and the post is forbidden from calling anything a first
listing "anywhere".

Run `python -m collectors.exchange_listings --backfill` once to load the history (rows older than the 24h
freshness window can never become candidates; they exist only so the prior-listing fact has a past).
"""

import logging
import re
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from pipeline import llm_usage
from pipeline.db import dict_cursor, get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "exchange_listings"
SOURCE_NAME = "exchange_announcements"
HOLD_ALL_CANDIDATES = False

DISPLAY = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit", "kucoin": "KuCoin"}
EXCHANGES = list(DISPLAY)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crypto-content-bot)", "Accept": "application/json"}

BINANCE_LIST_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
BINANCE_DETAIL_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
BINANCE_ARTICLE_URL = "https://www.binance.com/en/support/announcement/detail/{code}"
# Hosts, tried in order. Found 2026-11-15 from the FIRST Railway cycle (the dev machine saw different data):
#  * OKX answers by IP region. From Railway's US address, www.okx.com serves the OKX-US feed (en-us URLs, newest item 23
#    days old, a different set of listings); my.okx.com (the EEA endpoint) serves the global feed from anywhere.
#  * Bybit returns 403 to cloud/US addresses on api.bybit.com; its alternate API domains are tried after it.
OKX_URLS = ["https://my.okx.com/api/v5/support/announcements", "https://www.okx.com/api/v5/support/announcements"]
BYBIT_URLS = ["https://api.bybit.com/v5/announcements/index", "https://api.bytick.com/v5/announcements/index",
              "https://api.bybit.nl/v5/announcements/index"]
KUCOIN_URL = "https://api.kucoin.com/api/v3/announcements"
KUCOIN_ARTICLE_URL = "https://www.kucoin.com/announcement/{slug}"

RECENT_PAGES = 2             # pages per exchange per cycle (a day's announcements fit on one; two survive an outage)
BACKFILL_DAYS = 730
BACKFILL_MAX_PAGES = 60
CALL_DELAY_SECONDS = 0.5
TRADING_START_LOOKBACK_HOURS = 48   # only fetch a Binance article body for announcements this recent
DUPLICATE_WINDOW_DAYS = 30           # same exchange + same token inside this window = a re-announcement
STALE_SOURCE_DAYS = 7               # an exchange whose NEWEST announcement (any kind) is older is dead, not quiet

# ---------------------------------------------------------------- strict title filter

# Anything that is not a spot listing of a token. Checked against the whole title; one hit rejects it.
_NEGATIVE_RE = re.compile(
    r"perp|futures|margin|tokenized|\bstocks?\b|\bequity\b|tradfi|delist|\bconvert|x-perp|\boptions?\b|\bswaps?\b|"
    r"collateral|leverage|\betfs?\b|\bearn\b|\bloans?\b|bnpl|launchpool|splash|bstock|trading pairs?|trading bots?|"
    r"\bpre-?market|\bmigrat|\bswap\b|\bredenominat|\bairdrop(?!s?:)",
    re.IGNORECASE)

_PAIR_RE = re.compile(r"([^()]+?)\s*\(([^()]+)\)")      # "Name (SYM)"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _tokens_from_name_sym_list(segment: str) -> list[tuple[str, str]]:
    """'Genius Terminal (GENIUS) and OpenGradient (OPG)' -> [('Genius Terminal','GENIUS'), ('OpenGradient','OPG')]."""
    out = []
    for m in _PAIR_RE.finditer(segment):
        name = re.sub(r"^(?:and|,)\s+", "", _clean(m.group(1)), flags=re.IGNORECASE).strip(" ,")
        sym = _clean(m.group(2))
        if name and sym and " " not in sym:
            out.append((name, sym))
    return out


def parse_listing(exchange: str, title: str) -> list[dict]:
    """The spot token listings in one announcement title: [{"name","symbol","flags"}], or [] when the title is not
    a spot listing. Strict on purpose -- see the module docstring."""
    t = _clean(title)
    if not t or _NEGATIVE_RE.search(t):
        return []
    flags: list[str] = []
    tokens: list[tuple[str, str]] = []

    if exchange == "binance":
        m = re.match(r"^Binance Will List (?P<rest>.+)$", t, re.IGNORECASE)
        if not m:
            return []
        rest = m.group("rest")
        if re.search(r"seed tag", rest, re.IGNORECASE):
            flags.append("Seed Tag applied")
        if re.search(r"zero trading fee", rest, re.IGNORECASE):
            flags.append("zero-fee promotion")
        head = re.split(r"\s+(?:with Seed Tag|and Introduce|on Binance|Introduce)\b", rest, maxsplit=1, flags=re.IGNORECASE)[0]
        tokens = _tokens_from_name_sym_list(head)
        if not tokens:
            bare = re.match(r"^([A-Za-z0-9]{2,12})$", head.strip())
            if bare:                                  # "Binance Will List RWUSD and Introduce ..."
                tokens = [(bare.group(1), bare.group(1))]

    elif exchange == "okx":
        m = re.match(r"^OKX (?:to|will) list (?P<rest>.+?) for spot trading\b", t, re.IGNORECASE)
        if not m:
            return []
        rest = m.group("rest")
        # "CT/USDT (Concrete)", "MEGA (MegaETH)", "USDS", or a comma/and separated run of those
        for part in re.split(r",\s*|\s+and\s+", rest):
            pm = re.match(r"^\s*([A-Za-z0-9.\-]{1,15})(?:/[A-Za-z0-9]+)?(?:\s*\(([^()]+)\))?\s*$", part)
            if pm:
                sym = pm.group(1)
                tokens.append((_clean(pm.group(2) or sym), sym))

    elif exchange == "bybit":
        m = re.match(r"^Bybit to list (?P<rest>.+?)\s+on\s+spot\s*$", t, re.IGNORECASE)
        if not m:
            return []
        tokens = _tokens_from_name_sym_list(m.group("rest"))

    elif exchange == "kucoin":
        if re.match(r"^KuCoin\b", t, re.IGNORECASE):          # "KuCoin Futures ...", "KuCoin Spot to List Tokenized ..."
            return []
        m = re.match(r"^(?P<pre>(?:World Premiere|HODLer Airdrops):\s*)?(?P<rest>.+?)\s+(?:World Premiere Listing on KuCoin|Listed on KuCoin|Listing on KuCoin)\s*$",
                     t, re.IGNORECASE)
        if not m:
            return []
        if m.group("pre") and "world premiere" in m.group("pre").lower() or "world premiere" in t.lower():
            flags.append("World Premiere (KuCoin's own label)")
        if "hodler airdrops" in t.lower():
            flags.append("HODLer Airdrops")
        tokens = _tokens_from_name_sym_list(m.group("rest"))

    return [{"name": n, "symbol": s, "flags": list(flags)} for n, s in tokens]


# ---------------------------------------------------------------- token identity

_GENERIC_WORDS = {"protocol", "network", "finance", "labs", "token", "coin", "foundation", "dao"}


def _norm_name(name: str) -> str:
    words = re.sub(r"[^\w\s]", "", (name or "").lower()).split()
    words = [w for w in words if w not in _GENERIC_WORDS] or words
    return "".join(words)


def same_token(a: dict, b: dict) -> bool:
    """a/b: dicts with `token_name` (or `name`) and `symbol`. See the module docstring for why name first, symbol
    only when safe."""
    na = _norm_name(a.get("token_name") or a.get("name"))
    nb = _norm_name(b.get("token_name") or b.get("name"))
    sa, sb = (a.get("symbol") or "").upper(), (b.get("symbol") or "").upper()
    # a name that is just the symbol (OKX "USDS for spot trading") carries no name information
    if na and nb and na == nb and len(na) >= 3 and na not in (sa.lower(), sb.lower()):
        return True
    if sa and sa == sb:
        names_known = na and nb and na != sa.lower() and nb != sb.lower()
        return len(sa) >= 3 or not names_known
    return False


# ---------------------------------------------------------------- fetchers
# Each returns [{"id","title","ts_ms","url","desc"}], newest first, for `pages` pages. A fetcher raises on a
# failed call; collect() isolates it per exchange.

def _binance_page(page: int, size: int) -> list[dict]:
    d = get_json(BINANCE_LIST_URL, params={"type": 1, "catalogId": 48, "pageNo": page, "pageSize": size}, headers=HEADERS)
    arts = ((d.get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
    return [{"id": a["code"], "title": a["title"], "ts_ms": int(a["releaseDate"]),
             "url": BINANCE_ARTICLE_URL.format(code=a["code"]), "desc": ""} for a in arts]


def _first_working(urls: list[str], params: dict):
    """GET the first host that answers; raise the last error if none does."""
    last = None
    for url in urls:
        try:
            return get_json(url, params=params, headers=HEADERS)
        except Exception as exc:
            last = exc
            logger.warning("exchange_listings: %s failed (%s), trying the next host", url, exc)
    raise last


def _okx_canonical(url: str) -> str:
    """One id per announcement whichever regional host served it: .../en-us/help/<slug>, .../en-eu/help/<slug> and
    .../help/<slug> are the same announcement."""
    m = re.search(r"/help/([^/?#]+)", url or "")
    return f"https://www.okx.com/help/{m.group(1)}" if m else url


def _okx_page(page: int, size: int) -> tuple[list[dict], int]:
    d = _first_working(OKX_URLS, {"annType": "announcements-new-listings", "page": page})
    block = (d.get("data") or [{}])[0]
    rows = [{"id": _okx_canonical(a["url"]), "title": a["title"], "ts_ms": int(a["pTime"]), "url": _okx_canonical(a["url"]), "desc": ""}
            for a in block.get("details") or []]
    return rows, int(block.get("totalPage") or 1)


def _bybit_page(page: int, size: int) -> list[dict]:
    d = _first_working(BYBIT_URLS, {"locale": "en-US", "type": "new_crypto", "limit": size, "page": page})
    return [{"id": a.get("url") or a["title"], "title": a["title"], "ts_ms": int(a["dateTimestamp"]),
             "url": a.get("url") or "", "desc": a.get("description") or ""} for a in (d.get("result") or {}).get("list") or []]


def _kucoin_page(page: int, size: int, start_ms: int | None) -> list[dict]:
    params = {"currentPage": page, "pageSize": size, "annType": "new-listings", "lang": "en_US"}
    if start_ms:
        params.update({"startTime": start_ms, "endTime": int(time.time() * 1000)})
    d = get_json(KUCOIN_URL, params=params, headers=HEADERS)
    return [{"id": str(a["annId"]), "title": a["annTitle"], "ts_ms": int(a["cTime"]),
             "url": a.get("annUrl") or "", "desc": a.get("annDesc") or ""} for a in (d.get("data") or {}).get("items") or []]


def fetch_exchange(exchange: str, *, backfill: bool) -> list[dict]:
    """All announcements for one exchange: the recent pages, or the whole history when backfilling."""
    pages = BACKFILL_MAX_PAGES if backfill else RECENT_PAGES
    size = 50
    cutoff_ms = int((time.time() - BACKFILL_DAYS * 86400) * 1000)
    start_ms = cutoff_ms if backfill else None
    out: list[dict] = []
    total_pages = pages
    for page in range(1, pages + 1):
        if exchange == "binance":
            rows = _binance_page(page, size)
        elif exchange == "okx":
            rows, tp = _okx_page(page, size)
            total_pages = min(pages, tp)
        elif exchange == "bybit":
            rows = _bybit_page(page, size)
        else:
            rows = _kucoin_page(page, size, start_ms)
        out += rows
        if not rows or page >= total_pages or (backfill and min(r["ts_ms"] for r in rows) < cutoff_ms) \
                or (exchange in ("binance", "bybit", "kucoin") and len(rows) < size):
            break
        time.sleep(CALL_DELAY_SECONDS)
    return out


# ---------------------------------------------------------------- trading start time

_UTC_DT_RE = re.compile(r"(20\d\d-\d\d-\d\d)\s+(\d\d:\d\d)(?::\d\d)?\s*(?:\(UTC\)|UTC)")
_KUCOIN_DESC_RE = re.compile(r"Trading:\s*(\d{1,2}:\d\d)\s+on\s+([A-Z][a-z]+ \d{1,2}, 20\d\d)\s*\(UTC\)")


def _binance_trading_start(code: str) -> str | None:
    """ISO UTC time the announcement says trading opens, or None. Never guessed: the body is rich-text JSON, so the
    text nodes are flattened and the FIRST '<date> <time> (UTC)' after the word 'trading' is taken."""
    try:
        d = get_json(BINANCE_DETAIL_URL, params={"articleCode": code}, headers=HEADERS)
        body = str((d.get("data") or {}).get("body") or "")
    except Exception as exc:
        logger.warning("exchange_listings: binance detail fetch failed for %s: %s", code, exc)
        return None
    text = " ".join(re.findall(r'"text":"((?:[^"\\]|\\.)*)"', body))
    # Anchored on the sentence Binance always uses ("...will list X (SYM) and open trading for the following spot
    # trading pairs at 2026-10-30 10:00 (UTC)"). No match -> None: a withdrawal or algo-order time is NOT the trading
    # start, so there is deliberately no looser fallback.
    m = re.search(r"open trading[^.]{0,160}?\bat\s+" + _UTC_DT_RE.pattern, text, re.IGNORECASE)
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2)}:00+00:00"


def _kucoin_trading_start(desc: str) -> str | None:
    m = _KUCOIN_DESC_RE.search(desc or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(2)} {m.group(1)}", "%B %d, %Y %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.isoformat()


# ---------------------------------------------------------------- build items

def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def build_candidates(exchange: str, announcements: list[dict]) -> list[dict]:
    """One candidate per (announcement, token) that passes the strict filter. Not yet enriched with prior listings."""
    out = []
    for a in announcements:
        for tok in parse_listing(exchange, a["title"]):
            out.append({
                "external_id": f"{exchange}:{a['id']}:{tok['symbol']}".lower(),
                "exchange_key": exchange, "exchange": DISPLAY[exchange],
                "token_name": tok["name"], "symbol": tok["symbol"], "flags": tok["flags"],
                "announcement_title": _clean(a["title"]), "announcement_id": a["id"],
                "ts_ms": a["ts_ms"], "published_at": _iso(a["ts_ms"]),
                "announcement_url": a["url"], "desc": a.get("desc") or "",
            })
    return out


def attach_prior_listings(candidates: list[dict], known: list[dict]) -> None:
    """Fills `prior_listings` / `prior_count` on every candidate, in place. `known` = every listing already in the
    database (dicts with exchange_key/exchange/token_name/symbol/ts_ms/published_at). Compared against known +
    the other candidates, strictly earlier by announcement time."""
    universe = list(known) + [c for c in candidates if c["external_id"] not in {k.get("external_id") for k in known}]
    for c in candidates:
        priors = [u for u in universe
                  if u["ts_ms"] < c["ts_ms"] and u.get("external_id") != c["external_id"] and same_token(c, u)]
        priors.sort(key=lambda u: u["ts_ms"])
        # one line per exchange: the earliest announcement there is what matters
        seen, lines = set(), []
        for u in priors:
            if u["exchange_key"] in seen:
                continue
            seen.add(u["exchange_key"])
            lines.append({"exchange": u["exchange"], "exchange_key": u["exchange_key"],
                          "announced_at": u["published_at"], "symbol": u["symbol"], "token_name": u["token_name"]})
        c["prior_listings"] = lines
        c["prior_count"] = len(lines)
        # The same exchange announcing the same token again within DUPLICATE_WINDOW_DAYS is a re-announcement
        # (Bybit published "Bybit to List KOR Protocol" twice in two days), not a second listing.
        window = DUPLICATE_WINDOW_DAYS * 86_400_000
        c["duplicate"] = any(u["exchange_key"] == c["exchange_key"] and same_token(c, u) and (
            # an earlier announcement by the same exchange ...
            0 < c["ts_ms"] - u["ts_ms"] <= window
            # ... or a row we ALREADY STORE for it, whichever came first: the same announcement can reach us under
            # a different id (a regional host's URL), and the stored row wins
            or (u.get("stored") and abs(c["ts_ms"] - u["ts_ms"]) <= window)) for u in universe)
        # ...and it must not count as a "prior listing" of itself on the same exchange for later comparisons either


def _payload(c: dict, record_since: dict[str, str], trading_start: str | None) -> dict:
    return {
        "title": f"{c['exchange']} lists {c['token_name']} ({c['symbol']})" if c["token_name"].upper() != c["symbol"].upper()
                 else f"{c['exchange']} lists {c['symbol']}",
        "topic_key": f"{c['symbol']}:{c['exchange_key']}".lower(),
        "published_at": c["published_at"],
        "exchange": c["exchange"], "exchange_key": c["exchange_key"],
        "token_name": c["token_name"], "symbol": c["symbol"], "flags": c["flags"],
        "announcement_title": c["announcement_title"], "announcement_url": c["announcement_url"],
        "trading_start": trading_start,
        "prior_listings": c["prior_listings"], "prior_count": c["prior_count"],
        "record_since": record_since,
    }


def _load_known(conn) -> list[dict]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT external_id, payload FROM raw_items WHERE category = %s", (CATEGORY,))
        rows = cur.fetchall()
    known = []
    for r in rows:
        p = r["payload"] or {}
        if not p.get("published_at") or not p.get("exchange_key"):
            continue
        known.append({
            "external_id": r["external_id"], "stored": True, "exchange_key": p["exchange_key"], "exchange": p.get("exchange"),
            "token_name": p.get("token_name") or "", "symbol": p.get("symbol") or "", "published_at": p["published_at"],
            "ts_ms": int(datetime.fromisoformat(p["published_at"]).timestamp() * 1000),
        })
    return known


def _record_since(known: list[dict], candidates: list[dict]) -> dict[str, str]:
    """{exchange display name: 'YYYY-MM'} -- the oldest listing announcement we hold for each exchange. An
    UNDERestimate of how far back the record really reaches (the oldest announcement CRAWLED may predate the
    oldest LISTING), never an overestimate."""
    earliest: dict[str, int] = {}
    for u in list(known) + list(candidates):
        k = u["exchange_key"]
        earliest[k] = min(earliest.get(k, u["ts_ms"]), u["ts_ms"])
    return {DISPLAY[k]: datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m") for k, v in earliest.items()}


def collect(backfill: bool = False) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_exchange_listings") as state:
            now = datetime.now(timezone.utc)
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "api", {"exchanges": EXCHANGES})

            raw_by_exchange: dict[str, list[dict]] = {}
            errors: dict[str, str] = {}
            stale: dict[str, int] = {}
            for ex in EXCHANGES:
                # PER-EXCHANGE ISOLATION: one exchange's API being down must not cost the other three.
                try:
                    rows = fetch_exchange(ex, backfill=backfill)
                except Exception as exc:
                    errors[ex] = str(exc)[:200]
                    logger.warning("exchange_listings: %s fetch failed, continuing with the others: %s", ex, exc)
                    continue
                raw_by_exchange[ex] = rows
                newest = max((r["ts_ms"] for r in rows), default=None)
                if newest and (now.timestamp() * 1000 - newest) / 86_400_000 >= STALE_SOURCE_DAYS:
                    days = int((now.timestamp() * 1000 - newest) / 86_400_000)
                    stale[ex] = days
                    logger.error("exchange_listings: %s's newest announcement is %d days old -- feed looks dead", ex, days)
                    llm_usage._send_anomaly_alert(
                        conn, "listing_source_stale", ex,
                        f"🏦 <b>Exchange listing source gone quiet: {DISPLAY[ex]}</b>\nIts newest announcement of any kind "
                        f"is {days} days old (alert at {STALE_SOURCE_DAYS}). It still answers, so nothing else would flag it -- "
                        f"check the endpoint in collectors/exchange_listings.py.",
                        now, 7 * 24 * 60)
            state["details"]["exchange_errors"] = errors
            state["details"]["stale_sources"] = stale
            # what each host actually returned -- a dev machine and Railway see different data (2026-11-15)
            state["details"]["newest_announcement"] = {
                ex: _iso(max(r["ts_ms"] for r in rows)) for ex, rows in raw_by_exchange.items() if rows}
            if len(errors) == len(EXCHANGES):
                raise RuntimeError("every exchange announcement source failed")
            state["details"]["announcements"] = {k: len(v) for k, v in raw_by_exchange.items()}

            candidates: list[dict] = []
            for ex, rows in raw_by_exchange.items():
                candidates += build_candidates(ex, rows)

            known = _load_known(conn)
            known_ids = {k["external_id"] for k in known}
            new = [c for c in candidates if c["external_id"] not in known_ids]
            attach_prior_listings(new, known)
            duplicates = [c for c in new if c["duplicate"]]
            new = [c for c in new if not c["duplicate"]]
            state["details"]["re_announcements_skipped"] = len(duplicates)
            record_since = _record_since(known, new)

            # trading start: KuCoin carries it in the list payload; Binance needs the article body, fetched only for
            # announcements new this cycle (never during a backfill, never for old ones).
            items = []
            for c in new:
                start = None
                if c["exchange_key"] == "kucoin":
                    start = _kucoin_trading_start(c["desc"])
                elif c["exchange_key"] == "binance" and not backfill \
                        and (now.timestamp() * 1000 - c["ts_ms"]) / 3_600_000 <= TRADING_START_LOOKBACK_HOURS:
                    start = _binance_trading_start(c["announcement_id"])
                    time.sleep(CALL_DELAY_SECONDS)
                items.append((c["external_id"], _payload(c, record_since, start)))

            # A backfill is history: every row here is older than the freshness window except the last few hours'
            # worth, and those are real candidates, so nothing is held.
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"]["candidates"] = len(candidates)
            state["details"]["new"] = len(new)
            state["details"]["inserted"] = inserted
            state["details"]["backfill"] = backfill
            logger.info("exchange_listings: announcements=%s listings=%d new=%d inserted=%d errors=%s",
                        state["details"]["announcements"], len(candidates), len(new), inserted, list(errors))
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect(backfill="--backfill" in sys.argv)
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
