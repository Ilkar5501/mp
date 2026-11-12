"""Transactional player preferences and consumables."""
import json,time
from .community_store import CommunityStore
from .items import ITEM_BY_KEY
from .data import CHAR_BY_ID
from .shard_balances import credit

SCHEMA='''
CREATE TABLE IF NOT EXISTS autoclean_items(owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,PRIMARY KEY(owner_id,item_key));
CREATE TABLE IF NOT EXISTS pvp_shields(owner_id INTEGER PRIMARY KEY,protected_until REAL NOT NULL,reusable_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS af1_daily(owner_id INTEGER NOT NULL,day TEXT NOT NULL,marks INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner_id,day));
'''


class GameplayStore(CommunityStore):
    async def buy_clan_supply(self,uid,kind,stars=1,quantity=1):
        if kind in ('pvpshield','shiny_stone'):return await self.buy_consumable(uid,kind,quantity,clan=True)
        if kind not in ('mergecopy','blessing','starpack') or not 1<=stars<=5 or not 1<=quantity<=100:raise ValueError('Choose a valid supply and quantity from 1 to 100.')
        cost=stars*{'mergecopy':150,'blessing':200,'starpack':100}[kind]*quantity
        async with self.tx() as c:
            if not await self.clan(user_id=uid,conn=c):raise ValueError('Join a clan to use its shop.')
            paid=await c.execute('UPDATE clan_wallets SET coins=coins-? WHERE user_id=? AND coins>=?',(cost,uid,cost))
            if not paid.rowcount:raise ValueError(f'Requires {cost:,} Clan Dollars.')
            return [await self._issue_item(c,uid,kind,stars) for _ in range(quantity)],cost

    async def fuse(self,uid,first,second,*,confirm=False):
        async with self.tx() as c:
            rows=[]
            for iid in (first,second):
                row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,iid,iid))
                if not row:raise ValueError('Both ingredients must be cards you own.')
                if row['locked'] or row['equipped_item_id']:raise ValueError('Unlock and unequip both ingredients before fusion.')
                rows.append(row)
            expected={'Oikawa — Young Emperor','Kageyama — King of the Lost Court'}
            if {r['character_id'] for r in rows}!=expected or rows[0]['merge_level']!=rows[1]['merge_level']:raise ValueError('Use Young Emperor and King of the Lost Court at the same merge level.')
            for row in rows:
                await self._require_unused(c,uid,row['instance_id'])
            level=rows[0]['merge_level']
            if not 0<=level<=10:raise ValueError('Ingredient merges must be between +0 and +10.')
            factor=max(1,level);coins=100*factor;marks=25*factor
            wallet=await self.one(c,'SELECT af1_marks FROM event_wallets WHERE owner_id=?',(uid,))
            user=await self.one(c,'SELECT coins FROM users WHERE discord_id=?',(uid,))
            if not wallet or wallet['af1_marks']<marks or user['coins']<coins:raise ValueError(f'Fusion requires {coins:,} coins and {marks} AF1 Marks.')
            if not confirm:return level,coins,marks
            await c.execute('UPDATE users SET coins=coins-? WHERE discord_id=?',(coins,uid))
            await c.execute('UPDATE event_wallets SET af1_marks=af1_marks-? WHERE owner_id=?',(marks,uid))
            for row in rows:await c.execute('DELETE FROM cards WHERE instance_id=?',(row['instance_id'],))
            card=await self._issue_card(c,uid,rows[0]['original_owner_name'],CHAR_BY_ID['Oikawa & Kageyama — Kings of the Playground'])
            await c.execute('UPDATE cards SET merge_level=? WHERE instance_id=?',(level,card.instance_id))
            return self.db._card(await self.one(c,'SELECT * FROM cards WHERE instance_id=?',(card.instance_id,)))

    async def _require_unused(self,c,uid,iid):
        for table in ('saved_teams','teams','gauntlet_runs'):
            for row in await self.rows(c,f'SELECT * FROM {table} WHERE owner_id=?',(uid,)):
                if table=='gauntlet_runs' and row.get('status') in ('complete','claimed'):continue
                if any(v==iid for k,v in row.items() if k.endswith('_id')):raise ValueError('Remove the card from all saved teams and active activities first.')
        for table,col in [('pvp_teams','entries_json'),('af1_teams','entries_json')]:
            if await self.one(c,f'SELECT 1 FROM {table} WHERE owner_id=? AND instr({col},?)>0',(uid,json.dumps(iid))):raise ValueError('Remove the card from its PvP/AF1 team first.')
        for table,predicate in [('community_sessions',"status NOT IN ('complete','cancelled','expired','declined')"),('event_runs',"status='running'"),('raid_runs',"status IN ('running','broken')"),('pvp_runs',"status='running'"),('contest_runs',"status='running'")]:
            if await self.one(c,f'SELECT 1 FROM {table} WHERE ({predicate}) AND instr(state_json,?)>0',(json.dumps(iid),)):raise ValueError('This card is committed to an active activity.')
        if await self.one(c,"SELECT 1 FROM tournament_entries e JOIN tournaments t ON t.id=e.tournament_id WHERE t.status IN ('open','running') AND instr(e.snapshot_json,?)>0",(json.dumps(iid),)):raise ValueError('This card is in an active tournament.')

    async def use_stone(self,uid,item_id,card_id):
        async with self.tx() as c:
            stone=await self.one(c,"SELECT 1 FROM inventory_items WHERE id=? AND owner_id=? AND kind='shiny_stone'",(item_id,uid))
            row=await self.one(c,'SELECT * FROM cards WHERE instance_id=? AND owner_id=?',(card_id,uid))
            if not stone or not row:raise ValueError('The stone or selected card is no longer yours.')
            char=CHAR_BY_ID[row['character_id']];slots=set(json.loads(row['shiny_slots']))
            available=[i for i in range(1,len(char.shiny_arts)+1) if i not in slots]
            if not available:raise ValueError('This card has no remaining shiny artwork to unlock.')
            slot=min(available);slots.add(slot)
            await c.execute('UPDATE cards SET shiny_slots=?,art_slot=?,bane_stat=NULL WHERE instance_id=?',(json.dumps(sorted(slots)),slot,card_id))
            await c.execute('DELETE FROM inventory_items WHERE id=?',(item_id,))
            return slot

    async def lock_art(self,uid,iid,slot):
        async with self.tx() as c:
            row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,iid))
            if not row or slot not in [0]+json.loads(row['shiny_slots']):raise ValueError('This artwork is not unlocked on your card.')
            await c.execute('UPDATE cards SET art_slot=? WHERE instance_id=?',(slot,iid))
    async def autoclean(self,uid):
        async with self.db.connect() as c:
            return {r['item_key'] for r in await self.rows(c,'SELECT item_key FROM autoclean_items WHERE owner_id=?',(uid,))}

    async def set_autoclean_page(self,uid,page_keys,selected):
        if not set(selected)<=set(page_keys) or not set(page_keys)<=set(ITEM_BY_KEY):raise ValueError('Invalid equipment selection.')
        async with self.tx() as c:
            for key in page_keys:
                await c.execute('DELETE FROM autoclean_items WHERE owner_id=? AND item_key=?',(uid,key))
            for key in selected:await c.execute('INSERT INTO autoclean_items VALUES(?,?)',(uid,key))

    async def dismantle_card(self,uid,iid,*,confirm=False):
        async with self.tx() as c:
            row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,iid,iid))
            if not row:raise ValueError('That card is not in your collection.')
            iid=row['instance_id']
            await self._require_unused(c,uid,iid)
            if row['locked']:raise ValueError('Locked cards cannot be dismantled.')
            if row['equipped_item_id']:raise ValueError('Unequip this card before dismantling it.')
            saved=await self.rows(c,'SELECT * FROM saved_teams WHERE owner_id=?',(uid,))
            if any(iid==value for team in saved for key,value in team.items() if key.endswith('_id')):
                raise ValueError('Remove this card from all saved teams before dismantling it.')
            for table,column in [('pvp_teams','entries_json'),('af1_teams','entries_json')]:
                if await self.one(c,f'SELECT 1 FROM {table} WHERE owner_id=? AND instr({column},?)>0',(uid,json.dumps(iid))):
                    raise ValueError('Cards registered on PvP or AF1 teams cannot be dismantled.')
            for table,column,predicate in [('community_sessions','state_json',"status NOT IN ('complete','cancelled','expired','declined')"),('event_runs','state_json',"status='running'"),('raid_runs','state_json',"status IN ('running','broken')"),('pvp_runs','state_json',"status='running'"),('contest_runs','state_json',"status='running'")]:
                if await self.one(c,f'SELECT 1 FROM {table} WHERE ({predicate}) AND instr({column},?)>0',(json.dumps(iid),)):
                    raise ValueError('This card is committed to an active activity.')
            if await self.one(c,"SELECT 1 FROM tournament_entries e JOIN tournaments t ON t.id=e.tournament_id WHERE t.status IN ('open','running') AND instr(e.snapshot_json,?)>0",(json.dumps(iid),)):
                raise ValueError('This card is entered in an active tournament.')
            char=CHAR_BY_ID[row['character_id']];amount=char.rarity*(row['merge_level']+1)
            if confirm:
                await c.execute('DELETE FROM cards WHERE instance_id=? AND owner_id=?',(iid,uid))
                await credit(c,uid,'character_shard',char.id,amount)
            return char,amount

    async def buy_consumable(self,uid,kind,quantity=1,*,clan=False):
        prices={'pvpshield':(500,750),'shiny_stone':(10000,15000)}
        if kind not in prices or not isinstance(quantity,int) or not 1<=quantity<=100:raise ValueError('Choose a valid item and quantity from 1 to 100.')
        cost=prices[kind][int(clan)]*quantity
        async with self.tx() as c:
            if clan:
                if not await self.clan(user_id=uid,conn=c):raise ValueError('Join a clan to use the clan shop.')
                sql='UPDATE clan_wallets SET coins=coins-? WHERE user_id=? AND coins>=?'
            else:sql='UPDATE users SET coins=coins-? WHERE discord_id=? AND coins>=?'
            cur=await c.execute(sql,(cost,uid,cost))
            if not cur.rowcount:raise ValueError(f'Requires {cost:,} '+('Clan Dollars.' if clan else 'coins.'))
            ids=[await self._issue_item(c,uid,kind) for _ in range(quantity)]
            return ids,cost

    async def use_shield(self,uid,iid,at=None):
        at=time.time() if at is None else at
        async with self.tx() as c:
            item=await self.one(c,"SELECT * FROM inventory_items WHERE owner_id=? AND id=? AND kind='pvpshield'",(uid,iid))
            if not item:raise ValueError('That PvP Shield is not in your inventory.')
            old=await self.one(c,'SELECT * FROM pvp_shields WHERE owner_id=?',(uid,))
            if old and at<old['reusable_at']:raise ValueError(f'Your next shield is available <t:{int(old["reusable_at"])}:R>.')
            if await self.one(c,'SELECT 1 FROM pvp_busy WHERE owner_id=?',(uid,)):raise ValueError('Finish your current PvP battle before using a shield.')
            await c.execute('INSERT INTO pvp_shields VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET protected_until=excluded.protected_until,reusable_at=excluded.reusable_at',(uid,at+7200,at+21600))
            await c.execute('DELETE FROM inventory_items WHERE id=?',(iid,))
            return at+7200,at+21600
