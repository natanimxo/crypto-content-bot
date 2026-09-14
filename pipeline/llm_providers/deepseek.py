"""DeepSeek provider — default triage + write model (Section 4.2).

NOTE: the spec names the model "DeepSeek V4-Flash". As of this build DeepSeek's
public API exposes `deepseek-chat` / `deepseek-reasoner`; the exact model id
changes over time, so it's read from DEEPSEEK_MODEL_ID (default `deepseek-chat`)
rather than hardcoded — verify against https://api-docs.deepseek.com/quick_start/pricing
before relying on cost numbers, and update the env var/default if it's stale.
"""

import os

from pipeline.http import post_json

API_URL = "https://api.deepseek.com/chat/completions"


def generate(prompt: str, *, system: str | None = None, max_tokens: int = 800) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    model = os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Live bug, 2026-09-14 (Railway poller, approval_id=73): three consecutive
    # ReadTimeoutError -- the connection to api.deepseek.com succeeded every
    # time (never a ConnectionError/DNS failure), DeepSeek just didn't finish
    # generating within pipeline/http.py's 30s default before all 3 retries
    # were exhausted. That default is tuned for collectors' fast data-source
    # APIs (Section 12: "a dead source logs and gets skipped"), a completion
    # call is a slower, different workload -- 60s here, not a global change to
    # http.py's default, since collectors legitimately want to fail fast.
    resp = post_json(API_URL, json_body=body, headers=headers, timeout=60)
    return resp["choices"][0]["message"]["content"].strip()
