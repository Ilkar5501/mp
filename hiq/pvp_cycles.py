"""Weekly cumulative percentile packs on a shared two-week Elo board."""
import json
from datetime import date,datetime,timedelta

ANCHOR=date(2026,11,9)  # Monday; matches the game's training reset.
SCHEMA='''CREATE TABLE IF NOT EXISTS pvp_weeks (
 week TEXT PRIMARY KEY,cycle TEXT NOT NULL,phase INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0);'''


def week_start(day):
    d=date.fromisoformat(day) if isinstance(day,str) else day
    return d-timedelta(days=d.weekday())


def cycle_for(day):
    monday=week_start(day);index=(monday-ANCHOR).days//7
    return 'fortnight-'+(ANCHOR+timedelta(weeks=index//2*2)).isoformat(),index%2+1


def next_elo_reset(day):
    from .config import TIMEZONE
    _,phase=cycle_for(day)
    end=week_start(day)+timedelta(weeks=3-phase)
    return datetime(end.year,end.month,end.day,tzinfo=TIMEZONE)


def prize_stars(rank,total):
    """Round each bracket up; award every qualifying tier, once per week."""
    if not 1<=rank<=total:return []
    return [stars for stars in range(1,6) if rank<=(total*(6-stars)+4)//5]


async def migrate(store,c,day):
    marker='v12-biweekly-percentile-pvp'
    if await store.one(c,'SELECT 1 FROM economy_migrations WHERE version=?',(marker,)):return
    cycle,_=cycle_for(day)
    # Carry the live pre-update board once, without replaying already paid weeks.
    old_weekly=await store.one(c,"SELECT 1 FROM economy_migrations WHERE version='v12-weekly-pvp'")
    index=(week_start(day)-ANCHOR).days//7
    source='cycle-'+(ANCHOR+timedelta(weeks=index//5*5)).isoformat() if old_weekly else day[:7]
    async def carry(source,target):
        if source!=target:
            await c.execute('''INSERT OR IGNORE INTO pvp_ratings
                SELECT ?,owner_id,rating,wins,losses,draws FROM pvp_ratings WHERE month=?''',(target,source))
    await carry(source,cycle)
    for row in await store.rows(c,'SELECT * FROM pvp_weeks WHERE closed=0'):
        target,phase=cycle_for(row['week']);await carry(row['cycle'],target)
        await c.execute('UPDATE pvp_weeks SET cycle=?,phase=? WHERE week=?',(target,phase,row['week']))
    for row in await store.rows(c,"SELECT id,month,rated,state_json FROM pvp_runs WHERE status='running'"):
        target,_=cycle_for(json.loads(row['state_json'])['day'])
        await carry(row['month'],target)
        await c.execute('UPDATE pvp_runs SET month=? WHERE id=?',(target,row['id']))
        # A pending old match still needs its own cutoff settled after recovery.
        if row['rated']:
            week=week_start(json.loads(row['state_json'])['day']).isoformat()
            _,phase=cycle_for(week)
            await c.execute('INSERT OR IGNORE INTO pvp_weeks(week,cycle,phase) VALUES (?,?,?)',(week,target,phase))
    await c.execute("INSERT OR IGNORE INTO economy_migrations VALUES ('v12-weekly-pvp')")
    await c.execute('INSERT INTO economy_migrations VALUES (?)',(marker,))


async def settle(store,c,day):
    current=week_start(day);cycle,phase=cycle_for(current)
    await migrate(store,c,day)
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
        board=await store.rows(c,'''SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0
            ORDER BY rating DESC,wins DESC,losses ASC,owner_id''',(old_cycle,))
        for rank,player in enumerate(board,1):
            uid=player['owner_id']
            ids=[await store._issue_item(c,uid,'starpack',stars=stars) for stars in prize_stars(rank,len(board))]
            await c.execute('INSERT INTO pvp_monthly_awards VALUES (?,?,?,?,?)',('week-'+key,uid,rank,player['rating'],json.dumps(ids)))
        await c.execute('UPDATE pvp_weeks SET closed=1 WHERE week=?',(key,))
        cursor+=timedelta(weeks=1)
    return cycle,phase
