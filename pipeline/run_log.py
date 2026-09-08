"""Thin wrapper around run_logs (Section 12: 'every job writes to run_logs, success
or failure'). Use as a context manager so failures are logged even on exception."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone

from pipeline.db import dict_cursor


@contextmanager
def run_log(conn, job_name: str):
    started_at = datetime.now(timezone.utc)
    state = {"status": "success", "details": {}}
    try:
        yield state
    except Exception as exc:
        state["status"] = "failure"
        state["details"] = {**state.get("details", {}), "error": str(exc)}
        raise
    finally:
        with dict_cursor(conn) as cur:
            cur.execute(
                """INSERT INTO run_logs (job_name, status, details, started_at, finished_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    job_name,
                    state["status"],
                    json.dumps(state.get("details", {})),
                    started_at,
                    datetime.now(timezone.utc),
                ),
            )
        conn.commit()


def count_recent_failures(conn, job_name: str, n: int = 3) -> int:
    """How many of the last n runs of this job failed — used for the 'N consecutive
    failures' alert (Section 12)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT status FROM run_logs WHERE job_name = %s
               ORDER BY started_at DESC LIMIT %s""",
            (job_name, n),
        )
        rows = cur.fetchall()
    if len(rows) < n:
        return 0
    consecutive = 0
    for row in rows:
        if row["status"] == "failure":
            consecutive += 1
        else:
            break
    return consecutive
