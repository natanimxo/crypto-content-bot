"""Shared HTTP fetch with timeout + retry/backoff (Section 12: 'a dead source logs
and gets skipped, never blocks the rest of the run'). Every collector and LLM
provider should call through this instead of raw `requests` calls.

SECRET REDACTION (2026-09-15) -- real incident: a revoked/regenerated Telegram
bot token had been sitting in Railway's log storage in plaintext, because
pipeline/telegram_api.py builds every URL as `.../bot<TOKEN>/<method>` and a
failed call logged that URL verbatim. Audited every place a URL, request body,
header, or exception gets logged across this file, the LLM providers, and every
collector (pipeline/telegram_api.py's token-in-path is the reported case;
pipeline/llm_providers/gemini.py's `?key=<GEMINI_API_KEY>` is the same shape,
just query-string instead of path -- found live in the code, not yet an
incident, fixed preemptively; collectors/whale_movements.py's and
pipeline/write_post.py's Etherscan `apikey` is passed via `params=`, not
string-concatenated into `url`, but empirically verified below that this does
NOT make it safe).

The two redaction points below are BOTH required, not either/or:
  1. The `url` string this module logs directly.
  2. The exception's own message -- live-verified (2026-09-15, real requests
     calls against real failure modes) that `requests` embeds the FULLY
     RESOLVED url, params included, into a ConnectionError/MaxRetryError's and
     an HTTPError's string representation regardless of how the URL was built
     -- `params={"apikey": "SECRET"}` shows up as `...?apikey=SECRET...` in
     `str(exc)` exactly as if it had been hand-concatenated. (ReadTimeoutError
     happened NOT to include it in the one case checked -- not something to
     rely on holding for every exception type/library version, hence
     redacting unconditionally rather than only for the exception classes
     observed to need it.)

Point 2 matters beyond this module's own log line: get_json/post_json's
callers all catch bare `Exception` and several (bot/approval_poller.py,
scripts/poller_daemon.py) re-log a caught exception via `logger.exception(...)`,
which includes the exception's own `str()`. If the exception this module
raises still carries an unredacted URL, EVERY downstream logger.exception call
anywhere in the stack leaks the secret again, however many layers away — the
only fix that actually closes that off is sanitizing the exception ITSELF
before raising it, not just the warning line printed here. That's why the
except blocks below construct and raise a new, redacted exception rather than
`raise last_exc`'s original object; no caller anywhere in this codebase
catches a specific requests exception subtype (checked), so this doesn't
break any except-clause specificity.

NOT currently a redaction target: request headers. Checked live — no log line
in this codebase (this file included) ever logs `headers` today, which is
where DeepSeek's `Authorization: Bearer ...` and Anthropic's `x-api-key` live.
Nothing to fix right now, but worth remembering if a header ever gets added to
a log line later: it would need the same treatment.
"""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

# Query-string param names treated as secret-bearing (case-insensitive),
# covering every real one found in this codebase's collectors/providers
# (Gemini's `key`, Etherscan's `apikey`) plus the common names any future
# API is likely to use. Deliberately broad, including the generic `key` and
# `token` — over-redacting a log line's readability is a much cheaper
# mistake than under-redacting a real secret.
_SECRET_PARAM_RE = re.compile(
    r"([?&](?:key|api_?key|access_token|auth_?token|token|secret|password|pwd)=)[^&\s]+",
    re.IGNORECASE,
)

# Telegram's own URL shape: https://api.telegram.org/bot<numeric id>:<secret>/<method>
# — the token isn't a query param at all, it's baked into the path, hence a
# separate pattern from _SECRET_PARAM_RE above.
_TELEGRAM_TOKEN_PATH_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


def _redact(text: str) -> str:
    """Strip anything that looks like a secret out of a URL or exception
    message before it's ever handed to logging or re-raised. See this
    module's docstring for why BOTH call sites (the warning log line and
    the exception raised onward) need this, not just one."""
    text = _TELEGRAM_TOKEN_PATH_RE.sub("/bot***REDACTED***", text)
    text = _SECRET_PARAM_RE.sub(r"\1***REDACTED***", text)
    return text


def _redacted_failure(url: str, method: str, attempt: int, retries: int, exc: Exception) -> RuntimeError:
    """Builds the exception this module actually raises/logs — always a
    fresh RuntimeError carrying an already-redacted message, never the raw
    `exc` object (whose own __str__ can embed the unredacted, fully-resolved
    URL — see module docstring). Logs the same redacted text it raises, so
    the two can never drift apart into "log line is clean but the raised
    exception isn't" or vice versa."""
    safe_url = _redact(url)
    safe_reason = _redact(str(exc))
    logger.warning("%s %s failed (attempt %d/%d): %s", method, safe_url, attempt, retries, safe_reason)
    return RuntimeError(f"{method} {safe_url} failed: {safe_reason}")


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 20, retries: int = 3, backoff_seconds: float = 2.0):
    """GET a URL and return parsed JSON, retrying transient failures.

    Raises the last (redacted) failure if every attempt fails — callers
    (collectors) are expected to catch it, log to run_logs, and skip that
    source rather than let one dead API kill the whole collection cycle.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = _redacted_failure(url, "GET", attempt, retries, exc)
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
            last_exc = _redacted_failure(url, "POST", attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    raise last_exc
