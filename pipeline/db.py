"""Shared Postgres connection helper. Every module gets a connection from here —
never opens its own, so pooling/timeouts/env resolution stay in one place."""

import os

import psycopg2
import psycopg2.extras


def get_conn():
    """Return a new psycopg2 connection using DATABASE_URL from the environment.

    Callers are responsible for closing it (use `with get_conn() as conn:` or a
    try/finally) — this module does not pool connections, which is fine at the
    volume this system runs at (one short-lived GitHub Actions job at a time).
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env locally, or set the "
            "DATABASE_URL GitHub Actions Secret in CI."
        )
    return psycopg2.connect(dsn)


def dict_cursor(conn):
    """A cursor that returns rows as dict-like objects instead of plain tuples."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
