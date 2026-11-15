# Cloud Clicker — New Year Update 2027

## Release rule
Release only after implementation, migration checks, automated tests, and Telegram Mini App smoke testing pass. Work on `feature/new-year-update-2027`; never deploy or merge directly to `main` as part of development.

## Existing player data
- Preserve player IDs, balances, lifetime earnings, lifetime taps, upgrade levels, daily claims, and leaderboard history.
- Do not reinterpret existing lifetime counters as per-era counters.
- New per-era counters start at zero for era 1 only if a safe backfill cannot be derived; document any backfill decision before migration.
- Era transitions are irreversible. Previous era state remains archived and read-only.

## Eras
1. Cloud Kingdom — currency Cloudcoin; existing game state.
2. Cosmic Clouds — currency Cloudmoney; unlock after 5,000 taps made specifically in era 1.
3. Sky Empire — currency Skycoin; unlock after 50 upgrade purchases made specifically in era 2.
4. Divine Dimension — currency Aetherium; unlock after cumulative 1,000,000 coins earned across eras 2 and 3. Spending does not reduce this earned-total counter.

The player manually confirms each transition. Show requirements and progress in a dedicated “Эпохи” tab. Taps, purchases, and earned currency must be tracked per era on the server. Never trust client-supplied progress.

The proposed four-times stronger starting upgrades for each new era need a concrete balancing formula before implementation; do not assume the display-only values (+5, +20, +80) are final.

## Winter presentation
- Apply winter/holiday visual styling consistently to every page without erasing each era's distinct identity.
- Enlarge the tap target and cloud art; add a Santa hat to the cloud and retain accessible button semantics.
- Keep the winter event active until the next major game update is released.

## Gifts and boosts
- Daily sent-gift limits by current era: era 1 = 5, era 2 = 10, era 3 = 15, era 4 = 20.
- At most two gift-granted boosts may be active simultaneously. A slot becomes available when an active boost expires.
- Gifts are special items only, never direct currency transfers. They may be decorative or grant a manually activated boost.
- Enforce limits, ownership, activation, expiry, and anti-abuse checks server-side.

## Winter event
- A 31-day daily login calendar. Each day has a distinct ticket reward.
- Claim requires a login on that calendar day; missed days expire and cannot be reclaimed.
- Use server-authoritative dates/timezone boundaries; do not trust device clock.
- Tickets are a separate event currency and can be spent on winter gifts and cloud accessories.
- Ticket reward values beyond the first two days remain to be specified. Confirmed examples: day 1 = 10 tickets; day 2 = 12 tickets.
- Include winter quests, a festive ticket shop, era-themed exclusive cosmetics, and a legendary final cloud reward.
- No community Christmas tree or collective tree progression.

## Social features
- Referrals with Telegram initData validation, self-referral prevention, abuse controls, and referral statistics.
- Achievements with rewards, badges, and titles.
- Friend gifts with inventory, rarity, sent/received history, and manually activated boosts.
- Leaderboards for earned coins, taps, referrals, achievements, and eras.
- Public profiles with avatar, nickname, frame, achievements, and gift collection; never expose Telegram IDs or private data.
- No pets.

## Engineering requirements
- Review the full current schema and every mutation endpoint before designing migrations.
- Use additive, backward-compatible migrations; test against PostgreSQL and SQLite where supported.
- Keep all rewards, eligibility checks, rate limits, and progression calculations authoritative on the server.
- Add idempotency/unique constraints for claims and gift actions.
- Add tests for boundary conditions, duplicate requests, concurrent claims/purchases, expiry, era transition irreversibility, and preservation of existing data.
- Keep the current production Render service untouched until a reviewed PR is merged and explicitly approved for deployment.
