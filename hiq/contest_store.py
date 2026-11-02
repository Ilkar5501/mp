"""Persisted rotation, best daily scores, and exactly-once contest rewards."""
import json,random,uuid
from datetime import date,timedelta,datetime
from .community_store import CommunityStore,now
from .economy import local_date_key,week_key_for
from .tournament import snapshot_specs

SCHEMA='''
CREATE TABLE IF NOT EXISTS contest_rotations (start_day TEXT PRIMARY KEY,order_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contest_runs (id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,day TEXT NOT NULL,clan_id TEXT NOT NULL,status TEXT NOT NULL,state_json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS contest_one_running ON contest_runs(owner_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS contest_scores (owner_id INTEGER NOT NULL,day TEXT NOT NULL,week TEXT NOT NULL,clan_id TEXT NOT NULL,score INTEGER NOT NULL,PRIMARY KEY(owner_id,day));
CREATE TABLE IF NOT EXISTS contest_weeks (week TEXT PRIMARY KEY,results_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contest_prizes (week TEXT NOT NULL,owner_id INTEGER NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,PRIMARY KEY(week,owner_id,kind));
'''


class ContestStore(CommunityStore):
    async def rotation(self,day=None,conn=None):
        if conn is None:
            async with self.tx() as c:return await self.rotation(day,c)
        d=date.fromisoformat(day or local_date_key());anchor=date(2026,11,1)
        start=anchor+timedelta(days=((d-anchor).days//14)*14)
        row=await self.one(conn,'SELECT * FROM contest_rotations WHERE start_day=?',(start.isoformat(),))
        if row:order=json.loads(row['order_json'])
        else:
            order=list(range(14));random.SystemRandom().shuffle(order)
            await conn.execute('INSERT INTO contest_rotations VALUES (?,?)',(start.isoformat(),json.dumps(order)))
        return order[(d-start).days],start.isoformat()

    async def start(self,uid,username,slot,channel):
        day=local_date_key()
        async with self.tx() as c:
            clan=await self.clan(user_id=uid,conn=c)
            if not clan:raise ValueError('Join a clan before entering Clan Contest.')
            if await self.one(c,"SELECT 1 FROM contest_runs WHERE owner_id=? AND status='running'",(uid,)):raise ValueError('Your contest attempt is still running. Use ;contestresume to recover it.')
            players=await self._team_snapshot(c,uid,slot,6)
            index,_=await self.rotation(day,c);sid='contest-'+uuid.uuid4().hex[:12]
            state=dict(players=players,index=index,username=username,channel=channel)
            await c.execute("INSERT INTO contest_runs VALUES (?,?,?,?,'running',?)",(sid,uid,day,clan['id'],json.dumps(state)))
            return await self.run(sid,c)

    async def run(self,sid,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.run(sid,c)
        row=await self.one(conn,'SELECT * FROM contest_runs WHERE id=?',(sid,))
        if row:row['state']=json.loads(row.pop('state_json'))
        return row

    async def finish(self,sid,score,result):
        if score<0:raise ValueError('Contest scores cannot be negative.')
        async with self.tx() as c:
            row=await self.run(sid,c)
            if not row:raise ValueError('Contest attempt not found.')
            if row['status']=='complete':return row
            week=week_key_for(datetime.fromisoformat(row['day']))
            # A late result cannot alter an already settled week's standings.
            settled=await self.one(c,'SELECT 1 FROM contest_weeks WHERE week=?',(week,))
            old=await self.one(c,'SELECT * FROM contest_scores WHERE owner_id=? AND day=?',(row['owner_id'],row['day']))
            participation=0
            if not settled:
                if old:await c.execute('UPDATE contest_scores SET score=MAX(score,?) WHERE owner_id=? AND day=?',(score,row['owner_id'],row['day']))
                else:
                    await c.execute('INSERT INTO contest_scores VALUES (?,?,?,?,?)',(row['owner_id'],row['day'],week,row['clan_id'],score))
                    await self._credit(c,row['owner_id'],200);participation=200
            row['state'].update(score=score,result=result,participation=participation,week_closed=bool(settled))
            row['status']='complete';await c.execute("UPDATE contest_runs SET status='complete',state_json=? WHERE id=?",(json.dumps(row['state']),sid))
            return row

    async def leaderboard(self,week=None,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.leaderboard(week,c)
        from .economy import week_key
        week=week or week_key()
        clans=await self.rows(conn,'SELECT clan_id,SUM(score) AS score FROM contest_scores WHERE week=? GROUP BY clan_id ORDER BY score DESC,clan_id',(week,))
        players=await self.rows(conn,'SELECT owner_id,SUM(score) AS score FROM contest_scores WHERE week=? GROUP BY owner_id ORDER BY score DESC,owner_id',(week,))
        return clans,players

    async def settle(self):
        from .economy import week_key
        current=week_key();done=[]
        async with self.tx() as c:
            weeks=await self.rows(c,'SELECT DISTINCT week FROM contest_scores WHERE week<? AND week NOT IN (SELECT week FROM contest_weeks)',(current,))
            for entry in weeks:
                week=entry['week'];clans,players=await self.leaderboard(week,c)
                result=dict(clans=clans[:3],players=players[:3],tie_rule='Equal scores use clan ID or player ID ascending.')
                for rank,clan in enumerate(clans[:3]):
                    for member in await self.members(clan['clan_id'],c):
                        uid=member['user_id'];amount=(1000,750,500)[rank]
                        await c.execute("INSERT INTO contest_prizes VALUES (?,?,'clan',?)",(week,uid,amount));await self._credit(c,uid,amount)
                for rank,player in enumerate(players[:3]):
                    uid=player['owner_id'];amount=(1000,750,500)[rank]
                    await c.execute("INSERT INTO contest_prizes VALUES (?,?,'player',?)",(week,uid,amount))
                    await c.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(amount,uid))
                await c.execute('INSERT INTO contest_weeks VALUES (?,?)',(week,json.dumps(result)));done.append(week)
        return done
