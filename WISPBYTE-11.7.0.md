# HiQ Battler v11.7.0 — Ranked PvP

This release adds the requested Elo system that was missing from v11.6.3. It includes all previous updates.

## Commands

- `;pvp` — automatically fight the closest eligible Elo opponent using your default PvP format. The opponent can be any eligible registered player, not just someone in the current server.
- `;pvp 3v3` / `;pvp 6v6` — explicitly choose the automatic format. Slash PvP also has a format option.
- `;pvp @user [3v3|6v6]` — challenge a player using both saved PvP lineups for the chosen format. Your default applies if omitted.
- `;pvpset` / `;pvps` — choose separate 3v3 and 6v6 PvP teams from your normal saved teams, choose the default format, and lock either or both lineups.
- `;pvpset <team slot>` — copy that complete normal team into its matching PvP slot and make its format the PvP default.
- `;pvpboard` / `;pvpb` — open this month's shared Elo leaderboard, with page arrows.

All commands have slash forms. Existing tutorial and command cooldowns still apply. There is no queue that requires another person to be online; saved teams fight automatically.

## Matchmaking and ratings

There is **one shared monthly Elo rating** across 3v3 and 6v6. Battles always match equal formats. New monthly ratings start at **1000**, with **K=32**. At equal ratings, a decisive match changes Elo by ±16. Draws use a score of 0.5, and underdog wins grant a larger increase. Rating changes are equal and opposite.

Automatic matchmaking requires valid owned lineups, available players and equal formats. It selects the smallest Elo difference; equally close opponents are chosen randomly. It excludes an opponent if either participant appears in the other's last **five completed rated matches**, including rated targeted matches. Both players must therefore play at least five other rated matches before an automatic rematch. This history carries across monthly resets. Practice does not clear the restriction. If no eligible player exists, the command says so rather than relaxing the rule.

**Targeted PvP can affect each player's Elo only once per game day.** The allowance applies to both participants, including the defender, across both formats and all targeted opponents. If either player already used it, the match is practice for both; neither player's allowance is spent by that practice match. Automatic matches remain rated regardless of targeted allowance use. Chosen targeted opponents are controlled by this daily limit; they are not restricted by the automatic matchmaking lookback.

The existing PvP coin reward calculation, unique-opponent restriction and daily cap are retained for both rated and practice battles. This update does not change coin amounts. Repeated results never grant duplicate Elo or coins.

## PvP teams and locks

On first use, an existing complete active team becomes the initial PvP team for its format. Previously unconfigured players with a complete active team can therefore be matched without manually migrating.

`;teamuse` still changes the normal active team. It also updates the matching PvP lineup and selects that PvP format, unless that lineup is locked. The other format's lineup is preserved. The 🔒/🔓 buttons in `;pvpset` protect automatic replacement; explicit manual selection in that menu can change a locked lineup while retaining its lock.

PvP lineups store card references separately from normal saved team slots. Editing or clearing a normal saved team does not silently rewrite an existing PvP lineup. To apply an edited normal lineup, select it again in `;pvpset` or use `;teamuse` while unlocked. Unit merges, natures and equipment use the actual owned cards when a match begins. Merged-card references are repaired, and removed/traded cards invalidate the affected PvP lineup until updated. Stale menu edits are rejected.

Targeted format checks remain private. PvP still uses the normal PvP battle rules, including Kenma's eight-stack Fatigue cap.

## Monthly reset and prizes

Seasons and targeted daily limits use the existing game timezone (America/New_York by default). At the start of a new month, the ladder starts at 1000 again. Previous ratings and match history are retained as records.

The top **three** players with at least one rated match each receive **one 5★, 4★, 3★, 2★ and 1★ Star Pack** in `;inv`. Use `;open <item ID>` normally. Equal Elo is ordered by more wins, then fewer losses, then player ID. There are no placement-game or minimum-game requirements beyond having played one rated match.

A background check runs every minute after startup. PvP and leaderboard commands also check pending monthly payouts, so restarts cannot lose prizes. In-flight rated matches remain in the month when they began and finish before that month's prizes settle. Awards and the closed-month marker commit together, preventing duplicate packs on retries.

## Persistence and installation

Both participants are reserved while a match is running. Teams, seed and result settlement are persisted; retries resume the original match instead of rerolling it. A restart recovers unfinished simulations. Elo, coins and history settle atomically before result rendering. A rendering failure still leaves the result settled and sends a text/log fallback when possible.

Upload the **complete v11.7.0 Wispbyte ZIP**, preserving the existing database, .env and host settings. Stop and back up the bot first, replace the code/data folders, and restart with `python bot.py`. New PvP tables are added automatically without resetting cards, inventory, currency or other modes. No dependency was added. The website is already published and does not need a new upload for this PvP release.

v11.6.3 and earlier guides that state Elo is outstanding are historical; this guide supersedes that status. Unrelated pre-existing audit issues remain unchanged.

## Validation

All 234 local tests complete successfully, with the same five pre-existing expected audit failures. The 23 new PvP tests cover Elo math, real seeded simulation, exact opponent selection, bidirectional repeat avoidance, daily targeted limits, separate team formats, locks, stale controls, card lifecycle, ownership checks, concurrency, restart-safe settlement, monthly resets and pack payouts, leaderboard pages and prefix parsing. Runtime validation, all 23 battle-rule groups and offline registration of 76 slash commands pass.
