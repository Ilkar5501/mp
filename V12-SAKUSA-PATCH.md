# v12.0.0 — Sakusa balance patch

Sakusa — Coiled Precision's Relentless Pressure now grants a **50%** follow-up
chance per initial critical hit when he initiates combat (previously 45%).
The actual battle probability and both the short and extended skill text change.
The preliminary Jump check, independent hit critical rolls, once-per-combat
restriction, follow-up denial, and defending behavior are unchanged.

This is an incremental patch over the previously supplied bot update. It does
not contain the unfinished new-unit/fusion/shiny expansion.

Stop the bot, extract this ZIP over the existing installation, preserving folder
paths, then restart with `python bot.py`. No database migration or reset is needed.
The patch includes only `hiq/battle/sakusa.py`, `data/characters.json`, `VERSION`,
`README.md`, and these instructions. Do not replace or delete your SQLite database
or `.env`; neither is included.

The existing activity-triggered storage cleanup remains in your previous update;
this patch neither replaces nor removes it. Physical compaction of the existing
database remains a separate offline operation described in `STORAGE-RETENTION.md`.
