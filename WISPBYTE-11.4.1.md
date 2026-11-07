# PvP Fatigue and requested art updates — v11.4.1

## Changes

- Kenma's Fatigue has a maximum of eight stacks in the `pvp` battle mode used by `;pvp` and `/pvp`. At eight stacks, the existing formula gives Spd/Jmp−8, Pow/Qik−7 and Def/Rec−6. Stacks still persist across turns and can be cleansed.
- Other existing battle modes retain uncapped Fatigue, including normal battle, Regional/National Gauntlets, raids and Clan Contest. Tournament, Sealed, war and Prefecture rules are also unchanged.
- Kenma's card and named-effect descriptions explain the PvP exception.
- Updated Akaashi's card art and icon, plus Oikawa, Ushijima and Yaku's card art, using the five supplied URLs. Other icons remain unchanged.

## Install over v11.4.0

Stop the bot, then replace:

- `hiq/battle/engine.py`
- `data/characters.json`
- `VERSION`

The patch ZIP also contains this guide and the updated README. Restart the bot after copying the files. Keep the existing database, environment variables and dependencies; this update has no database migration.

The complete Wispbyte ZIP contains all current runtime files. The gallery patch contains `index.html`, which is ready to replace the character archive page. The root archive and `mpgallery-publish/index.html` in the workspace are both updated with embedded images and matching Kenma wording.

## Verified

- Five focused Fatigue regressions cover both PvP sides, the eight-stack boundary, persistence, cleansing, uncapped non-PvP stacking and card explanations.
- All 23 battle-rule regression groups and runtime data validation pass.
- All 24 existing Clan Contest regressions and nine onboarding/reward regressions pass. Offline Discord validation loads all cogs and registers all 68 slash commands.
- Both archives load and decode every updated image, display the updated Kenma wording, and have no browser script errors. The mobile detail page fits its viewport.

Tiny Titans, One for All, new characters, unique items, currencies, PvP team defaults/locks, matchmaking and Elo rewards are proposals only in this patch. No live bot deployment or gallery publishing was performed.
