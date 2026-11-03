# Restore uncapped Fatigue — v11.3.9

At the user's request, this release reverts the v11.3.8 Fatigue cap. Fatigue again has no maximum in all battle modes, with the original pre-cap stacking, stat calculations and Excellence interaction. The bot and archive descriptions again say uncapped. All v11.3.7 damage-report, alias and equipment-picker features are retained.

From v11.3.8, stop the bot, replace VERSION, data/characters.json, hiq/battle/types.py, hiq/battle/engine.py, hiq/battle/talent.py and hiq/contest.py, then restart. Keep your database and .env. No dependency or database changes. Existing scores and prizes are retained.

The complete runtime ZIP includes all prior updates. The historical WISPBYTE-11.3.8.md guide is superseded by this release.
