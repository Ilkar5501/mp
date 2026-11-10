# Storage retention update

This update keeps inventories, card IDs/ownership, balances, pity, shards, progression,
saved teams, clan membership/roles, W/L/Elo, contest scores, compact war records,
and reward/transaction safeguards. Active or broken sessions remain recoverable.

Completed PvP, contest, Tiny Titans/AF1, raid, Prefecture, Sealed and tournament
replay rows are eligible for deletion 30 minutes after completion. This storage
grace period is independent of Discord button timeouts. Data-changing activity
schedules one cleanup batch 30 minutes later. Additional writes share that job
without creating more jobs or postponing it; read-only commands do not schedule
cleanup. The write signal is in memory and performs no additional SQL.

Each batch removes bounded groups (250 rows per category). If queued records remain
(too new or part of a backlog), one later batch is scheduled. Once the queue is empty,
no cleanup timer remains. One startup queue read restores scheduling after a restart.
There is no permanent minute-by-minute polling. Normal storage retention is roughly
30–60 minutes; a backlog, downtime or failed cleanup can extend it. The cleanup
deletes snapshots/logs together; it does not reverse rewards.
Old completed rows receive a 30-minute grace period on first installation.
Completed tournaments awaiting their final public result are preserved for retry.
Tournament entry/match snapshots remain while their bracket is active.

Normal battles and Gauntlet logs are already transient. Heavy result views now have
an absolute maximum 30-minute lifetime, even if clicked repeatedly. Existing shorter
inactivity timeouts remain. Expired persistent tournament/Sealed/Prefecture controls
are unregistered. Reopening an expired result is no longer supported.

Full character definitions are deliberately preserved in **active** team snapshots:
some modes modify character stats, and an interrupted battle must resume with its
original rules. Removing completed snapshots addresses their long-term duplication
without making unfinished battles depend on subsequently changed roster definitions.

Market listings remain while active, including escrow. After closure the bot makes
one best-effort receipt DM attempt to the seller and buyer (if any), covering sale,
cancellation, or expiry. A blocked DM does not affect settlement. These receipt
attempts are at-most-once: a crash between claiming and sending can lose a DM.
Existing interaction responses also show settlement results. Closed listings, bids,
and delivery markers expire after 30 minutes. Historical listings existing at first
installation are marked as already notified, preventing a burst of old receipt DMs.
Discord messages already delivered remain on Discord; this cleanup removes bot data.

Only each player's last-five-match opponent window remains in `pvp_history`, because
automatic matchmaking uses it. This is trimmed inside the ranked-match settlement
transaction, rather than rescanned by background cleanup. The offline cleanup also
trims any existing history. Monthly ratings/prizes and small reward markers remain.
Daily/weekly caps, redemption claims and settlement guards are not reset or discarded.
Compact completed wars remain, including participants and series scores.

## Deploy the code

Stop the bot. Preserve `.env`, your configured `HIQ_DB_PATH`, and all database files.
Upload the supplied patch over the existing v11.7.3 runtime, or use the complete
runtime archive. The ZIP contains no credentials or production database. Do not
replace the database with an empty one. Run the offline cleanup below if desired,
then restart with `python bot.py`. No new dependencies are required.

## Inspect the large database without uploading it

Use the actual database path configured on the host, not a guessed duplicate:

```sh
python scripts/cleanup_storage.py /path/to/hiq_battler.sqlite3
```

The default is read-only. It prints allocated bytes, reusable free pages and the
20 largest tables/indexes (where SQLite's dbstat module is available). Send the
printed report if you want a size diagnosis; no database upload is necessary.

## One-time cleanup and compaction

**Keep the bot stopped throughout this operation.** Use a backup directory with
enough disk space, preferably on another persistent volume. This command creates
and verifies a full SQLite backup before deleting anything:

```sh
python scripts/cleanup_storage.py /path/to/hiq_battler.sqlite3 --apply --bot-stopped --backup-dir /path/to/backups --expire-completed --drop-legacy --vacuum
```

`--expire-completed` also discards existing completed replays immediately, without
the initial grace period. Active sessions and undelivered tournament finals are kept.
`--drop-legacy` removes obsolete `battles` and `battle_relations` analytics tables.
`--vacuum` compacts freed pages and checkpoints/truncates the WAL. Cleanup alone
makes space reusable inside SQLite but does not necessarily shrink its file.

For a 1.4 GB database, budget roughly 1.4 GB for the backup and up to another 2.8 GB
of free space for compaction, in addition to existing disk usage. The script checks
available space; it does not delete backups or other files to make room. If
compaction is skipped for insufficient space, cleanup has already committed and
the verified backup remains. Free space and repeat the command to compact later.
Do not manually delete a live `-wal` or `-shm` file.

Afterward confirm the printed integrity check succeeded, restart the bot and check
one inventory, balance, saved team, clan, PvP board and contest board. If recovery
is needed, stop the bot and restore the verified backup as the configured database;
keep the current files separately until recovery is verified.

A ZIP of a stopped, consistent database backup might compress substantially, but
compression is not guaranteed. The printed size report is enough for the first
diagnosis and avoids sharing player data.
