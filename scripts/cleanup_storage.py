"""Read-only size report by default; explicit, backed-up offline cleanup."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import sys
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hiq.retention import install, prune


def report(conn):
    size = conn.execute('PRAGMA page_size').fetchone()[0]
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    free = conn.execute('PRAGMA freelist_count').fetchone()[0]
    print(f'Database allocated: {pages*size/1024**2:.2f} MiB; reusable free pages: {free*size/1024**2:.2f} MiB')
    try:
        rows = conn.execute('SELECT name,SUM(pgsize) FROM dbstat GROUP BY name ORDER BY SUM(pgsize) DESC LIMIT 20').fetchall()
        print('Largest tables/indexes:')
        for name, count in rows:
            print(f'  {count/1024**2:10.2f} MiB  {name}')
    except sqlite3.OperationalError:
        print('This SQLite build lacks dbstat; table byte totals are unavailable.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--bot-stopped', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--expire-completed', action='store_true', help='also remove existing terminal replay rows immediately')
    parser.add_argument('--drop-legacy', action='store_true', help='remove obsolete battles/battle_relations tables')
    parser.add_argument('--vacuum', action='store_true', help='compact the database after cleanup')
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error('Database must be an existing file.')
    if not args.apply:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
            report(conn)
        print('Read-only report. No rows deleted. Use --apply --bot-stopped --backup-dir PATH to clean.')
        return
    if not args.bot_stopped or args.backup_dir is None:
        parser.error('--apply requires --bot-stopped and --backup-dir; stop the bot first.')
    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (path.name+'.'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.backup')
    required = path.stat().st_size
    if shutil.disk_usage(backup_dir).free < required:
        parser.error('Not enough space on the backup volume.')
    # Connect in rw mode, never accidentally create a new production database.
    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=60)) as conn:
        conn.execute('PRAGMA busy_timeout=60000')
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database quick_check failed; no cleanup performed.')
        with closing(sqlite3.connect(backup)) as dest:
            conn.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup verification failed; no cleanup performed.')
        print(f'Verified backup: {backup}')
        install(conn)
        counts = prune(conn, expire_completed=args.expire_completed, drop_legacy=args.drop_legacy)
        print('Deleted terminal replay rows:', counts)
        if args.vacuum:
            if shutil.disk_usage(path.parent).free < 2*path.stat().st_size:
                raise RuntimeError('Cleanup committed and backed up, but compaction skipped: need up to twice the database size in free space. Retry --vacuum after freeing space.')
            conn.execute('VACUUM')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Integrity check failed; preserve the backup and do not restart the bot.')
        print('Integrity check: ok')
        report(conn)
    print('Complete. Progress, wallets, collections, scores, war records and reward safeguards were preserved.')


if __name__ == '__main__':
    main()
