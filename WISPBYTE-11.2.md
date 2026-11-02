# Wispbyte update — v11.2

Stop the bot, back up its SQLite database, and upload the current `bot.py`, `VERSION`, `data/items.json`, and the entire `hiq/` folder from `main`. Keep the folder structure: this release adds tutorial, character-note, quest-view, and Clan Contest modules, not just changes to existing files.

Restart with the existing `bot.py` startup command. Requirements are unchanged. Keep `.env`, the database, and existing Wispbyte settings. New database tables are created automatically; do not delete the database to install this update.

## Commands

- `;start`: rewritten guide, with the existing one-time tutorial and starter-pack protections retained.
- `;qv`: four pages for Coins, Clan Dollars, Shards, and rollover.
- Character pages: **Click to learn more** opens private, separately paged skill/effect explanations.
- `;clancontest`: today's featured unit and supports.
- `;clancontest <slot>`: enter with a complete 6v6 team. Retry freely; only the best score each day counts.
- `;contestboard`: weekly clan and player leaderboards.
- `;contestresume`: recover an interrupted attempt from its saved lineup and seed.

## Reward changes

Battle wins pay 50 Coins, capped at 450 per daily allowance. The displayed Battle counter has 25 per daily allowance; it is not a hard attempt limit. Prefecture wins pay 100, capped at 300. Gauntlets pay 200 per base clear and up to another 50 for a new weekly lineup, capped at 250 per daily allowance and per type. A repeat clear with a fresh lineup can fill an unpaid bonus. Lineup identity is the unordered set of three character variants; Regional and National keep separate weekly lists.

Tournament wins pay a 300-Coin base. Wins in lobbies with at least four humans can also fill that day's 150-Coin bonus. Repeat CPU wins cannot fill the bonus portion. The cap is 450 per daily allowance.

Battle, Prefecture, Gauntlet, Tournament, and Raid allowances accumulate linearly through the week: days 1–3 have one, two, then three daily allowances. Unused allowances expire Monday at midnight in the configured game timezone. Existing earned currencies and recorded usage are preserved during installation. PvP, war rewards, check-ins, daily packs, donations, and Clan Contest participation do not roll over.

## Clan Contest

Each featured unit is Neutral +10 without equipment. It retains normal skills and gets School Bonds from its six supports. Only the featured unit can fight or be targeted. Supports use only their explicitly selected eligible slots; they cannot act, save, or trigger Ultimates. Each formation's support slots are shown in the contest preview.

The match ends after 20 turns or when the player's team is defeated. Damage carries across the featured unit's HP bars immediately, including overkill; its current bar supplies HP-threshold checks. Scoring counts damage that reaches its HP pool, including skill damage; blocked damage is not scored. Healing can refill its current bar but does not subtract earned damage.

First completed participation gives 200 Clan Dollars per day, even with a score of zero. Better retries replace the best daily score, rather than adding every attempt together. That day's clan score belongs to the clan recorded on the first completed attempt; changing clans cannot move or duplicate it.

The rotation is saved and shuffled every 14 days, anchored on 1 November 2026. All players see the same featured unit that day. Weekly scoring resets Monday independently of the two-week rotation.

Weekly top-three clans award 1,000 / 750 / 500 Clan Dollars to every member present when settlement runs. Top-three individual players receive 1,000 / 750 / 500 Coins, independently of clan placement. A background task settles completed weeks once and retries after failures/restarts. Equal scores sort by clan/player ID ascending. Late results cannot change a week whose prizes have already been settled.

Optional: set `COMMUNITY_SERVER_URL` to your server invite to add the link to the tutorial. No invite was supplied for this release.

## Item names

The 14 renamed items retain their original inventory/equipment/shard keys and effects. Old full names remain searchable. Notable changes include Ace's Approach, Ace's Defense, Signature Move, and Conditioning Circuit. All remaining catalogue names match the website.
