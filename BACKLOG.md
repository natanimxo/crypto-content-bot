# Backlog

Deliberately-deferred items — noticed and worth tracking, not yet implemented.
Now that this repo is pushed (github.com/natanimxo/crypto-content-bot), these
could move to GitHub Issues instead; until that switch is made, this file is
the one place to check.

## Scoring

- **DeFi yields: penalize implausible APY spikes instead of rewarding them.**
  Live-observed 2026-09-09: pools with 200%+ APY and 100+ percentage-point
  `apy_pct_7d` jumps (e.g. `raydium-amm WSOL-USDC`, `pepeteam-swaves SWAVES`)
  scored 87-90/100 — as high as a healthy, stable yield. `impact` saturates at
  33%+ APY with no ceiling-awareness, and `novelty` rewards a big 7d swing
  regardless of direction or plausibility. In practice this pattern usually
  signals unsustainable token emissions, not a real opportunity.
  Candidate fix: a penalty when `apy` is far above the category's own rolling
  median/percentile (relative, not a fixed cutoff — "high" depends on market
  conditions), which needs a rolling stat over recent `raw_items`, not just the
  single payload being scored. See TODO comment at
  `pipeline/score.py::score_defi_yields`.
  **Deliberately not implemented yet** — validate the MVP notify → approve →
  write → labeled-delivery loop end-to-end first, before touching scoring weights.
  (Validated 2026-09-10 — loop confirmed working; still not implemented, now
  just genuinely next-up rather than blocked on validation.)

## Telegram / bot reliability

- **Unresolved: Telegram callback_query taps sometimes don't appear in the next
  `getUpdates` poll at all, or appear with significant delay, and — once they
  do appear — often fail `answerCallbackQuery` with "query is too old and
  response timeout expired" even on the very first answer attempt (not just
  after a slow write delays it).** Live-observed repeatedly 2026-09-09 across
  Approve, Reject, Edit, and (briefly, before that flow was removed) Publish
  taps — e.g. a tap visible in one manual `getUpdates` peek was simply absent
  from the very next call moments later with the same offset; on other taps,
  the state change succeeded but the acknowledgment still came back stale
  immediately. No root cause identified — ruled out: our offset math (verified
  correct via direct API calls), the bot token mismatch, the allow-list, and
  (per a later, cleaner comparison) our own manual diagnostic polling being the
  cause of the very first instances, though that doesn't explain the pattern
  recurring on fresh, never-peeked-at taps later in the same session. Current
  mitigations only manage the *symptoms*, not the cause:
    - `bot/approval_poller.py`'s two-phase run() (ack every tap in a batch
      before any tap's slow LLM write) prevents one tap's latency from starving
      another's acknowledgment.
    - `_safe_ack()` ensures a failed acknowledgment never blocks or drops the
      actual state change / deferred work — this is why the pattern is no
      longer *functionally* blocking, but the underlying Telegram-side
      flakiness is unexplained and could still cause a tap to be silently lost
      if it never arrives in any poll before GitHub Actions' cron naturally
      moves the offset forward. Worth real investigation once this runs on the
      live 5-minute cron instead of manual polling, where the pattern might
      look completely different (or vanish, or worsen) under real timing.
    - Since Publish/Cancel no longer exists (2026-09-10 — see below), this is
      now scoped to Approve/Reject/Edit only, but the mechanism is identical
      and there's no reason to assume it's specific to the removed flow.

## Documentation

- **Update the technical spec doc** to reflect two implemented decisions that
  diverge from its original text:
    1. **No direct channel publishing.** Section 11 ("Publishing") originally
       specified the bot calling Telegram's `sendMessage` straight to each
       channel's `chat_id`. As of 2026-09-10 (operator direction) this was
       removed entirely — the bot never posts to a channel itself. The
       operator receives the final labeled text and forwards/copies it
       manually; "Publish/Cancel" became "Mark as sent/Discard", which only
       logs to `posts` for history/dedup. Sections 9 and 11 both assume
       auto-publish and should be corrected or annotated.
    2. **Label header on every delivered post.** Not explicitly speced —
       Section 10 covers the *notification* label (the digest eyebrow line)
       but not a label on the final written post itself. Since the operator
       now manually forwards content, every final post leads with
       `<category label> → <channel display name>` (e.g.
       "🌾 DEFI YIELDS → Crypto Notebook") so it's unambiguous which channel
       it's for. Worth folding into Section 8 or a new subsection, since it's
       now a real, load-bearing part of the operator workflow, not a nice-to-have.
  The spec doc itself lives outside this repo (per the README's note to
  Claude Code) — this entry is the pointer so it isn't forgotten.
