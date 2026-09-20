"""Turns scored raw_items into the list that actually gets notified to the
operator. This is where Section 7's cooldown suppression and Section 9's
soft-cap pacing happen — both deterministic, both before anything reaches an
LLM.

An item can clear review_threshold and still not be notified this cycle,
either because its topic was already surfaced within cooldown_hours, or
because the channel/category already hit its soft_daily_cap for the trailing
window -- in both cases it's simply skipped, not marked as anything, so a
later cycle can pick it back up once cap headroom or cooldown allows it.

CAP WINDOW, corrected 2026-09-16 (real incident, operator direction): this
used to reset at UTC midnight ("today" = calendar date in UTC). For an
operator at UTC+3, that's 3am local -- quota burns out during their evening,
sits fully spent overnight while they sleep, then the WHOLE cap frees up at
once the moment UTC rolls over, dumping everything that had backed up in one
notification. Verified live, 2026-09-15/16: a run at 20:09 UTC (still
"today" locally... no, still 2026-09-15 UTC) sent zero news items despite 9
clearing threshold (cap already spent); the very next run, at 00:09 UTC
(2026-09-16, cap freshly reset), sent 27 candidates across 3 channels in one
burst. That's gating-then-flushing, the opposite of pacing.

Fixed by moving from a calendar-day boundary to a rolling window
(CAP_WINDOW_HOURS, trailing hours from now) -- capacity drains and refills
continuously as old notifications age out of the window, so there's no
single moment where the whole cap resets and no timezone to get right (or
wrong) in the first place.

STARVATION, same incident, a real and separate problem from the boundary
itself (confirmed twice, by raw_item_id, not inferred): a candidate that
clears threshold but loses out to the cap has no seniority under pure
score-ranking -- every cycle re-ranks by score from scratch, so a mid-scoring
item in a high-volume category (news: ~20/21 clearing threshold most cycles
against a cap far below that) can lose to fresher, higher-scoring arrivals
indefinitely. Not dropped, but "never selected" reads the same as "dropped"
from the operator's side. Fixed with a small, capped age bonus added to a
candidate's EFFECTIVE ranking score (see _age_bonus) -- never its stored or
displayed score, only the order candidates are picked in.

A fourth gate, `raw_items.held` (2026-09-11), is the odd one out: it's never
set by anything in collect/score, only by the operator (scripts/hold_candidates.py)
choosing to pace a release -- e.g. "send me a few first so I can gauge write
quality before the rest." Unlike the other three, releasing a held item
doesn't happen automatically with time; it stays excluded until explicitly
un-held.
"""

from datetime import datetime, timezone

from pipeline import entities as entity_lib
from pipeline.db import dict_cursor
from pipeline.score import load_category_config

# Same value as collectors/news.py's and collectors/macro_news.py's own
# DEDUP_MERGE_THRESHOLD (kept as a separate constant here, not imported --
# pipeline/ is lower-level than collectors/, collectors import FROM
# pipeline, not the other way around). Used for the cross-cycle same-story
# dedup pass in get_new_candidates below; see its comment for why this
# needs to exist as its own pass, separate from the collectors' own
# same-cycle dedup.
CROSS_CYCLE_DEDUP_THRESHOLD = 0.5

# Rolling window every soft cap (category- and channel-level) is measured
# against, replacing the old UTC-calendar-day boundary. 24h, matching the
# original "roughly a day's pacing" intent of soft_daily_cap -- the name
# stays as-is (still reads naturally under a rolling interpretation); only
# the boundary semantics changed.
CAP_WINDOW_HOURS = 24.0

# How far back a candidate is compared against items ALREADY NOTIFIED, to catch
# a feed re-serving a story that already went out (real incident 2026-09-18..20:
# identical BBC headlines re-sent 1-3 days apart, past the 12-24h topic_key
# cooldown, because a fresh raw_item shares no id with the notified copy and
# the in-pool dedup below never saw the notified one). 7 days: long enough to
# cover multi-day feed re-serves, short enough that a genuinely recurring
# headline ("Bitcoin falls below $X") is not suppressed forever.
NOTIFIED_DEDUP_LOOKBACK_DAYS = 7

# Per-digest subject cap: at most ONE candidate per lead subject
# (entity_lib.lead_subject) in a category's selection, highest effective score
# wins. Headline-driven categories only. Real case 2026-09-17: "Circle Launches
# Arc Mainnet" and "Circle debuts Arc blockchain..." shipped in one digest;
# no similarity rule could separate them from distinct same-topic stories
# without merging 1,402 pairs, so this caps by subject instead -- deterministic
# and auditable, at the accepted cost of also spacing out genuinely distinct
# same-subject stories (audited on 117 sent items: 9 would be spaced out, incl.
# separate Bitcoin and SEC stories). Capped-out items are DEFERRED, not dropped:
# they stay unnotified and eligible next cycle (age bonus applies), so this
# never silently discards news -- it only limits one digest to one per subject.
SUBJECT_CAP_CATEGORIES = {"news", "macro_news"}

# Anti-starvation age bonus -- added to a candidate's SCORE to get its
# EFFECTIVE ranking score for cap-selection purposes only; never written
# back to `scores.score`, never what's shown to the operator ("Score X/100"
# in the notification is always the real, unmodified score). Deliberately
# small and capped well below a typical "barely clears threshold" vs.
# "genuinely excellent" score gap (real news candidates observed spanning
# roughly 50-95) -- so a stale, mediocre candidate can gain enough ground to
# beat something only modestly better after waiting, but can never leapfrog
# something that's actually much better just by sitting around. +1 point per
# 6h waited, capped at +12 after 3 days (~18 collection cycles at the
# post-Railway 4h cadence) -- long enough to be a real, deliberate wait, not
# noise from one slow cycle.
AGE_BONUS_HOURS_PER_POINT = 6.0
AGE_BONUS_MAX_POINTS = 12.0


def _age_bonus(collected_at, now: datetime) -> float:
    if not collected_at:
        return 0.0
    age_hours = max(0.0, (now - collected_at).total_seconds() / 3600)
    return min(AGE_BONUS_MAX_POINTS, age_hours / AGE_BONUS_HOURS_PER_POINT)


def _already_notified_ids(conn, category: str) -> set[int]:
    """raw_item ids that have appeared in any notification before (so we never
    re-notify the same item, independent of cooldown/topic logic)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT DISTINCT r.id AS raw_item_id
               FROM notifications n
               JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = %s""",
            (category,),
        )
        return {row["raw_item_id"] for row in cur.fetchall()}


def _topics_in_cooldown(conn, category: str, cooldown_hours: float) -> set[str]:
    """topic_keys that were notified within the cooldown window — suppresses
    same-topic repeats (Section 7) regardless of score."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT DISTINCT r.payload->>'topic_key' AS topic_key
               FROM notifications n
               JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = %s
                 AND n.sent_at > now() - (%s || ' hours')::interval""",
            (category, cooldown_hours),
        )
        return {row["topic_key"] for row in cur.fetchall() if row["topic_key"]}


def _recently_notified_stories(conn, category: str, days: float = NOTIFIED_DEDUP_LOOKBACK_DAYS) -> list[dict]:
    """(entities, title) of every item in this category notified within the
    lookback, for the same-story test in get_new_candidates."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT DISTINCT r.id, r.payload
               FROM notifications n
               JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = %s
                 AND n.sent_at > now() - (%s || ' days')::interval""",
            (category, days),
        )
        out = []
        for row in cur.fetchall():
            p = row["payload"] or {}
            out.append({
                "title": p.get("title") or "",
                "ent": {"tickers": set(p.get("tickers") or []), "phrases": set(p.get("phrases") or []),
                        "figures": set(p.get("figures") or [])},
            })
        return out


def _category_notified_recent_count(conn, channel: str, category: str,
                                     window_hours: float = CAP_WINDOW_HOURS) -> int:
    """How many items of this specific category were notified to this channel
    in the trailing `window_hours` — category_config.soft_daily_cap is a
    per-category pace, not a channel-wide one (channel_config.soft_daily_cap
    is the separate combined cap, applied in bot/notify.py across all of a
    channel's categories -- see that module's _channel_notified_recent_count,
    same rolling-window mechanism)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT COUNT(*) AS n
               FROM notifications nf
               JOIN raw_items r ON r.id = ANY(nf.candidate_raw_item_ids)
               WHERE nf.channel = %s AND r.category = %s
                 AND nf.sent_at > now() - (%s || ' hours')::interval""",
            (channel, category, window_hours),
        )
        return cur.fetchone()["n"] or 0


def get_new_candidates(conn, category: str, channel: str) -> list[dict]:
    """Return raw_items (with score + breakdown + collected_at) that are new,
    clear threshold, aren't in cooldown, and still fit under this channel's
    rolling-window soft_daily_cap for the category — ordered by EFFECTIVE
    score (real score + age bonus) descending, highest first. The stored/
    returned `score` field is always the real one; the age bonus only
    affects this ordering, see _age_bonus's docstring."""
    cfg = load_category_config(conn, category)
    threshold = float(cfg["review_threshold"])
    cooldown_hours = float(cfg["cooldown_hours"])

    already_notified = _already_notified_ids(conn, category)
    cooling_down_topics = _topics_in_cooldown(conn, category, cooldown_hours)

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id AS raw_item_id, r.payload, r.collected_at, s.score, s.score_breakdown
               FROM raw_items r
               JOIN scores s ON s.raw_item_id = r.id
               WHERE r.category = %s AND s.score >= %s AND r.held = FALSE
                 AND (r.payload->>'assigned_channel' IS NULL OR r.payload->>'assigned_channel' = %s)""",
            (category, threshold, channel),
        )
        rows = cur.fetchall()

    candidates = []
    for row in rows:
        if row["raw_item_id"] in already_notified:
            continue
        topic_key = (row["payload"] or {}).get("topic_key")
        if topic_key and topic_key in cooling_down_topics:
            continue
        candidates.append(row)

    # Cross-CYCLE same-story dedup (2026-09-16, real incident) -- collectors'
    # own same-cycle dedup (collectors/news.py, collectors/macro_news.py)
    # only compares candidates gathered within ONE collect() call; it has no
    # way to catch a story a feed re-serves across SEPARATE collection
    # cycles, each time as a "new" raw_item with its own external_id. Real
    # case: "AI regulation faces political deadlock..." was collected from
    # bbc_world and bbc_business 7h41m apart -- two different collect()
    # runs -- and both ended up unheld and unnotified at the same time, so
    # they bundled into the same digest. topic_key alone isn't safe for
    # this (deliberately coarse, "same primary subject" not "same specific
    # event" -- pipeline/entities.py) -- collapsing by it here would risk
    # merging two genuinely different stories about the same broad subject.
    # Uses the identical precision-first test as collection-time dedup
    # instead (entity_lib.is_duplicate_story: exact title match OR a real
    # fingerprint overlap), just applied across every currently-eligible,
    # not-yet-notified candidate for this category, regardless of which
    # cycle collected it. O(n^2) in the (small, per-category, per-cycle)
    # candidate list -- fine at this scale.
    # Also compared against items already NOTIFIED in the lookback window
    # (NOTIFIED_DEDUP_LOOKBACK_DAYS): without that, a duplicate that lands in a
    # LATER cycle than its twin's notification is compared against nothing.
    notified_stories = _recently_notified_stories(conn, category)
    deduped = []
    for row in candidates:
        p = row["payload"] or {}
        title = p.get("title") or ""
        ent = {
            "tickers": set(p.get("tickers") or []),
            "phrases": set(p.get("phrases") or []),
            "figures": set(p.get("figures") or []),
        }
        # EXACT title only, not fingerprint: audited against every real sent
        # pair in the 7-day window (2026-09-21) -- all 5 identical-title hits
        # were genuine re-sends, but all 5 fingerprint-only hits were
        # different events ("Bitcoin falls below $76k" vs "Fed rate hike";
        # "House Democrats face divisions" vs "House passes bill").
        if any(entity_lib.titles_match(title, n["title"]) for n in notified_stories):
            continue
        if any(
            entity_lib.is_duplicate_story(ent, title, kept["_ent"], kept["_title"], CROSS_CYCLE_DEDUP_THRESHOLD)
            for kept in deduped
        ):
            continue
        row["_ent"], row["_title"] = ent, title
        deduped.append(row)
    candidates = [{k: v for k, v in row.items() if k not in ("_ent", "_title")} for row in deduped]

    # float(...) matters here, not cosmetic -- scores.score is NUMERIC, which
    # psycopg2 returns as Decimal; Decimal + the plain float _age_bonus
    # returns raises TypeError outright (verified live before shipping this).
    now = datetime.now(timezone.utc)
    candidates.sort(key=lambda row: float(row["score"]) + _age_bonus(row["collected_at"], now), reverse=True)

    if category in SUBJECT_CAP_CATEGORIES:
        seen_subjects, capped = set(), []
        for row in candidates:  # already sorted best-first
            subject = entity_lib.lead_subject((row["payload"] or {}).get("title"))
            if subject is not None and subject in seen_subjects:
                continue
            if subject is not None:
                seen_subjects.add(subject)
            capped.append(row)
        candidates = capped

    soft_cap = cfg.get("soft_daily_cap")
    if soft_cap is not None:
        already_recent = _category_notified_recent_count(conn, channel, category)
        remaining = max(0, int(soft_cap) - already_recent)
        candidates = candidates[:remaining]

    return candidates
