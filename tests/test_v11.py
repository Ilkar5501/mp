"""Offline gameplay, persistence, cap and Discord-registration regressions."""
import asyncio,copy,json,random,sys,tempfile,time,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch,AsyncMock,MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hiq.database import Database
from hiq.community_store import CommunityStore,now
from hiq.raid_store import RaidStore
from hiq.rewards import RewardStore
from hiq.data import CHARACTERS,CHAR_BY_ID
from hiq.items import ITEMS,CHARACTER_SHARD_COST
from hiq.raids import cpu_specs,team_index,choose_character,drop_count,RARITY_WEIGHTS,DROP_WEIGHTS
from hiq.sealed import make_draft,pick,bracket,team_specs,validate_team
from hiq.sealed_store import SealedStore
from hiq.tournament import snapshot_specs
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.models import AbilityDef


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();self.store=CommunityStore(self.db)
        for uid in range(1,11):await self.db.ensure_user(uid)

    async def asyncTearDown(self):self.temp.cleanup()

    async def war(self,size=5,bans=True):
        clans=[await self.store.create_clan(uid,str(uid),f'Clan {uid}','🏐') for uid in (1,6)]
        async with self.store.tx() as c:
            for lead in (1,6):
                clan=clans[0 if lead==1 else 1]
                for uid in range(lead+1,lead+5):
                    await c.execute('INSERT INTO clan_members(user_id,clan_id,username,joined_at) VALUES (?,?,?,?)',(uid,clan['id'],str(uid),now()))
        await self.store.appoint(1,2);await self.store.appoint(6,7)
        row=await self.store.challenge(2,clans[1]['id'],3,100,size)
        row=await self.store.respond_war(row['id'],7,True)
        if bans:
            await self.store.ban_war_unit(row['id'],2,CHARACTERS[-1].id)
            row=await self.store.ban_war_unit(row['id'],7,CHARACTERS[-1].id)
        return row

    async def save(self,row):
        async with self.store.tx() as c:await self.store._save(c,row)

    async def ready_teams(self,row):
        st=row['state'];st['teams']=[snapshot_specs(team_specs('test',uid,[c.id for c in CHARACTERS[:6]])) for uid in st['sends']]
        await self.save(row);return row

    async def test_reward_totals_and_day_reset(self):
        rewards=RewardStore(self.db)
        values=[(await rewards.award(1,'battle',day='2026-10-27'))['coins'] for _ in range(51)]
        self.assertEqual(values[:5],[50]*5);self.assertEqual(values[5:50],[4]*45);self.assertEqual(sum(values),430)
        self.assertEqual((await rewards.award(1,'battle',day='2026-10-28'))['coins'],50)
        for key in ('gauntlet_regional','gauntlet_national'):
            self.assertEqual([(await rewards.award(1,key))['coins'] for _ in range(3)],[150,50,0])
        self.assertEqual([(await rewards.award(1,'war'))['clan_coins'] for _ in range(2)],[200,0])

    async def test_checkin_and_six_donations(self):
        await self.war()
        for uid in range(1,6):bonus,_=await self.store.checkin(uid,str(uid))
        self.assertTrue(bonus)
        for uid in range(1,6):self.assertEqual(await self.store.wallet(uid),200)
        with self.assertRaises(ValueError):await self.store.checkin(1,'1')
        async with self.store.tx() as c:
            cards=[await self.store._issue_card(c,1,'One',CHARACTERS[0]) for _ in range(7)]
        for card in cards[:6]:await self.store.donate(1,card.instance_id)
        with self.assertRaises(ValueError):await self.store.donate(1,cards[-1].instance_id)

    async def test_bulkcraft_preserves_school_shards_and_remainders(self):
        char=CHARACTERS[0];cost=CHARACTER_SHARD_COST[char.rarity];item=ITEMS[0]
        from hiq.shard_balances import credit,balance
        async with self.store.tx() as c:
            await credit(c,1,'character_shard',char.id,cost*2+1)
            await credit(c,1,'item_shard',item['key'],item['shard_cost']+1)
            await c.execute('INSERT INTO shards VALUES (?,?,?)',(1,char.school,100))
        made=await RaidStore(self.db).craft_all_items(1,'One')
        self.assertEqual(sum(r['count'] for r in made),3)
        cards=await self.db.get_cards(1);self.assertEqual(len(cards),2)
        self.assertTrue(all(c.merge_level==0 and c.boon_stat is None and c.bane_stat is None for c in cards))
        self.assertEqual((await self.db.get_shards(1))[char.school],100)
        async with self.db.connect() as c:self.assertEqual(await balance(c,1,'character_shard',char.id),1)
        self.assertEqual(await RaidStore(self.db).craft_all_items(1),[])

    async def test_recycling_count_excludes_equipment_and_is_atomic(self):
        key=ITEMS[0]['key'];raid=RaidStore(self.db)
        async with self.store.tx() as c:
            ids=[await self.store._issue_item(c,1,'equipment',detail=key) for _ in range(4)]
            card=await self.store._issue_card(c,1,'One',CHARACTERS[0])
            await c.execute('UPDATE cards SET equipped_item_id=?,equipment_key=? WHERE instance_id=?',(ids[0],key,card.instance_id))
        with self.assertRaises(ValueError):await raid.recycle_equipment(1,key=key,count=4)
        self.assertEqual(len(await self.store.items(1)),4)
        result=await raid.recycle_equipment(1,key=key,count=2);self.assertEqual(result['count'],2)
        self.assertEqual(len(await self.store.items(1)),2)
        self.assertIsNotNone((await self.db.get_card(card.instance_id)).equipped_item_id)
        for bad in (0,-1,True):
            with self.assertRaises(ValueError):await raid.recycle_equipment(1,key=key,count=bad)

    async def raid_run(self,kind='coins',earned=0,slots=3,all_capped=False):
        store=RaidStore(self.db);sid='raid-test';day=__import__('hiq.economy',fromlist=['local_date_key']).local_date_key()
        async with self.store.tx() as c:
            await c.execute('INSERT INTO economy_daily VALUES (?,?,?,0,?,?)',(1,day,'raid',earned if kind=='coins' else 300,earned if kind=='clancoins' else 300))
            for reward in store.reward_caps('tactics',{},'extreme'):
                if reward['kind'] in ('item_shard','character_shard'):
                    used=reward['cap'] if all_capped or reward['kind']!=kind else earned
                    await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(1,day,reward['key'],used))
            state=dict(theme='tactics',difficulty='extreme',format=6)
            await c.execute('INSERT INTO raid_runs VALUES (?,?,?, ?,?,?)',(sid,1,100,'running',json.dumps(state),now()))
        with patch('hiq.raids.drop_count',return_value=slots):row=await store.finish(sid,{'winner_side':'A'})
        return store,row

    async def test_raid_duplicate_currency_drops_share_cap(self):
        store,row=await self.raid_run(earned=100)
        self.assertEqual([r['amount'] for r in row['state']['rewards']],[100,100])
        self.assertEqual(row['state']['currency']['coins'],200)
        self.assertEqual((await store.shard_progress(1))['coins'],300)
        with self.assertRaises(ValueError):await store.finish(row['id'],{'winner_side':'A'})

    async def test_raid_clan_currency_cap(self):
        store,row=await self.raid_run(kind='clancoins',earned=200)
        self.assertEqual(row['state']['currency']['clan_coins'],100)
        self.assertEqual((await store.shard_progress(1))['clancoins'],300)

    async def test_raid_duplicate_character_shards_stop_at_daily_cap(self):
        store,row=await self.raid_run(kind='character_shard',earned=49)
        self.assertEqual(len(row['state']['rewards']),1)
        self.assertEqual(row['state']['rewards'][0]['kind'],'character_shard')
        self.assertEqual((await store.shard_progress(1))['character_shard'],50)

    async def test_raid_all_capped_is_empty(self):
        _,row=await self.raid_run(earned=300,all_capped=True)
        self.assertEqual(row['state']['rewards'],[]);self.assertEqual(row['state']['reward_status'],'all_capped')

    async def test_war_bans_coleaders_and_inactive_both_cancel(self):
        row=await self.war(bans=False)
        self.assertTrue(await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1))
        row=await self.store.session(row['id']);self.assertEqual(row['status'],'cancelled')
        self.assertEqual((await self.store.clan(user_id=1))['losses'],0)

    async def test_war_one_missing_ban_loses_and_reward_once(self):
        row=await self.war(bans=False)
        await self.store.ban_war_unit(row['id'],2,CHARACTERS[0].id)
        await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1)
        done=await self.store.session(row['id']);self.assertEqual(done['state']['winner'],0)
        self.assertEqual((await self.store.clan(user_id=6))['losses'],1)
        self.assertEqual(await self.store.wallet(1),200)
        self.assertFalse(await self.store.expire_war(row['id'],at=time.time()+999999))

    async def test_war_both_missing_sends_cancel(self):
        row=await self.war();await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1)
        self.assertEqual((await self.store.session(row['id']))['status'],'cancelled')

    async def test_war_one_missing_send_forfeits(self):
        row=await self.war();await self.store.nominate(row['id'],1,2,1)
        await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1)
        self.assertEqual((await self.store.session(row['id']))['state']['winner'],0)

    async def test_war_send_does_not_reset_team_deadline(self):
        row=await self.war();before=row['state']['deadlines'][0]
        row=await self.store.nominate(row['id'],1,2,1)
        self.assertEqual(row['state']['deadlines'][0],before)
        row=await self.store.nominate(row['id'],6,7,1);row=await self.ready_teams(row)
        row['state']['teams'][0]=None;await self.save(row)
        await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1)
        row=await self.store.session(row['id']);self.assertEqual(row['state']['eliminated'][0],[2])
        self.assertEqual(row['state']['series'][0]['score'],[0,2]);self.assertEqual(row['state']['series'][0]['reason'],'Auto-loss due to inactivity')
        self.assertEqual(row['state']['stage'],'winner_send');self.assertIsNone(row['state']['deadlines'][0])

    async def test_war_both_teams_missing_eliminates_both(self):
        row=await self.war();await self.store.nominate(row['id'],1,2,1);row=await self.store.nominate(row['id'],6,7,1)
        await self.store.expire_war(row['id'],at=max(row['state']['deadlines'])+1)
        row=await self.store.session(row['id'])
        self.assertEqual(row['state']['eliminated'],[[2],[7]]);self.assertEqual(row['state']['stage'],'blind')
        self.assertIsNone(row['state']['series'][0]['winner'])

    async def test_quick_war_roster_locks_first_three_and_finishes_at_three(self):
        row=await self.war(size=3);st=row['state'];st['participants']=[[1,2,3],[6,7,8]];await self.save(row)
        self.assertEqual([m['user_id'] for m in self.store.eligible_war_members(st,0)],[1,2,3])
        with self.assertRaises(ValueError):await self.store.nominate(row['id'],1,4,1)
        st['sends']=[1,6];st['eliminated'][1]=[7,8];row['status']='running';await self.save(row)
        row=await self.store.finish_series(row['id'],1,0,[2,0]);self.assertEqual(row['status'],'complete')

    async def test_sequential_war_response_window_starts_on_winner_send(self):
        row=await self.war();st=row['state'];st['sends']=[1,6];row['status']='running';await self.save(row)
        row=await self.store.finish_series(row['id'],1,0,[2,0])
        self.assertEqual(row['state']['stage'],'winner_send');self.assertIsNone(row['state']['deadlines'][1])
        deadline=row['state']['deadlines'][0]
        with self.assertRaises(ValueError):await self.store.nominate(row['id'],6,7,2)
        row=await self.store.nominate(row['id'],1,1,2)
        self.assertEqual(row['state']['deadlines'][0],deadline);self.assertIsNotNone(row['state']['deadlines'][1])

    async def test_sealed_picks_atomic_and_survive_reopen(self):
        store=SealedStore(self.db);row=await store.create([dict(id=i,name=str(i)) for i in (1,2,3)],100)
        uid=row['state']['order'][0];pack=row['state']['queues'][str(uid)][0];cid=row['state']['packs'][pack][0]
        outcomes=await asyncio.gather(*(store.choose(row['id'],uid,pack,8,cid) for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(isinstance(o,ValueError) for o in outcomes),1)
        reopened=await SealedStore(Database(self.db.path)).session(row['id'])
        self.assertEqual(reopened['state']['picks'][str(uid)],[cid])

    async def drafted(self,n=3):
        store=SealedStore(self.db);row=await store.create([dict(id=i,name=str(i)) for i in range(1,n+1)],100)
        while row['status']=='drafting':
            for uid in row['state']['order']:
                queue=row['state']['queues'][str(uid)]
                if not queue:continue
                pid=queue[0];cards=row['state']['packs'][pid]
                row,_,_=await store.choose(row['id'],uid,pid,len(cards),cards[0])
        return store,row

    async def test_sealed_private_team_revision_and_simultaneous_lock(self):
        store,row=await self.drafted();sid=row['id']
        for uid in row['state']['order']:
            selected=list({CHAR_BY_ID[cid].name:cid for cid in row['state']['picks'][str(uid)]}.values())
            for position,cid in enumerate(selected[:6]):
                version=row['state']['team_versions'].get(str(uid),0);row=await store.place(sid,uid,position,cid,version)
            version=row['state']['team_versions'][str(uid)];row=await store.submit(sid,uid,version)
            if len(row['state']['teams'])==1:
                row=await store.place(sid,uid,0,selected[0],version)
                self.assertNotIn(str(uid),row['state']['teams'])
                with self.assertRaises(ValueError):await store.submit(sid,uid,version)
                row=await store.submit(sid,uid,version+1)
        self.assertEqual(row['status'],'running')
        with self.assertRaises(ValueError):await store.place(sid,1,0,row['state']['picks']['1'][0],row['state']['team_versions']['1'])
        matches=0
        while row['status']=='running':
            st=row['state'];a,b=st['bracket'][st['match_index']]
            row=await store.record(sid,st['bracket_round'],st['match_index'],a,[2,0] if b else [],[])
            matches+=bool(b)
        self.assertEqual(matches,2);self.assertIn(row['state']['champion'],row['state']['order'])

    async def test_sealed_expiration_replacement_and_access(self):
        store=SealedStore(self.db);users=[dict(id=i,name=str(i)) for i in (1,2)]
        old=await store.create(users,100);new=await store.create(users,100)
        self.assertEqual((await store.session(old['id']))['status'],'expired')
        with self.assertRaises(ValueError):await store.read_active(new['id'],3)
        with patch('hiq.sealed_store.time.time',return_value=new['state']['expires_at']+1):
            with self.assertRaises(ValueError):await store.read_active(new['id'],1)
            await store.expire()
        self.assertEqual((await store.session(new['id']))['status'],'expired')

    async def test_sealed_real_seven_player_tournament_recovery_and_no_rewards(self):
        store,row=await self.drafted(7);sid=row['id']
        for uid in row['state']['order']:
            cids=list({CHAR_BY_ID[cid].name:cid for cid in row['state']['picks'][str(uid)]}.values())[:6]
            for position,cid in enumerate(cids):
                row=await store.place(sid,uid,position,cid,row['state']['team_versions'].get(str(uid),0))
            row=await store.submit(sid,uid,row['state']['team_versions'][str(uid)])
        from hiq.cogs.sealed import SealedCog
        from types import SimpleNamespace
        cog=SealedCog(SimpleNamespace(db=self.db));cog.update_board=AsyncMock();cog.channel_notice=AsyncMock()
        await cog.run(sid)
        complete=await store.session(sid);self.assertEqual(complete['status'],'complete')
        played=[m for m in complete['state']['matches'] if m['games']]
        self.assertEqual(len(played),6);self.assertEqual(len(complete['state']['matches']),7)
        for match in played:
            self.assertEqual(max(match['score']),2);self.assertEqual(len(match['games']),sum(match['score']))
            self.assertTrue(all(g['mode']=='sealed' for g in match['games']))
        await cog.run(sid)
        self.assertEqual((await store.session(sid))['state']['matches'],complete['state']['matches'])
        for uid in range(1,8):self.assertEqual((await self.db.get_user(uid)).coins,0)

    async def test_war_team_can_submit_before_opposing_send_and_rejects_late(self):
        row=await self.war();row=await self.store.nominate(row['id'],1,2,1)
        await self.db.ensure_saved_team(2,1)
        async with self.store.tx() as c:cards=[await self.store._issue_card(c,2,'Two',char) for char in CHARACTERS[:3]]
        await self.db.replace_saved_team(2,1,*(card.instance_id for card in cards))
        row=await self.store.select_war_team(row['id'],2,1,1)
        self.assertIsNotNone(row['state']['teams'][0]);self.assertIsNone(row['state']['sends'][1])
        with self.assertRaises(ValueError):await self.store.select_war_team(row['id'],3,1,1)
        row['state']['teams'][0]=None;row['state']['deadlines']=[time.time()-1,time.time()+100];await self.save(row)
        with self.assertRaises(ValueError):await self.store.select_war_team(row['id'],2,1,1)

    async def test_reinitialization_preserves_wallet_ids_and_earned_caps(self):
        await RewardStore(self.db).award(1,'battle')
        async with self.store.tx() as c:card=await self.store._issue_card(c,1,'One',CHARACTERS[0])
        await Database(self.db.path).init()
        self.assertIsNotNone(await self.db.get_card(card.instance_id))
        self.assertEqual((await self.db.get_user(1)).coins,50)
        self.assertEqual((await RewardStore(self.db).daily(1))['battle']['coins'],50)

    async def test_duplicate_item_shards_stop_at_recipe_daily_cap(self):
        store=RaidStore(self.db);item=next(i for i in ITEMS if i['theme']=='tactics');day=__import__('hiq.economy',fromlist=['local_date_key']).local_date_key()
        async with self.store.tx() as c:
            for reward in store.reward_caps('tactics',{}):
                if reward['kind'] not in ('coins','clancoins'):
                    await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(1,day,reward['key'],reward['cap']-(1 if reward['key']==item['key'] else 0)))
            await c.execute('INSERT INTO economy_daily VALUES (?,?,?,0,300,300)',(1,day,'raid'))
            await c.execute('INSERT INTO raid_runs VALUES (?,?,?, ?,?,?)',('raid-test',1,100,'running',json.dumps(dict(theme='tactics',difficulty='extreme')),now()))
        with patch('hiq.raids.drop_count',return_value=3):row=await store.finish('raid-test',dict(winner_side='A'))
        self.assertEqual(len(row['state']['rewards']),1);self.assertEqual(row['state']['rewards'][0]['detail'],item['key'])

    async def test_character_shard_weekly_cap_blocks_remaining_daily_allowance(self):
        from hiq.economy import week_key
        async with self.store.tx() as c:await c.execute('INSERT INTO raid_shard_weekly VALUES (?,?,?)',(1,week_key(),350))
        _,row=await self.raid_run(kind='character_shard',earned=0)
        self.assertEqual(row['state']['rewards'],[])

    async def test_war_offer_dms_both_officers_and_accepts_from_dm(self):
        from hiq.cogs.clans import ClanCog,WarView
        from types import SimpleNamespace
        old=await self.war();old['status']='cancelled';await self.save(old)
        bot=SimpleNamespace(db=self.db,add_view=MagicMock())
        cog=ClanCog(bot);cog.dm=AsyncMock(return_value=True);cog.update_board=AsyncMock()
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,display_name='One'),channel=SimpleNamespace(id=100),send=AsyncMock(return_value=SimpleNamespace(id=123)))
        offer=await cog.send_war_offer(ctx,old['state']['clans'][1],3,3)
        self.assertEqual({call.args[0] for call in cog.dm.call_args_list},{6,7})
        view=WarView(cog,offer['id'],offer)
        interaction=SimpleNamespace(user=SimpleNamespace(id=7),guild=None,response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
        await view.accept.callback(interaction)
        self.assertEqual((await self.store.session(offer['id']))['status'],'active')
        with self.assertRaises(ValueError):await view.decline.callback(interaction)

    async def test_both_final_players_inactive_cancels_without_clan_result(self):
        row=await self.war(size=3);st=row['state'];st['participants']=[[1,2,3],[6,7,8]]
        st['eliminated']=[[1,2],[6,7]];st['sends']=[3,8];st['stage']='teams';await self.save(row)
        await self.store.expire_war(row['id'],at=max(st['deadlines'])+1)
        self.assertEqual((await self.store.session(row['id']))['status'],'cancelled')
        self.assertEqual((await self.store.clan(user_id=1))['losses'],0)

    async def test_refresh_preserves_active_war_and_handles_sealed_sessions(self):
        from hiq.cogs.utility import UtilityCog
        from types import SimpleNamespace
        row=await self.war()
        with self.assertRaises(ValueError):await self.store.refresh_session(row['id'],1,broken=True)
        draft=await SealedStore(self.db).create([dict(id=i,name=str(i)) for i in (1,2)],100)
        cog=UtilityCog(SimpleNamespace(db=self.db,get_cog=lambda name:None))
        cog._message_missing=AsyncMock(return_value=True)
        context=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        await cog.refresh.callback(cog,context)
        self.assertEqual((await self.store.session(row['id']))['status'],'active')
        self.assertEqual((await self.store.session(draft['id']))['status'],'drafting')


class PureTests(unittest.TestCase):
    def gear_engine(self,left_key=None,right_key=None):
        base=CHARACTERS[0]
        def specs(owner,key):
            specs=team_specs('gear',owner,[c.id for c in CHARACTERS[:6]])
            return [(replace(card,equipment_key=key if i==0 else None),replace(char,name=f'Test{owner}-{i}',abilities={k:AbilityDef('','') for k in ('U','A','B','C')},stats=dict(hp=1000,pow=60,qik=0,def_=20,rec=20,spd=20,jmp=20,**{'def':20})),lane) for i,(card,char,lane) in enumerate(specs)]
        engine=BattleEngine(specs(1,left_key),specs(2,right_key),seed=1);engine.turn=1
        for unit in engine.units:unit.max_cd=unit.cd=99
        context=CombatContext(engine,engine.team_a.units[0],engine.team_b.units[0],engine.team_a.units[0],engine.team_b.units[0])
        return engine,context

    def test_firm_contact_once_per_attack_including_brave(self):
        totals=[]
        for gear in (None,'firm_contact'):
            engine,context=self.gear_engine(gear);engine.attacks_twice=lambda ctx,u:True
            engine.attack_action(context,context.attacker,context.defender)
            first=context.attacker.damage_dealt
            engine.attack_action(context,context.attacker,context.defender,followup=True)
            totals.append((first,context.attacker.damage_dealt))
        self.assertEqual(totals[1][0]-totals[0][0],3)
        self.assertEqual(totals[1][1]-totals[0][1],6)

    def test_loaded_reception_uses_def_rec_only_when_defending(self):
        from collections import defaultdict
        engine,context=self.gear_engine(right_key='loaded_reception');u=context.defender
        for initiated in (False,True):
            mods=defaultdict(int);engine.equipment_mods(context,u,context.attacker,initiated,mods)
            self.assertEqual(mods['def'],u.max_cd+2 if not initiated else 0)
            self.assertEqual(mods['rec'],u.max_cd+2 if not initiated else 0)
            self.assertEqual(mods['pow'],0);self.assertEqual(mods['qik'],0)

    def test_disruptive_whistle_targets_enemy_row_and_expires(self):
        engine,context=self.gear_engine('disruptive_whistle')
        engine.equipment_after_combat(context)
        for unit in engine.team_b.units:
            expected=-4 if unit.lane=='front' else 0
            self.assertEqual(unit.visible_penalty('pow'),expected)
            self.assertEqual(unit.visible_penalty('qik'),expected)
            unit.actions_taken+=1;unit.expire_after_action()
            self.assertEqual(unit.visible_penalty('pow'),0)

    def test_rock_solid_plan_cardinal_strict_rec_comparison(self):
        engine,context=self.gear_engine('rock_solid_game_plan');u=context.attacker
        u.base['rec']=40;targets=engine.cardinal_foes(u)
        for foe in engine.team_b.units:foe.base['rec']=20
        targets[-1].base['rec']=40
        engine.equipment_turn_start(u)
        for foe in engine.team_b.units:
            expected=-3 if foe in targets and foe.base['rec']<40 else 0
            self.assertEqual(foe.visible_penalty('pow'),expected);self.assertEqual(foe.visible_penalty('qik'),expected)

    def test_asynchronous_pack_passing_every_player_count(self):
        for n in range(2,8):
            for seed in range(12):
                rng=random.Random(seed);st=make_draft([dict(id=i,name=str(i)) for i in range(n)],list(CHAR_BY_ID),rng)
                while st['stage']=='drafting':
                    ready=[uid for uid in st['order'] if st['queues'][str(uid)]];uid=rng.choice(ready)
                    pid=st['queues'][str(uid)][0];cards=st['packs'][pid];pick(st,uid,pid,len(cards),rng.choice(cards))
                self.assertTrue(all(len(p)==8 for p in st['picks'].values()))
                self.assertTrue(all(len({CHAR_BY_ID[cid].name for cid in picks})>=6 for picks in st['picks'].values()))
                all_picks=[cid for picks in st['picks'].values() for cid in picks];self.assertEqual(len(set(all_picks)),n*8)

    def test_bracket_byes_for_2_to_7(self):
        for n in range(2,8):
            pairs=bracket(list(range(n)),random.Random(1));slots=1<<(n-1).bit_length()
            self.assertEqual(sum(b is None for a,b in pairs),slots-n)
            self.assertEqual(sorted(p for pair in pairs for p in pair if p is not None),list(range(n)))

    def test_exact_drop_distributions_and_extreme_rarity(self):
        rng=random.Random(42)
        for difficulty in DROP_WEIGHTS:
            counts={n:0 for n in DROP_WEIGHTS[difficulty]};stars={r:0 for r in RARITY_WEIGHTS[difficulty]}
            for _ in range(20000):counts[drop_count(rng,difficulty)]+=1;stars[choose_character(rng,CHARACTERS,difficulty).rarity]+=1
            for n,p in DROP_WEIGHTS[difficulty].items():self.assertAlmostEqual(counts[n]/20000,p,delta=.015)
            for n,p in RARITY_WEIGHTS[difficulty].items():self.assertAlmostEqual(stars[n]/20000,p,delta=.015)

    def test_extreme_teams_and_legacy_teams(self):
        for theme in ('tactics','tempo','offense','defense','recovery'):
            specs=cpu_specs(6,team_index(6,theme,0,'extreme'),'extreme')
            self.assertEqual(len(specs),6);self.assertTrue(all(c.merge_level==10 and c.equipment_key for c,ch,lane in specs))
        recovery=cpu_specs(6,team_index(6,'recovery',0,'extreme'),'extreme')
        self.assertIn('Kiryu — Last Hope',[ch.id for c,ch,lane in recovery])
        with self.assertRaises(ValueError):team_index(3,'tactics',0,'extreme')
        for fmt in (3,6):
            easy=cpu_specs(fmt,0,'easy');hard=cpu_specs(fmt,0,'hard')
            self.assertTrue(all(c.merge_level==0 for c,ch,lane in easy))
            self.assertEqual([ch.id for c,ch,lane in easy],[ch.id for c,ch,lane in hard])


if __name__=='__main__':unittest.main(verbosity=2)
