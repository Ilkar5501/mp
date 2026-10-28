"""Permanently remove legacy battle logs and win-rate analytics.

Stop the bot and back up the database before running this script. Account,
collection, progression, clan, tournament, war, and prefecture data are kept.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "hiq_battler.sqlite3"


def size_label(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def table_count(db: sqlite3.Connection, table: str) -> int:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return 0
    return int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete stored battle logs/win-rate analytics and compact the database."
    )
    parser.add_argument("database", nargs="?", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm permanent deletion after making an external backup",
    )
    args = parser.parse_args()
    path = args.database.expanduser().resolve()

    if not path.is_file():
        raise SystemExit(f"Database not found: {path}")
    if not args.confirm:
        raise SystemExit(
            "No changes made. Stop the bot, download a backup, then rerun with --confirm."
        )

    before_size = path.stat().st_size
    with sqlite3.connect(path, timeout=60) as db:
        db.execute("PRAGMA busy_timeout=60000")
        battles = table_count(db, "battles")
        relations = table_count(db, "battle_relations")
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DROP TABLE IF EXISTS battle_relations")
            db.execute("DROP TABLE IF EXISTS battles")
            db.commit()
        except Exception:
            db.rollback()
            raise

        print(f"Removed {battles:,} stored battles and {relations:,} analytics rows.")
        print("Compacting database. Do not interrupt this step...")
        db.execute("VACUUM")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"SQLite integrity check failed: {integrity}")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    after_size = path.stat().st_size
    print(
        f"Complete: {size_label(before_size)} -> {size_label(after_size)} "
        f"({size_label(before_size - after_size)} reclaimed)."
    )
    print("Account, collection, progression, clan, and active-session data were preserved.")


if __name__ == "__main__":
    main()
