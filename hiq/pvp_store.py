"""Monthly Elo, persistent PvP teams, reserved opponents and once-only rewards."""
import json
import secrets
import uuid
from datetime import datetime

from .community_store import CommunityStore,now
from .config import TIMEZONE
from .data import CHAR_BY_ID
from .tournament import snapshot_specs

BASE_ELO=1000
ELO_K=32
REPEAT_WINDOW=5

class PvPTeamUnavailable(ValueError):
    pass

SCHEMA='''
CREATE TABLE IF NOT EXISTS pvp_profiles (
 owner_id INTEGER PRIMARY KEY,default_format INTEGER NOT NULL DEFAULT 3 CHECK(default_format IN (3,6)),
 display_name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pvp_teams (
 owner_id INTEGER NOT NULL,format INTEGER NOT NULL CHECK(format IN (3,6)),name TEXT NOT NULL,
 entries_json TEXT NOT NULL,locked INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,format));
CREATE TABLE IF NOT EXISTS pvp_ratings (
 month TEXT NOT NULL,owner_id INTEGER NOT NULL,rating INTEGER NOT NULL DEFAULT 1000,
 wins INTEGER NOT NULL DEFAULT 0,losses INTEGER NOT NULL DEFAULT 0,draws INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(month,owner_id));
CREATE TABLE IF NOT EXISTS pvp_runs (
 id TEXT PRIMARY KEY,month TEXT NOT NULL,format INTEGER NOT NULL,a_id INTEGER NOT NULL,b_id INTEGER NOT NULL,
 rated INTEGER NOT NULL,targeted INTEGER NOT NULL,status TEXT NOT NULL,state_json TEXT NOT NULL,
 result_json TEXT,settlement_json TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_busy (owner_id INTEGER PRIMARY KEY,match_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_history (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,match_id TEXT NOT NULL UNIQUE,a_id INTEGER NOT NULL,b_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS pvp_history_a ON pvp_history(a_id,sequence);
CREATE INDEX IF NOT EXISTS pvp_history_b ON pvp_history(b_id,sequence);
CREATE TABLE IF NOT EXISTS pvp_targeted_daily (
 day TEXT NOT NULL,owner_id INTEGER NOT NULL,match_id TEXT NOT NULL,PRIMARY KEY(day,owner_id));
CREATE TABLE IF NOT EXISTS pvp_monthly_awards (
 month TEXT NOT NULL,owner_id INTEGER NOT NULL,rank INTEGER NOT NULL,rating INTEGER NOT NULL,
 item_ids_json TEXT NOT NULL,PRIMARY KEY(month,owner_id));
CREATE TABLE IF NOT EXISTS pvp_closed_months (month TEXT PRIMARY KEY,closed_at TEXT NOT NULL);
'''


def calendar(at=None):
    at=at or datetime.now(TIMEZONE)
    if at.tzinfo is None:raise ValueError('PvP dates need a timezone.')
    local=at.astimezone(TIMEZONE)
    return local.strftime('%Y-%m'),local.date().isoformat()


def elo_delta(a,b,score):
    expected=1/(1+10**(max(-4000,min(4000,b-a))/400))
    return round(ELO_K*(score-expected))


async def auto_team_tx(conn,uid,team):
    """Only ;teamuse calls this: lock prevents replacing that format's lineup."""
    entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
    await conn.execute('INSERT INTO pvp_profiles(owner_id,default_format) VALUES (?,?) ON CONFLICT(owner_id) DO UPDATE SET default_format=excluded.default_format',(uid,team.team_format))
    await conn.execute('''INSERT INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)
        ON CONFLICT(owner_id,format) DO UPDATE SET name=excluded.name,entries_json=excluded.entries_json,
        revision=revision+1 WHERE pvp_teams.locked=0''',(uid,team.team_format,team.name,json.dumps(entries)))


async def replace_pvp_refs(conn,consumed,survivor):
    rows=await (await conn.execute('SELECT owner_id,format,entries_json FROM pvp_teams WHERE instr(entries_json,?)>0',(consumed,))).fetchall()
    for row in rows:
        entries=json.loads(row['entries_json']);present=any(e['id']==survivor for e in entries) if survivor else False
        for entry in entries:
            if entry['id']==consumed:entry['id']=None if present else survivor
        await conn.execute('UPDATE pvp_teams SET entries_json=?,revision=revision+1 WHERE owner_id=? AND format=?',(json.dumps(entries),row['owner_id'],row['format']))


class PvPStore(CommunityStore):
    @staticmethod
    def unpack(row):
        if row:
            for key in ('state','result','settlement'):
                value=row.get(key+'_json')
                if value:row[key]=json.loads(value)
        return row

    async def profile_tx(self,c,uid,display_name=''):
        user=await self.one(c,'SELECT * FROM users WHERE discord_id=?',(uid,))
        if not user:raise ValueError('This player has no PvP team yet.')
        active=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,user['active_team_slot']))
        fmt=active['team_format'] if active else 3
        await c.execute('INSERT OR IGNORE INTO pvp_profiles(owner_id,default_format) VALUES (?,?)',(uid,fmt))
        if display_name:await c.execute('UPDATE pvp_profiles SET display_name=? WHERE owner_id=?',(display_name[:100],uid))
        if active:
            team=self.db._saved_team(active)
            if team.complete:
                # Bootstrap legacy active teams once, without overwriting either PvP slot.
                entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
                await c.execute('INSERT OR IGNORE INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)',(uid,fmt,team.name,json.dumps(entries)))
        return await self.one(c,'SELECT * FROM pvp_profiles WHERE owner_id=?',(uid,))

    async def specs_tx(self,c,uid,fmt):
        row=await self.one(c,'SELECT * FROM pvp_teams WHERE owner_id=? AND format=?',(uid,fmt))
        if not row:raise ValueError(f'Choose a {fmt}v{fmt} PvP team with ;pvpset.')
        entries=json.loads(row['entries_json']);specs=[]
        for entry in entries:
            raw=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))
            if not raw:raise ValueError('A PvP card is missing. Update the team with ;pvpset.')
            card=self.db._card(raw);char=CHAR_BY_ID.get(card.character_id)
            if not char:raise ValueError('A PvP card is unavailable. Update ;pvpset.')
            specs.append((card,char,entry['lane']))
        if len(specs)!=fmt or len({char.name for _,char,_ in specs})!=fmt or sum(lane=='front' for _,_,lane in specs)!=(1 if fmt==3 else 3) or any(lane not in ('front','back') for _,_,lane in specs):
            raise ValueError('This PvP lineup is incomplete or invalid. Update ;pvpset.')
        return specs

    async def preferences(self,uid,name=''):
        async with self.tx() as c:
            profile=await self.profile_tx(c,uid,name)
            teams=await self.rows(c,'SELECT * FROM pvp_teams WHERE owner_id=? ORDER BY format',(uid,))
            for row in teams:
                row['entries']=json.loads(row['entries_json'])
                try:await self.specs_tx(c,uid,row['format']);row['valid']=True
                except ValueError:row['valid']=False
            return dict(profile=profile,teams={r['format']:r for r in teams})

    async def set_team(self,uid,slot,*,revision=None,expected_format=None):
        async with self.tx() as c:
            await self.profile_tx(c,uid)
            raw=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,slot))
            if not raw:raise ValueError('Choose one of your saved team slots.')
            team=self.db._saved_team(raw)
            if expected_format is not None and team.team_format!=expected_format:raise ValueError('That team changed format. Reopen ;pvpset.')
            if not team.complete:raise ValueError('Complete that team with ;teamedit first.')
            existing=await self.one(c,'SELECT * FROM pvp_teams WHERE owner_id=? AND format=?',(uid,team.team_format))
            if revision is not None and (existing['revision'] if existing else -1)!=revision:raise ValueError('Your PvP team changed. Reopen ;pvpset.')
            entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
            await c.execute('''INSERT INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)
                ON CONFLICT(owner_id,format) DO UPDATE SET name=excluded.name,entries_json=excluded.entries_json,revision=revision+1''',(uid,team.team_format,team.name,json.dumps(entries)))
            await self.specs_tx(c,uid,team.team_format)
            return team.team_format

    async def set_default(self,uid,fmt):
        if fmt not in (3,6):raise ValueError('Choose 3v3 or 6v6.')
        async with self.tx() as c:
            await self.profile_tx(c,uid);await self.specs_tx(c,uid,fmt)
            await c.execute('UPDATE pvp_profiles SET default_format=? WHERE owner_id=?',(fmt,uid))

    async def set_lock(self,uid,fmt,locked,revision=None):
        if fmt not in (3,6):raise ValueError('Choose 3v3 or 6v6.')
        async with self.tx() as c:
            row=await self.one(c,'SELECT * FROM pvp_teams WHERE owner_id=? AND format=?',(uid,fmt))
            if not row:raise ValueError('Choose that PvP team first.')
            if revision is not None and row['revision']!=revision:raise ValueError('Your PvP team changed. Reopen ;pvpset.')
            await c.execute('UPDATE pvp_teams SET locked=?,revision=revision+1 WHERE owner_id=? AND format=?',(int(locked),uid,fmt))

    async def rating_tx(self,c,uid,month):
        await c.execute('INSERT OR IGNORE INTO pvp_ratings(month,owner_id) VALUES (?,?)',(month,uid))
        return await self.one(c,'SELECT * FROM pvp_ratings WHERE month=? AND owner_id=?',(month,uid))

    async def recent_tx(self,c,uid):
        rows=await self.rows(c,'SELECT a_id,b_id FROM pvp_history WHERE a_id=? OR b_id=? ORDER BY sequence DESC LIMIT ?',(uid,uid,REPEAT_WINDOW))
        return {r['b_id'] if r['a_id']==uid else r['a_id'] for r in rows}

    async def start(self,uid,*,target=None,fmt=None,name='',target_name='',channel=0,at=None):
        month,day=calendar(at)
        targeted=target is not None
        async with self.tx() as c:
            old=await self.one(c,"SELECT * FROM pvp_runs WHERE a_id=? AND status='running' ORDER BY created_at LIMIT 1",(uid,))
            if old:return self.unpack(old)
            if await self.one(c,'SELECT 1 FROM pvp_busy WHERE owner_id=?',(uid,)):raise ValueError('Your team is already in a PvP match. Try again after it finishes.')
            await self.settle_tx(c,month)
            profile=await self.profile_tx(c,uid,name)
            fmt=fmt or profile['default_format']
            if fmt not in (3,6):raise ValueError('Choose 3v3 or 6v6.')
            a=await self.specs_tx(c,uid,fmt);ar=await self.rating_tx(c,uid,month)
            rated=True;reason='';b=None
            if target is not None:
                if target==uid:raise ValueError('Choose another player.')
                other=await self.profile_tx(c,target,target_name)
                if await self.one(c,'SELECT 1 FROM pvp_busy WHERE owner_id=?',(target,)):raise ValueError('That player is already in a PvP match.')
                try:b=await self.specs_tx(c,target,fmt)
                except ValueError:
                    raise PvPTeamUnavailable(f'{target_name or "That player"} has no usable {fmt}v{fmt} PvP team. Choose another format or update ;pvpset.') from None
                used=await self.one(c,'SELECT 1 FROM pvp_targeted_daily WHERE day=? AND owner_id IN (?,?)',(day,uid,target))
                rated=not bool(used)
                if not rated:reason='Practice: one player already used their rated targeted match today.'
            else:
                recent=await self.recent_tx(c,uid);candidates=[]
                owners=await self.rows(c,'''SELECT discord_id FROM users WHERE discord_id<>? AND tutorial_started=1
                    AND NOT EXISTS(SELECT 1 FROM pvp_busy WHERE owner_id=discord_id)''',(uid,))
                for owner in owners:
                    oid=owner['discord_id']
                    if oid in recent:continue
                    try:
                        candidate_profile=await self.profile_tx(c,oid)
                        candidate=await self.specs_tx(c,oid,fmt)
                    except ValueError:continue
                    if uid in await self.recent_tx(c,oid):continue
                    rating=await self.rating_tx(c,oid,month)
                    candidates.append((abs(rating['rating']-ar['rating']),oid,candidate,candidate_profile))
                if not candidates:raise ValueError(f'No eligible {fmt}v{fmt} opponent. Recent opponents need five other rated matches, and both teams must be available.')
                closest=min(row[0] for row in candidates)
                _,target,b,other=secrets.choice([row for row in candidates if row[0]==closest])
            await self.rating_tx(c,target,month)
            sid='pvp-'+uuid.uuid4().hex[:16]
            state=dict(a=snapshot_specs(a),b=snapshot_specs(b),seed=secrets.randbits(62),day=day,
                       name_a=name or profile['display_name'] or f'Player {uid}',
                       name_b=target_name or other['display_name'] or f'Player {target}',channel=channel,reason=reason)
            await c.execute("INSERT INTO pvp_runs VALUES (?,?,?,?,?,?,?,'running',?,NULL,NULL,?)",(sid,month,fmt,uid,target,int(rated),int(targeted),json.dumps(state),now()))
            if targeted and rated:
                for owner in (uid,target):await c.execute('INSERT INTO pvp_targeted_daily VALUES (?,?,?)',(day,owner,sid))
            for owner in (uid,target):await c.execute('INSERT INTO pvp_busy VALUES (?,?)',(owner,sid))
            return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def finish(self,sid,result,*,at=None):
        current,_=calendar(at)
        if result.get('winner_side') not in ('A','B','draw'):raise ValueError('Invalid PvP result.')
        async with self.tx() as c:
            row=self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))
            if not row:raise ValueError('PvP match not found.')
            if row['status']=='done':return row
            a,b=row['a_id'],row['b_id'];month=row['month'];delta=0
            ar=await self.rating_tx(c,a,month);br=await self.rating_tx(c,b,month)
            winner=result['winner_side']
            if row['rated']:
                score={'A':1,'B':0,'draw':.5}[winner];delta=elo_delta(ar['rating'],br['rating'],score)
                for owner,change,win,loss in ((a,delta,winner=='A',winner=='B'),(b,-delta,winner=='B',winner=='A')):
                    await c.execute('UPDATE pvp_ratings SET rating=rating+?,wins=wins+?,losses=losses+?,draws=draws+? WHERE month=? AND owner_id=?',(change,int(win),int(loss),int(winner=='draw'),month,owner))
                await c.execute('INSERT INTO pvp_history(match_id,a_id,b_id) VALUES (?,?,?)',(sid,a,b))
                from .retention import PVP_HISTORY_TRIM_SQL
                await c.execute(PVP_HISTORY_TRIM_SQL)
            reward=None;winner_id=a if winner=='A' else b if winner=='B' else None
            if winner_id:
                from .rewards import award_in
                reward=await award_in(c,winner_id,'pvp',opponent=b if winner=='A' else a,day=row['state']['day'])
            settlement=dict(a_before=ar['rating'],b_before=br['rating'],a_after=ar['rating']+delta,b_after=br['rating']-delta,delta=delta,reward=reward,winner_id=winner_id)
            await c.execute("UPDATE pvp_runs SET status='done',result_json=?,settlement_json=? WHERE id=?",(json.dumps(result),json.dumps(settlement),sid))
            await c.execute('DELETE FROM pvp_busy WHERE match_id=?',(sid,))
            await self.settle_tx(c,current)
            return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def settle_tx(self,c,current):
        months=await self.rows(c,'''SELECT DISTINCT month FROM pvp_ratings WHERE month<?
            AND month NOT IN (SELECT month FROM pvp_closed_months) ORDER BY month''',(current,))
        for row in months:
            month=row['month']
            if await self.one(c,"SELECT 1 FROM pvp_runs WHERE month=? AND status='running' AND rated=1",(month,)):continue
            winners=await self.rows(c,'''SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0
                ORDER BY rating DESC,wins DESC,losses ASC,owner_id LIMIT 3''',(month,))
            for rank,winner in enumerate(winners,1):
                ids=[await self._issue_item(c,winner['owner_id'],'starpack',stars=stars) for stars in (5,4,3,2,1)]
                await c.execute('INSERT INTO pvp_monthly_awards VALUES (?,?,?,?,?)',(month,winner['owner_id'],rank,winner['rating'],json.dumps(ids)))
            await c.execute('INSERT INTO pvp_closed_months VALUES (?,?)',(month,now()))

    async def settle(self,at=None):
        month,_=calendar(at)
        async with self.tx() as c:await self.settle_tx(c,month)

    async def board(self,uid,at=None):
        month,_=calendar(at)
        async with self.tx() as c:
            await self.settle_tx(c,month)
            rating=await self.rating_tx(c,uid,month)
            rows=await self.rows(c,'''SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0
                ORDER BY rating DESC,wins DESC,losses ASC,owner_id''',(month,))
            return month,rows,rating

    async def run(self,sid):
        async with self.db.connect() as c:return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def pending(self):
        async with self.db.connect() as c:return await self.rows(c,"SELECT id FROM pvp_runs WHERE status='running' ORDER BY created_at")
