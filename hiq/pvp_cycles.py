"""Weekly first-place prizes on a shared five-week Elo board."""
import json
from datetime import date,timedelta

ANCHOR=date(2026,11,9)  # Monday; matches the game's training reset.
SCHEMA='''CREATE TABLE IF NOT EXISTS pvp_weeks (
 week TEXT PRIMARY KEY,cycle TEXT NOT NULL,phase INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0);'''


def week_start(day):
    d=date.fromisoformat(day) if isinstance(day,str) else day
    return d-timedelta(days=d.weekday())


def cycle_for(day):
    monday=week_start(day);index=(monday-ANCHOR).days//7
    return 'cycle-'+(ANCHOR+timedelta(weeks=index//5*5)).isoformat(),index%5+1


async def settle(store,c,day):
    current=week_start(day);cycle,phase=cycle_for(current)
    migrated=await store.one(c,"SELECT 1 FROM economy_migrations WHERE version='v12-weekly-pvp'")
    if not migrated:
        # Carry the live monthly board into week one, once. Historical monthly
        # boards are left intact; this release does not pay obsolete prizes.
        await c.execute('''INSERT OR IGNORE INTO pvp_ratings
            SELECT ?,owner_id,rating,wins,losses,draws FROM pvp_ratings WHERE month=?''',(cycle,day[:7]))
        await c.execute("UPDATE pvp_runs SET month=? WHERE status='running' AND month NOT LIKE 'cycle-%'",(cycle,))
        await c.execute("INSERT INTO economy_migrations VALUES ('v12-weekly-pvp')")
    await c.execute('INSERT OR IGNORE INTO pvp_weeks(week,cycle,phase) VALUES (?,?,?)',(current.isoformat(),cycle,phase))
    oldest=await store.one(c,'SELECT MIN(week) AS week FROM pvp_weeks WHERE closed=0')
    cursor=date.fromisoformat(oldest['week'])
    while cursor<current:
        key=cursor.isoformat();old_cycle,old_phase=cycle_for(cursor)
        await c.execute('INSERT OR IGNORE INTO pvp_weeks(week,cycle,phase) VALUES (?,?,?)',(key,old_cycle,old_phase))
        row=await store.one(c,'SELECT closed FROM pvp_weeks WHERE week=?',(key,))
        if row['closed']:cursor+=timedelta(weeks=1);continue
        # Finish matches that began before the cutoff before taking the snapshot.
        pending=await store.rows(c,"SELECT state_json FROM pvp_runs WHERE month=? AND status='running' AND rated=1",(old_cycle,))
        if any(week_start(json.loads(r['state_json'])['day'])<=cursor for r in pending):break
        winner=await store.one(c,'''SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0
            ORDER BY rating DESC,wins DESC,losses ASC,owner_id LIMIT 1''',(old_cycle,))
        if winner:
            uid=winner['owner_id'];iid=await store._issue_item(c,uid,'starpack',stars=old_phase)
            from .youth_rewards import currency
            coins=(5-old_phase)*100
            if coins:await currency(c,uid,coins)
            await c.execute('INSERT INTO pvp_monthly_awards VALUES (?,?,?,?,?)',('week-'+key,uid,1,winner['rating'],json.dumps([iid])))
        await c.execute('UPDATE pvp_weeks SET closed=1 WHERE week=?',(key,))
        cursor+=timedelta(weeks=1)
    return cycle,phase
