# Wispbyte update — v11.1

Stop the bot, download the following files from this repository's `main` branch, and upload them to the same paths in Wispbyte. Create the new `hiq/battle/anchor.py` file as well as replacing the existing files.

```
VERSION
data/characters.json
hiq/rewards.py
hiq/cogs/battle.py
hiq/prefecture.py
hiq/battle/engine.py
hiq/battle/expansion.py
hiq/battle/anchor.py
```

Restart the bot using its existing `bot.py` startup file. No dependency change or database reset is required. Preserve your `.env`, SQLite database, and existing Wispbyte settings.

This update adds Iizuna — Peerless Anchor (Regal Rally, 4 CD), updates Crown of Precision's card art, and changes daily PvP rewards to 100 coins per distinct opponent for the first four eligible wins (400 total). Existing same-day PvP usage is retained, so players who already earned rewards under the old rate may receive a smaller transition-day total; the next daily reset starts the full new allowance.

It also includes the previously requested fix preventing healing from reviving a team at 0 HP. Guiding Break is enforced by both normal and Prefecture target selection, and Clean Receive blocks healing through the complete after-combat window before expiring.

The website is maintained separately in `Ilkar5501/mpgallery`. Its `index.html` and README changes are not Wispbyte bot files.
