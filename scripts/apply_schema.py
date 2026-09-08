"""Apply db/schema.sql against DATABASE_URL. Safe to rerun (every statement is
CREATE TABLE/INDEX IF NOT EXISTS).

    python scripts/apply_schema.py
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import get_conn  # noqa: E402

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "schema.sql")


def main():
    load_dotenv()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print("Schema applied.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
