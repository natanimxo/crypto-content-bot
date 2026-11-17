# Handoff — 2026-11-16

For a fresh Claude Code session picking this project up cold. This is STATE + the rules the operator has set + what is open. `BACKLOG.md` is the long decision log (read its
"READ FIRST -- traps" block, then the section for whatever you touch); `intelligence-bot-spec-v2.md` is the original design. The previous handoff (2026-09-11, stale: it says
"not yet live") is kept at `docs/HANDOFF-2026-09-11-archive.md` for the bug history of the first three categories.

## STATUS AT THE END OF THE 2026-11-16 SESSION (read this first)

The session was cut short by the operator's usage limit. Nothing was left half-rewired in committed code: HEAD passes all four regression scripts (`check_earning`, `check_sources`, `check_exchange_listings`,
`check_voice_checks`) and the cycle scripts import cleanly. The poller was healthy (last `approval_poll` success 12:42 UTC). **No more test or regenerated cards are being sent**: the one-off regeneration scripts
(scratchpad, not in the repo) finished; nothing in the repo sends test cards. (The daily below-the-line digest and the normal cards are production features and will still send.)

### LIVE NOW (committed, pushed, running on the Railway cron services)
- Earn Stack: guaranteed / raffle / competition classification with prize structure + odds in the ORIGINAL currency; skill fact; competitions lowest priority and at most 2 a day; the third floor (expected payout >= $10,
  or for competitions a largest-prize floor of $100); referral counts in value; recruitment-shaped offers get a trust penalty; the daily below-the-line digest with an Approve button per item; Superteam / Zindi / Immunefi / Devpost / Doctor of Credit / 4 airdrop channels.
- Earn Stack display name (key `coincraft`, chat_id unchanged). Hustle shares 2 / 2 / 1 / 1 seeded into the DB (the cap change is already in effect).
- New collectors wired into `scripts/collect_cycle.py` (they ran from the 2026-11-17 04:00 UTC cycle on): Whale Alert (`collect_whale_alert`), ZachXBT (`collect_zachxbt`), Wu Blockchain + Cointelegraph inside `collect_news`,
  Binance Announcements corroboration inside `collect_exchange_listings`, `collect_startup_news` (+ scorer, facts, template, config, channel wiring). Verified locally and then from Railway (see below).

### HALF-DONE (where it stopped)
- **Railway verification: DONE (2026-11-17 04:05 UTC cycle).** All five new channel sources serve from Railway with the same freshness and posts-per-day as locally, and every new collector ran and stored scored items
  (whale_alert 14, wublockchain 10, cointelegraph 13, zachxbt 1, startup_news 5). Hustle sent exactly 2 / 2 / 1 / 1 in its first 24 hours. The `collect-earning` service is healthy (every 2 hours, last 04:33 UTC).
- **Cards the operator already received in the old competition format**: regenerated in place where possible (two Zindi candidate cards were edited; two Devpost drafts were regenerated and re-sent, one header message was sent twice).
  Two drafts first went out with WRONG claims and were corrected: "one $25,000 prize" (Devpost's `prizes_counts.cash` is prize categories, not winners) and "needs machine learning skill" for a Photoshop design contest (a theme tag is not a
  requirement). The Meta VR Start draft was already published, so it could not be regenerated and still carries the OLD wording ("average per participant" style): check it before forwarding.
- The `skill` fallback fix (a Devpost theme tag must not become a skill) is committed.
- `BACKLOG.md` now has the section "Channels as sources and Phase 6 startup_news" (the Railway table, what was not built and why, the Cointelegraph reversal).

### STILL TO DO (the operator's open asks, in the order he gave them)
1. **Earn Stack "full control" spec -- NOT STARTED.** The operator's words (relayed at the end of the session): remove the floors, caps and gates; a money-only score; everything else becomes a LABEL; a digest with Approve / Skip; and a
   command that lists what was dropped. I have no copy of the detailed spec in this session's conversation, only that summary: get the spec text from him before building. NONE of it is implemented: the floors (trust 40 / value 40 / expected
   payout $10 / competition $100), the cap of 8, the 2-a-day competition cap, the hard gate and the recruitment penalty are all still in force. Hard gates he has said stay out: seed phrase / upfront fee (forgery and KYC-evasion he handles by hand).
2. **Referral / recruitment-shaped offers: the operator's latest statement is that he does NOT want to cut referral offers or ANY recruitment-shaped offer.** This reverses the 2026-11-16 ruling that gave recruitment-shaped offers a 30-point trust
   penalty (the Raenest bounty was expired for it). NOT implemented: the penalty is still in `pipeline/earning.py`. Under the full-control spec these become labels ("recruitment-shaped", "referral promotion") on the card, not penalties and not exclusions.
3. **Phase 6 + the channel builds: BUILT, committed and VERIFIED from Railway.** Still outstanding there: the startup_jobs name link is built strictly but unproven (expected 0-1 a month); `startup_news` "notable launches" were deliberately not built.
4. Nov 23 report (scheduled task `earn-stack-draft-attempts-nov23`), Discord (waits on the operator's server and bot), the Bybit-from-Railway decision.

### The operator's decisions this session, for the record
- Competitions: show prize structure and odds, never pool / entrants; pool / entrants only for raffles; never convert currency; "skill required" fact; fewer competitions (priority + 2 a day) -- DONE.
- Channels: build Whale Alert (transfers only, tx-hash dedupe), Wu Blockchain, Cointelegraph (reverses his September exclusion: deliberate), ZachXBT, Binance Announcements (corroboration only); skip Cryptopolitan, ICO Drops, CryptoRank, CryptoQuant,
  CoinMarketCap News, TON Community, CertiK -- DONE (built and verified on Railway).
- Phase 6: startup_news from Tech.eu / Sifted / TechCrunch Venture RSS, skip @tech, Hustle shares 2 / 2 / 1 / 1, startup_jobs name link at low priority -- DONE (built and verified on Railway).
- Skip the forgery / KYC-evasion gate (he handles those by hand) -- honoured.
- Full-control spec and "keep every referral / recruitment-shaped offer" -- recorded above, NOT done.

## What this is

A pipeline that collects items, scores them, sends the operator a candidate card on Telegram, and on **Approve** writes the post (DeepSeek) and sends a forwardable preview.
**The bot never posts to a channel**: the operator copies / forwards the text himself (`pipeline/publish.py`). Nothing reaches a reader without his tap.

Stack: Python on Railway, Supabase Postgres, DeepSeek (`deepseek-v4-flash` writer; `deepseek-flash` + the `deepseek_read_url` tool for landing-page reads), Telegram bot polling.

## Railway services (project `fortunate-success`, production)

| Service | What | Schedule |
|---|---|---|
| `crypto-content-bot` | always-on poller (approve / edit / reject taps, previews) | always on; redeploys on every push (~60-90 s gap) |
| `collect-cycle` | `scripts/collect_cycle.py`: every collector -> score -> notify for ALL channels | cron `0 */4 * * *` |
| `collect-earning` | `scripts/collect_earning.py`: earning_opportunities only -> notify Earn Stack, then the below-the-line digest (once a day), source probes | cron `30 */2 * * *` |

IaC is `.railway/railway.ts`; `railway config apply` is authoritative. A cron service must EXIT. A service is not "running" until it has a `run_logs` row: `collect-earning` ran
twice doing nothing because its `DATABASE_URL` was the placeholder `db.xxxxxxxxxxxx.supabase.co`. `railway ssh` is not used (needs a registered key).
**Verify every source FROM RAILWAY** (a dev machine and Railway see different data: OKX served a regional feed, Bybit and Coinbase Learn answer 403 from Railway). Daily probes are in
`run_logs` jobs `source_probe` and `telegram_probe` (`pipeline/source_probe.py`); both re-run whenever their candidate set changes.

## Channels -> categories (config/channel_config.yaml; `chat_id`s live there)

| Key | Displayed as | Categories (daily caps) |
|---|---|---|
| `crypto_wall_street` | Crypto Wall Street | news (shared with Alpha Edge), whale_movements, exchange_listings (cap 4) |
| `alpha_edge_crypto` | Alpha Edge Crypto | news (shared), defi_yields |
| `crypto_notebook` | Crypto Notebook | gems_security, hacks_exploits |
| `hustle_to_million` | Hustle to Million (cap 6) | tool_launches **2**, startup_jobs **2**, macro_news **1**, startup_news **1** (a category's own cap IS its share) |
| `coincraft` | **Earn Stack** (renamed from CoinCraft 2026-11-16; the key and chat_id never changed) | earning_opportunities (cap 8; competitions at most 2/day, lowest priority) |

Voices: `config/channel_voices.yaml` (one profile per channel; the category supplies facts + guardrails, the channel supplies voice; guardrails beat voice). Config is YAML in git, upserted
into the DB by `scripts/seed_config.py` (`seed_categories`, `seed_channels`, `seed_voices`); the DB row is what runs.

## Sources by category (the Telegram / RSS sources added 2026-11-16 were verified from Railway on 2026-11-17)

- **news**: CoinDesk, The Block, Decrypt, Unchained, The Defiant, Protos (RSS) + **Wu Blockchain** (`@wublockchainenglish`, tier 3) + **Cointelegraph** (`@cointelegraph`, tier 2). Cointelegraph reverses the
  operator's 2026-09-11 exclusion (deliberate, 2026-11-16). Telegram posts go through the same dedup, a text-based opinion / sponsored / promo gate, and the copyright guard.
- **whale_movements**: Etherscan wallet watch + **Whale Alert** (`@whale_alert_io`): transfers only (never mints, burns, news posts, or a transfer with no exchange side), parsed deterministically,
  **deduped by tx hash both ways**. Adds BTC and other chains.
- **hacks_exploits**: DefiLlama `/hacks` + **ZachXBT** (`@investigations`, tier 3): only a post stating a loss with an amount >= $500k; the claim is attributed to him and never stated as confirmed;
  deduped against DefiLlama by protocol name.
- **exchange_listings**: Binance / OKX / Bybit / KuCoin announcement APIs (spot only; Bybit is blocked from Railway). **Binance Announcements** (`@binance_announcements`) = corroboration only: it sets a flag, it never creates an item.
- **earning_opportunities** (income only): Devpost, Doctor of Credit, Superteam Earn, Zindi, Immunefi audit competitions, four airdrop Telegram channels. See "Earn Stack rules".
- **startup_news** (Phase 6): Tech.eu, Sifted, TechCrunch Venture RSS. Funding rounds with a STATED amount and acquisitions only; amounts shown exactly as the outlet wrote them.
- **Rejected on purpose (do not re-propose without new facts)**: Cryptopolitan (half price-bot posts, ~60 views), ICO Drops (news, not offers), CryptoRank / CryptoQuant (analysis, no category), CoinMarketCap News / TON Community /
  CertiK (no live public channel found), @tech (1 funding amount in 123 posts), Galxe, exchange campaigns (KuCoin/Binance/Bitget), bug-bounty platform feeds (kept in BACKLOG "available if volume disappoints").

## Earn Stack rules (operator-set; the scoring is deliberate)

1. **Two scores, never blended**: `value` (what it is worth) and `trust` (checkable facts). Selection = trust >= 40 AND value >= 40 AND (expected payout >= $10, or for a competition the largest prize >= $100; UNKNOWN expected payout passes
   and scores 20-25 on the payout component). Order by value. Both numbers, plus referral / KYC / skill facts, are on every card.
2. **Award kind** (`pipeline/prizes.py`): `guaranteed`, `raffle` (pool / entrants is meaningful: the only kind with an expected amount), `competition` (prize structure + odds shown, no expected amount, e.g.
   "Top 3 share €25,000 (€12k / €8k / €5k) · ~142 entrants · 3 winners"). **Amounts are ALWAYS shown in the original currency, never converted.** Devpost's `prizes_counts.cash` is NOT a winner count (it counts prize categories).
3. **Hard gate** (item stored HELD, never a candidate): seed phrase / key, connect-wallet-to-claim, upfront fee, roundups, not income (free games, freebies, credits). The wallet rule is NARROW (claim-shaped only) for exactly
   `superteam` / `zindi` / `immunefi`; broad for every relay (the Telegram channels, Devpost, Doctor of Credit). **No forgery / KYC-evasion gate: the operator handles those by hand** (e.g. the PumpFun posts).
4. **Referral** (the recruitment penalty below is SUPERSEDED by the operator's last statement -- he wants none cut; see "STILL TO DO" #2; it is still in the code): counts in value (<= 12 points, added on top); availability is read from the offer TEXT only (a code on the source's own link is "unconfirmed"); referral parameters are still stripped from every stored link (the operator adds
   his own). **Recruitment-shaped** (referral earnings above the base payout, multi-level / downline wording, or required work = promoting with your OWN referral code): no bonus, trust -30, no official-source exemption.
5. **Token-denominated reward** (not a stablecoin): an unclear amount, never priced, value capped at 30 (below the floor).
6. **Selection**: non-competitions first; competitions only fill slots nothing else wants, at most 2 per rolling 24h. One card per 2-hour cycle.
7. **Daily "below the line" digest** (`bot/below_line.py`): every item that failed only a SOFT floor, with scores + reason + an Approve button into the normal write flow. Outside the cap (`notifications.kind = 'below_line'`; every cap
   query counts only `kind = 'candidate'`). `python -m bot.below_line --dry-run --hours 72` previews it.
8. Cards: curated-find voice (a "why this one" clause drawn ONLY from the facts' "what stands out" line), band 400-750 characters, 9 retries. `earning_draft` rows in `run_logs` count attempts.
9. Any calibration data on this category from before 2026-11-16 is SUSPECT (a parser that could not read "50$" or Russian / Portuguese deposits, before the referral bonus and the competition change).

## Standing operator rules (all categories)

- Flag mistakes and surprises unprompted; confirm a plan before a big build; do not widen sources when you can tighten floors; near misses leave a floor alone; "unknown is never coded as clear".
- Never state an inference as a fact on a card. Never relabel or convert currency. Never reproduce a source's wording (copyright guard). No investment advice anywhere.
- A source is not verified until it has run from Railway. Dead sources flag themselves (stale-feed alerts); do not chase the ones already recorded as dead.
- Hustle shares of its cap of 6 are 2 / 2 / 1 / 1; the channel cap rarely has to choose.

## Open items / dates

- **2026-11-23 09:00 local**: a scheduled one-time task (`earn-stack-draft-attempts-nov23`) runs `python scripts/earning_report.py --drafts --days 7` and REPORTS mean attempts, the share needing 4+, and any source that keeps
  failing. Decision rule: if most cards need 4+ attempts, WIDEN the 400-750 band rather than keep retrying. It only recommends.
- **Discord** (Earn Stack): waits on the operator creating the server and bot.
- Bybit from Railway (403): honest "not tracked after <date>" wording, a non-US proxy, or drop it: undecided.
- Penalty-ignores-odds for giveaways (a design question), a competition size floor of $100 (the assistant's choice, one constant: `prizes.COMPETITION_MIN_TOP_PRIZE_USD`), the connect-wallet gate beyond the three hosted platforms: all in BACKLOG.
- startup_jobs name link on startup_news: built strictly, expected ~0-1 a month; verify it ever fires before investing more.
- `scripts/check_reproduction_masking.py` reports 2 PROBLEM cases and did so before the 2026-11-16 changes (it reads real DB items); not caused by them, not investigated.

## Traps (the expensive ones)

- **Python in a bash heredoc halves backslashes** on this Windows setup: `\\n` becomes a real newline, `\\b` a backspace character. Write regex / string edits with the Edit or Write tool, or a script file; then `grep -c $'\x08' file`
  and `python -c "import ast; ast.parse(...)"`. This corrupted five files before it was caught.
- Local DNS can drop (scripts fail); `railway config plan` fails on an IaC CLI-version check (not applied); `SIGALRM` does not exist on Windows; DeepSeek's balance lags by minutes (see BACKLOG READ FIRST).
- Telegram `t.me/s/<handle>` shows only the newest ~20 posts; a handle that answers 200 may be a hijacked or inactive channel (CoinMarketCapNews): check the newest post date AND the title.

## How to check things

```
python scripts/check_earning.py       # earning scoring / gate / prizes / digest / selection (no DB, no network)
python scripts/check_sources.py       # Whale Alert / news Telegram / ZachXBT / Binance corroboration / startup_news / Hustle shares
python scripts/check_exchange_listings.py ; python scripts/check_voice_checks.py
python scripts/earning_report.py [--unproven | --drafts --days 7]
python -m bot.below_line --dry-run --hours 72
python -m pipeline.source_probe [probe ids]          # local; Railway runs the same list daily
```
Railway: `railway logs --service collect-earning --lines 100`; `railway variables --service <name> --kv` (never print secrets); the DB is the same Supabase for all services, so a local script that writes is a production write.

## Where the history is

`BACKLOG.md` sections, in order of build: Phase 0-4 (reliability, voices, channel remap, exchange_listings), Phase 5 (earning_opportunities, rounds 1-9, the source research, the platform sources, referral, competitions),
"Channels and Phase 6" (this session's last block).
