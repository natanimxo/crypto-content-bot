"""Shared HTTP fetch with timeout + retry/backoff (Section 12: 'a dead source logs
and gets skipped, never blocks the rest of the run'). Every collector and LLM
provider should call through this instead of raw `requests` calls."""

import logging
import time

import requests

logger = logging.getLogger(__name__)


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 20, retries: int = 3, backoff_seconds: float = 2.0):
    """GET a URL and return parsed JSON, retrying transient failures.

    Raises the last exception if every attempt fails — callers (collectors) are
    expected to catch it, log to run_logs, and skip that source rather than let
    one dead API kill the whole collection cycle.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    raise last_exc


def post_json(url: str, *, json_body: dict | None = None, headers: dict | None = None,
              timeout: int = 30, retries: int = 3, backoff_seconds: float = 2.0):
    """POST JSON and return parsed JSON response, with the same retry policy as get_json."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=json_body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            logger.warning("POST %s failed (attempt %d/%d): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    raise last_exc
