"""Atomic raid rewards, crafting and equipment ownership."""
from __future__ import annotations
import hashlib,json,random,uuid
from .community_store import CommunityStore,now
from .items import ITEMS,ITEM_BY_KEY,THEMES,CHARACTER_SHARD_COST,can_equip
from .data import CHAR_BY_ID,CHARACTERS
from .economy import week_key,local_date_key
from .progression import craft_cost,TRAINING_SCHOOLS
from .tournament import snapshot_specs
from .shard_balances import balance as shard_balance,consume as consume_shards

SCHEMA='''
CREATE UNIQUE INDEX IF NOT EXISTS one_equipment_instance ON cards(equipped_item_id) WHERE equipped_item_id IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS card_trade_unequip AFTER UPDATE OF owner_id ON cards
WHEN OLD.owner_id<>NEW.owner_id BEGIN
 UPDATE cards SET equipped_item_id=NULL,equipment_key=NULL WHERE instance_id=NEW.instance_id;
END;
CREATE TRIGGER IF NOT EXISTS item_trade_unequip AFTER UPDATE OF owner_id ON inventory_items
WHEN OLD.owner_id<>NEW.owner_id BEGIN
 UPDATE cards SET equipped_item_id=NULL,equipment_key=NULL WHERE equipped_item_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS item_delete_unequip AFTER DELETE ON inventory_items BEGIN
 UPDATE cards SET equipped_item_id=NULL,equipment_key=NULL WHERE equipped_item_id=OLD.id;
END;
CREATE TABLE IF NOT EXISTS raid_weekly (
 owner_id INTEGER NOT NULL,week TEXT NOT NULL,reward_key TEXT NOT NULL,amount INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,week,reward_key));
CREATE TABLE IF NOT EXISTS raid_cursor (owner_id INTEGER NOT NULL,format INTEGER NOT NULL,next_index INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner_id,format));
-- Theme rotations start fresh; old format-only cursors remain for legacy runs.
CREATE TABLE IF NOT EXISTS raid_theme_cursor (
 owner_id INTEGER NOT NULL,format INTEGER NOT NULL,theme TEXT NOT NULL,next_index INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,format,theme));
CREATE TABLE IF NOT EXISTS raid_runs (
 id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,status TEXT NOT NULL,
 state_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_raid ON raid_runs(owner_id) WHERE status IN ('running','broken');
'''

class RaidStore(CommunityStore):
    async def craft_all_items(self,uid,username=''):
        crafted=[]
        async with self.tx() as c:
            for item in ITEMS:
                count=(await shard_balance(c,uid,'item_shard',item['key']))//item['shard_cost']
                if not count:continue
                await consume_shards(c,uid,'item_shard',item['key'],count*item['shard_cost'])
                ids=[await self._issue_item(c,uid,'equipment',detail=item['key']) for _ in range(count)]
                crafted.append(dict(item=item,count=count,ids=ids))
            # Character-specific balances only. School shards are never read or spent.
            for char in CHARACTERS:
                cost=CHARACTER_SHARD_COST[char.rarity]
                count=(await shard_balance(c,uid,'character_shard',char.id))//cost
                if not count:continue
                await consume_shards(c,uid,'character_shard',char.id,count*cost)
                ids=[]
                for _ in range(count):
                    card=await self._issue_card(c,uid,username,char)
                    await c.execute('UPDATE cards SET boon_stat=NULL,bane_stat=NULL WHERE instance_id=?',(card.instance_id,))
                    ids.append(card.instance_id)
                crafted.append(dict(character=char,count=count,ids=ids,cost=cost))
        return crafted

    async def recycle_equipment(self,uid,*,item_id=None,key=None,count=None):
        if (item_id is None)==(key is None):raise ValueError('Choose an item ID or item name.')
        if count is not None and (isinstance(count,bool) or not isinstance(count,int) or count<1):raise ValueError('Quantity must be a positive whole number.')
        async with self.tx() as c:
            rows=await self.rows(c,"SELECT i.* FROM inventory_items i WHERE i.owner_id=? AND i.kind='equipment' AND "+('i.id=?' if item_id else 'i.detail=?')+' AND NOT EXISTS (SELECT 1 FROM cards WHERE equipped_item_id=i.id)',(uid,item_id or key))
            if not rows:raise ValueError('No matching unequipped equipment is available in your inventory.')
            rows.sort(key=lambda r:(r['created_at'],r['id']))
            if count is not None:
                if len(rows)<count:raise ValueError(f'Only {len(rows)} matching unequipped items are available; nothing was recycled.')
                rows=rows[:count]
            if any(r['detail'] not in ITEM_BY_KEY for r in rows):raise ValueError('An item has no valid recycling recipe.')
            payout=sum(ITEM_BY_KEY[r['detail']]['shard_cost'] for r in rows)
            for row in rows:await c.execute('DELETE FROM inventory_items WHERE id=? AND owner_id=?',(row['id'],uid))
            await self._credit(c,uid,payout)
            return dict(count=len(rows),clan_coins=payout)

    async def equip(self,uid,card_id,item_id=None):
        async with self.tx() as c:
            card=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,card_id,card_id))
            if not card:raise ValueError('Choose a card you own.')
            key=None
            if item_id:
                item=await self.one(c,"SELECT * FROM inventory_items WHERE id=? AND owner_id=? AND kind='equipment'",(item_id,uid))
                if not item or item['detail'] not in ITEM_BY_KEY:raise ValueError('Choose an equipment item you own.')
                if not can_equip(ITEM_BY_KEY[item['detail']],CHAR_BY_ID[card['character_id']]):raise ValueError('That unit cannot equip this item.')
                used=await self.one(c,'SELECT instance_id FROM cards WHERE equipped_item_id=?',(item_id,))
                if used and used['instance_id']!=card['instance_id']:raise ValueError('Unequip that item from its current card first.')
                key=item['detail']
            await c.execute('UPDATE cards SET equipped_item_id=?,equipment_key=? WHERE instance_id=?',(item_id,key,card['instance_id']))
            return self.db._card(await self.one(c,'SELECT * FROM cards WHERE instance_id=?',(card['instance_id'],)))

    async def equipment_options(self,uid,card):
        async with self.db.connect() as c:
            rows=await self.rows(c,"SELECT i.* FROM inventory_items i LEFT JOIN cards c ON c.equipped_item_id=i.id WHERE i.owner_id=? AND i.kind='equipment' AND (c.instance_id IS NULL OR c.instance_id=?) ORDER BY i.detail,i.id",(uid,card.instance_id))
        return [i for i in rows if i['detail'] in ITEM_BY_KEY and can_equip(ITEM_BY_KEY[i['detail']],CHAR_BY_ID[card.character_id])]

    async def craft_sources(self,uid,char):
        async with self.db.connect() as c:
            count=await shard_balance(c,uid,'character_shard',char.id)
            school=await self.one(c,'SELECT quantity FROM shards WHERE owner_id=? AND school=?',(uid,char.school))
        return {'character':(count,CHARACTER_SHARD_COST[char.rarity]),'school':((school['quantity'] if school else 0),craft_cost(char.rarity)) if char.school in TRAINING_SCHOOLS else (0,10**9)}

    async def craft(self,uid,username,target,source='item'):
        await self.db.ensure_user(uid)
        async with self.tx() as c:
            if source=='item':
                item=ITEM_BY_KEY.get(target)
                if not item:raise ValueError('No matching item.')
                kind,detail,cost='item_shard',target,item['shard_cost']
            else:
                char=CHAR_BY_ID.get(target)
                if not char or source not in ('character','school'):raise ValueError('No matching crafting recipe.')
                kind,detail,cost='character_shard',target,CHARACTER_SHARD_COST[char.rarity]
            if source=='school':
                if char.school not in TRAINING_SCHOOLS:raise ValueError('That character has no school-shard recipe.')
                cost=craft_cost(char.rarity)
                cur=await c.execute('UPDATE shards SET quantity=quantity-? WHERE owner_id=? AND school=? AND quantity>=?',(cost,uid,char.school,cost))
                if not cur.rowcount:raise ValueError('Not enough school shards.')
            else:
                await consume_shards(c,uid,kind,detail,cost)
            if source=='item':return await self._issue_item(c,uid,'equipment',detail=target)
            card=await self._issue_card(c,uid,username,char)
            await c.execute('UPDATE cards SET boon_stat=NULL,bane_stat=NULL WHERE instance_id=?',(card.instance_id,))
            card.boon_stat=card.bane_stat=None
            return card

    async def weekly(self,uid,conn=None,week=None):
        if conn is None:
            async with self.db.connect() as c:return await self.weekly(uid,c,week)
        row=await self.one(conn,'SELECT amount FROM raid_shard_weekly WHERE owner_id=? AND week_key=?',(uid,week or week_key()))
        return {'character_shard':row['amount']} if row else {}

    async def shard_progress(self,uid,conn=None,*,day=None,week=None):
        if conn is None:
            async with self.db.connect() as c:return await self.shard_progress(uid,c,day=day,week=week)
        day=day or local_date_key();week=week or week_key()
        progress={r['reward_key']:r['amount'] for r in await self.rows(conn,'SELECT reward_key,amount FROM raid_shard_daily WHERE owner_id=? AND day_key=?',(uid,day))}
        progress['character_shard_weekly']=(await self.weekly(uid,conn,week)).get('character_shard',0)
        from .rewards import daily
        currency=(await daily(conn,uid,day)).get('raid',{})
        progress['coins']=currency.get('coins',0)
        progress['clancoins']=currency.get('clan_coins',0)
        return progress

    def reward_caps(self,theme,earned,difficulty='hard'):
        """Evaluate every reward against earned totals, never inventory balances."""
        from .raids import DIFFICULTIES
        if theme not in THEMES:raise ValueError('Choose tactics, offense, defense, tempo or recovery.')
        if difficulty not in DIFFICULTIES:raise ValueError('Choose Easy, Hard or Extreme.')
        group=[i for i in ITEMS if i['theme']==theme]
        total=sum(float(i['drop_rate']) for i in group)
        if not group or total<=0:raise ValueError('Raid item pool has no valid drop weights.')
        item_weight=.40 if difficulty=='easy' else .755
        candidates=[]
        def add(kind,key,cap,weight,amount,detail='',weekly_remaining=None):
            used=int(earned.get(key,0));remaining=max(0,int(cap)-used)
            if weekly_remaining is not None:remaining=min(remaining,max(0,weekly_remaining))
            if weight<=0:raise ValueError('Raid reward weights must be positive.')
            candidates.append(dict(kind=kind,detail=detail,key=key,weight=weight,
                                   cap=cap,earned=used,remaining=remaining,amount=min(amount,remaining)))
        for item in group:
            add('item_shard',item['key'],int(item['shard_cost'])*2,
                item_weight*float(item['drop_rate'])/total,1,item['key'])
        add('character_shard','character_shard',50,.50 if difficulty=='easy' else .145,1,
            weekly_remaining=350-int(earned.get('character_shard_weekly',0)))
        add('coins','coins',300,.05,100)
        add('clancoins','clancoins',300,.05,100)
        return candidates

    def reward_pool(self,theme,earned,difficulty='hard'):
        pool=[dict(r) for r in self.reward_caps(theme,earned,difficulty) if r['remaining']>0]
        total=sum(r['weight'] for r in pool)
        for reward in pool:reward['weight']/=total
        return pool

    async def next_index(self,uid,fmt,theme='tactics'):
        from .raids import RAID_POOLS
        if fmt not in (3,6) or theme not in THEMES:raise ValueError('Choose a raid theme and 3v3 or 6v6.')
        async with self.db.connect() as c:
            row=await self.one(c,'SELECT next_index FROM raid_theme_cursor WHERE owner_id=? AND format=? AND theme=?',(uid,fmt,theme))
            return (row['next_index'] if row else 0)%len(RAID_POOLS[theme][fmt])

    async def start(self,uid,username,channel,slot,fmt,theme,expected_index=None,difficulty='hard'):
        from .raids import RAID_TEAMS,RAID_POOLS,team_index,cpu_specs,raid_team,DIFFICULTIES
        if difficulty not in DIFFICULTIES:raise ValueError('Choose Easy, Hard or Extreme.')
        if difficulty=='extreme' and fmt!=6:raise ValueError('Extreme raids are 6v6 only.')
        async with self.tx() as c:
            if await self.one(c,"SELECT id FROM raid_runs WHERE owner_id=? AND status IN ('running','broken')",(uid,)):raise ValueError('You have an unfinished raid. Use ;raidresume or ;refresh.')
            if fmt not in (3,6):raise ValueError('Choose 3v3 or 6v6.')

            a=await self._team_snapshot(c,uid,slot,fmt)
            row=await self.one(c,'SELECT next_index FROM raid_theme_cursor WHERE owner_id=? AND format=? AND theme=?',(uid,fmt,theme))
            rotation_index=(row['next_index'] if row else 0)%len(RAID_POOLS[theme][fmt])
            if expected_index is not None and expected_index!=rotation_index:raise ValueError('Your raid rotation changed. Open ;raid again to see the next opponent.')
            index=team_index(fmt,theme,rotation_index,difficulty);sid='raid-'+uuid.uuid4().hex[:12]
            state={'a':a,'b':snapshot_specs(cpu_specs(fmt,index,difficulty)),'format':fmt,'theme':theme,'index':index,'rotation_index':rotation_index,'rotation_version':3 if difficulty=='extreme' else 2,'difficulty':difficulty,'opponent_name':raid_team(fmt,index,difficulty)['name'],'username':username}
            await c.execute("INSERT INTO raid_runs VALUES (?,?,?,'running',?,?)",(sid,uid,channel,json.dumps(state),now()))
            return await self.raid(sid,c)

    async def raid(self,sid,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.raid(sid,c)
        row=await self.one(conn,'SELECT * FROM raid_runs WHERE id=?',(sid,))
        if row:row['state']=json.loads(row.pop('state_json'))
        return row

    async def finish(self,sid,result):
        async with self.tx() as c:
            row=await self.raid(sid,c)
            if not row or row['status'] not in ('running','broken'):raise ValueError('That raid is already resolved or closed.')
            uid=row['owner_id'];st=row['state'];reward=None
            if result['winner_side']=='A':
                reward_week=week_key();reward_day=local_date_key()
                earned=await self.shard_progress(uid,c,day=reward_day,week=reward_week)
                difficulty=st.get('difficulty','hard')
                caps=self.reward_caps(st['theme'],earned,difficulty)
                pool=self.reward_pool(st['theme'],earned,difficulty)
                all_capped=all(r['remaining']==0 for r in caps)
                if bool(pool)==all_capped:raise RuntimeError('Raid eligibility check disagrees with reward pool.')
                st['reward_check']=dict(day=reward_day,week=reward_week,earned=dict(earned),
                    candidates=caps,eligible_keys=[r['key'] for r in pool],all_capped=all_capped)
                rng=random.Random(int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big'))
                from .raids import drop_count
                slots=drop_count(rng,difficulty);rewards=[];coins=clan=0
                for _ in range(slots):
                    # Rebuild after each slot. Duplicate drops are legal only while eligible.
                    pool=self.reward_pool(st['theme'],earned,difficulty)
                    if not pool:break
                    reward=dict(rng.choices(pool,weights=[r['weight'] for r in pool],k=1)[0])
                    kind=reward['kind'];stars=0
                    if kind=='character_shard':
                        from .raids import choose_character
                        char=choose_character(rng,CHARACTERS,st.get('difficulty','hard'));reward['detail']=char.id;stars=char.rarity
                    if kind=='coins':
                        coins+=reward['amount']
                        await c.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(reward['amount'],uid))
                    elif kind=='clancoins':
                        clan+=reward['amount']
                        await self._credit(c,uid,reward['amount'])
                    else:
                        await self._issue_item(c,uid,kind,stars,reward['detail'])
                        reward['balance']=await shard_balance(c,uid,kind,reward['detail'])
                        await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,1) ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET amount=amount+1',(uid,reward_day,reward['key']))
                        if kind=='character_shard':
                            await c.execute('INSERT INTO raid_shard_weekly VALUES (?,?,1) ON CONFLICT(owner_id,week_key) DO UPDATE SET amount=amount+1',(uid,reward_week))
                            earned['character_shard_weekly']=earned.get('character_shard_weekly',0)+1
                    earned[reward['key']]=earned.get(reward['key'],0)+reward['amount']
                    rewards.append(reward)
                reward=rewards[0] if rewards else None
                st['rewards']=rewards;st['drop_slots']=slots
                await c.execute('''INSERT INTO economy_daily VALUES (?,?,?,1,?,?)
                    ON CONFLICT(owner_id,day_key,reward_key) DO UPDATE SET
                    wins=wins+1,coins=coins+excluded.coins,clan_coins=clan_coins+excluded.clan_coins''',
                    (uid,reward_day,'raid',coins,clan))
                if reward is None and not all_capped:raise RuntimeError('Eligible raid victory has no reward.')
                st['reward_status']='awarded' if reward else 'all_capped'
                st['currency']=dict(coins=coins,clan_coins=clan)
                st['reward_version']=3
                if st.get('rotation_version')==2:
                    # Progress and rewards commit together, once, only after victory.
                    await c.execute('INSERT INTO raid_theme_cursor VALUES (?,?,?,1) ON CONFLICT(owner_id,format,theme) DO UPDATE SET next_index=next_index+1',(uid,st['format'],st['theme']))
            st.update(result=result,reward=reward)
            await c.execute("UPDATE raid_runs SET status='complete',state_json=? WHERE id=?",(json.dumps(st),sid))
            # Keep replays bounded while weekly reward totals remain independent.
            await c.execute("DELETE FROM raid_runs WHERE owner_id=? AND status='complete' AND id<>? AND id NOT IN (SELECT id FROM raid_runs WHERE owner_id=? AND status='complete' AND id<>? ORDER BY created_at DESC LIMIT 19)",(uid,sid,uid,sid))
            return await self.raid(sid,c)

    async def pending(self,uid=None):
        async with self.db.connect() as c:return await self.rows(c,"SELECT id,owner_id FROM raid_runs WHERE status IN ('running','broken')"+(' AND owner_id=?' if uid else ''),(uid,) if uid else ())

    async def mark_broken(self,sid):
        async with self.tx() as c:await c.execute("UPDATE raid_runs SET status='broken' WHERE id=? AND status='running'",(sid,))

    async def cancel_pending(self,uid):
        async with self.tx() as c:
            cur=await c.execute("UPDATE raid_runs SET status='cancelled' WHERE owner_id=? AND status IN ('running','broken')",(uid,))
            return cur.rowcount
