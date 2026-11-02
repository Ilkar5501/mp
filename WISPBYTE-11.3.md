# v11.3 — separate war phases and tutorial invite

Starting from v11.2, stop the bot and replace these files from GitHub `main`:

```
VERSION
bot.py
hiq/community_store.py
hiq/war_activity.py
hiq/cogs/clans.py
hiq/tutorial.py
```

Restart using the existing startup command. Keep `.env` and your database. Requirements are unchanged.

## Deadlines

- Acceptance starts one shared 24-hour window for each clan to ban **and** send its opening player. Completing bans does not restart this timer. Each clan can send after locking its own ban; it need not wait for the opposing ban.
- If one clan has not completed both tasks at the deadline, that clan loses the war. If neither clan has completed both, the war is cancelled without a clan result.
- In subsequent rounds, the previous winner gets 24 hours to send. Its declaration starts the opponent's separate 24-hour response window.
- The second send starts a fresh 24-hour team-selection window for **both** sent players. Team selection is unavailable before both sends are in.
- Missing a team means an automatic match loss and elimination for that player, not an immediate clan forfeit. Missing a player send means a clan forfeit. If both players miss their teams, both are eliminated; the next round uses simultaneous sends because there was no winner. If both rosters are exhausted, the existing no-result cancellation applies.

Officers receive direct messages with mentions and war controls when their send window opens. Successful reminders are recorded, so reopening the board or restarting does not normally send them again. Failed DMs are retried; the channel board remains available if a recipient blocks DMs.

Existing wars waiting for teams receive at least a fresh 24-hour player window on migration from the old shared-clock version. Existing officer deadlines are retained. The migration is saved once and does not repeatedly extend timers.

## Tutorial

The guide now includes `https://discord.gg/pnAhQaKUNN`. An explicit `COMMUNITY_SERVER_URL` environment setting can still override that default.
