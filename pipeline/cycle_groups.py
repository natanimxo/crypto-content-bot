"""The two staggered cron groups (operator 2026-11-21): every channel pushes every 4 hours, in two groups two hours apart.

  Group A  Crypto Wall Street, Alpha Edge Crypto, Crypto Notebook      cron `0 */4 * * *`     00 04 08 12 16 20 UTC   Railway service `collect-cycle`   (scripts/collect_cycle.py)
  Group B  Hustle to Million, Earn Stack (coincraft), We Talk A Lot     cron `0 2-23/4 * * *`  02 06 10 14 18 22 UTC   Railway service `collect-earning` (scripts/collect_earning.py)

A group's run collects ONLY its own categories and notifies ONLY its own channels. This module is the single place that says which is which; scripts/check_cycle_groups.py pins that every channel and every
collector is in exactly one group and that every channel's categories are fed by its own group's collectors (a category collected by group B for a group-A channel would sit unnoticed until the wrong cycle).
The cron strings are mirrored in .railway/railway.ts (the source of truth for Railway); the check compares them.

Pacing: select_candidates.CYCLES_PER_DAY is 24 / 4 = 6 for every category now (Earn Stack was on its own 2-hour cadence, per-cycle share 1/12; on 4 hours it is 1/6, so ceil(8 / 6) = 2 cards a cycle and the daily total is unchanged).
"""

GROUPS = {
    "A": {
        "label": "Group A",
        "cron": "0 */4 * * *",
        "service": "collect-cycle",
        "channels": ["crypto_wall_street", "alpha_edge_crypto", "crypto_notebook"],
        "collectors": ["defi_yields", "whale_movements", "whale_alert", "gems_security", "news", "hacks_exploits", "zachxbt", "rekt", "technique_explainer", "exchange_listings"],
        "notify_job": "notify",
        "summary_job": "collect_cycle",
    },
    "B": {
        "label": "Group B",
        "cron": "0 2-23/4 * * *",
        "service": "collect-earning",
        "channels": ["hustle_to_million", "coincraft", "we_talk_a_lot"],
        "collectors": ["tool_launches", "startup_jobs", "macro_news", "startup_news", "earning_opportunities", "weird_news", "satire", "jokes",
                       "dark_history", "unusual_deaths", "world_records", "conspiracies", "weird_facts", "shower_thoughts"],
        "notify_job": "notify_group_b",
        "summary_job": "collect_earning_cycle",
    },
}


def group_of_channel(channel: str) -> str | None:
    return next((g for g, d in GROUPS.items() if channel in d["channels"]), None)
