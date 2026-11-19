"""Small per-day reward breakdowns; no battle snapshots or extra wallet credits."""
import json
from datetime import datetime
from .economy import week_key_for
from .data import CHAR_BY_ID
from .youth_rewards import extra,currency,highest,OIKAWA

SCHEMA='''
CREATE TABLE IF NOT EXISTS gauntlet_rewards (
 owner_id INTEGER NOT NULL,day_key TEXT NOT NULL,mode TEXT NOT NULL,
 clear_coins INTEGER NOT NULL DEFAULT 0,lineup_coins INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,day_key,mode));
CREATE TABLE IF NOT EXISTS raid_currency_extras (
 owner_id INTEGER NOT NULL,day_key TEXT NOT NULL,kind TEXT NOT NULL,
 amount INTEGER NOT NULL DEFAULT 0,claims INTEGER NOT NULL DEFAULT 0,estimated INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,day_key,kind));
'''


async def gauntlet_award(c,uid,key,days,lineup):
    from .rewards import daily
    if lineup is None or len(lineup)!=3 or len(set(lineup))!=3 or any(cid not in CHAR_BY_ID for cid in lineup):
        raise ValueError('A gauntlet lineup needs three distinct character variants.')
    signature=json.dumps(sorted(lineup),ensure_ascii=False)
    fresh=bool((await c.execute('INSERT OR IGNORE INTO gauntlet_lineups VALUES (?,?,?,?)',(uid,week_key_for(datetime.fromisoformat(days[-1])),key,signature))).rowcount)
    amounts={};chosen_any=None;extra_award=False
    for column,base,eligible,reward_key in [('clear_coins',200,True,key),('lineup_coins',50,fresh,key+'_lineup')]:
        amounts[column]=0
        if not eligible:continue
        rows=await (await c.execute(f'SELECT day_key,{column} FROM gauntlet_rewards WHERE owner_id=? AND mode=? AND day_key BETWEEN ? AND ?',(uid,key,days[0],days[-1]))).fetchall()
        used={r['day_key']:r[column] for r in rows}
        chosen=next((day for day in days if used.get(day,0)<base),None)
        amount=max(0,base-used.get(chosen,0)) if chosen else 0
        if chosen is None:
            allowance=await extra(c,uid,reward_key,days)
            if allowance:chosen,rate=allowance;amount=int(base*rate);extra_award=True
        if chosen is None:continue
        chosen_any=chosen;amounts[column]=amount
        await c.execute(f'''INSERT INTO gauntlet_rewards(owner_id,day_key,mode,{column}) VALUES (?,?,?,?)
            ON CONFLICT(owner_id,day_key,mode) DO UPDATE SET {column}={column}+excluded.{column}''',(uid,chosen,key,amount))
        await c.execute('''INSERT INTO economy_daily VALUES (?,?,?,?,?,0)
            ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET wins=wins+excluded.wins,coins=coins+excluded.coins''',(uid,chosen,key,int(column=='clear_coins'),amount))
    paid=await currency(c,uid,sum(amounts.values()),qualifies=chosen_any is not None)
    progress=(await daily(c,uid,days[-1])).get(key,{})
    return dict(coins=paid,clan_coins=0,wins=progress.get('wins',0),duplicate=False,allowance_day=chosen_any,
                extra_allowance=extra_award,fresh_lineup=fresh,**amounts)


async def record_raid_extra(c,uid,day,kind,amount):
    await c.execute('''INSERT INTO raid_currency_extras VALUES (?,?,?,?,1,0)
        ON CONFLICT(owner_id,day_key,kind) DO UPDATE SET amount=amount+excluded.amount,claims=claims+1''',(uid,day,kind,amount))


async def migrate(c):
    marker='12.1.13-reward-components'
    if await (await c.execute('SELECT 1 FROM economy_migrations WHERE version=?',(marker,))).fetchone():return
    await c.execute('BEGIN IMMEDIATE')
    try:
        for row in await (await c.execute("SELECT * FROM economy_daily WHERE reward_key IN ('gauntlet_regional','gauntlet_national')")).fetchall():
            uid,day,key=row['owner_id'],row['day_key'],row['reward_key']
            rate=(await highest(c,uid,OIKAWA) or 0)*.05
            claimed=await (await c.execute('SELECT claims FROM youth_daily WHERE owner_id=? AND day=? AND reward_key=?',(uid,day,key))).fetchone()
            clear=min(row['coins'],200+(int(200*rate) if claimed else 0));bonus=row['coins']-clear
            await c.execute('INSERT OR IGNORE INTO gauntlet_rewards VALUES (?,?,?,?,?)',(uid,day,key,clear,bonus))
            if bonus>50 or (claimed and rate==0):
                await c.execute('INSERT OR IGNORE INTO youth_daily VALUES (?,?,?,1)',(uid,day,key+'_lineup'))
        for row in await (await c.execute("SELECT * FROM youth_daily WHERE reward_key IN ('raid_coins','raid_clancoins')")).fetchall():
            rate=(await highest(c,row['owner_id'],OIKAWA) or 0)*.05
            # Old replay expiry discarded exact paid amounts. Preserve claims and
            # label this display-only estimate; never alter either wallet.
            await c.execute('INSERT OR IGNORE INTO raid_currency_extras VALUES (?,?,?,?,?,1)',(row['owner_id'],row['day'],row['reward_key'][5:],int(100*rate)*row['claims'],row['claims']))
        await c.execute('INSERT INTO economy_migrations VALUES (?)',(marker,));await c.commit()
    except BaseException:
        await c.rollback();raise
