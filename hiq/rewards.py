"""Daily allowances bank within the current calendar week, transactionally."""
from .economy import local_date_key
from .allowances import allowance_days

LIMITS={'battle':9,'tournament':1,'gauntlet_regional':1,'gauntlet_national':1,
        'pvp':4,'prefecture':3,'war':1}
ROLLOVER_MODES={'battle','tournament','gauntlet_regional','gauntlet_national','prefecture','raid'}

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
CREATE TABLE IF NOT EXISTS gauntlet_lineups (owner_id INTEGER NOT NULL,week_key TEXT NOT NULL,mode TEXT NOT NULL,lineup TEXT NOT NULL,PRIMARY KEY(owner_id,week_key,mode,lineup));
CREATE TABLE IF NOT EXISTS economy_migrations (version TEXT PRIMARY KEY);
'''


async def daily(conn, uid, day=None):
    day=day or local_date_key()
    days=await allowance_days(conn,uid,day)
    from .youth_rewards import highest,OIKAWA
    extra_limit=int(await highest(conn,uid,OIKAWA) is not None)
    cur=await conn.execute('SELECT * FROM economy_daily WHERE owner_id=? AND day_key BETWEEN ? AND ?',(uid,days[0],days[-1]))
    progress={}
    for row in await cur.fetchall():
        key=row['reward_key']
        if key not in ROLLOVER_MODES and row['day_key']!=day:continue
        total=progress.setdefault(key,dict(wins=0,coins=0,clan_coins=0))
        total['wins']+=min(row['wins'],LIMITS.get(key,row['wins'])+(extra_limit if key!='prefecture' else 0)) if key in ROLLOVER_MODES else row['wins']
        total['coins']+=row['coins'];total['clan_coins']+=row['clan_coins']
    return progress


async def award_in(conn, uid, key, *, day=None, opponent=None, human_tournament=False,lineup=None):
    """Caller owns the transaction; wallet and cap usage always commit together."""
    day=day or local_date_key()
    from datetime import datetime,timezone
    await conn.execute('INSERT OR IGNORE INTO users(discord_id,created_at) VALUES (?,?)',(uid,datetime.now(timezone.utc).isoformat()))
    if key not in LIMITS:raise ValueError('Unknown reward mode; raid drops must use RaidStore.finish.')
    days=await allowance_days(conn,uid,day) if key in ROLLOVER_MODES else [day]
    cur=await conn.execute('SELECT * FROM economy_daily WHERE owner_id=? AND reward_key=? AND day_key BETWEEN ? AND ?',(uid,key,days[0],day))
    rows={r['day_key']:dict(r) for r in await cur.fetchall()}
    def wins_on(d):return rows.get(d,{}).get('wins',0)
    coins=clan=0;duplicate=False;chosen=None;unique=False
    if key=='pvp':
        if opponent is None or opponent==uid:raise ValueError('Choose another player.')
        cur=await conn.execute('INSERT OR IGNORE INTO economy_pvp_opponents VALUES (?,?,?)',(uid,day,opponent))
        duplicate=not bool(cur.rowcount)
        if duplicate:
            progress=(await daily(conn,uid,day)).get(key,{})
            return dict(coins=0,clan_coins=0,wins=progress.get('wins',0),duplicate=True,allowance_day=None)
        chosen=day if wins_on(day)<LIMITS['pvp'] else None
        if chosen:coins=min(100,max(0,400-rows.get(day,{}).get('coins',0)))
    elif key in ('battle','prefecture','tournament') or key.startswith('gauntlet_'):
        unique=False
        if key.startswith('gauntlet_'):
            if lineup is None:raise ValueError('A gauntlet clear needs its three character variants.')
            import json
            from .economy import week_key_for
            from datetime import datetime
            signature=json.dumps(sorted(lineup),ensure_ascii=False)
            from .data import CHAR_BY_ID
            if len(lineup)!=3 or len(set(lineup))!=3 or any(cid not in CHAR_BY_ID for cid in lineup):
                raise ValueError('A gauntlet lineup needs three distinct character variants.')
            cur=await conn.execute('INSERT OR IGNORE INTO gauntlet_lineups VALUES (?,?,?,?)',(uid,week_key_for(datetime.fromisoformat(day)),key,signature))
            unique=bool(cur.rowcount)
        base={'battle':450,'prefecture':300,'tournament':300}.get(key,200)
        bonus=150 if key=='tournament' and human_tournament else 50 if unique else 0
        chosen=next((d for d in days if rows.get(d,{}).get('coins',0)<base+bonus),None)
        if chosen:
            used=rows.get(chosen,{}).get('coins',0)
            if key in ('battle','prefecture'):coins=min(50 if key=='battle' else 100,max(0,base-used))
            else:coins=max(0,base-used)+min(bonus,max(0,base+bonus-max(base,used)))
    else:
        chosen=day if wins_on(day)<LIMITS[key] else None
        if chosen and key=='war':coins=clan=200
    from .youth_rewards import extra,currency
    extra_award=False
    if chosen is None and key!='prefecture':
        allowance=await extra(conn,uid,key,days)
        if allowance:
            chosen,rate=allowance;extra_award=True
            normal={'battle':50,'tournament':450 if human_tournament else 300,'gauntlet_regional':250 if unique else 200,'gauntlet_national':250 if unique else 200,'pvp':100,'war':200}[key]
            coins=int(normal*rate)
            if key=='war':clan=coins
    await conn.execute('''INSERT INTO economy_daily VALUES (?,?,?,?,?,?)
        ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET
        wins=wins+1,coins=coins+excluded.coins,clan_coins=clan_coins+excluded.clan_coins''',
        (uid,chosen or day,key,1,coins,clan))
    coins=await currency(conn,uid,coins,qualifies=chosen is not None)
    clan=await currency(conn,uid,clan,clan=True,qualifies=key=='war' and chosen is not None)
    progress=(await daily(conn,uid,day))[key]
    return dict(coins=coins,clan_coins=clan,wins=progress['wins'],duplicate=duplicate,allowance_day=chosen,extra_allowance=extra_award)


class RewardStore:
    def __init__(self,db):self.db=db

    async def daily(self,uid,day=None):
        async with self.db.connect() as conn:return await daily(conn,uid,day)

    async def days(self,uid,day=None):
        async with self.db.connect() as conn:return await allowance_days(conn,uid,day)

    async def award(self,uid,key,*,record_win=True,**kwargs):
        await self.db.ensure_user(uid)
        async with self.db._lock,self.db.connect() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            result=await award_in(conn,uid,key,**kwargs)
            if key=='battle' and record_win:
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
