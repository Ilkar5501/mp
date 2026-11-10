"""Bound completed replay storage without touching progress or active sessions.

Uses only sqlite3 so the same implementation works in the offline maintenance CLI.
Expiry is measured from the terminal transition, not from the start of a battle.
"""
import sqlite3
import time
from contextlib import closing

RETENTION_SECONDS = 30 * 60
PVP_HISTORY_TRIM_SQL = '''WITH appearances AS (
    SELECT sequence,a_id AS player FROM pvp_history
    UNION ALL SELECT sequence,b_id FROM pvp_history
), recent AS (
    SELECT sequence,ROW_NUMBER() OVER
        (PARTITION BY player ORDER BY sequence DESC) AS position FROM appearances
) DELETE FROM pvp_history WHERE sequence NOT IN
    (SELECT sequence FROM recent WHERE position<=5)'''
# Table, eligibility predicate. Never interpolate caller-supplied SQL identifiers.
TARGETS = {
    'pvp_runs': "status='done'",
    'contest_runs': "status='complete'",
    'event_runs': "status IN ('done','abandoned')",
    'raid_runs': "status IN ('complete','cancelled')",
    'community_sessions': "kind IN ('prefecture','sealed') AND status IN ('complete','expired','cancelled','closed')",
    'tournaments': "status='cancelled' OR (status='complete' AND results_message_id IS NOT NULL)",
    'market_auctions': "status IN ('sold','expired','cancelled')",
}


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def install(conn, now=None):
    """Idempotent migration; existing completed records get a 30-minute grace."""
    now = time.time() if now is None else now
    conn.execute('''CREATE TABLE IF NOT EXISTS replay_expiry (
        kind TEXT NOT NULL, record_id TEXT NOT NULL, expires_at REAL NOT NULL,
        PRIMARY KEY(kind,record_id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS replay_expiry_due ON replay_expiry(expires_at)')
    present = tables(conn)
    if 'market_auctions' in present:
        conn.execute('''CREATE TABLE IF NOT EXISTS market_receipt_delivery (
            auction_id TEXT NOT NULL, owner_id INTEGER NOT NULL,
            PRIMARY KEY(auction_id,owner_id))''')
        if 'market_receipt_delivery' not in present:
            # Installing this release must not DM years of old auction receipts.
            conn.execute('''INSERT OR IGNORE INTO market_receipt_delivery
                SELECT id,seller_id FROM market_auctions WHERE status!='active' ''')
            conn.execute('''INSERT OR IGNORE INTO market_receipt_delivery
                SELECT id,winner_id FROM market_auctions WHERE status!='active' AND winner_id IS NOT NULL''')
    for table, predicate in TARGETS.items():
        if table not in present:
            continue
        # NEW.id selects only this changed row; the predicate is evaluated against
        # its current contents, including tournament result-delivery status.
        for action in ('INSERT', 'UPDATE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS retention_{table}_{action.lower()}
                AFTER {action} ON {table}
                WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id AND ({predicate}))
                BEGIN INSERT OR IGNORE INTO replay_expiry VALUES
                    ('{table}',NEW.id,unixepoch()+{RETENTION_SECONDS}); END''')
        conn.execute(f'''INSERT OR IGNORE INTO replay_expiry
            SELECT ?,id,? FROM {table} WHERE {predicate}''', (table, now + RETENTION_SECONDS))
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS retention_{table}_delete
            AFTER DELETE ON {table} BEGIN DELETE FROM replay_expiry
            WHERE kind='{table}' AND record_id=OLD.id; END''')
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS retention_{table}_reactivate
            AFTER UPDATE ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id AND ({predicate}))
            BEGIN DELETE FROM replay_expiry WHERE kind='{table}' AND record_id=NEW.id; END''')
    conn.commit()


def prune(conn, now=None, expire_completed=False, drop_legacy=False, batch_size=None, trim_history=True):
    """Caller holds the application's write lock, or has stopped the bot.

    Delete children and parents together. Keep currency/caps/reward markers,
    ratings, contest scores, saved teams and compact war history unchanged.
    """
    now = time.time() if now is None else now
    present = tables(conn)
    removed = {}
    with conn:
        for table, predicate in TARGETS.items():
            if table not in present:
                continue
            due = '' if expire_completed else "AND id IN (SELECT record_id FROM replay_expiry WHERE kind=? AND expires_at<=?)"
            args = () if expire_completed else (table, now)
            limit = '' if batch_size is None else f' LIMIT {max(1,int(batch_size))}'
            ids = [r[0] for r in conn.execute(f'SELECT id FROM {table} WHERE ({predicate}) {due}{limit}', args)]
            for rid in ids:
                if table == 'tournaments':
                    for child in ('tournament_entries', 'tournament_matches'):
                        if child in present:
                            conn.execute(f'DELETE FROM {child} WHERE tournament_id=?', (rid,))
                elif table == 'market_auctions' and 'market_bids' in present:
                    conn.execute('DELETE FROM market_bids WHERE auction_id=?', (rid,))
                    if 'market_receipt_delivery' in present:
                        conn.execute('DELETE FROM market_receipt_delivery WHERE auction_id=?', (rid,))
                conn.execute(f'DELETE FROM {table} WHERE id=?', (rid,))
                conn.execute('DELETE FROM replay_expiry WHERE kind=? AND record_id=?', (table, rid))
            removed[table] = len(ids)
        # Keep only matches needed by either player's last-five-opponents rule.
        # Rows are tiny; this is deliberately conservative across both players.
        if trim_history and 'pvp_history' in present:
            conn.execute(PVP_HISTORY_TRIM_SQL)
        if drop_legacy:
            for table in ('battle_relations', 'battles'):
                if table in present:
                    conn.execute(f'DROP TABLE {table}')
    return removed


def maintain(path):
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        conn.execute('PRAGMA busy_timeout=30000')
        removed = prune(conn,batch_size=250,trim_history=False)
        pending = conn.execute('SELECT 1 FROM replay_expiry LIMIT 1').fetchone() is not None
        return removed, pending
