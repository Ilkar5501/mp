"""Biweekly Elo, persistent PvP teams, reserved opponents and once-only rewards."""
import json
import secrets
import uuid
import time
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
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,match_id TEXT NOT NULL UNIQUE,a_id INTEGER NOT NULL,b_id INTEGER NOT NULL,
 day TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS pvp_history_a ON pvp_history(a_id,sequence);
CREATE INDEX IF NOT EXISTS pvp_history_b ON pvp_history(b_id,sequence);
CREATE TABLE IF NOT EXISTS pvp_targeted_daily (
 day TEXT NOT NULL,owner_id INTEGER NOT NULL,match_id TEXT NOT NULL,PRIMARY KEY(day,owner_id));
CREATE TABLE IF NOT EXISTS pvp_pair_results (
 season TEXT NOT NULL,low_id INTEGER NOT NULL,high_id INTEGER NOT NULL,winner_id INTEGER NOT NULL,day TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(season,low_id,high_id));
CREATE INDEX IF NOT EXISTS pvp_pair_results_winner ON pvp_pair_results(season,winner_id);
CREATE TABLE IF NOT EXISTS pvp_opponent_queue (
 board TEXT NOT NULL,day TEXT NOT NULL,owner_id INTEGER NOT NULL,opponent_id INTEGER NOT NULL,
 queue_key TEXT NOT NULL,faces INTEGER NOT NULL DEFAULT 0,served INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(board,day,owner_id,opponent_id));
CREATE TABLE IF NOT EXISTS pvp_monthly_awards (
 month TEXT NOT NULL,owner_id INTEGER NOT NULL,rank INTEGER NOT NULL,rating INTEGER NOT NULL,
 item_ids_json TEXT NOT NULL,PRIMARY KEY(month,owner_id));
CREATE TABLE IF NOT EXISTS pvp_closed_months (month TEXT PRIMARY KEY,closed_at TEXT NOT NULL);
'''


def calendar(at=None):
    at=at or datetime.now(TIMEZONE)
    if at.tzinfo is None:raise ValueError('PvP dates need a timezone.')
    local=at.astimezone(TIMEZONE)
    from .pvp_cycles import cycle_for
    day=local.date().isoformat()
    return cycle_for(day)[0],day


def elo_delta(a,b,score):
    expected=1/(1+10**(max(-4000,min(4000,b-a))/400))
    return round(ELO_K*(score-expected))


async def migrate_pair_results(c):
    for table in ('pvp_pair_results','pvp_history'):
        columns=await (await c.execute('PRAGMA table_info('+table+')')).fetchall()
        if 'day' not in {r['name'] for r in columns}:
            await c.execute("ALTER TABLE "+table+" ADD COLUMN day TEXT NOT NULL DEFAULT ''")
    queue_columns=await (await c.execute('PRAGMA table_info(pvp_opponent_queue)')).fetchall()
    if 'served' not in {r['name'] for r in queue_columns}:
        await c.execute('ALTER TABLE pvp_opponent_queue ADD COLUMN served INTEGER NOT NULL DEFAULT 0')
    await c.commit()
    marker='12.1.13-pvp-rematches'
    if await (await c.execute('SELECT 1 FROM economy_migrations WHERE version=?',(marker,))).fetchone():return
    rows=await (await c.execute("SELECT * FROM pvp_runs WHERE status='done' AND rated=1 AND result_json IS NOT NULL ORDER BY created_at,id")).fetchall()
    for row in rows:
        side=json.loads(row['result_json']).get('winner_side')
        if side not in ('A','B'):continue
        a,b=row['a_id'],row['b_id']
        day=json.loads(row['state_json']).get('day','')
        await c.execute('INSERT INTO pvp_pair_results VALUES (?,?,?,?,?) ON CONFLICT(season,low_id,high_id) DO UPDATE SET winner_id=excluded.winner_id,day=excluded.day WHERE excluded.day>=pvp_pair_results.day',(row['month'],min(a,b),max(a,b),a if side=='A' else b,day))
    await c.execute('INSERT INTO economy_migrations VALUES (?)',(marker,));await c.commit()


async def auto_team_tx(conn,uid,team):
    """Only ;teamuse calls this: lock prevents replacing that format's lineup."""
    entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
    await conn.execute('INSERT INTO pvp_profiles(owner_id,default_format) VALUES (?,?) ON CONFLICT(owner_id) DO UPDATE SET default_format=excluded.default_format',(uid,team.team_format))
    await conn.execute('''INSERT INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)
        ON CONFLICT(owner_id,format) DO UPDATE SET name=excluded.name,entries_json=excluded.entries_json,
        revision=revision+1 WHERE pvp_teams.locked=0''',(uid,team.team_format,team.name,json.dumps(entries)))
    row=await (await conn.execute('SELECT locked FROM pvp_teams WHERE owner_id=? AND format=?',(uid,team.team_format))).fetchone()
    if not row['locked']:
        await conn.execute('INSERT INTO pvp_team_sources VALUES (?,?,?) ON CONFLICT(owner_id,format) DO UPDATE SET slot=excluded.slot',(uid,team.team_format,team.slot))


async def replace_pvp_refs(conn,consumed,survivor):
    rows=await (await conn.execute('SELECT owner_id,format,entries_json FROM pvp_teams WHERE instr(entries_json,?)>0',(consumed,))).fetchall()
    for row in rows:
        entries=json.loads(row['entries_json']);present=any(e['id']==survivor for e in entries) if survivor else False
        for entry in entries:
            if entry['id']==consumed:entry['id']=None if present else survivor
        await conn.execute('UPDATE pvp_teams SET entries_json=?,revision=revision+1 WHERE owner_id=? AND format=?',(json.dumps(entries),row['owner_id'],row['format']))


class PvPStore(CommunityStore):
    ladder='pvp'
    week_table='pvp_weeks'

    def window(self,at=None):return calendar(at)

    async def match_specs(self,c,uid,fmt,day):return await self.specs_tx(c,uid,fmt)
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
        registered=await self.rows(c,'SELECT * FROM pvp_teams WHERE owner_id=?',(uid,))
        for existing in registered:
            if existing['locked']:continue
            source=await self.one(c,'SELECT slot FROM pvp_team_sources WHERE owner_id=? AND format=?',(uid,existing['format']))
            raw=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,source['slot'])) if source else None
            if not raw:continue
            team=self.db._saved_team(raw)
            entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
            if team.complete and team.team_format==existing['format'] and json.loads(existing['entries_json'])!=entries:
                await c.execute('UPDATE pvp_teams SET name=?,entries_json=?,revision=revision+1 WHERE owner_id=? AND format=?',(team.name,json.dumps(entries),uid,team.team_format))
                existing.update(name=team.name,entries_json=json.dumps(entries))
        usable=set()
        for existing in registered:
            try:await self.validate_entries(c,uid,existing['format'],json.loads(existing['entries_json']))
            except (ValueError,TypeError):continue
            usable.add(existing['format'])
        saved=await self.rows(c,'SELECT * FROM saved_teams WHERE owner_id=? ORDER BY CASE WHEN slot=? THEN 0 ELSE 1 END,slot',(uid,user['active_team_slot']))
        for existing in registered:
            if await self.one(c,'SELECT 1 FROM pvp_team_sources WHERE owner_id=? AND format=?',(uid,existing['format'])):continue
            options=[self.db._saved_team(raw) for raw in saved if raw['team_format']==existing['format']]
            options=[team for team in options if team.complete]
            if not options:continue
            current=json.loads(existing['entries_json'])
            exact=[team for team in options if [dict(id=iid,lane=lane) for _,iid,lane in team.entries]==current]
            same_name=[team for team in options if team.name==existing['name']]
            source=(exact or same_name or options)[0]
            await c.execute('INSERT INTO pvp_team_sources VALUES (?,?,?)',(uid,existing['format'],source.slot))
        for raw in saved:
            team=self.db._saved_team(raw)
            if not team.complete or team.team_format in usable:continue
            entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
            try:await self.validate_entries(c,uid,team.team_format,entries)
            except ValueError:continue
            # Recover missing/invalid legacy slots even if an invalid lock was set.
            await c.execute('''INSERT INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)
                ON CONFLICT(owner_id,format) DO UPDATE SET name=excluded.name,entries_json=excluded.entries_json,
                locked=0,revision=revision+1''',(uid,team.team_format,team.name,json.dumps(entries)))
            from .loadouts import unlock
            await unlock(c,uid,'pvp',team.team_format)
            await c.execute('INSERT INTO pvp_team_sources VALUES (?,?,?) ON CONFLICT(owner_id,format) DO UPDATE SET slot=excluded.slot',(uid,team.team_format,team.slot))
            usable.add(team.team_format)
        profile=await self.one(c,'SELECT * FROM pvp_profiles WHERE owner_id=?',(uid,))
        from .lineups import identities
        formats={r['format'] for r in registered}-usable
        if not usable:formats.add(3)
        owned=await self.rows(c,'SELECT * FROM cards WHERE owner_id=? ORDER BY merge_level DESC,obtained_at,instance_id',(uid,)) if formats else []
        selected=[];seen=set()
        for card in owned:
            char=CHAR_BY_ID.get(card['character_id'])
            if char and not identities(char)&seen:selected.append(card);seen.update(identities(char))
        if len(selected)<6:
            plain=[];plain_names=set()
            for card in owned:
                char=CHAR_BY_ID.get(card['character_id'])
                if char and not char.is_duo and char.name not in plain_names:plain.append(card);plain_names.add(char.name)
            if len(plain)>len(selected):selected=plain
        for size in formats:
            if len(selected)<size:continue
            entries=[dict(id=card['instance_id'],lane='front' if i<(1 if size==3 else 3) else 'back') for i,card in enumerate(selected[:size])]
            await c.execute('''INSERT INTO pvp_teams(owner_id,format,name,entries_json) VALUES (?,?,?,?)
                ON CONFLICT(owner_id,format) DO UPDATE SET name=excluded.name,entries_json=excluded.entries_json,locked=0,revision=revision+1''',(uid,size,'Recovered team',json.dumps(entries)))
            usable.add(size)
        if usable and profile['default_format'] not in usable:
            await c.execute('UPDATE pvp_profiles SET default_format=? WHERE owner_id=?',(min(usable),uid))
        return await self.one(c,'SELECT * FROM pvp_profiles WHERE owner_id=?',(uid,))

    async def specs_tx(self,c,uid,fmt):
        row=await self.one(c,'SELECT * FROM pvp_teams WHERE owner_id=? AND format=?',(uid,fmt))
        if not row:raise ValueError(f'Choose a {fmt}v{fmt} PvP team with ;pvpset.')
        entries=json.loads(row['entries_json'])
        await self.validate_entries(c,uid,fmt,entries)
        from .loadouts import locked,snapshot,resolve
        if row['locked']:
            if await locked(c,uid,'pvp',fmt) is None:await snapshot(c,uid,'pvp',fmt,entries)
            return await resolve(c,self.db,uid,'pvp',fmt,entries)
        return await self.validate_entries(c,uid,fmt,entries)

    async def validate_entries(self,c,uid,fmt,entries):
        if not isinstance(entries,list) or len(entries)!=fmt or any(not isinstance(e,dict) or not isinstance(e.get('id'),str) or e.get('lane') not in ('front','back') for e in entries):
            raise ValueError('This PvP lineup is incomplete or invalid. Update ;pvpset.')
        specs=[]
        for entry in entries:
            raw=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))
            if not raw:raise ValueError('A PvP card is missing. Update the team with ;pvpset.')
            card=self.db._card(raw);char=CHAR_BY_ID.get(card.character_id)
            if not char:raise ValueError('A PvP card is unavailable. Update ;pvpset.')
            specs.append((card,char,entry['lane']))
        if len(specs)!=fmt or not __import__('hiq.lineups',fromlist=['valid_characters']).valid_characters([char for _,char,_ in specs]) or sum(lane=='front' for _,_,lane in specs)!=(1 if fmt==3 else 3) or any(lane not in ('front','back') for _,_,lane in specs):
            raise ValueError('This PvP lineup is incomplete or invalid. Update ;pvpset.')
        return specs

    async def shielded_tx(self,c,uid,at=None):
        at=time.time() if at is None else at
        return bool(await self.one(c,'SELECT 1 FROM pvp_shields WHERE owner_id=? AND protected_until>?',(uid,at)))

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
            await c.execute('INSERT INTO pvp_team_sources VALUES (?,?,?) ON CONFLICT(owner_id,format) DO UPDATE SET slot=excluded.slot',(uid,team.team_format,slot))
            if existing and existing['locked']:
                from .loadouts import resolve,snapshot,unlock
                source=await resolve(c,self.db,uid,'saved',slot,entries)
                await unlock(c,uid,'pvp',team.team_format)
                await snapshot(c,uid,'pvp',team.team_format,[dict(id=card.instance_id,lane=lane,item_id=card.equipped_item_id) for card,_,lane in source])
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
            from .loadouts import snapshot,unlock
            if locked:
                await self.specs_tx(c,uid,fmt)
                await snapshot(c,uid,'pvp',fmt,json.loads(row['entries_json']))
            else:
                source=await self.one(c,'SELECT slot FROM pvp_team_sources WHERE owner_id=? AND format=?',(uid,fmt))
                raw=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,source['slot'])) if source else None
                if raw:
                    team=self.db._saved_team(raw)
                    if not team.complete or team.team_format!=fmt:raise ValueError('Complete the source team or choose another team before unlocking this PvP lineup.')
                    entries=[dict(id=iid,lane=lane) for _,iid,lane in team.entries]
                    await self.validate_entries(c,uid,fmt,entries)
                    await c.execute('UPDATE pvp_teams SET name=?,entries_json=? WHERE owner_id=? AND format=?',(team.name,json.dumps(entries),uid,fmt))
                await unlock(c,uid,'pvp',fmt)
            await c.execute('UPDATE pvp_teams SET locked=?,revision=revision+1 WHERE owner_id=? AND format=?',(int(locked),uid,fmt))

    async def rating_tx(self,c,uid,month):
        await c.execute('INSERT OR IGNORE INTO pvp_ratings(month,owner_id) VALUES (?,?)',(month,uid))
        return await self.one(c,'SELECT * FROM pvp_ratings WHERE month=? AND owner_id=?',(month,uid))

    async def recent_tx(self,c,uid,day):
        rows=await self.rows(c,'SELECT a_id,b_id FROM pvp_history WHERE day=? AND (a_id=? OR b_id=?) ORDER BY sequence DESC LIMIT ?',(day,uid,uid,REPEAT_WINDOW))
        return {r['b_id'] if r['a_id']==uid else r['a_id'] for r in rows}

    async def start(self,uid,*,target=None,fmt=None,name='',target_name='',channel=0,at=None):
        month,day=self.window(at)
        targeted=target is not None
        async with self.tx() as c:
            await c.execute('DELETE FROM pvp_opponent_queue WHERE board=? AND day<?',(self.ladder,day))
            if await self.shielded_tx(c,uid):raise ValueError('Your PvP Shield is active. You cannot initiate PvP until it expires.')
            if target is not None and await self.shielded_tx(c,target):raise ValueError('That player has an active PvP Shield.')
            old=await self.one(c,"SELECT * FROM pvp_runs WHERE a_id=? AND status='running' ORDER BY created_at LIMIT 1",(uid,))
            if old:
                if old['month'].startswith('ttb:')!=(self.ladder=='pvp_ttb'):raise ValueError('Finish your pending PvP match before switching ladders.')
                return self.unpack(old)
            if await self.one(c,'SELECT 1 FROM pvp_busy WHERE owner_id=?',(uid,)):raise ValueError('Your team is already in a PvP match. Try again after it finishes.')
            await self.settle_tx(c,month,day)
            from .pvp_cycles import week_start
            if await self.one(c,'SELECT 1 FROM '+self.week_table+' WHERE closed=0 AND week<?',(week_start(day).isoformat(),)):
                raise ValueError('A match from the previous week is still resolving. Try again after it finishes.')
            profile=await self.profile_tx(c,uid,name)
            fmt=fmt or profile['default_format']
            if fmt not in (3,6):raise ValueError('Choose 3v3 or 6v6.')
            a=await self.match_specs(c,uid,fmt,day);ar=await self.rating_tx(c,uid,month)
            rated=True;reason='';b=None
            if target is not None:
                if target==uid:raise ValueError('Choose another player.')
                other=await self.profile_tx(c,target,target_name)
                if await self.one(c,'SELECT 1 FROM pvp_busy WHERE owner_id=?',(target,)):raise ValueError('That player is already in a PvP match.')
                try:b=await self.match_specs(c,target,fmt,day)
                except ValueError:
                    raise PvPTeamUnavailable(f'{target_name or "That player"} has no usable {fmt}v{fmt} PvP team. Choose another format or update ;pvpset.') from None
            else:
                candidates=[]
                owners=await self.rows(c,'''SELECT discord_id FROM users WHERE discord_id<>? AND tutorial_started=1
                    AND NOT EXISTS(SELECT 1 FROM pvp_busy WHERE owner_id=discord_id)''',(uid,))
                for owner in owners:
                    oid=owner['discord_id']
                    if await self.shielded_tx(c,oid):continue
                    try:
                        candidate_profile=await self.profile_tx(c,oid)
                        candidate=await self.match_specs(c,oid,fmt,day)
                    except ValueError:continue
                    rating=await self.rating_tx(c,oid,month)
                    candidates.append((abs(rating['rating']-ar['rating']),oid,candidate,candidate_profile))
                if not candidates:raise ValueError(f'No available {fmt}v{fmt} opponent. Both players need usable teams and must be available without an active shield.')
                previous_wins=await self.rows(c,'SELECT low_id,high_id FROM pvp_pair_results WHERE season=? AND day=? AND winner_id=?',(month,day,uid))
                unrated_targets={r['high_id'] if r['low_id']==uid else r['low_id'] for r in previous_wins}
                eligible=[row for row in candidates if row[1] not in unrated_targets]
                if eligible:
                    for row in eligible:
                        await c.execute('INSERT OR IGNORE INTO pvp_opponent_queue(board,day,owner_id,opponent_id,queue_key,faces) VALUES (?,?,?,?,?,0)',(self.ladder,day,uid,row[1],secrets.token_hex(12)))
                    queue=await self.rows(c,'SELECT opponent_id,queue_key,faces,served FROM pvp_opponent_queue WHERE board=? AND day=? AND owner_id=?',(self.ladder,day,uid))
                    order={r['opponent_id']:(r['faces']>0,r['faces'],r['queue_key']) for r in queue}
                    unserved={r['opponent_id'] for r in queue if not r['served']}
                    front=[row for row in eligible if row[1] in unserved]
                    ineligible=[row for row in candidates if row[1] in unrated_targets]
                    if front:chosen=min(front,key=lambda row:order[row[1]])
                    elif ineligible:chosen=secrets.choice(ineligible)
                    else:chosen=min(eligible,key=lambda row:order[row[1]])
                    _,target,b,other=chosen
                else:
                    _,target,b,other=secrets.choice(candidates)
                await c.execute('UPDATE pvp_opponent_queue SET served=1 WHERE board=? AND day=? AND owner_id=? AND opponent_id=?',(self.ladder,day,uid,target))
            pair=await self.one(c,'SELECT winner_id FROM pvp_pair_results WHERE season=? AND day=? AND low_id=? AND high_id=?',(month,day,min(uid,target),max(uid,target)))
            if pair and pair['winner_id']==uid:
                rated=False
                reason='Unrated rematch: you won the last ranked match against this opponent today. Elo is unchanged. Your opponent can initiate a ranked rematch immediately. Eligibility resets at midnight in the game timezone.'
            await self.rating_tx(c,target,month)
            sid='pvp-'+uuid.uuid4().hex[:16]
            state=dict(a=snapshot_specs(a),b=snapshot_specs(b),seed=secrets.randbits(62),day=day,
                       name_a=name or profile['display_name'] or f'Player {uid}',
                       name_b=target_name or other['display_name'] or f'Player {target}',channel=channel,reason=reason)
            await c.execute("INSERT INTO pvp_runs VALUES (?,?,?,?,?,?,?,'running',?,NULL,NULL,?)",(sid,month,fmt,uid,target,int(rated),int(targeted),json.dumps(state),now()))
            for owner in (uid,target):await c.execute('INSERT INTO pvp_busy VALUES (?,?)',(owner,sid))
            return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def finish(self,sid,result,*,at=None):
        current,day=self.window(at)
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
                await c.execute('INSERT INTO pvp_history(match_id,a_id,b_id,day) VALUES (?,?,?,?)',(sid,a,b,row['state']['day']))
                from .retention import PVP_HISTORY_TRIM_SQL
                await c.execute(PVP_HISTORY_TRIM_SQL)
                if winner in ('A','B'):
                    previous=await self.one(c,'SELECT winner_id FROM pvp_pair_results WHERE season=? AND day=? AND low_id=? AND high_id=?',(month,row['state']['day'],min(a,b),max(a,b)))
                    winner_id=a if winner=='A' else b;loser_id=b if winner=='A' else a
                    if previous and previous['winner_id']==loser_id:
                        await c.execute('UPDATE pvp_opponent_queue SET served=0 WHERE board=? AND day=? AND owner_id=? AND opponent_id=?',(self.ladder,row['state']['day'],loser_id,winner_id))
                    await c.execute('INSERT INTO pvp_pair_results VALUES (?,?,?,?,?) ON CONFLICT(season,low_id,high_id) DO UPDATE SET winner_id=excluded.winner_id,day=excluded.day WHERE excluded.day>=pvp_pair_results.day',(month,min(a,b),max(a,b),a if winner=='A' else b,row['state']['day']))
            reward=None;winner_id=a if winner=='A' else b if winner=='B' else None
            if winner_id:
                from .rewards import award_in
                reward=await award_in(c,winner_id,self.ladder,opponent=b if winner=='A' else a,day=row['state']['day'])
            consolation=None
            if winner_id:
                from .youth_rewards import loss_reward
                loser=b if winner=='A' else a
                if loss_reward(row['state']['b' if winner=='A' else 'a'],key=f'{sid}:loss'):
                    consolation=await award_in(c,loser,self.ladder,opponent=winner_id,day=row['state']['day'])
            settlement=dict(a_before=ar['rating'],b_before=br['rating'],a_after=ar['rating']+delta,b_after=br['rating']-delta,delta=delta,reward=reward,winner_id=winner_id)
            settlement['loss_reward']=consolation
            for owner,opponent in ((a,b),(b,a)):
                await c.execute('''INSERT INTO pvp_opponent_queue(board,day,owner_id,opponent_id,queue_key,faces) VALUES (?,?,?,?,?,1)
                    ON CONFLICT(board,day,owner_id,opponent_id) DO UPDATE SET faces=faces+1''',
                    (self.ladder,row['state']['day'],owner,opponent,secrets.token_hex(12)))
            await c.execute("UPDATE pvp_runs SET status='done',result_json=?,settlement_json=? WHERE id=?",(json.dumps(result),json.dumps(settlement),sid))
            await c.execute('DELETE FROM pvp_busy WHERE match_id=?',(sid,))
            await self.settle_tx(c,current,day)
            return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def settle_tx(self,c,current,day=None):
        from .pvp_cycles import settle
        return await settle(self,c,day or calendar()[1])

    async def settle(self,at=None):
        month,day=self.window(at)
        async with self.tx() as c:await self.settle_tx(c,month,day)

    async def board(self,uid,at=None):
        month,day=self.window(at)
        async with self.tx() as c:
            await self.settle_tx(c,month,day)
            rating=await self.rating_tx(c,uid,month)
            rows=await self.rows(c,'''SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0
                ORDER BY rating DESC,wins DESC,losses ASC,owner_id''',(month,))
            return month,rows,rating

    async def run(self,sid):
        async with self.db.connect() as c:return self.unpack(await self.one(c,'SELECT * FROM pvp_runs WHERE id=?',(sid,)))

    async def pending(self):
        async with self.db.connect() as c:return await self.rows(c,"SELECT id FROM pvp_runs WHERE status='running' ORDER BY created_at")
