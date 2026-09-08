"""Operator alerting (Section 12: '3 consecutive failures on any job → Telegram
alert to you — a separate, no-buttons message'). Called after every run_log
context exits; cheap no-op when things are healthy.
"""

import os

from pipeline.run_log import count_recent_failures
from pipeline.telegram_api import send_message

FAILURE_THRESHOLD = 3


def check_and_alert(conn, job_name: str) -> None:
    consecutive = count_recent_failures(conn, job_name, n=FAILURE_THRESHOLD)
    if consecutive < FAILURE_THRESHOLD:
        return

    operator_chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not operator_chat_id:
        return  # nothing we can do without a chat id — collect.yml will still fail loudly in Actions

    send_message(
        operator_chat_id,
        f"⚠️ '{job_name}' has failed {consecutive} times in a row. Check GitHub Actions logs.",
    )
