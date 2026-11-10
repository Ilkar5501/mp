# HiQ Battler v11.7.4 — bounded replay storage

Completed battle/session details become eligible for removal after 30 minutes;
activity-triggered cleanup batches run at 30-minute intervals only while work is
queued. Button timeouts are independent. Progress, collections,
balances, saved teams, pity, scores, Elo/W/L, clan roles/records and reward safeguards
are preserved. Active/broken battles remain recoverable. Market closures generate
best-effort receipt DMs; completed listing data expires instead of accumulating.

See [STORAGE-RETENTION.md](STORAGE-RETENTION.md) for the exact retention behavior,
read-only storage report and backed-up offline cleanup/compaction command.

Stop the bot, preserve your database, `.env` and host settings, upload the runtime
or patch ZIP, and restart with `python bot.py`. This version adds retention tables
and triggers automatically. No new packages are needed. For a large existing
database, use the offline cleanup before restarting to remove the old backlog and
physically reclaim file space. The backup and compaction steps need free disk space.
