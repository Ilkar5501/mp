"""Saved AF1 teams, repeat-safe event rewards and signature crafting."""
import json,secrets,uuid
from datetime import datetime,timezone
from .community_store import CommunityStore,now
from .data import CHAR_BY_ID
from .event_modes import EVENT_TEAMS,cpu_specs,validate_af1
from .items import SIGNATURE_ITEMS
from .shard_balances import balance,credit
from .tournament import snapshot_specs,restore_specs

SCHEMA='''
CREATE TABLE IF NOT EXISTS af1_teams (
 owner_id INTEGER NOT NULL,slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
 name TEXT NOT NULL,entries_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,slot));
CREATE TABLE IF NOT EXISTS event_wallets (
 owner_id INTEGER PRIMARY KEY,tt_tokens INTEGER NOT NULL DEFAULT 0 CHECK(tt_tokens>=0),
 af1_marks INTEGER NOT NULL DEFAULT 0 CHECK(af1_marks>=0),af1_slot INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS event_signature_shards (
 owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
 PRIMARY KEY(owner_id,item_key));
CREATE TABLE IF NOT EXISTS event_runs (
 id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,
 state_json TEXT NOT NULL,result_json TEXT,earned INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_event_run ON event_runs(owner_id) WHERE status='running';
'''

class EventStore(CommunityStore):
    @staticmethod
    def unpack(row):
        if row and 'entries_json' in row:row['entries']=json.loads(row['entries_json'])
        if row and 'state_json' in row:row['state']=json.loads(row['state_json'])
        return row

    async def wallet(self,uid):
        async with self.tx() as c:
            await c.execute('INSERT OR IGNORE INTO event_wallets(owner_id) VALUES (?)',(uid,))
            row=await self.one(c,'SELECT * FROM event_wallets WHERE owner_id=?',(uid,))
            row['shards']={i['key']:await balance(c,uid,'item_shard',i['key']) for i in SIGNATURE_ITEMS}
            row['characters']={i['character_id']:await balance(c,uid,'character_shard',i['character_id']) for i in SIGNATURE_ITEMS}
            return row

    async def team(self,uid,slot=1,name=''):
        if not 1<=slot<=5:raise ValueError('Choose AF1 slot 1–5.')
        async with self.tx() as c:
            await c.execute('INSERT OR IGNORE INTO af1_teams VALUES (?,?,?,?,0)',(uid,slot,f'AF1 Team {slot}',json.dumps([None]*7)))
            if name:
                await c.execute('UPDATE af1_teams SET name=?,revision=revision+1 WHERE owner_id=? AND slot=?',(name[:60],uid,slot))
            return self.unpack(await self.one(c,'SELECT * FROM af1_teams WHERE owner_id=? AND slot=?',(uid,slot)))

    async def set_entry(self,uid,slot,index,iid,lane,revision):
        if not 0<=index<7 or lane not in ('front','back'):raise ValueError('Choose a slot and row.')
        async with self.tx() as c:
            row=self.unpack(await self.one(c,'SELECT * FROM af1_teams WHERE owner_id=? AND slot=?',(uid,slot)))
            if not row or row['revision']!=revision:raise ValueError('This team changed. Reopen ;af1team.')
            entries=row['entries'];entries[index]={'id':iid,'lane':lane} if iid else None
            chars=[]
            for entry in filter(None,entries):
                card=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))
                if not card:raise ValueError('A card is no longer yours. Remove it and choose another.')
                char=CHAR_BY_ID[card['character_id']]
                if char.name=='Fukunaga':raise ValueError('Fukunaga cannot enter AF1.')
                chars.append(char.id)
            if len(chars)!=len(set(chars)):raise ValueError('Use different card variants.')
            await c.execute('UPDATE af1_teams SET entries_json=?,revision=revision+1 WHERE owner_id=? AND slot=?',(json.dumps(entries),uid,slot))
            return self.unpack(await self.one(c,'SELECT * FROM af1_teams WHERE owner_id=? AND slot=?',(uid,slot)))

    async def _specs(self,c,uid,entries,*,allow_empty_supports=False):
        if not entries or entries[0] is None or (not allow_empty_supports and any(e is None for e in entries)):raise ValueError('Choose your main first: ;af1team.')
        specs=[]
        for e in entries:
            if e is None:continue
            raw=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,e['id']))
            if not raw:raise ValueError('A saved card is no longer yours. Edit the team.')
            card=self.db._card(raw);specs.append((card,CHAR_BY_ID[card.character_id],e['lane']))
        return specs

    async def use(self,uid,slot):
        async with self.tx() as c:
            row=self.unpack(await self.one(c,'SELECT * FROM af1_teams WHERE owner_id=? AND slot=?',(uid,slot)))
            if not row:raise ValueError('Create this team with ;af1team first.')
            validate_af1(await self._specs(c,uid,row['entries'],allow_empty_supports=True),complete=True)
            await c.execute('INSERT OR IGNORE INTO event_wallets(owner_id) VALUES (?)',(uid,))
            await c.execute('UPDATE event_wallets SET af1_slot=? WHERE owner_id=?',(slot,uid))
            return row

    async def start(self,uid,mode,index):
        if mode not in ('tiny_titans','all_for_one') or not 0<=index<len(EVENT_TEAMS[mode]):raise ValueError('Choose a CPU team from the menu.')
        async with self.tx() as c:
            old=self.unpack(await self.one(c,"SELECT * FROM event_runs WHERE owner_id=? AND status='running'",(uid,)))
            if old:
                if old['mode']!=mode:raise ValueError('Resume your other event with ;tt or ;af1, or use ;eventreset.')
                return old
            recent=await self.one(c,'SELECT created_at FROM event_runs WHERE owner_id=? ORDER BY created_at DESC LIMIT 1',(uid,))
            if recent:
                elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(recent['created_at'])).total_seconds()
                remaining=int(EVENT_TEAMS.get('cooldown_seconds',20)-elapsed+0.999)
                if remaining>0:raise ValueError(f'Next event battle in {remaining}s.')
            await c.execute('INSERT OR IGNORE INTO event_wallets(owner_id) VALUES (?)',(uid,))
            if mode=='all_for_one':
                pref=await self.one(c,'SELECT af1_slot FROM event_wallets WHERE owner_id=?',(uid,))
                team=self.unpack(await self.one(c,'SELECT * FROM af1_teams WHERE owner_id=? AND slot=?',(uid,pref['af1_slot'])))
                if not team:raise ValueError('Create your team with ;af1team first.')
                a=await self._specs(c,uid,team['entries'],allow_empty_supports=True);validate_af1(a,complete=True)
            else:
                user=await self.one(c,'SELECT active_team_slot FROM users WHERE discord_id=?',(uid,))
                raw=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,user['active_team_slot'])) if user else None
                if not raw or raw['team_format']!=6:raise ValueError('Select a complete 6v6 team with ;teamuse first.')
                team=self.db._saved_team(raw)
                if not team.complete:raise ValueError('Complete your active 6v6 team first: ;teamedit.')
                a=await self._specs(c,uid,[{'id':iid,'lane':lane} if iid else None for _,iid,lane in team.entries])
            challenge=EVENT_TEAMS[mode][index]
            state=dict(a=snapshot_specs(a),b=snapshot_specs(cpu_specs(mode,index)),seed=secrets.randbits(62),opponent=index,
                       label=challenge['name'],reward_policy='signature_drop_v2',signature_key=challenge.get('signature_key'),
                       drop_roll=secrets.randbelow(10000)<int(10000*EVENT_TEAMS.get('signature_drop_chance',.5)))
            sid='event-'+uuid.uuid4().hex[:16]
            await c.execute('INSERT INTO event_runs VALUES (?,?,?,\'running\',?,NULL,0,?)',(sid,uid,mode,json.dumps(state),now()))
            return self.unpack(await self.one(c,'SELECT * FROM event_runs WHERE id=?',(sid,)))

    async def finish(self,uid,sid,result):
        async with self.tx() as c:
            row=self.unpack(await self.one(c,'SELECT * FROM event_runs WHERE owner_id=? AND id=?',(uid,sid)))
            if not row:raise ValueError('This battle is unavailable.')
            if row['status']!='running':return 0
            state=row['state'];key=state.get('signature_key')
            reward=int(row['mode']=='tiny_titans' and state.get('reward_policy')=='signature_drop_v2'
                       and result['winner_side']=='A' and state.get('drop_roll',False) and key in {i['key'] for i in SIGNATURE_ITEMS})
            if reward:await credit(c,uid,'item_shard',key,1)
            await c.execute("UPDATE event_runs SET status='done',result_json=?,earned=? WHERE id=?",(json.dumps(result),reward,sid))
            return reward

    async def abandon(self,uid):
        async with self.tx() as c:
            cur=await c.execute("UPDATE event_runs SET status='abandoned' WHERE owner_id=? AND status='running'",(uid,))
            return cur.rowcount

    async def craft(self,uid,key):
        from .raid_store import RaidStore
        return await RaidStore(self.db).craft(uid,'',key)

async def migrate_legacy_signature_shards(conn):
    """Move v11.5 shop shards into regular counted shards once, without loss."""
    await conn.execute('BEGIN IMMEDIATE')
    try:
        await conn.execute('''INSERT INTO shard_balances(owner_id,kind,detail,quantity)
            SELECT owner_id,'item_shard',item_key,quantity FROM event_signature_shards WHERE quantity>0
            ON CONFLICT(owner_id,kind,detail) DO UPDATE SET quantity=quantity+excluded.quantity''')
        await conn.execute('UPDATE event_signature_shards SET quantity=0 WHERE quantity>0')
        await conn.commit()
    except BaseException:
        await conn.rollback();raise

async def replace_merged_refs(conn,consumed,survivor):
    rows=await (await conn.execute('SELECT owner_id,slot,entries_json FROM af1_teams WHERE instr(entries_json,?)>0',(consumed,))).fetchall()
    for row in rows:
        entries=json.loads(row['entries_json'])
        for i,entry in enumerate(entries):
            if entry and entry['id']==consumed:
                if survivor:entry['id']=survivor
                else:entries[i]=None
        await conn.execute('UPDATE af1_teams SET entries_json=?,revision=revision+1 WHERE owner_id=? AND slot=?',(json.dumps(entries),row['owner_id'],row['slot']))
