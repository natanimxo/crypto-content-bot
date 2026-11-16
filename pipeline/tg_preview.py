"""Public Telegram channel posts via t.me/s/<handle> (no login, no API key) -- shared by the channel-backed collectors (Whale Alert, Wu Blockchain,
Cointelegraph, ZachXBT, Binance Announcements). The parsing itself lives in collectors/earning_opportunities.py (written first, for the earning
channels); this is the thin fetch layer the others use.

A channel is verified FROM RAILWAY before anything is built on it (pipeline/source_probe.TELEGRAM_CANDIDATES, run_logs `telegram_probe`).
"""

import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", "Accept-Language": "en"}


def fetch_posts(handle: str, retries: int = 2) -> list[dict]:
    """[{post, id, time: datetime (aware), text, links, views}] for the newest ~20 posts, oldest first. Raises when the page cannot be read or has no posts
    (a dead or preview-less channel must be a visible failure, not a silent empty list)."""
    from collectors.earning_opportunities import parse_telegram_preview
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(f"https://t.me/s/{handle}", headers=HEADERS, timeout=25)
            r.raise_for_status()
            posts = parse_telegram_preview(r.text, handle)
            if not posts:
                raise RuntimeError(f"t.me/s/{handle} returned no posts")
            out = [{**p, "id": int(p["post"].split("/")[-1]), "time": datetime.fromisoformat(p["time"])} for p in posts]
            return sorted(out, key=lambda p: p["time"])
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"t.me/s/{handle} failed: {str(last)[:120]}")
