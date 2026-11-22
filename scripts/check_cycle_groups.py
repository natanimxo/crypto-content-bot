"""No-database, no-network regression table for the two staggered cron groups (operator 2026-11-21, pipeline/cycle_groups.py).

Pins: every channel and every collector is in exactly one group; every channel's categories are fed by its own group's collectors; the cron strings in .railway/railway.ts equal the groups' and fire two hours
apart; a group's run executes only its collectors and notifies only its channels; Earn Stack's per-cycle card limit is 2 on 4-hour cycles (daily total unchanged).

    python scripts/check_cycle_groups.py
"""

import contextlib
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yaml  # noqa: E402

FAILS: list[str] = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")


def fire_hours(cron: str) -> list[int]:
    """Hours of the day a '<minute> <hours> * * *' cron fires: supports '*/N' and 'A-B/N' (all this project uses)."""
    minute, hours = cron.split()[:2]
    assert minute == "0", cron
    m = re.fullmatch(r"(?:(\d+)-(\d+)|\*)/(\d+)", hours)
    lo, hi, step = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m.group(1) else (0, 23, int(m.group(3)))
    return list(range(lo, hi + 1, step))


def main():
    import collect_cycle as CC
    from pipeline import cycle_groups as G
    from pipeline import select_candidates as SEL

    chans = yaml.safe_load(open(os.path.join(ROOT, "config", "channel_config.yaml"), encoding="utf-8"))
    all_ch = sorted(chans)
    in_groups = sorted(c for g in G.GROUPS.values() for c in g["channels"])
    check("every channel is in exactly one group", in_groups, all_ch)
    check("group A is the three crypto channels, group B is Hustle, Earn Stack and We Talk A Lot", (sorted(G.GROUPS["A"]["channels"]), sorted(G.GROUPS["B"]["channels"])),
          (["alpha_edge_crypto", "crypto_notebook", "crypto_wall_street"], ["coincraft", "hustle_to_million", "we_talk_a_lot"]))
    coll = sorted(CC.COLLECTORS)
    in_coll = sorted(k for g in G.GROUPS.values() for k in g["collectors"])
    check("every collector is in exactly one group OR is paused (nothing is dropped, nothing runs twice)", (sorted(in_coll + G.PAUSED_COLLECTORS), set(in_coll) & set(G.PAUSED_COLLECTORS)), (coll, set()))
    check("paused (2026-11-22, We Talk A Lot jokes only): the eight other humor categories and the jokes collector itself", sorted(G.PAUSED_COLLECTORS),
          sorted(["weird_news", "satire", "jokes", "dark_history", "unusual_deaths", "world_records", "conspiracies", "weird_facts", "shower_thoughts"]))
    for gk, g in G.GROUPS.items():
        fed = {CC.SCORED_AS.get(k, k) for k in g["collectors"]}
        need = {cat for ch in g["channels"] for cat in chans[ch]["categories"]}
        check(f"group {gk}: every category its channels list is collected by this group's own collectors (a paused one excepted)", sorted(need - fed - set(G.PAUSED_COLLECTORS)), [])
    # the Railway file is the source of truth for the schedule
    ts = open(os.path.join(ROOT, ".railway", "railway.ts"), encoding="utf-8").read()
    for gk, g in G.GROUPS.items():
        m = re.search(r'service\("' + re.escape(g["service"]) + r'",.*?cronSchedule: "([^"]+)"', ts, re.S)
        check(f"railway.ts: service {g['service']} has the group {gk} cron", m.group(1) if m else None, g["cron"])
    a, b = fire_hours(G.GROUPS["A"]["cron"]), fire_hours(G.GROUPS["B"]["cron"])
    check("group A fires at 00 04 08 12 16 20 UTC, group B at 02 06 10 14 18 22 UTC", (a, b), ([0, 4, 8, 12, 16, 20], [2, 6, 10, 14, 18, 22]))
    check("the groups are exactly two hours apart (every 4 hours each, interleaved, no shared hour)", (sorted(a + b) == list(range(0, 24, 2)), set(a) & set(b)), (True, set()))
    # Earn Stack: 4-hour cycles, so 2 cards a cycle keeps the daily total
    cat_cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "category_config.yaml"), encoding="utf-8"))
    cap = cat_cfg["earning_opportunities"]["soft_daily_cap"]
    per_cycle = math.ceil(cap / SEL.CYCLES_PER_DAY_BY_CATEGORY.get("earning_opportunities", SEL.CYCLES_PER_DAY))
    check("Earn Stack: no special 2-hour pacing any more; per-cycle limit is 2 (was 1) and 6 cycles x 2 covers the daily cap of 8", (SEL.CYCLES_PER_DAY_BY_CATEGORY, per_cycle, 6 * per_cycle >= cap, cap), ({}, 2, True, 8))

    # a group's run executes only its collectors and notifies only its channels (everything external stubbed)
    calls = {"collect": [], "score": [], "notify": [], "extras": 0, "summary": []}
    real = (CC.COLLECTORS, CC.get_conn, CC.score_new_items, CC.notify.run, CC.run_log, CC.llm_usage.run_checks, CC.check_and_alert, CC.load_dotenv)

    class FakeConn:
        def close(self):
            pass

    @contextlib.contextmanager
    def fake_run_log(conn, job):
        state = {"status": "success", "details": {}}
        yield state
        calls["summary"].append((job, state["details"].get("group"), state["status"]))
    try:
        CC.COLLECTORS = {k: (lambda k=k: calls["collect"].append(k) or 1) for k in real[0]}
        CC.get_conn = lambda: FakeConn()
        CC.score_new_items = lambda conn, cat: calls["score"].append(cat) or 0
        CC.notify.run = lambda only_channels=None, job_name="notify": calls["notify"].append((sorted(only_channels), job_name))
        CC.run_log = fake_run_log
        CC.llm_usage.run_checks = lambda conn: None
        CC.check_and_alert = lambda conn, job: None
        CC.load_dotenv = lambda *a, **k: None
        CC.run_cycle("A")
        a_collect, a_notify, a_summary = list(calls["collect"]), list(calls["notify"]), list(calls["summary"])
        for k in ("collect", "score", "notify", "summary"):
            calls[k].clear()
        CC.run_cycle("B", after_notify=lambda guarded: calls.__setitem__("extras", calls["extras"] + 1))
        b_collect, b_notify, b_summary = list(calls["collect"]), list(calls["notify"]), list(calls["summary"])
        b_score = list(calls["score"])
    finally:
        (CC.COLLECTORS, CC.get_conn, CC.score_new_items, CC.notify.run, CC.run_log, CC.llm_usage.run_checks, CC.check_and_alert, CC.load_dotenv) = real
    check("group A's run collects exactly group A's collectors and notifies exactly group A's channels (job 'notify')", (sorted(a_collect) == sorted(G.GROUPS["A"]["collectors"]), a_notify, a_summary),
          (True, [(sorted(G.GROUPS["A"]["channels"]), "notify")], [("collect_cycle", "A", "success")]))
    check("group B's run collects exactly group B's collectors, notifies exactly group B's channels, runs the extras once", (sorted(b_collect) == sorted(G.GROUPS["B"]["collectors"]), b_notify, b_summary, calls["extras"]),
          (True, [(sorted(G.GROUPS["B"]["channels"]), "notify_group_b")], [("collect_earning_cycle", "B", "success")], 1))
    check("group B scores the jokes library without collecting it, and runs no paused collector", ("jokes" in b_score, "jokes" in b_collect, [k for k in G.PAUSED_COLLECTORS if k in b_collect], [k for k in G.PAUSED_COLLECTORS if k != "jokes" and k in b_score]),
          (True, False, [], []))
    check("no collector runs in both groups, and the earning collector is not in group A any more", (set(a_collect) & set(b_collect), "earning_opportunities" in a_collect, "earning_opportunities" in b_collect), (set(), False, True))

    # ---- jokes only, twice a day (operator 2026-11-22): what notify may send for We Talk A Lot, and when
    from datetime import datetime, timezone

    from bot import notify as N
    humor = ["weird_news", "satire", "jokes", "dark_history", "unusual_deaths", "world_records", "conspiracies", "weird_facts", "shower_thoughts"]
    at = lambda h: datetime(2026, 11, 23, h, 5, tzinfo=timezone.utc)
    check("jokes only: the humor channel sends only jokes, and only in the 06:00 and 18:00 UTC runs of the six group-B runs", {h: N.humor_scope("we_talk_a_lot", humor, at(h)) for h in (2, 6, 10, 14, 18, 22)},
          {2: None, 6: ["jokes"], 10: None, 14: None, 18: ["jokes"], 22: None})
    check("jokes only: every other channel is untouched by the scope", (N.humor_scope("coincraft", ["earning_opportunities"], at(14)), N.humor_scope("hustle_to_million", ["tool_launches"], at(6))), (["earning_opportunities"], ["tool_launches"]))
    check("jokes only: the other categories are paused, not deleted -- they are still in the channel's config and the collectors dict", (all(c in chans["we_talk_a_lot"]["categories"] for c in humor), all(k in CC.COLLECTORS for k in G.PAUSED_COLLECTORS)), (True, True))
    check("jokes only: one card a run, and the jokes cap lets two a day through (the rolling 24h window needs a third slot)", (N.CHANNEL_PER_CYCLE_LIMIT["we_talk_a_lot"], cat_cfg["jokes"]["soft_daily_cap"] >= 3), (1, True))
    check("jokes only: the two send hours are group B's 06:00 and 18:00 slots", (sorted(h for h in fire_hours(G.GROUPS["B"]["cron"]) if h in __import__("pipeline.verbatim", fromlist=["x"]).load_config()["send_hours_utc"]), __import__("pipeline.verbatim", fromlist=["x"]).load_config()["active_categories"]), ([6, 18], ["jokes"]))

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f_ in FAILS:
            print("  -", f_)
        sys.exit(1)
    print("cycle groups behave as expected")


if __name__ == "__main__":
    main()
