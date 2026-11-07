# HiQ Battler v11.6.2

## Sakusa: Relentless Pressure

The preliminary live-Jmp crit check determines whether Sakusa's attacks have one hit or two. It does not itself count as a hit.

Every actual hit has its own crit check. Each **initial critical hit** gets a **45% follow-up roll**, stopping once one succeeds. If the first roll fails and the second hit crits, the second can retry. If the first succeeds, the second hit still rolls crit but needs no further follow-up roll.

An earned follow-up has the same hit count: two hits if the preliminary check passed, one hit otherwise. This allows both a four-hit sequence and a single-hit attack followed by a single-hit follow-up. Follow-up hits cannot earn further follow-ups. Normal follow-up denial still applies. Perfectionism continues to stack after combat; the offensive crit multiplier remains 1.1× and Dispel remains 50% per hit.

When both initial hits crit, the two 45% chances give a 69.75% chance to earn the follow-up, before denial effects.

## Website

The new **Tier List** page uses all 67 character-variant icons, not card art. It has exactly six tiers: S, A, B, C, D, F. Drag icons between tiers and the unranked pool, reorder them, search the pool, or reset. Tap an icon and then a tier label for mobile placement; keyboard activation works the same way. Lists stay in memory and reset on reload.

The page is self-contained HTML/CSS/JavaScript with embedded icons. It works on GitHub Pages without a backend or build process. Upload **index.html, items.html, tierlist.html and .nojekyll** from the gallery ZIP together. Keep the files in the same directory so their relative navigation links work.

Sakusa's gallery advice is **Jmp > Spd ~ Pow** for his boon and **HP** for his bane. Jmp helps win his Jmp checks and land critical hits to set up four-hit attacks. HP has the least impact on his combat. His supplied images and 50 base Pow are retained.

## Install

Prepared locally, not deployed or published. The full bot ZIP contains every current runtime file. The incremental bot patch requires v11.6.1; stop the bot, back up the database, replace all patch files and restart with the same environment and database. No schema or dependency change is introduced. The gallery ZIP is separate from the bot ZIP.

Ranked Elo PvP and the five previously documented audit issues are unchanged by this update.

## Validation

The 204-test bot suite completes successfully with the same five pre-existing expected failures. All 23 battle-rule groups and runtime data validation pass. Browser checks cover both gallery copies and tier pages: 67 icon images decode offline, all six tiers work, drag/reorder/return, search, reset, reload reset, keyboard activation and mobile tap placement pass, with no browser errors or mobile overflow. Sakusa’s guidance and ability text match the bot.
