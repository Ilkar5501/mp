"""Daily allowances bank within the current calendar week, transactionally."""
from .economy import local_date_key
from .allowances import allowance_days

LIMITS={'battle':50,'tournament':3,'gauntlet_regional':2,'gauntlet_national':2,
        'pvp':8,'prefecture':6,'war':1}
ROLLOVER_MODES={'tournament','gauntlet_regional','gauntlet_national','prefecture','raid'}

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
    day=day or local_date_key()
    days=await allowance_days(conn,uid,day)
    cur=await conn.execute('SELECT * FROM economy_daily WHERE owner_id=? AND day_key BETWEEN ? AND ?',(uid,days[0],days[-1]))
    progress={}
    for row in await cur.fetchall():
        key=row['reward_key']
        if key not in ROLLOVER_MODES and row['day_key']!=day:continue
        total=progress.setdefault(key,dict(wins=0,coins=0,clan_coins=0))
        total['wins']+=min(row['wins'],LIMITS.get(key,row['wins'])) if key in ROLLOVER_MODES else row['wins']
        total['coins']+=row['coins'];total['clan_coins']+=row['clan_coins']
    return progress


async def award_in(conn, uid, key, *, day=None, opponent=None, human_tournament=False):
    """Caller owns the transaction; wallet and cap usage always commit together."""
    day=day or local_date_key()
    from datetime import datetime,timezone
    await conn.execute('INSERT OR IGNORE INTO users(discord_id,created_at) VALUES (?,?)',(uid,datetime.now(timezone.utc).isoformat()))
    if key not in LIMITS:raise ValueError('Unknown reward mode; raid drops must use RaidStore.finish.')
    days=await allowance_days(conn,uid,day) if key in ROLLOVER_MODES else [day]
    cur=await conn.execute('SELECT * FROM economy_daily WHERE owner_id=? AND reward_key=? AND day_key BETWEEN ? AND ?',(uid,key,days[0],day))
    rows={r['day_key']:dict(r) for r in await cur.fetchall()}
    def wins_on(d):return rows.get(d,{}).get('wins',0)
    coins=clan=0;duplicate=False;chosen=None
    if key=='pvp':
        if opponent is None or opponent==uid:raise ValueError('Choose another player.')
        cur=await conn.execute('INSERT OR IGNORE INTO economy_pvp_opponents VALUES (?,?,?)',(uid,day,opponent))
        duplicate=not bool(cur.rowcount)
        if duplicate:
            progress=(await daily(conn,uid,day)).get(key,{})
            return dict(coins=0,clan_coins=0,wins=progress.get('wins',0),duplicate=True,allowance_day=None)
        chosen=day if wins_on(day)<8 else None
        if chosen:coins=50
    else:
        # Bank each day's high-paying tier separately from its lower-paying tier.
        # Extra unpaid wins never spend tomorrow's not-yet-accrued allowance.
        tiers=(5,50) if key=='battle' else (1,2) if key.startswith('gauntlet_') else (LIMITS[key],)
        for threshold in tiers:
            chosen=next((d for d in days if wins_on(d)<threshold),None)
            if chosen:break
        if chosen:
            wins=wins_on(chosen)
            if key=='battle':coins=50 if wins<5 else 4
            elif key=='tournament':coins=150 if human_tournament else 100
            elif key.startswith('gauntlet_'):coins=150 if wins==0 else 50
            elif key=='prefecture':coins=50
            elif key=='war':coins=clan=200
    await conn.execute('''INSERT INTO economy_daily VALUES (?,?,?,?,?,?)
        ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET
        wins=wins+1,coins=coins+excluded.coins,clan_coins=clan_coins+excluded.clan_coins''',
        (uid,chosen or day,key,1,coins,clan))
    if coins:await conn.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(coins,uid))
    if clan:await conn.execute('INSERT INTO clan_wallets VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET coins=coins+excluded.coins',(uid,clan))
    progress=(await daily(conn,uid,day))[key]
    return dict(coins=coins,clan_coins=clan,wins=progress['wins'],duplicate=duplicate,allowance_day=chosen)


class RewardStore:
    def __init__(self,db):self.db=db

    async def daily(self,uid,day=None):
        async with self.db.connect() as conn:return await daily(conn,uid,day)

    async def days(self,uid,day=None):
        async with self.db.connect() as conn:return await allowance_days(conn,uid,day)

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
