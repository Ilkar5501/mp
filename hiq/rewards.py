"""Shared, transactional daily win rewards for the 10.1 economy."""
from .economy import local_date_key

SCHEMA = '''
CREATE TABLE IF NOT EXISTS economy_daily (
 owner_id INTEGER NOT NULL, day_key TEXT NOT NULL, reward_key TEXT NOT NULL,
 wins INTEGER NOT NULL DEFAULT 0, coins INTEGER NOT NULL DEFAULT 0,
 clan_coins INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,day_key,reward_key));
CREATE TABLE IF NOT EXISTS economy_pvp_opponents (
 owner_id INTEGER NOT NULL,day_key TEXT NOT NULL,opponent_id INTEGER NOT NULL,
 PRIMARY KEY(owner_id,day_key,opponent_id));
CREATE TABLE IF NOT EXISTS raid_shard_daily (
 owner_id INTEGER NOT NULL,day_key TEXT NOT NULL,reward_key TEXT NOT NULL,amount INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,day_key,reward_key));
CREATE TABLE IF NOT EXISTS raid_shard_weekly (
 owner_id INTEGER NOT NULL,week_key TEXT NOT NULL,amount INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,week_key));
CREATE TABLE IF NOT EXISTS economy_migrations (version TEXT PRIMARY KEY);
'''


async def daily(conn, uid, day=None):
    cur=await conn.execute('SELECT * FROM economy_daily WHERE owner_id=? AND day_key=?',(uid,day or local_date_key()))
    return {row['reward_key']:dict(row) for row in await cur.fetchall()}


async def award_in(conn, uid, key, *, day=None, opponent=None, human_tournament=False):
    """Caller owns the transaction; wallet and cap usage always commit together."""
    day=day or local_date_key()
    from datetime import datetime,timezone
    await conn.execute('INSERT OR IGNORE INTO users(discord_id,created_at) VALUES (?,?)',(uid,datetime.now(timezone.utc).isoformat()))
    progress=(await daily(conn,uid,day)).get(key,{'wins':0,'coins':0,'clan_coins':0})
    wins=progress['wins'];coins=clan=0
    if key=='pvp':
        if opponent is None or opponent==uid:raise ValueError('Choose another player.')
        cur=await conn.execute('INSERT OR IGNORE INTO economy_pvp_opponents VALUES (?,?,?)',(uid,day,opponent))
        if not cur.rowcount:return dict(coins=0,clan_coins=0,wins=wins,duplicate=True)
    if key=='battle':coins=30 if wins<5 else 4 if wins<50 else 0
    elif key=='tournament':coins=(150 if human_tournament else 100) if wins<3 else 0
    elif key in ('gauntlet_regional','gauntlet_national'):coins=(100,50)[wins] if wins<2 else 0
    elif key=='raid':coins=50 if wins<6 else 0;clan=25 if wins<4 else 0
    elif key=='pvp':coins=50 if wins<8 else 0
    elif key=='prefecture':coins=50 if wins<6 else 0
    elif key=='war':coins=300 if wins<1 else 0;clan=100 if wins<1 else 0
    else:raise ValueError('Unknown reward mode.')
    await conn.execute('''INSERT INTO economy_daily VALUES (?,?,?,?,?,?)
        ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET
        wins=wins+1,coins=coins+excluded.coins,clan_coins=clan_coins+excluded.clan_coins''',
        (uid,day,key,1,coins,clan))
    if coins:await conn.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(coins,uid))
    if clan:await conn.execute('INSERT INTO clan_wallets VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET coins=coins+excluded.coins',(uid,clan))
    return dict(coins=coins,clan_coins=clan,wins=wins+1,duplicate=False)


class RewardStore:
    def __init__(self,db):self.db=db

    async def daily(self,uid,day=None):
        async with self.db.connect() as conn:return await daily(conn,uid,day)

    async def award(self,uid,key,**kwargs):
        await self.db.ensure_user(uid)
        async with self.db._lock,self.db.connect() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            result=await award_in(conn,uid,key,**kwargs)
            if key=='battle':
                from .economy import week_key
                await conn.execute('''UPDATE users SET daily_win_date=?,daily_wins=?,
                    weekly_wins=CASE WHEN week_key=? THEN weekly_wins+1 ELSE 1 END,
                    week_key=?,weekly_claimed='[]' WHERE discord_id=?''',
                    (kwargs.get('day') or local_date_key(),result['wins'],week_key(),week_key(),uid))
            await conn.commit()
            return result


async def migrate(conn):
    """Reset old economy allowances once; keep currencies, cards and results."""
    await conn.execute('BEGIN IMMEDIATE')
    cur=await conn.execute("SELECT 1 FROM economy_migrations WHERE version='10.1'")
    if await cur.fetchone():
        await conn.commit();return
    await conn.execute("UPDATE users SET daily_win_date=NULL,daily_wins=0,week_key=NULL,weekly_wins=0,weekly_claimed='[]'")
    for table in ('clan_daily','clan_checkin_bonus','clan_weekly','prefecture_daily','tournament_daily','raid_weekly','gauntlet_claims'):
        await conn.execute('DELETE FROM '+table)
    await conn.execute("INSERT INTO economy_migrations VALUES ('10.1')")
    await conn.commit()
