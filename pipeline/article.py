"""Article text for the humor channel's news and satire cards (operator 2026-11-20: "include the source's own first paragraph or two, plus the outlet and date").

WHAT THIS RETURNS: the article's OWN words, untouched: the first one or two body paragraphs exactly as the page prints them (tags removed, entities decoded, whitespace collapsed), the outlet's name
(the page's og:site_name, else its domain) and the article's own publication date when the page states one. Nothing is rewritten, shortened or summarized; the only choice made is WHICH paragraphs
(the first body paragraphs, whole, never cut mid-paragraph). A page that gives no paragraph (paywall, bot wall, JS-only page, an error) returns paragraphs=[] with a status: the caller then sends a headline-only
card, which the operator allows only when "the source genuinely has nothing more".

Two ways in: `from_html(html, url)` for a page already fetched (and for the feeds that carry the full text, `paragraphs_from_fragment`), `fetch(url)` to download it first.
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 20
MIN_PARAGRAPH_CHARS = 60          # shorter lines are bylines, captions, "Advertisement"
MAX_PARAGRAPHS = 2
MAX_TOTAL_CHARS = 900             # a second paragraph is added only while the total stays within this; the first paragraph is always kept whole
MAX_FIRST_PARAGRAPH_CHARS = 1500  # a single paragraph longer than this is a wall of text, not a lede: treated as "nothing more"
_SKIP_CLASS = re.compile(r"related|promo|newsletter|comment|share|social|byline|author|caption|credit|advert|sponsor|signup|subscribe|breadcrumb|teaser|footer|sidebar|cookie|consent|paywall-(?:message|prompt|cta|banner|overlay|gate|wall)|^paywall$|metadata|dateline|disclaimer|standfirst|subhead|subtitle|strapline|(?<!body-)description|text-to-speech|^dek$|summary", re.I)
_BOILERPLATE = re.compile(r"^(advertisement|sign up|subscribe|follow us|read more|read also|related:|click here|copyright|all rights reserved|by |photo:|image:|\(reuters\)\s*$|updated:|published:|share this|listen to this article|story continues|this article was|we use cookies|for more|download the)", re.I)
_BOILERPLATE_ANYWHERE = re.compile(r"(javascript is (disabled|required)|enable cookies|please enable|subscribe to (read|continue)|create a free account|already a subscriber|sign in to (read|continue)|unusual traffic|are you a robot)", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _meta(soup, *names) -> str:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return ""


def _domain_name(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    host = re.sub(r"^(www|edition|m|amp)\.", "", host)
    return host


def _article_date(soup) -> datetime | None:
    raw = _meta(soup, "article:published_time", "og:article:published_time", "datePublished", "date", "pubdate", "parsely-pub-date", "sailthru.date")
    if not raw:
        t = soup.find("time", attrs={"datetime": True})
        raw = t["datetime"] if t else ""
    if not raw:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', str(soup))
        raw = m.group(1) if m else ""
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_WP_CLASS = re.compile(r"^(category|tag|post|type|status|format|wp|hentry|entry|has|is|menu|page|taxonomy)-", re.I)


_NESTED_BLOCKS = ["div", "aside", "section", "article", "figure", "ul", "ol", "table", "script", "style", "iframe", "form", "button", "noscript", "h1", "h2", "h3", "h4", "h5", "h6", "nav", "footer"]


def _p_text(p) -> str:
    """The paragraph's own words. A page whose HTML is malformed can nest block elements INSIDE a <p> (Variety puts its 'Related Stories' card and its ad slot there, Blooloop-style wrappers too): the
    parser keeps them as children, and get_text() would print the card's words as part of the paragraph. Those nested blocks are dropped before the text is read (on a copy, the page is untouched)."""
    import copy
    q = copy.copy(p)
    for el in q.find_all(_NESTED_BLOCKS):
        el.decompose()
    return _clean(q.get_text())


def _skippable(p) -> bool:
    """A paragraph that is page chrome, not article body: inside a nav / footer / figure ... tag, or within its nearest four ancestors a class TOKEN that names promo / share / byline / caption / standfirst
    ... (token by token, and never WordPress's own category-/tag-/post- slugs: a whole-container class string matches almost anything)."""
    chain = [p] + list(p.parents)
    for parent in chain:
        if parent.name in ("nav", "footer", "aside", "form", "figure", "figcaption", "header", "button", "noscript"):
            return True
    for parent in chain[:5]:
        if parent.name in ("body", "html", "article", "main"):
            break
        tokens = list(parent.get("class") or []) + ([parent["id"]] if parent.get("id") else [])
        if any(_SKIP_CLASS.search(t) for t in tokens if not _WP_CLASS.match(t)):
            return True
    return False


def _pick(paragraphs: list[str]) -> list[str]:
    """The first body paragraph, whole, and the second while the total stays inside MAX_TOTAL_CHARS. [] when the first is missing or a wall of text."""
    good = [p for p in paragraphs if len(p) >= MIN_PARAGRAPH_CHARS and not _BOILERPLATE.match(p) and not _BOILERPLATE_ANYWHERE.search(p)]
    if not good or len(good[0]) > MAX_FIRST_PARAGRAPH_CHARS:
        return []
    out = [good[0]]
    for p in good[1:MAX_PARAGRAPHS]:
        if sum(len(x) for x in out) + len(p) <= MAX_TOTAL_CHARS:
            out.append(p)
    return out


def paragraphs_from_fragment(fragment: str) -> list[str]:
    """First body paragraphs of an HTML fragment a feed carries (content:encoded / description): the source's own words, as above."""
    soup = BeautifulSoup(fragment or "", "html.parser")
    paras = [_p_text(p) for p in soup.find_all("p") if not p.find_parent(["blockquote", "figure"])]
    if not paras:   # a feed with bare text and <br> instead of <p>
        text = BeautifulSoup(re.sub(r"(?i)<br\s*/?>", "\n\n", fragment or ""), "html.parser").get_text()
        paras = [_clean(x) for x in re.split(r"\n\s*\n", text)]
    return _pick([p for p in paras if p])


def from_html(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    outlet = _meta(soup, "og:site_name", "application-name", "twitter:site") or ""
    outlet = outlet.lstrip("@") if outlet.startswith("@") else outlet
    if not outlet:
        outlet = _domain_name(url)
    root = soup.find("article") or soup.find(attrs={"itemprop": "articleBody"}) or soup.find("main") or soup.body or soup
    paras = [_p_text(p) for p in root.find_all("p") if not _skippable(p)]
    picked = _pick(paras)
    if not picked and root is not soup.body:   # the <article> tag held only chrome: try the whole page once
        picked = _pick([_p_text(p) for p in (soup.body or soup).find_all("p") if not _skippable(p)])
    return {"outlet": outlet, "date": _article_date(soup), "paragraphs": picked, "status": "ok" if picked else "no_paragraphs", "headline": _meta(soup, "og:title")}


def fetch(url: str) -> dict:
    """Download `url` and extract. Never raises: a failure is a status, so the card falls back to the headline only."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return {"outlet": _domain_name(url), "date": None, "paragraphs": [], "status": f"http_{r.status_code}", "headline": ""}
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else (r.apparent_encoding or "utf-8")
        out = from_html(r.text, r.url)
        out["final_url"] = r.url
        return out
    except Exception as exc:
        logger.info("article: %s could not be fetched: %s", url, exc)
        return {"outlet": _domain_name(url), "date": None, "paragraphs": [], "status": "error", "headline": ""}


def dateline(outlet: str, when: datetime | None) -> str:
    """'BBC, 20 Nov:' -- the outlet and the day. Without a date, 'BBC:'."""
    if not outlet:
        return ""
    if when is None:
        return f"{outlet}:"
    return f"{outlet}, {when.day} {when.strftime('%b')}:"
