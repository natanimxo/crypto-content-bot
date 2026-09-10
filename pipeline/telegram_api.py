"""Thin wrapper around the Telegram Bot API. Every module that talks to Telegram
(publish.py, bot/notify.py, bot/approval_poller.py) goes through here — keeps the
token/base-URL/error-handling in one place.
"""

import os

from pipeline.http import get_json, post_json

BASE = "https://api.telegram.org/bot{token}"


def _base_url() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return BASE.format(token=token)


def send_message(chat_id: str, text: str, reply_markup: dict | None = None,
                  reply_to_message_id: int | None = None,
                  disable_web_page_preview: bool = False) -> dict:
    """Returns the Telegram `result` object (includes message_id).

    disable_web_page_preview should be True for every actual post/content
    message (2026-09-10, operator direction: no hyperlinks in posts, and this
    is a defensive belt-and-suspenders measure even though post_format.py no
    longer emits any <a> tags — it can't guarantee an LLM's free-form prose
    never happens to contain something URL-shaped).
    """
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        body["reply_markup"] = reply_markup
    if reply_to_message_id:
        body["reply_to_message_id"] = reply_to_message_id
    if disable_web_page_preview:
        body["disable_web_page_preview"] = True
    resp = post_json(f"{_base_url()}/sendMessage", json_body=body)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {resp}")
    return resp["result"]


def edit_message_text(chat_id: str, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
    body = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        body["reply_markup"] = reply_markup
    resp = post_json(f"{_base_url()}/editMessageText", json_body=body)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram editMessageText failed: {resp}")
    return resp["result"]


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    body = {"callback_query_id": callback_query_id}
    if text:
        body["text"] = text
    post_json(f"{_base_url()}/answerCallbackQuery", json_body=body)


def get_updates(offset: int | None = None, timeout: int = 0) -> list:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = get_json(f"{_base_url()}/getUpdates", params=params, timeout=timeout + 15)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {resp}")
    return resp["result"]
