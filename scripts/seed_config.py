"""Upsert config/category_config.yaml and config/channel_config.yaml into the DB.

Run this once after applying db/schema.sql, and again any time you want to reset
the DB's tunable config back to what's checked into git (an operator may have
since tuned thresholds/weights/models directly in Supabase — this overwrites that).

    python scripts/seed_config.py
"""

import json
import os
import sys

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.db import get_conn  # noqa: E402

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def seed_categories(conn, path: str):
    with open(path, "r", encoding="utf-8") as f:
        categories = yaml.safe_load(f) or {}

    with conn.cursor() as cur:
        for category, cfg in categories.items():
            cur.execute(
                """INSERT INTO category_config
                       (category, score_weights, review_threshold, cooldown_hours,
                        soft_daily_cap, triage_model, write_model, write_benchmark_status,
                        label, voice, prompt_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (category) DO UPDATE SET
                       score_weights = EXCLUDED.score_weights,
                       review_threshold = EXCLUDED.review_threshold,
                       cooldown_hours = EXCLUDED.cooldown_hours,
                       soft_daily_cap = EXCLUDED.soft_daily_cap,
                       triage_model = EXCLUDED.triage_model,
                       write_model = EXCLUDED.write_model,
                       write_benchmark_status = EXCLUDED.write_benchmark_status,
                       label = EXCLUDED.label,
                       voice = EXCLUDED.voice,
                       prompt_notes = EXCLUDED.prompt_notes""",
                (
                    category,
                    json.dumps(cfg["score_weights"]),
                    cfg.get("review_threshold", 50),
                    cfg.get("cooldown_hours", 6),
                    cfg.get("soft_daily_cap"),
                    cfg.get("triage_model", "deepseek-v4-flash"),
                    cfg.get("write_model", "deepseek-v4-flash"),
                    cfg.get("write_benchmark_status", "trial"),
                    cfg.get("label"),
                    cfg.get("voice"),
                    cfg.get("prompt_notes"),
                ),
            )
            print(f"  category_config: {category}")
    conn.commit()


def seed_channels(conn, path: str):
    with open(path, "r", encoding="utf-8") as f:
        channels = yaml.safe_load(f) or {}

    with conn.cursor() as cur:
        for channel, cfg in channels.items():
            chat_id = cfg.get("chat_id")
            if not chat_id:
                print(f"  WARNING: no chat_id set for '{channel}' in channel_config.yaml — "
                      f"publishing to it will fail until you add one.")
            cur.execute(
                """INSERT INTO channel_config (channel, categories, region_profile, soft_daily_cap, chat_id)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (channel) DO UPDATE SET
                       categories = EXCLUDED.categories,
                       region_profile = EXCLUDED.region_profile,
                       soft_daily_cap = EXCLUDED.soft_daily_cap,
                       chat_id = EXCLUDED.chat_id""",
                (
                    channel,
                    cfg["categories"],
                    cfg.get("region_profile", "default"),
                    cfg.get("soft_daily_cap"),
                    chat_id,
                ),
            )
            print(f"  channel_config: {channel}")
    conn.commit()


def main():
    load_dotenv()
    conn = get_conn()
    try:
        print("Seeding category_config...")
        seed_categories(conn, os.path.join(CONFIG_DIR, "category_config.yaml"))
        print("Seeding channel_config...")
        seed_channels(conn, os.path.join(CONFIG_DIR, "channel_config.yaml"))
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
