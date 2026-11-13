# HiQ Battler v12

Discord auto-battler using Python 3.12 and SQLite. Run `pip install -r requirements.txt`, set `DISCORD_TOKEN` in the host environment, and start `python bot.py`. Existing database files are upgraded automatically without resetting wallets, collections, equipment IDs, or earned daily caps.

## Deploying from GitHub to Wispbyte

1. Stop the bot and back up the current SQLite database, then install the v12 runtime or changed-files update.
2. Keep the existing `DISCORD_TOKEN`, database, and `HIQ_DB_PATH`. Store the database on persistent host storage, outside any checkout directory that the deployment replaces.
3. Install `requirements.txt` and restart with `python bot.py`. The existing Dockerfile also supports deployment.
4. Verify `;questview`, `;raid`, `;clanwar`, and `;sealed` in a test channel. Slash commands synchronize at startup; prefix commands remain available if Discord sync fails.

No Discord token or production database is included in the repository.

## Latest release: v12.1.6 — AF1 rotation and Takeru's challenge team

AF1 now shows each player's next opponent instead of a CPU selector. Victories
advance through all twelve challenges, looping after the last; losses, draws,
and abandoned battles keep the same opponent. Progress survives restarts.
Pending battles resume their original opponent, and saved AF1 teams are retained.
Winning still advances at the daily Mark cap. Hinata's loss rewards do not advance
the rotation.

Takeru's Tiny Titans challenge now uses the requested all-+10 roster: Akifumi,
Oikawa & Kageyama, and Miracle Maker Hinata in front; Takeru, Atsumu, and Daichi
in back, with their specified equipment and traits. Zunda Mochi Bento's recipe
and shard allowance are unchanged. This release includes the earlier v12 updates,
including SQLite retention and the offline cleanup tool.

## v12.1.5 — Daisho, Takeru and Zunda Mochi Bento

Daisho's Ultimate no longer checks Speed; its guaranteed follow-up requires a
visible stat penalty when the Ultimate triggers. Takeru retains the original
Spd ≥ foe Spd−4 condition and Rally Surge restores 20 HP. His new exclusive
Zunda Mochi Bento reduces maximum cooldown once per match, grants Qik/Spd+5,
and grants Inspiration on an Ultimate trigger. Inspiration halves non-Ultimate
percentage damage reduction, including equipment/statuses; Ultimate, flat and
fixed-damage protection remain unchanged. Earn Bento shards in the appended
Takeru Tiny Titans challenge and craft with the standard signature recipe.
See V12.1.5-DAISHO-TAKERU.md.

## Previous release: v12.1.4 — reward fixes

Tournament wins and four-human participation now have independent allowances;
every human entrant earns eligible participation rewards. Regression checks confirm
Young Emperor's extra Gauntlet clear includes its fresh-team bonus. Capped
reward messages also cover Furious Upstart's successful loss-reward rolls, and
PvP displays those loss rewards. Existing balances and used caps are preserved.
See V12.1.4-REWARD-FIXES.md.

## Previous release: v12.1.3 — AF1 results and cumulative update

AF1 results now show the two mains' damage dealt, tanked and mitigated, with each
side's supports in a two-row, three-column grid underneath. Results include the
winner's trophy, turns, AF1 Marks and battle-log access, without OPOM/DPOM awards.
The cumulative archive includes all v12.1.0–v12.1.2 changes. Apply its changed-files
ZIP directly over v11.7.4 (with or without the Sakusa v12.0.0 patch), or use the
full runtime with your existing database. See V12.1.3-CUMULATIVE.md.

## Previous release: v12.1.2 — quest earnings guide

`;qv` has a fifth page explaining Young Emperor, King of the Lost Court and
the Duo's effects on earnings, including merge scaling, caps and ownership.
Reward calculations are unchanged.

## Previous release: v12.1.1 — Duo search and emoji

Searching Oikawa or Kageyama in collection/gallery now includes their Duo.
Duo card headers and collection rows use the supplied custom Duo emoji.
Collection reward bonuses depend on current ownership; removing the last copy
stops future bonuses without undoing earned rewards or resetting used caps.

## Previous release: v12.1.0 — gameplay expansion

All five new characters are implemented: Ikejiri — Old Friend, Hinata — Furious
Upstart, Oikawa — Young Emperor, Kageyama — King of the Lost Court, and the
Oikawa & Kageyama — Kings of the Playground Duo. The four regular characters
are available through normal matching-rarity pulls; the Duo is created with
`;fuse` using its two matching-merge ingredients and capped AF1 Marks.

Includes their combat and reward effects, shiny artwork unlocks/selection,
shiny-slot merging, dismantling, persistent equipment autoclean, tradeable PvP
Shields, bulk clan-shop purchases, weekly five-week PvP prizes, team recovery,
Tiny Titans shard limits/formatting, and revised help pages. Sakusa's 50% change
and the complete SQLite retention/maintenance update remain included.

See [V12-GAMEPLAY-UPDATE.md](V12-GAMEPLAY-UPDATE.md) for installation, exact
mechanics and the new commands. Existing databases upgrade automatically;
do not upload an empty database or delete the existing one.

## Previous release: v12.0.0

Sakusa — Coiled Precision now has a 50% follow-up roll per initial critical hit
when initiating combat. The once-per-combat restriction and all other Sakusa
mechanics are preserved. See [V12-SAKUSA-PATCH.md](V12-SAKUSA-PATCH.md).

## Previous release: v11.7.4

See [WISPBYTE-11.7.4.md](WISPBYTE-11.7.4.md) and [STORAGE-RETENTION.md](STORAGE-RETENTION.md).
Completed battle/session details become eligible for cleanup after 30 minutes;
data-changing activity schedules shared delayed batches instead of continuous polling, preserving permanent
progress, ratings, scores, clan records and small reward safeguards. Includes a
read-only table-size report and verified-backup cleanup/compaction tool for existing
large databases. Active sessions and original character definitions remain recoverable.

## Previous release: v11.7.3

See [WISPBYTE-11.7.3.md](WISPBYTE-11.7.3.md). Tiny Titans (`;tt`), All For One (`;af1`) and both Gauntlets (`;gn`) now use five-second cooldowns. Event timers remain independent and run from battle start.

See [WISPBYTE-11.7.1.md](WISPBYTE-11.7.1.md) for Sakusa’s initiation-only B, the standard illustrated Tiny Titans 6v6 result, and the inventory category selector. Signature equipment and event shards have separate categories; search, arrows and stacking/ID controls remain available.

See [WISPBYTE-11.7.0.md](WISPBYTE-11.7.0.md) for the shared monthly PvP Elo ladder. `;pvp` matches the closest eligible player in the selected format; `;pvp @user` is rated only if neither participant has used their targeted rating allowance that day. Further targeted matches are practice. `;pvpset` / `;pvps` selects and locks separate 3v3 and 6v6 teams; `;pvpboard` / `;pvpb` shows the monthly standings. The top three each receive one pack of every rarity, and Elo starts fresh at 1000 each month.

See [WISPBYTE-11.6.3.md](WISPBYTE-11.6.3.md) for `;links`, inventory search and 25-entry pages. Sakusa’s per-critical-hit follow-up checks (now 50% in v12) and the website tier maker from v11.6.2 remain included. His 50 Pow, supplied artwork and position-based equipment restrictions from v11.6.1 are retained. The event modes and signatures from [v11.6.0](WISPBYTE-11.6.0.md) remain included. `;tt` opens four Tiny Titans 6v6 CPU challenges. Each clear has a 50% chance to drop one matching signature shard. There is no event shop. `;craft` makes each signature for 25 item shards, 25 matching character shards and 500 coins. `;items` shows all four signatures and their effects.

`;af1team` edits five saved AF1 teams, `;af1use <slot>` selects one, and `;af1` shows your next CPU opponent in a persistent twelve-team rotation. Win to advance; losses and abandoned runs keep the same opponent. After the final team, the rotation repeats. AF1 uses main HP ×6, inactive supports in unrestricted rows, PT self-targeting and two effectless Guiding Break stacks. Fukunaga is banned from AF1 and legal in Tiny Titans. `;eventreset` abandons a pending event without rewards. AF1 awards 1 Mark per successful run, up to 25 per day, for Duo fusion.

Sakusa — Coiled Precision and the Suga, Lev, Aran and Tanaka signatures are implemented. Sakusa gains Perfectionism after combat and uses independent preliminary and actual-hit crit rolls. His portrait and icon are included. Ranked PvP now uses the shared monthly ladder described above.

See [WISPBYTE-11.4.1.md](WISPBYTE-11.4.1.md) for the retained PvP-only eight-stack Fatigue cap and the requested Akaashi, Oikawa, Ushijima and Yaku art updates. All other battle modes retain uncapped Fatigue.

See [WISPBYTE-11.4.0.md](WISPBYTE-11.4.0.md) for the preceding release's deployment instructions and rules. Clan Contest weekly prizes now use the sum of daily percentages, with the daily leader at 100% for both players and clans. `;cb` shows the daily breakdown. `;market`, `;auction` and `;myauctions` provide cross-server auctions with Coin/Clan Dollar bids, optional buyouts and restart-safe escrow. School/character shards can be listed; item shards cannot.

Merges replace consumed cards in saved teams. A confirmed +10 donor transfer keeps the survivor's nature. `;teamedit <slot> <new name>` renames a team; omitting the name opens the existing editor. The tournament CPU pool includes the equipped Echigo/Hinata/Akifumi/Atsumu/Kageyama/Teradomari formation. Only Teradomari is +10. The v11 sections below describe historical changes; the latest release guide takes precedence where rules changed.

## v11 changes

### Weekly rollover for activity rewards

Unused **Raid, Tournament, Regional/National Gauntlet, and Prefecture** reward allowances carry forward within the current calendar week. One day's allowance unlocks at local midnight; future days are never unlocked early. On Sunday, a player registered by Monday can use all seven days' remaining allowances before the Monday 00:00 reset in the configured game timezone (America/New_York by default). New players accrue from their registration day. Unused allowances expire at that weekly reset rather than carrying into another week.

Existing partial completions are deducted from the bank. Each Gauntlet's 150-coin and 50-coin clear allowances accrue separately, with the remaining 150-coin clears used first. Tournament allowance is three paid championship wins per elapsed day across both modes; CPU/human payouts stay 100/150. Prefecture accrues six paid wins (300 coins) per elapsed day. Completing extra unpaid activities does not consume future days' allowances.

Raids accrue 300 coins, 300 clan coins, 50 character shards, and twice each item's recipe cost in item shards per elapsed day. All raids share these limits, each drop rechecks the remaining bank, and character shards retain the independent 350/week cap. Currency and shard awards remain random drops. `;questview` and `;items` show accrued limits and usage. Rewards, item/card IDs and already-earned cap usage are preserved through deployment and restart; no reset migration is needed.

Daily packs, donation limits, clan check-ins, normal battle, PvP, and war rewards retain their daily reset behavior. Training's existing weekly rules are unchanged.

- Battle wins: first five pay 50 each, next 45 pay 4 each (430/day). Regional and National Gauntlets each pay 150 then 50 (200/day). War victories pay 200 coins and 200 clan coins, once daily per player.
- Clan check-in pays 150, plus a 50 bonus when all five members check in. Donation cap: six/day.
- Raids: Easy always rolls one drop; Hard rolls one/two at 70%/30%; Extreme rolls one/two/three at 5%/40%/55%. Each slot independently draws from the remaining eligible rewards; duplicate drops are allowed. Currency drops award up to 100, capped at 300 coins and 300 clan coins daily across all raids. Existing item and character shard caps remain shared and are checked between slots.
- Character shard rarities: Easy 40/30/25/4/1% for 1–5★; Hard 30/25/20/15/10%; Extreme 27/40/33% for 3–5★. Easy and Hard enemy lineups are preserved. Extreme has five new fully equipped +10 teams, one per theme, and supports 6v6 only. Its Recovery lineup uses Kiryu — Last Hope. Extreme retains Hard's base reward-type weights; no separate type weights were specified.
- `;recycle water bottle 5` recycles exactly five unequipped copies, or fails without deleting anything if insufficient copies exist. Recycling by card/item ID and `;allrecycle name` still work.
- `;inv toggle` (also `;inv view`) switches between stacked counts and individual IDs. `;inv stacked` and `;inv regular` select a view explicitly. The choice is saved; IDs remain valid for `;equip`.
- `;allcraft` crafts all complete item and character recipes. Crafted characters are Neutral +0. School shards are never spent.
- Disruptive Whistle applies Pow/Qik−4 to the foe's line through next actions. Firm Contact adds +3 once per attack, including counters and follow-ups; a two-hit attack receives the bonus once. Loaded Reception grants Def/Rec when defending. Rock Solid Game Plan's description now matches its existing Pow/Qik−3 behavior.

## Clan wars

Use `;clanwar` for a selector, or `;clanwar clanID 3v3 quick` / `;clanwar clanID 6v6 long`. Quick uses the first three distinct players sent by each clan; Long uses five. Both formats retain best-of-three individual matches and winner-first sequential sends after the opening round.

Leaders and co-leaders can submit bans and sends. Both challenged officers receive the offer by DM and can accept/decline there or in the channel. Channel boards show each side's deadline.

The accepted war opens a 24-hour ban window. Both missing bans cancel without a result; one missing ban forfeits. Completing bans opens both initial send windows. Each send and its player's team submission share one 24-hour deadline; selecting a player does not extend it. Sent players can submit immediately, including before the opposing send. Later rounds open the winner's window first and the opponent's window when the winner sends.

A missing send forfeits the war; two missing opening sends cancel without a result. A sent player who misses team submission takes an automatic 0–2 match loss and becomes ineligible. If both players miss submission, both are eliminated and neither receives a match win. Boards display **Auto-loss due to inactivity** instead of a numeric defeat. If both remaining rosters are exhausted together, the war cancels without a clan result. Deadlines survive restarts; legacy active wars derive their first deadline from their last saved update.

## Sealed Draft

Use `;sealed @User1 @User2 …` or `/sealed` with 2–7 mentioned human participants. All 65 current variants in `characters.json` are eligible; exactly eight unique variants per entrant are sampled without replacement, leaving the rest unused. Passing order is randomized. Picks pass immediately, with private active packs and FIFO queues. Channel controls remain available if DMs are closed.

After eight picks each, use **Edit Team** to place six drafted variants into Front Left/Middle/Right and Back Left/Middle/Right. Final teams allow only one variant of each player name. Pool creation limits extra variants to two across the selected pool, guaranteeing that any eight drafted cards contain at least six different player names; all 65 variants remain eligible across drafts. Teams remain private, and editing a submitted lineup clears its submission status. The final submission atomically locks every team and starts a randomized single-elimination tournament, with byes as needed. Existing 6v6 combat runs best-of-three and stops at two wins. Match results and battle logs are available on the board. Sealed does not grant collection cards or currency.

`;sealedview` reopens the channel's latest draft; `;sealedview ID` opens a specific draft. A paused tournament can resume from its last committed match. Draft state survives restarts. A draft expires 30 minutes after creation, or when a new draft starts in its channel; expiration is announced there. There is no individual AFK pick timer.

## Offline validation

These checks use disposable SQLite databases and never log in to Discord:

```sh
python -m unittest discover -s tests -v
python scripts/validate_battle_rules.py
python scripts/validate_runtime.py
python scripts/validate_discord.py
```
