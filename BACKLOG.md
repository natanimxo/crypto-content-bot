# Backlog

Deliberately-deferred items — noticed and worth tracking, not yet implemented.
Once this repo is pushed to GitHub these could move to Issues instead; until
then this is the one place to check.

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
  write → publish loop end-to-end first, before touching scoring weights.
