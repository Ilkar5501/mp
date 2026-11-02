# Clan Contest formations — v11.3.3

Stop the bot, replace these files from GitHub main, and restart:

- `VERSION`
- `hiq/contest.py`
- `hiq/contest_store.py`
- `hiq/contest_render.py`
- `hiq/cogs/contest.py`
- `hiq/battle/engine.py`
- `hiq/battle/talent.py`

Keep the database and `.env`. No dependencies or character data change in this patch. If updating from a release older than 11.3.2, use the complete runtime ZIP so the earlier changes are included.

The 14 formations now follow distinct jump, speed, school, anti-crit, bonus-count, Burden and Rec identities. Both Korai positions have their own day. Last Hope Kiryu is featured with Usuri; Iizuna supports Iwaizumi; Kanoka has Last Hope Kiryu, Kanbayashi and Usuri. Oikawa supports only the Ushijima, front-line Korai and Tenma formations. See `CLAN-CONTEST-TEAMS.md` for every support and its allowed slots.

Only eligible slots apply to inactive supports. Atsumu C does not grant Excellence, Usuri C does not grant Siphon or the B-slot stat aura, Kenma B/C does not generate Fatigue, and Shirabu C does not grant the A-slot maximum-cooldown reduction. Kiryu support ignores Burden. The positional exceptions for Nishinoya, Komi, Nozawa, Tenma, Kanbayashi and Kosuke follow the Clan Contest rules.

Supports resolve their start-of-turn effects in the normal pre-effect Speed order. Random ally grants select the active featured unit when eligible. Prodigious Talent support cannot grant Open Play to that unit on consecutive turns; as the featured unit, his C slot uses its self-buff fallback because the six inactive supports are not eligible recipients.

The current saved 14-day order remains in place, using the revised formations for new attempts. Saved attempts from before this update retain their original formation and rules when resumed. Existing scores and rewards are retained.
