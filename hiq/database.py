from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .config import DB_PATH, GAME_CONFIG
from .models import CardInstance, SavedTeam, TeamRecord, UserProgress

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    discord_id INTEGER PRIMARY KEY,
    coins INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_daily_date TEXT,
    daily_win_date TEXT,
    daily_wins INTEGER NOT NULL DEFAULT 0,
    week_key TEXT,
    weekly_wins INTEGER NOT NULL DEFAULT 0,
    weekly_claimed TEXT NOT NULL DEFAULT '[]',
    tutorial_started INTEGER NOT NULL DEFAULT 0,
    starter_daily_claimed INTEGER NOT NULL DEFAULT 0,
    active_team_slot INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tutorial_rewards (
    owner_id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_serials (
    card_id TEXT PRIMARY KEY,
    next_serial INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cards (
    instance_id TEXT PRIMARY KEY,
    full_instance_id TEXT UNIQUE NOT NULL,
    card_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    original_owner_id INTEGER NOT NULL,
    original_owner_name TEXT NOT NULL,
    serial_no INTEGER NOT NULL,
    merge_level INTEGER NOT NULL DEFAULT 0,
    obtained_at TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    boon_stat TEXT,
    bane_stat TEXT,
    nature_rolled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(owner_id) REFERENCES users(discord_id)
);
CREATE INDEX IF NOT EXISTS idx_cards_owner ON cards(owner_id);
CREATE INDEX IF NOT EXISTS idx_cards_char ON cards(owner_id, character_id);

-- Legacy table kept for backward compatibility/migration from the first build.
CREATE TABLE IF NOT EXISTS teams (
    owner_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('attack','defense')),
    front_id TEXT NOT NULL,
    back1_id TEXT NOT NULL,
    back2_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, kind),
    FOREIGN KEY(front_id) REFERENCES cards(instance_id),
    FOREIGN KEY(back1_id) REFERENCES cards(instance_id),
    FOREIGN KEY(back2_id) REFERENCES cards(instance_id)
);

CREATE TABLE IF NOT EXISTS saved_teams (
    owner_id INTEGER NOT NULL,
    slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 15),
    name TEXT NOT NULL,
    front_id TEXT,
    back1_id TEXT,
    back2_id TEXT,
    team_format INTEGER NOT NULL DEFAULT 3 CHECK(team_format IN (3,6)),
    front2_id TEXT,
    front3_id TEXT,
    back3_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, slot),
    FOREIGN KEY(front_id) REFERENCES cards(instance_id),
    FOREIGN KEY(back1_id) REFERENCES cards(instance_id),
    FOREIGN KEY(back2_id) REFERENCES cards(instance_id)
);

CREATE TABLE IF NOT EXISTS gauntlet_runs (
    owner_id INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('regional','national')),
    week_key TEXT NOT NULL,
    school TEXT NOT NULL,
    next_game INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    team_slot INTEGER NOT NULL,
    front_id TEXT NOT NULL,
    back1_id TEXT NOT NULL,
    back2_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, mode)
);

CREATE TABLE IF NOT EXISTS gauntlet_claims (
    owner_id INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('regional','national')),
    week_key TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, mode, week_key)
);

CREATE TABLE IF NOT EXISTS shards (
    owner_id INTEGER NOT NULL,
    school TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(owner_id, school)
);

CREATE TABLE IF NOT EXISTS training_progress (
    owner_id INTEGER NOT NULL,
    school TEXT NOT NULL,
    week_key TEXT NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0 CHECK(wins >= 0),
    PRIMARY KEY(owner_id, school, week_key)
);

CREATE TABLE IF NOT EXISTS pity (
    owner_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL,
    armed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS test_teams (
    owner_id INTEGER PRIMARY KEY,
    front_character_id TEXT,
    back1_character_id TEXT,
    back2_character_id TEXT,
    updated_at TEXT NOT NULL
);
'''


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Database:
    def __init__(self, path: str | Path = DB_PATH):
        self.path = str(path)
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            from .tournament_store import SCHEMA as TOURNAMENT_SCHEMA
            await db.executescript(TOURNAMENT_SCHEMA)
            from .community_store import SCHEMA as COMMUNITY_SCHEMA
            await db.executescript(COMMUNITY_SCHEMA)
            await self._migrate_saved_teams(db)
            await self._migrate_columns(db)
            from .raid_store import SCHEMA as RAID_SCHEMA
            await db.executescript(RAID_SCHEMA)
            await self._migrate_character_ids(db)
            await self._migrate_legacy_team(db)
            await self._backfill_natures(db)
            await db.commit()
            from .shard_balances import SCHEMA as SHARD_SCHEMA, migrate_legacy_shards
            await db.executescript(SHARD_SCHEMA)
            await migrate_legacy_shards(db)
            from .rewards import SCHEMA as REWARD_SCHEMA, migrate as migrate_rewards
            await db.executescript(REWARD_SCHEMA)
            await migrate_rewards(db)

    async def _migrate_saved_teams(self,db):
        # SQLite CHECK constraints need a transactional table rebuild. Copy every
        # existing field unchanged; no card rows or IDs are recreated.
        columns=await self._columns(db,'saved_teams')
        cur=await db.execute("SELECT sql FROM sqlite_master WHERE name='saved_teams'")
        ddl=(await cur.fetchone())[0]
        if {'team_format','front2_id','front3_id','back3_id'} <= columns and 'AND 15' in ddl:
            return
        await db.execute('BEGIN IMMEDIATE')
        new_ddl=SCHEMA.split('CREATE TABLE IF NOT EXISTS saved_teams (',1)[1].split('CREATE TABLE IF NOT EXISTS gauntlet_runs',1)[0]
        await db.execute('CREATE TABLE saved_teams_v61 ('+new_ddl.strip().rstrip(';'))
        preserved=[c for c in ('owner_id','slot','name','front_id','back1_id','back2_id','updated_at','team_format','front2_id','front3_id','back3_id') if c in columns]
        names=','.join(preserved)
        await db.execute(f'INSERT INTO saved_teams_v61 ({names}) SELECT {names} FROM saved_teams')
        await db.execute('DROP TABLE saved_teams')
        await db.execute('ALTER TABLE saved_teams_v61 RENAME TO saved_teams')
        await db.commit()

    @staticmethod
    def _saved_team(row):
        return SavedTeam(row['owner_id'],row['slot'],row['name'],row['front_id'],row['back1_id'],row['back2_id'],
            row['team_format'],row['front2_id'],row['front3_id'],row['back3_id'])

    async def _columns(self, db: aiosqlite.Connection, table: str) -> set[str]:
        cur = await db.execute(f'PRAGMA table_info({table})')
        return {row[1] for row in await cur.fetchall()}

    async def _migrate_columns(self, db: aiosqlite.Connection) -> None:
        test_columns = await self._columns(db, 'test_teams')
        for name,ddl in (('team_format','INTEGER NOT NULL DEFAULT 3'),('front2_character_id','TEXT'),('front3_character_id','TEXT'),('back3_character_id','TEXT')):
            if name not in test_columns:
                await db.execute(f'ALTER TABLE test_teams ADD COLUMN {name} {ddl}')
        for table,column,ddl in [('redeem_codes','expires_at','INTEGER'),('test_teams','settings_json',"TEXT NOT NULL DEFAULT '{}'"),('tournament_rewards','champion_coins','INTEGER NOT NULL DEFAULT 0'),('tournament_rewards','participation_coins','INTEGER NOT NULL DEFAULT 0')]:
            if column not in await self._columns(db,table):await db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
        await db.execute("UPDATE tournament_rewards SET champion_coins=CASE WHEN kind='win' THEN amount ELSE 0 END,participation_coins=CASE WHEN kind='participation' THEN amount ELSE 0 END WHERE champion_coins=0 AND participation_coins=0 AND amount>0")
        users = await self._columns(db, 'users')
        for name, ddl in (
            ('tutorial_started', 'INTEGER NOT NULL DEFAULT 0'),
            ('starter_daily_claimed', 'INTEGER NOT NULL DEFAULT 0'),
            ('active_team_slot', 'INTEGER NOT NULL DEFAULT 1'),
        ):
            if name not in users:
                await db.execute(f'ALTER TABLE users ADD COLUMN {name} {ddl}')
        cards = await self._columns(db, 'cards')
        had_boon = 'boon_stat' in cards
        had_bane = 'bane_stat' in cards
        for name in ('boon_stat', 'bane_stat', 'equipped_item_id', 'equipment_key'):
            if name not in cards:
                await db.execute(f'ALTER TABLE cards ADD COLUMN {name} TEXT')
        if 'nature_rolled' not in cards:
            await db.execute('ALTER TABLE cards ADD COLUMN nature_rolled INTEGER NOT NULL DEFAULT 0')
            # Existing cards predate v3's one-time nature marker. Preserve any boon/bane
            # values already present (for example from v2), while treating genuinely
            # pre-nature v1 cards as Neutral. No existing card is rolled during startup.
            if had_boon or had_bane:
                await db.execute('UPDATE cards SET nature_rolled=1')
            else:
                await db.execute('UPDATE cards SET boon_stat=NULL, bane_stat=NULL, nature_rolled=1')

    async def _migrate_character_ids(self, db: aiosqlite.Connection) -> None:
        """Map legacy roster identifiers to the current character IDs.

        Early builds stored some cards with a bare-name character_id (for example
        ``Suga``) while the current roster uses the titled ID
        ``Suga — Relentless Setter``.  The persistent card_id normally contains
        name:title, so prefer that exact stable identity.  Bare-name fallback is
        only used when the current roster has exactly one version of that name;
        ambiguous multi-version characters are never guessed.
        """
        from .data import CHARACTERS

        current_ids = {c.id for c in CHARACTERS}
        by_card_id = {c.card_id: c.id for c in CHARACTERS}
        by_name: dict[str, list[str]] = {}
        for c in CHARACTERS:
            by_name.setdefault(c.name, []).append(c.id)

        cur = await db.execute('SELECT DISTINCT character_id, card_id FROM cards')
        rows = await cur.fetchall()
        for old_id, card_id in rows:
            if old_id in current_ids:
                continue
            new_id = by_card_id.get(card_id)
            if new_id is None:
                # Legacy IDs were often simply the character name.
                candidates = by_name.get(old_id, [])
                if len(candidates) == 1:
                    new_id = candidates[0]
            if new_id is None and card_id:
                # Last safe fallback: old card IDs begin with ``Name:``.
                old_name = str(card_id).split(':', 1)[0]
                candidates = by_name.get(old_name, [])
                if len(candidates) == 1:
                    new_id = candidates[0]
            if new_id is not None:
                await db.execute(
                    'UPDATE cards SET character_id=? WHERE character_id=? AND card_id=?',
                    (new_id, old_id, card_id),
                )

    async def _migrate_legacy_team(self, db: aiosqlite.Connection) -> None:
        await db.execute('''
            INSERT OR IGNORE INTO saved_teams(owner_id,slot,name,front_id,back1_id,back2_id,updated_at)
            SELECT owner_id,1,'Team 1',front_id,back1_id,back2_id,updated_at
            FROM teams WHERE kind='attack'
        ''')
        await db.execute('''
            UPDATE users SET active_team_slot=1
            WHERE discord_id IN (SELECT owner_id FROM saved_teams WHERE slot=1)
              AND (active_team_slot IS NULL OR active_team_slot<1 OR active_team_slot>15)
        ''')

    @staticmethod
    def _nature_for(instance_id: str, character_id: str) -> tuple[str | None, str | None]:
        from .data import CHAR_BY_ID
        from .stats import roll_nature
        return roll_nature(CHAR_BY_ID[character_id], instance_id)

    async def _backfill_natures(self, db: aiosqlite.Connection) -> None:
        # Safety for interrupted/older migrations: any pre-existing card that still lacks
        # the one-time marker is legacy data and becomes Neutral. Nature RNG is reserved
        # exclusively for create_card(), so service restarts can never reroll a collection.
        await db.execute('''
            UPDATE cards
            SET boon_stat=NULL, bane_stat=NULL, nature_rolled=1
            WHERE COALESCE(nature_rolled,0)=0
        ''')

    @asynccontextmanager
    async def connect(self):
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def ensure_user(self, discord_id: int) -> None:
        async with self.connect() as db:
            await db.execute(
                'INSERT OR IGNORE INTO users(discord_id, created_at) VALUES (?,?)',
                (discord_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def get_user(self, discord_id: int) -> UserProgress:
        await self.ensure_user(discord_id)
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM users WHERE discord_id=?', (discord_id,))
            row = await cur.fetchone()
        return UserProgress(
            discord_id=row['discord_id'],
            coins=row['coins'],
            last_daily_date=row['last_daily_date'],
            daily_win_date=row['daily_win_date'],
            daily_wins=row['daily_wins'],
            week_key=row['week_key'],
            weekly_wins=row['weekly_wins'],
            weekly_claimed=row['weekly_claimed'],
            tutorial_started=bool(row['tutorial_started']),
            starter_daily_claimed=bool(row['starter_daily_claimed']),
            active_team_slot=int(row['active_team_slot'] or 1),
        )

    async def set_user_fields(self, discord_id: int, **fields) -> None:
        if not fields:
            return
        await self.ensure_user(discord_id)
        cols = ', '.join(f'{k}=?' for k in fields)
        vals = list(fields.values()) + [discord_id]
        async with self.connect() as db:
            await db.execute(f'UPDATE users SET {cols} WHERE discord_id=?', vals)
            await db.commit()

    async def add_coins(self, discord_id: int, amount: int) -> int:
        await self.ensure_user(discord_id)
        async with self.connect() as db:
            await db.execute('UPDATE users SET coins=coins+? WHERE discord_id=?', (amount, discord_id))
            await db.commit()
            cur = await db.execute('SELECT coins FROM users WHERE discord_id=?', (discord_id,))
            return int((await cur.fetchone())['coins'])

    async def spend_coins(self, discord_id: int, amount: int) -> bool:
        await self.ensure_user(discord_id)
        async with self._lock:
            async with self.connect() as db:
                cur = await db.execute('SELECT coins FROM users WHERE discord_id=?', (discord_id,))
                row = await cur.fetchone()
                if int(row['coins']) < amount:
                    return False
                await db.execute('UPDATE users SET coins=coins-? WHERE discord_id=?', (amount, discord_id))
                await db.commit()
                return True

    async def _next_serial(self, db: aiosqlite.Connection, card_id: str) -> int:
        cur = await db.execute('SELECT next_serial FROM card_serials WHERE card_id=?', (card_id,))
        row = await cur.fetchone()
        if row is None:
            serial = 1
            await db.execute('INSERT INTO card_serials(card_id,next_serial) VALUES (?,?)', (card_id, 2))
        else:
            serial = int(row['next_serial'])
            await db.execute('UPDATE card_serials SET next_serial=? WHERE card_id=?', (serial + 1, card_id))
        return serial

    async def create_card(self, owner_id: int, owner_name: str, character_id: str, card_id: str, *, force_neutral: bool = False) -> CardInstance:
        await self.ensure_user(owner_id)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                serial = await self._next_serial(db, card_id)
                instance_id = uuid.uuid4().hex[:12]
                full_id = f'{card_id}:{serial:06d}'
                now = datetime.now(timezone.utc)
                boon, bane = (None, None) if force_neutral else self._nature_for(instance_id, character_id)
                await db.execute('''
                    INSERT INTO cards(instance_id, full_instance_id, card_id, character_id, owner_id,
                                      original_owner_id, original_owner_name, serial_no, merge_level,
                                      obtained_at, locked, boon_stat, bane_stat, nature_rolled)
                    VALUES (?,?,?,?,?,?,?,?,0,?,0,?,?,1)
                ''', (instance_id, full_id, card_id, character_id, owner_id, owner_id, owner_name, serial, now.isoformat(), boon, bane))
                await db.commit()
        return CardInstance(instance_id, full_id, card_id, character_id, owner_id, owner_id, owner_name, serial, 0, now, False, boon, bane)

    @staticmethod
    def _card(row) -> CardInstance:
        return CardInstance(
            instance_id=row['instance_id'], full_instance_id=row['full_instance_id'], card_id=row['card_id'],
            character_id=row['character_id'], owner_id=row['owner_id'], original_owner_id=row['original_owner_id'],
            original_owner_name=row['original_owner_name'], serial_no=row['serial_no'], merge_level=row['merge_level'],
            obtained_at=_dt(row['obtained_at']), locked=bool(row['locked']),
            boon_stat=row['boon_stat'], bane_stat=row['bane_stat'],
            equipped_item_id=row['equipped_item_id'] if 'equipped_item_id' in row.keys() else None,
            equipment_key=row['equipment_key'] if 'equipment_key' in row.keys() else None,
        )

    async def get_card(self, instance_or_full_id: str) -> CardInstance | None:
        async with self.connect() as db:
            cur = await db.execute(
                'SELECT * FROM cards WHERE instance_id=? OR full_instance_id=?',
                (instance_or_full_id, instance_or_full_id),
            )
            row = await cur.fetchone()
        return self._card(row) if row else None

    async def get_cards(self, owner_id: int) -> list[CardInstance]:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM cards WHERE owner_id=? ORDER BY obtained_at DESC', (owner_id,))
            rows = await cur.fetchall()
        return [self._card(r) for r in rows]

    async def get_duplicates(self, owner_id: int, character_id: str, exclude_id: str) -> list[CardInstance]:
        async with self.connect() as db:
            cur = await db.execute('''
                SELECT * FROM cards WHERE owner_id=? AND character_id=? AND instance_id<>? AND locked=0
                ORDER BY merge_level ASC, obtained_at ASC
            ''', (owner_id, character_id, exclude_id))
            rows = await cur.fetchall()
        return [self._card(r) for r in rows]

    async def toggle_lock(self, owner_id: int, instance_id: str) -> bool | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT locked FROM cards WHERE instance_id=? AND owner_id=?', (instance_id, owner_id))
            row = await cur.fetchone()
            if not row:
                return None
            new = 0 if row['locked'] else 1
            await db.execute('UPDATE cards SET locked=? WHERE instance_id=?', (new, instance_id))
            await db.commit()
            return bool(new)

    async def is_card_on_team(self,instance_id:str) -> bool:
        async with self.connect() as db:
            cur=await db.execute("SELECT 1 FROM saved_teams WHERE front_id=? OR front2_id=? OR front3_id=? OR back1_id=? OR back2_id=? OR back3_id=? UNION ALL SELECT 1 FROM teams WHERE front_id=? OR back1_id=? OR back2_id=? LIMIT 1",(instance_id,)*9)
            return await cur.fetchone() is not None

    async def split_card(self, owner_id: int, instance_or_full_id: str, owner_name: str) -> tuple[CardInstance, list[CardInstance]]:
        """Reset one exact owned card to +0 and recover its merges as Neutral copies.

        The existing row keeps all metadata and team references. New rows and
        serials commit together with the reset, so a retry cannot duplicate cards.
        Command authorization belongs to the owner-only collection command.
        """
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur = await db.execute(
                    'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',
                    (owner_id, instance_or_full_id, instance_or_full_id),
                )
                row = await cur.fetchone()
                if not row:
                    raise ValueError('That exact card ID was not found in the specified user’s collection.')
                original = self._card(row)
                count = int(original.merge_level)
                if count <= 0:
                    raise ValueError('That card is already +0 and has no merges to split.')
                if count > 10:
                    raise ValueError('That card has an invalid merge level above +10; no changes were made.')
                await db.execute('UPDATE cards SET merge_level=0 WHERE instance_id=?', (original.instance_id,))
                copies = []
                now = datetime.now(timezone.utc)
                for _ in range(count):
                    serial = await self._next_serial(db, original.card_id)
                    iid = uuid.uuid4().hex[:12]
                    full_id = f'{original.card_id}:{serial:06d}'
                    await db.execute('''
                        INSERT INTO cards(instance_id,full_instance_id,card_id,character_id,owner_id,
                            original_owner_id,original_owner_name,serial_no,merge_level,obtained_at,
                            locked,boon_stat,bane_stat,nature_rolled)
                        VALUES (?,?,?,?,?,?,?,?,0,?,0,NULL,NULL,1)
                    ''', (iid,full_id,original.card_id,original.character_id,owner_id,
                          owner_id,owner_name,serial,now.isoformat()))
                    copies.append(CardInstance(iid,full_id,original.card_id,original.character_id,
                        owner_id,owner_id,owner_name,serial,0,now,False,None,None))
                await db.commit()
                original.merge_level = 0
                return original, copies

    async def merge_cards(self, owner_id: int, survivor_id: str, consumed_id: str, max_level: int = 10) -> CardInstance:
        '''Merge exactly two explicitly selected card instances.

        Card 1 survives and keeps its ID, boon/bane, ownership metadata, and obtained
        timestamp. Card 2 is consumed. The new merge value is m1 + m2 + 1, because
        each physical card represents one copy. Locked cards are never eligible.
        '''
        if survivor_id == consumed_id:
            raise ValueError('Choose two different card instances.')
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                # Read/validate under the same write transaction as splitting,
                # trading and recycling; never use stale merge levels or owners.
                cur = await db.execute('SELECT * FROM cards WHERE instance_id=? OR full_instance_id=?', (survivor_id, survivor_id))
                row = await cur.fetchone()
                survivor = self._card(row) if row else None
                cur = await db.execute('SELECT * FROM cards WHERE instance_id=? OR full_instance_id=?', (consumed_id, consumed_id))
                row = await cur.fetchone()
                consumed = self._card(row) if row else None
                if not survivor or survivor.owner_id != owner_id:
                    raise ValueError('Card 1 was not found in your collection.')
                if not consumed or consumed.owner_id != owner_id:
                    raise ValueError('Card 2 was not found in your collection.')
                if survivor.instance_id == consumed.instance_id:
                    raise ValueError('Choose two different card instances.')
                if survivor.locked or consumed.locked:
                    raise ValueError('Locked cards cannot be used in a merge. Unlock the card first.')
                if survivor.character_id != consumed.character_id:
                    raise ValueError('Both cards must have the same character name and title.')
                new_level = int(survivor.merge_level) + int(consumed.merge_level) + 1
                if new_level > int(max_level):
                    raise ValueError(
                        f'That merge would create +{new_level}, above the +{max_level} cap. '
                        f'Card 1 is +{survivor.merge_level}; Card 2 is +{consumed.merge_level}.'
                    )
                survivor_id, consumed_id = survivor.instance_id, consumed.instance_id
                await self._clear_card_from_teams_tx(db,consumed_id)
                await db.execute('DELETE FROM cards WHERE instance_id=? AND owner_id=?', (consumed_id, owner_id))
                await db.execute(
                    'UPDATE cards SET merge_level=? WHERE instance_id=? AND owner_id=?',
                    (new_level, survivor_id, owner_id),
                )
                await db.commit()

        updated = await self.get_card(survivor_id)
        if not updated:
            raise RuntimeError('Merged card could not be reloaded.')
        return updated

    # Backward-compatible method name; callers must now provide the exact consumed ID.
    async def merge_card(self, owner_id: int, base_id: str, consumed_id: str, max_level: int = 10) -> CardInstance:
        return await self.merge_cards(owner_id, base_id, consumed_id, max_level)

    # ----- saved teams -----
    async def ensure_saved_team(self, owner_id: int, slot: int, name: str | None = None) -> SavedTeam:
        if not 1 <= slot <= 15:
            raise ValueError('Team slot must be between 1 and 15.')
        await self.ensure_user(owner_id)
        now = datetime.now(timezone.utc).isoformat()
        team_name = (name or f'Team {slot}').strip()[:40] or f'Team {slot}'
        async with self.connect() as db:
            await db.execute('''
                INSERT OR IGNORE INTO saved_teams(owner_id,slot,name,front_id,back1_id,back2_id,updated_at)
                VALUES (?,?,?,NULL,NULL,NULL,?)
            ''', (owner_id, slot, team_name, now))
            if name is not None:
                await db.execute('UPDATE saved_teams SET name=?,updated_at=? WHERE owner_id=? AND slot=?', (team_name, now, owner_id, slot))
            await db.commit()
        return await self.get_saved_team(owner_id, slot)

    async def get_saved_team(self, owner_id: int, slot: int) -> SavedTeam | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM saved_teams WHERE owner_id=? AND slot=?', (owner_id, slot))
            row = await cur.fetchone()
        if not row:
            return None
        return self._saved_team(row)

    async def list_saved_teams(self, owner_id: int) -> list[SavedTeam]:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM saved_teams WHERE owner_id=? ORDER BY slot', (owner_id,))
            rows = await cur.fetchall()
        return [self._saved_team(r) for r in rows]

    async def clear_saved_team(self, owner_id: int, slot: int) -> None:
        await self.ensure_saved_team(owner_id, slot)
        async with self.connect() as db:
            await db.execute(
                'UPDATE saved_teams SET front_id=NULL,back1_id=NULL,back2_id=NULL,front2_id=NULL,front3_id=NULL,back3_id=NULL,updated_at=? WHERE owner_id=? AND slot=?',
                (datetime.now(timezone.utc).isoformat(), owner_id, slot),
            )
            await db.commit()

    async def tutorial_reward_claimed(self,owner_id:int) -> bool:
        async with self.connect() as db:
            cur=await db.execute('SELECT 1 FROM tutorial_rewards WHERE owner_id=?',(owner_id,))
            return await cur.fetchone() is not None

    async def complete_tutorial(self,owner_id:int,owner_name:str) -> CardInstance | None:
        """Claim neutral Suga once, in the same transaction as completion/serial allocation."""
        from .data import CHAR_BY_ID
        c=CHAR_BY_ID['Suga — Relentless Setter']
        await self.ensure_user(owner_id)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur=await db.execute('SELECT 1 FROM tutorial_rewards WHERE owner_id=?',(owner_id,))
                if await cur.fetchone(): return None
                serial=await self._next_serial(db,c.card_id)
                iid=uuid.uuid4().hex[:12];full=f'{c.card_id}:{serial:06d}'
                now=datetime.now(timezone.utc)
                await db.execute("""
                    INSERT INTO cards(instance_id,full_instance_id,card_id,character_id,owner_id,
                        original_owner_id,original_owner_name,serial_no,merge_level,obtained_at,locked,boon_stat,bane_stat,nature_rolled)
                    VALUES (?,?,?,?,?,?,?,?,0,?,0,NULL,NULL,1)
                """,(iid,full,c.card_id,c.id,owner_id,owner_id,owner_name,serial,now.isoformat()))
                await db.execute('INSERT INTO tutorial_rewards(owner_id,instance_id,completed_at) VALUES (?,?,?)',(owner_id,iid,now.isoformat()))
                await db.execute('UPDATE users SET tutorial_started=1 WHERE discord_id=?',(owner_id,))
                await db.commit()
        return await self.get_card(iid)

    async def replace_saved_team(self,owner_id:int,slot:int,front_id,back1_id,back2_id,*,
                                 front2_id=None,front3_id=None,back3_id=None,team_format=None,expected=None,expected_format=None):
        from .data import CHAR_BY_ID
        if not 1<=slot<=15: raise ValueError('Team slot must be between 1 and 15.')
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur=await db.execute('SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(owner_id,slot))
                row=await cur.fetchone()
                if not row: raise ValueError('That team no longer exists.')
                current=self._saved_team(row)
                if expected is not None and tuple(expected)!=current.card_ids or expected_format is not None and expected_format!=current.team_format:
                    raise ValueError('This team changed while you were editing. Reopen `;teamedit` to use its latest lineup.')
                fmt=current.team_format if team_format is None else int(team_format)
                if fmt not in (3,6): raise ValueError('Team format must be 3v3 or 6v6.')
                if fmt==3: front2_id=front3_id=back3_id=None
                ids=(front_id,front2_id,front3_id,back1_id,back2_id,back3_id)
                names=[]
                for iid in ids:
                    if not iid: continue
                    cur=await db.execute('SELECT character_id FROM cards WHERE owner_id=? AND instance_id=?',(owner_id,iid))
                    card=await cur.fetchone()
                    if not card: raise ValueError('A selected card is no longer in your collection. Reopen the editor.')
                    names.append(CHAR_BY_ID[card['character_id']].name)
                if len(names)!=len(set(names)): raise ValueError('A team can use only one version of each character name.')
                await db.execute('UPDATE saved_teams SET front_id=?,front2_id=?,front3_id=?,back1_id=?,back2_id=?,back3_id=?,team_format=?,updated_at=? WHERE owner_id=? AND slot=?',
                    (*ids,fmt,datetime.now(timezone.utc).isoformat(),owner_id,slot))
                await db.commit()
        return await self.get_saved_team(owner_id,slot)

    async def change_team_format(self,owner_id,slot,team_format):
        team=await self.get_saved_team(owner_id,slot)
        if not team: raise ValueError('That team no longer exists.')
        if team.team_format==team_format:return team
        return await self.replace_saved_team(owner_id,slot,team.front_id if team_format==6 else None,team.back1_id if team_format==6 else None,team.back2_id if team_format==6 else None,
            front2_id=team.front2_id,front3_id=team.front3_id,back3_id=team.back3_id,team_format=team_format,
            expected=team.card_ids,expected_format=team.team_format)

    async def set_team_member(self,owner_id:int,slot:int,instance_id:str,lane:str,*,expected_format=None):
        from .data import CHAR_BY_ID
        if lane not in ('front','back'):raise ValueError('Lane must be front or back.')
        await self.ensure_saved_team(owner_id,slot)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur=await db.execute('SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(owner_id,slot))
                team=self._saved_team(await cur.fetchone())
                if expected_format is not None and team.team_format!=expected_format:
                    raise ValueError('The team format changed. Pick this card again in the current builder.')
                cur=await db.execute('SELECT character_id FROM cards WHERE owner_id=? AND instance_id=?',(owner_id,instance_id))
                card=await cur.fetchone()
                if not card:raise ValueError('That card is not in your collection.')
                for iid in team.card_ids:
                    if not iid:continue
                    cur=await db.execute('SELECT character_id FROM cards WHERE instance_id=?',(iid,));old=await cur.fetchone()
                    if old and CHAR_BY_ID[old['character_id']].name==CHAR_BY_ID[card['character_id']].name:
                        raise ValueError('That player is already selected. Reset this team or use `;teamedit` to swap players.')
                position=next((key for _,key,pos_lane in team.positions if pos_lane==lane and not getattr(team,key)),None)
                if not position:raise ValueError(f'The {lane}line is full. Reset the team or use `;teamedit`.')
                await db.execute(f'UPDATE saved_teams SET {position}=?,updated_at=? WHERE owner_id=? AND slot=?',(instance_id,datetime.now(timezone.utc).isoformat(),owner_id,slot))
                await db.commit()
        team=await self.get_saved_team(owner_id,slot)
        if team.complete:await self.set_user_fields(owner_id,active_team_slot=slot)
        return team

    async def active_team(self, owner_id: int) -> SavedTeam | None:
        """Return the explicitly selected active slot without silently substituting another team.

        An active team may become incomplete when a consumed merge card leaves a saved slot
        empty. Callers decide whether completeness is required and should surface that state to
        the player rather than unexpectedly switching teams.
        """
        user = await self.get_user(owner_id)
        return await self.get_saved_team(owner_id, user.active_team_slot)

    async def set_active_team(self, owner_id: int, slot: int) -> SavedTeam:
        team = await self.get_saved_team(owner_id, slot)
        if not team or not team.complete:
            raise ValueError('That team slot is not complete.')
        await self.set_user_fields(owner_id, active_team_slot=slot)
        return team

    # Legacy helpers retained for old commands/data.
    async def set_team(self, owner_id: int, kind: str, front_id: str, back1_id: str, back2_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self.connect() as db:
            await db.execute('''
                INSERT INTO teams(owner_id,kind,front_id,back1_id,back2_id,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(owner_id,kind) DO UPDATE SET front_id=excluded.front_id, back1_id=excluded.back1_id,
                    back2_id=excluded.back2_id, updated_at=excluded.updated_at
            ''', (owner_id, kind, front_id, back1_id, back2_id, now))
            await db.commit()

    async def get_team(self, owner_id: int, kind: str) -> TeamRecord | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM teams WHERE owner_id=? AND kind=?', (owner_id, kind))
            row = await cur.fetchone()
        if not row:
            return None
        return TeamRecord(owner_id=row['owner_id'], kind=row['kind'], front_id=row['front_id'], back1_id=row['back1_id'], back2_id=row['back2_id'])

    async def random_defense_owner(self, exclude_owner: int) -> int | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT owner_id FROM teams WHERE kind="defense" AND owner_id<>? ORDER BY RANDOM() LIMIT 1', (exclude_owner,))
            row = await cur.fetchone()
        return int(row['owner_id']) if row else None

    async def _clear_card_from_teams_tx(self,db,instance_id):
        keys=('front_id','front2_id','front3_id','back1_id','back2_id','back3_id')
        assignments=','.join(f'{key}=CASE WHEN {key}=? THEN NULL ELSE {key} END' for key in keys)
        conditions=' OR '.join(f'{key}=?' for key in keys)
        await db.execute(f'UPDATE saved_teams SET {assignments},updated_at=? WHERE {conditions}',
            ((instance_id,)*6)+(datetime.now(timezone.utc).isoformat(),)+((instance_id,)*6))
        await db.execute('DELETE FROM teams WHERE front_id=? OR back1_id=? OR back2_id=?',(instance_id,)*3)

    async def recycle_card(self, owner_id: int, instance_id: str, payout: int) -> tuple[CardInstance, int]:
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur = await db.execute('SELECT * FROM cards WHERE instance_id=? AND owner_id=?', (instance_id, owner_id))
                row = await cur.fetchone()
                if not row:
                    await db.rollback()
                    raise ValueError('That card is not in your collection.')
                card = self._card(row)
                if card.locked:
                    await db.rollback()
                    raise ValueError('Locked cards cannot be recycled. Unlock the card first.')
                await self._clear_card_from_teams_tx(db, instance_id)
                await db.execute('DELETE FROM cards WHERE instance_id=? AND owner_id=?', (instance_id, owner_id))
                await db.execute('UPDATE users SET coins=coins+? WHERE discord_id=?', (int(payout), owner_id))
                await db.commit()
        return card, int(payout)

    async def get_shards(self, owner_id: int) -> dict[str, int]:
        async with self.connect() as db:
            cur = await db.execute('SELECT school,quantity FROM shards WHERE owner_id=?', (owner_id,))
            rows = await cur.fetchall()
        return {str(r['school']): int(r['quantity']) for r in rows}

    async def shard_balance(self, owner_id: int, school: str) -> int:
        async with self.connect() as db:
            cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (owner_id, school))
            row = await cur.fetchone()
        return int(row['quantity']) if row else 0

    async def add_shards(self, owner_id: int, school: str, amount: int) -> int:
        await self.ensure_user(owner_id)
        async with self.connect() as db:
            await db.execute('''
                INSERT INTO shards(owner_id,school,quantity) VALUES (?,?,?)
                ON CONFLICT(owner_id,school) DO UPDATE SET quantity=quantity+excluded.quantity
            ''', (owner_id, school, int(amount)))
            await db.commit()
            cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (owner_id, school))
            return int((await cur.fetchone())['quantity'])

    async def spend_shards(self, owner_id: int, school: str, amount: int) -> bool:
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (owner_id, school))
                row = await cur.fetchone()
                balance = int(row['quantity']) if row else 0
                if balance < amount:
                    await db.rollback()
                    return False
                await db.execute('UPDATE shards SET quantity=quantity-? WHERE owner_id=? AND school=?', (int(amount), owner_id, school))
                await db.commit()
                return True

    async def training_wins(self, owner_id: int, school: str, week: str) -> int:
        async with self.connect() as db:
            cur = await db.execute('SELECT wins FROM training_progress WHERE owner_id=? AND school=? AND week_key=?', (owner_id, school, week))
            row = await cur.fetchone()
        return int(row['wins']) if row else 0

    async def record_training_win(self, owner_id: int, school: str, week: str) -> int:
        async with self.connect() as db:
            await db.execute('''
                INSERT INTO training_progress(owner_id,school,week_key,wins) VALUES (?,?,?,1)
                ON CONFLICT(owner_id,school,week_key) DO UPDATE SET wins=wins+1
            ''', (owner_id, school, week))
            await db.commit()
            cur = await db.execute('SELECT wins FROM training_progress WHERE owner_id=? AND school=? AND week_key=?', (owner_id, school, week))
            return int((await cur.fetchone())['wins'])

    async def award_training_shard(self, owner_id: int, school: str, week: str, max_wins: int = 9) -> tuple[bool, int, int]:
        """Atomically award one weekly training success and one persistent school shard.

        Returns ``(awarded, weekly_wins, shard_balance)``. The cap is rechecked inside
        the transaction so simultaneous commands cannot award a tenth weekly shard.
        """
        await self.ensure_user(owner_id)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur = await db.execute(
                    'SELECT wins FROM training_progress WHERE owner_id=? AND school=? AND week_key=?',
                    (owner_id, school, week),
                )
                row = await cur.fetchone()
                wins = int(row['wins']) if row else 0
                if wins >= int(max_wins):
                    cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (owner_id, school))
                    shard_row = await cur.fetchone()
                    await db.rollback()
                    return False, wins, int(shard_row['quantity']) if shard_row else 0
                new_wins = wins + 1
                await db.execute('''
                    INSERT INTO training_progress(owner_id,school,week_key,wins) VALUES (?,?,?,?)
                    ON CONFLICT(owner_id,school,week_key) DO UPDATE SET wins=excluded.wins
                ''', (owner_id, school, week, new_wins))
                await db.execute('''
                    INSERT INTO shards(owner_id,school,quantity) VALUES (?,?,1)
                    ON CONFLICT(owner_id,school) DO UPDATE SET quantity=quantity+1
                ''', (owner_id, school))
                cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (owner_id, school))
                balance = int((await cur.fetchone())['quantity'])
                await db.commit()
                return True, new_wins, balance

    async def get_pity(self, owner_id: int) -> dict | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM pity WHERE owner_id=?', (owner_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def set_pity(self, owner_id: int, character_id: str, armed: bool = False) -> None:
        await self.ensure_user(owner_id)
        async with self.connect() as db:
            await db.execute('''
                INSERT INTO pity(owner_id,character_id,armed) VALUES (?,?,?)
                ON CONFLICT(owner_id) DO UPDATE SET character_id=excluded.character_id,armed=excluded.armed
            ''', (owner_id, character_id, 1 if armed else 0))
            await db.commit()

    async def set_pity_armed(self, owner_id: int, armed: bool) -> None:
        async with self.connect() as db:
            await db.execute('UPDATE pity SET armed=? WHERE owner_id=?', (1 if armed else 0, owner_id))
            await db.commit()

    async def clear_pity(self, owner_id: int) -> None:
        async with self.connect() as db:
            await db.execute('DELETE FROM pity WHERE owner_id=?', (owner_id,))
            await db.commit()

    async def get_test_team(self, owner_id: int) -> dict | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM test_teams WHERE owner_id=?', (owner_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def save_test_team(self, owner_id: int, front: str | None, back1: str | None, back2: str | None, *, team_format: int = 3, front2: str | None = None, front3: str | None = None, back3: str | None = None) -> None:
        if team_format not in (3,6): raise ValueError("Test format must be 3 or 6.")
        await self.ensure_user(owner_id)
        async with self._lock,self.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            previous=await (await db.execute('SELECT settings_json FROM test_teams WHERE owner_id=?',(owner_id,))).fetchone()
            prior=json.loads(previous['settings_json']) if previous else {}
            picked={'front_character_id':front,'front2_character_id':front2,'front3_character_id':front3,'back1_character_id':back1,'back2_character_id':back2,'back3_character_id':back3}
            configs={}
            for key,cid in picked.items():
                if cid:
                    config=next((dict(v) for v in prior.values() if v.get('character_id')==cid),{'character_id':cid})
                    configs[key]=config
            await db.execute('''
                INSERT INTO test_teams(owner_id,front_character_id,back1_character_id,back2_character_id,updated_at,team_format,front2_character_id,front3_character_id,back3_character_id,settings_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id) DO UPDATE SET front_character_id=excluded.front_character_id,
                    back1_character_id=excluded.back1_character_id,back2_character_id=excluded.back2_character_id,
                    team_format=excluded.team_format,front2_character_id=excluded.front2_character_id,
                    front3_character_id=excluded.front3_character_id,back3_character_id=excluded.back3_character_id,updated_at=excluded.updated_at,settings_json=excluded.settings_json
            ''', (owner_id, front, back1, back2, datetime.now(timezone.utc).isoformat(),team_format,front2,front3,back3,json.dumps(configs)))
            await db.commit()

    async def set_test_modifiers(self,owner_id,slot,character_id,kind,value):
        from .data import CHAR_BY_ID
        from .items import ITEM_BY_KEY,can_equip
        if kind not in ('equipment','merge','nature'):raise ValueError('Unknown test-team setting.')
        async with self._lock,self.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            row=await (await db.execute('SELECT * FROM test_teams WHERE owner_id=?',(owner_id,))).fetchone()
            slots=('front_character_id','back1_character_id','back2_character_id') if row and row['team_format']==3 else ('front_character_id','front2_character_id','front3_character_id','back1_character_id','back2_character_id','back3_character_id')
            if not row or slot not in slots or row[slot]!=character_id:raise ValueError('The test-team slot changed. Select the unit again.')
            char=CHAR_BY_ID[character_id];settings=json.loads(row['settings_json']);config=settings.setdefault(slot,{'character_id':character_id})
            if kind=='equipment':
                if value is not None and (value not in ITEM_BY_KEY or not can_equip(ITEM_BY_KEY[value],char)):raise ValueError('That unit cannot equip this item.')
                config['equipment_key']=value
            elif kind=='merge':
                if not isinstance(value,int) or not 0<=value<=10:raise ValueError('Merge must be from 0 to 10.')
                config['merge_level']=value
            else:
                boon,bane=value
                allowed={'hp',char.offense_key,'def','rec','spd','jmp'}
                if (boon,bane)!=(None,None) and (boon not in allowed or bane not in allowed):raise ValueError('Choose a valid boon and bane.')
                if boon==bane:boon=bane=None
                config['boon_stat']=boon;config['bane_stat']=bane
            await db.execute('UPDATE test_teams SET settings_json=? WHERE owner_id=?',(json.dumps(settings),owner_id));await db.commit()

    async def execute_trade(self, user_a: int, offer_a: dict, user_b: int, offer_b: dict) -> None:
        """Atomically exchange cards, items, currencies and counted shards."""
        if user_a == user_b:
            raise ValueError('You cannot trade with yourself.')
        await self.ensure_user(user_a)
        await self.ensure_user(user_b)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')

                async def validate(uid: int, offer: dict) -> None:
                    for key in ('cards', 'items'):
                        values = offer.get(key, [])
                        if len(values) != len(set(values)):
                            raise ValueError('Each card or item may be offered only once.')
                    if int(offer.get('coins', 0)) < 0 or any(int(q) < 0 for q in offer.get('shards', {}).values()):
                        raise ValueError('Trade quantities cannot be negative.')
                    from .shard_balances import KINDS,balance as shard_balance
                    for kind,groups in offer.get('raid_shards',{}).items():
                        if kind not in KINDS or not isinstance(groups,dict):raise ValueError('Unknown shard group.')
                        for detail,qty in groups.items():
                            if not isinstance(qty,int) or qty<0:raise ValueError('Shard quantities must be nonnegative whole numbers.')
                            if qty>await shard_balance(db,uid,kind,detail):raise ValueError('A player no longer has enough '+detail+' shards.')
                    for iid in offer.get('items', []):
                        cur = await db.execute('SELECT owner_id FROM inventory_items WHERE id=?', (iid,))
                        item = await cur.fetchone()
                        if not item or item['owner_id'] != uid:
                            raise ValueError(f'Inventory item `{iid}` is no longer owned by the offering player.')
                    coins = max(0, int(offer.get('coins', 0)))
                    cur = await db.execute('SELECT coins FROM users WHERE discord_id=?', (uid,))
                    row = await cur.fetchone()
                    if not row or int(row['coins']) < coins:
                        raise ValueError('A player no longer has enough coins for this trade.')
                    for school, qty in offer.get('shards', {}).items():
                        qty = max(0, int(qty))
                        cur = await db.execute('SELECT quantity FROM shards WHERE owner_id=? AND school=?', (uid, school))
                        r = await cur.fetchone()
                        if (int(r['quantity']) if r else 0) < qty:
                            raise ValueError(f'A player no longer has enough {school} shards.')
                    for iid in offer.get('cards', []):
                        cur = await db.execute('SELECT owner_id,locked FROM cards WHERE instance_id=?', (iid,))
                        r = await cur.fetchone()
                        if not r or int(r['owner_id']) != uid:
                            raise ValueError(f'Card `{iid}` is no longer owned by the offering player.')
                        if int(r['locked']):
                            raise ValueError(f'Card `{iid}` is locked and cannot be traded.')

                try:
                    await validate(user_a, offer_a)
                    await validate(user_b, offer_b)
                    for uid, offer, other in ((user_a, offer_a, user_b), (user_b, offer_b, user_a)):
                        coins = max(0, int(offer.get('coins', 0)))
                        if coins:
                            await db.execute('UPDATE users SET coins=coins-? WHERE discord_id=?', (coins, uid))
                            await db.execute('UPDATE users SET coins=coins+? WHERE discord_id=?', (coins, other))
                        for school, qty in offer.get('shards', {}).items():
                            qty = max(0, int(qty))
                            if not qty:
                                continue
                            await db.execute('UPDATE shards SET quantity=quantity-? WHERE owner_id=? AND school=?', (qty, uid, school))
                            await db.execute('''
                                INSERT INTO shards(owner_id,school,quantity) VALUES (?,?,?)
                                ON CONFLICT(owner_id,school) DO UPDATE SET quantity=quantity+excluded.quantity
                            ''', (other, school, qty))
                        from .shard_balances import credit as credit_shards,consume as consume_shards
                        for kind,groups in offer.get('raid_shards',{}).items():
                            for detail,qty in groups.items():
                                if qty:
                                    await consume_shards(db,uid,kind,detail,qty)
                                    await credit_shards(db,other,kind,detail,qty)
                        for iid in offer.get('items', []):
                            await db.execute('UPDATE inventory_items SET owner_id=? WHERE id=?', (other, iid))
                        for iid in offer.get('cards', []):
                            await self._clear_card_from_teams_tx(db, iid)
                            await db.execute('UPDATE cards SET owner_id=? WHERE instance_id=?', (other, iid))
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

    # ----- gauntlet -----
    async def get_gauntlet_run(self, owner_id: int, mode: str) -> dict | None:
        async with self.connect() as db:
            cur = await db.execute('SELECT * FROM gauntlet_runs WHERE owner_id=? AND mode=?', (owner_id, mode))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def save_gauntlet_run(self, owner_id: int, mode: str, week: str, school: str, next_game: int, status: str,
                                team_slot: int, front_id: str, back1_id: str, back2_id: str) -> None:
        async with self.connect() as db:
            await db.execute('''
                INSERT INTO gauntlet_runs(owner_id,mode,week_key,school,next_game,status,team_slot,front_id,back1_id,back2_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,mode) DO UPDATE SET week_key=excluded.week_key,school=excluded.school,
                    next_game=excluded.next_game,status=excluded.status,team_slot=excluded.team_slot,
                    front_id=excluded.front_id,back1_id=excluded.back1_id,back2_id=excluded.back2_id,updated_at=excluded.updated_at
            ''', (owner_id, mode, week, school, next_game, status, team_slot, front_id, back1_id, back2_id, datetime.now(timezone.utc).isoformat()))
            await db.commit()

    async def gauntlet_claimed(self, owner_id: int, mode: str, week: str) -> bool:
        async with self.connect() as db:
            cur = await db.execute('SELECT 1 FROM gauntlet_claims WHERE owner_id=? AND mode=? AND week_key=?', (owner_id, mode, week))
            return (await cur.fetchone()) is not None

    async def claim_gauntlet(self, owner_id: int, mode: str, week: str, reward: int = 0) -> bool:
        await self.ensure_user(owner_id)
        async with self._lock:
            async with self.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                cur=await db.execute('INSERT OR IGNORE INTO gauntlet_claims(owner_id,mode,week_key,claimed_at) VALUES (?,?,?,?)',
                    (owner_id,mode,week,datetime.now(timezone.utc).isoformat()))
                claimed=cur.rowcount == 1
                if claimed and reward:
                    await db.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(int(reward),owner_id))
                await db.commit()
                return claimed

    async def leaderboard(self, limit: int = 10) -> list[tuple[int, int]]:
        async with self.connect() as db:
            cur = await db.execute('SELECT discord_id, coins FROM users ORDER BY coins DESC LIMIT ?', (limit,))
            rows = await cur.fetchall()
        return [(int(r['discord_id']), int(r['coins'])) for r in rows]
