# HiQ Battler v11.7.1

## Sakusa — initiation-only Relentless Pressure

Sakusa's B now applies only when he initiates combat: the preliminary Jmp crit check, attack-twice effect, 45% critical-hit follow-up rolls and fixed 1.1× offensive critical multiplier are all restricted to initiating. His counters use normal attack and critical rules. Other skills or equipment can still grant their own normal follow-up effects.

Perfectionism still stacks after either offensive or defensive combat, and Unravel can still Dispel buffs on counterattacks. His base stats, Ultimate, C and signature equipment rules are unchanged. The bot and website descriptions match the corrected condition.

## Tiny Titans — normal 6v6 result image

Tiny Titans now uses the existing regular-battle result helper and renderer. Results include the illustrated six-versus-six PNG, both teams' HP, OPOM, DPOM, turns, full battle log and the challenge shard drop. Both sides use the actual boosted Tiny Titans character definitions.

The result image is produced before reward settlement. A render error leaves the saved battle pending with no reward settled, so retrying resumes its fixed seed and drop roll. There is no text-only successful Tiny Titans result path. This change does not alter challenge rosters, drop chances or crafting costs.

## Inventory categories

`;inv` now has a category dropdown:

- Non-equip Items — packs, blessings, merge copies and other non-equipment inventory items.
- School Shards.
- Character Shards — retained separately so existing character-shard balances remain accessible.
- Equip Shards — normal equipment shards.
- Equip Items — normal equipment copies.
- Unique Equip Shards — all signature shards, including Tiny Titans challenge drops.
- Unique Equip Items — crafted signature equipment copies.

An All categories option is also available. Plain `;inv` opens the first category containing entries, starting with Non-equip Items if present. `;inv <name>` initially searches across all categories, preserving existing name-search behavior. Search within an open menu respects the selected category. Changing categories retains the search text and resets the page.

Pages remain limited to 25 entries, with previous/next arrows when applicable. The Stack items/Show IDs button remains and preserves the category and search. The stacked preference remains saved; the category is local to the open menu. Counts, IDs, equipped-copy markers, ownership restrictions and fuzzy case/punctuation-insensitive search are retained. No inventory items or balances are migrated or consumed by the category change.

## Upload and preservation

The complete Wispbyte ZIP includes v11.7.0 ranked PvP and every previous prepared update. Stop the bot, back up its database, extract and upload all runtime files, and keep the live database, .env and host settings. Start with `python bot.py`. No dependency or schema change is introduced by v11.7.1.

Ranked matchmaking, team locks, daily targeted Elo limits, monthly reset and prizes remain unchanged. Unrelated local audit files remain untouched. The website only changes Sakusa's B description.

## Validation

The 241-test local suite completes successfully with the same five pre-existing expected failures. The additional cases cover initiation-only Sakusa, retained defensive A/C behavior, unique versus standard inventory categories, category/search/stacking interactions, standard six-versus-six result attachments and reward safety on render failure. Runtime validation, all 23 battle-rule groups and offline registration of 76 slash commands pass. The real battle renderer also produced a verified 1440×1305 Tiny Titans PNG with twelve cached player icons.
