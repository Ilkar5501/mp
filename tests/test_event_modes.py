import asyncio,json,tempfile,unittest
from pathlib import Path
from dataclasses import replace,asdict
from unittest.mock import Mock,patch
from types import SimpleNamespace
from unittest.mock import AsyncMock
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.contest import character
from hiq.sealed import team_specs
from hiq.event_modes import TinyTitansEngine,AllForOneEngine,EVENT_TEAMS,cpu_specs,boosted_character
from hiq.event_store import EventStore
from hiq.database import Database
from hiq.items import ITEM_BY_KEY,ITEMS,can_equip
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance

KEY='suga_mapo_tofu_bowls'
def specs(names,item=None):
    ids=[character(n.split(' — ')[0],n.split(' — ')[1] if ' — ' in n else None).id for n in names]
    rows=team_specs('events',1,ids)
    if item:rows[0]=(replace(rows[0][0],equipment_key=item),rows[0][1],rows[0][2])
    return rows

class EventEngineTests(unittest.TestCase):
    def test_tiny_titans_examples_and_unused_offense(self):
        for name,expected in [('Suga',(164,55,34,34,39,35)),('Lev',(160,60,40,33,43,44)),('Aran',(180,57,30,29,32,41))]:
            c=character(name);original=dict(c.stats);b=boosted_character(c)
            self.assertEqual(tuple(b.stats[k] for k in ('hp',c.offense_key,'def','rec','spd','jmp')),expected)
            self.assertEqual(b.stats['qik' if c.offense_key=='pow' else 'pow'],0)
            self.assertEqual(c.stats,original)
        self.assertEqual(boosted_character(character('Iizuna')).stats,character('Iizuna').stats)

    def test_fukunaga_allowed_in_titans_banned_in_af1(self):
        a=specs(['Fukunaga','Suga','Lev','Kai','Kita','Daichi']);b=cpu_specs('tiny_titans',0)
        TinyTitansEngine(a,b)
        with self.assertRaisesRegex(ValueError,'Fukunaga'):AllForOneEngine(a[:1],b[:1])
        with self.assertRaisesRegex(ValueError,'Fukunaga'):AllForOneEngine(specs(['Iizuna','Fukunaga']),b[:1])

    def test_hp_is_six_times_main_and_supports_never_fight(self):
        a=cpu_specs('all_for_one',5);b=cpu_specs('all_for_one',6)
        e=AllForOneEngine(a,b,seed=17)
        self.assertEqual(e.team_a.max_hp,e.team_a.units[0].base['hp'])
        normal=BattleEngine(a,b)
        self.assertEqual(e.team_a.max_hp,normal.team_a.units[0].base['hp']*6)
        result=e.run()
        for t in e.teams.values():
            for u in t.units[1:]:
                self.assertEqual((u.actions_taken,u.attacks_made,u.attacks_tanked),(0,0,0))
                self.assertFalse(u.ult_triggered_this_combat)

    def test_rows_have_no_capacity_and_item_supports_are_inactive(self):
        a=[(replace(card,equipment_key='burden_of_expectation'),c,'back') for card,c,_ in cpu_specs('all_for_one',6)]
        e=AllForOneEngine(a,a)
        self.assertEqual(len(e.team_a.backs()),7)
        self.assertTrue(all(u.card.equipment_key is None for u in e.team_a.units[1:]))

    def test_pt_targets_himself_every_turn(self):
        e=AllForOneEngine(specs(['Kageyama — Prodigious Talent','Suga']),specs(['Iizuna']),seed=3)
        u=e.team_a.units[0]
        for _ in range(3):
            e.start_turn();self.assertIn('Open Play',u.statuses)
            u.statuses.pop('Open Play');e.end_turn()
        normal=BattleEngine(specs(['Kageyama — Prodigious Talent']),specs(['Iizuna']))
        normal.start_turn();self.assertNotIn('Open Play',normal.team_a.units[0].statuses)

    def test_guiding_break_stacks_are_targetable_damage_and_refund(self):
        e=AllForOneEngine(specs(['Iizuna']),specs(['Kanoka']),seed=1)
        u=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,u,d,u,d)
        self.assertTrue(e.inflict_guiding_break(u,d));self.assertTrue(e.inflict_guiding_break(u,d))
        self.assertFalse(e.inflict_guiding_break(u,d));self.assertEqual(e.guiding_break_count(e.team_b),2)
        self.assertTrue(e.attack_target_allowed(d));self.assertIs(e.choose_target(u)[0],d)
        st=e.combat_stats(ctx);self.assertEqual(e.anchor_damage_bonus(ctx,u,st),int(st[u.uid]['rec']*.2))
        u.cd=3;e.anchor_after_combat(ctx);self.assertEqual(u.cd,2)
        d.actions_taken+=1;d.expire_after_action();self.assertEqual(e.guiding_break_count(e.team_b),0)
        e.inflict_guiding_break(u,d);d.clear_debuffs();self.assertEqual(e.guiding_break_count(e.team_b),0)
        n=BattleEngine(specs(['Iizuna']),specs(['Kanoka']));n.inflict_guiding_break(n.team_a.units[0],n.team_b.units[0]);self.assertFalse(n.attack_target_allowed(n.team_b.units[0]))

    def test_nozawa_support_can_neutralize_enemy_school_bonds(self):
        e=AllForOneEngine(specs(['Aone','Koganegawa']),specs(['Iizuna','Nozawa']))
        a,d=e.team_a.units[0],e.team_b.units[0]
        self.assertEqual(e.expansion_bond_penalty(CombatContext(e,a,d,a,d),a),1)

    def test_shibayama_counts_inactive_allies_for_nekoma_main(self):
        e=AllForOneEngine(cpu_specs('all_for_one',0),specs(['Iizuna']))
        main,source=e.team_a.units[:2]
        for u in e.team_a.units:u.base['rec']=100;u.base['def']=0
        e.support_turn(source)
        for stat in ('pow','qik','def','rec','spd','jmp'):self.assertEqual(main.visible_bonus(stat),4)

    def test_cpu_pools_match_confirmed_challenges_and_main_merges(self):
        self.assertEqual([r['name'] for r in EVENT_TEAMS['tiny_titans']], ["Suga's Challenge","Lev's Challenge","Aran's Challenge","Tanaka's Challenge"])
        for i in range(4):self.assertTrue(all(card.merge_level==10 for card,_,_ in cpu_specs('tiny_titans',i)))
        for i in range(12):self.assertEqual(cpu_specs('all_for_one',i)[0][0].merge_level,10)

    def test_signature_is_exact_variant_and_raid_pool_stays_sixty(self):
        item=ITEM_BY_KEY[KEY];self.assertTrue(can_equip(item,character('Suga')))
        self.assertFalse(can_equip(item,replace(character('Suga'),id='Suga — Other Card',title='Other Card')))
        self.assertEqual(len(ITEMS),60)

    def test_playbook_initiation_cancels_the_penalty_without_extra_speed(self):
        changes=[]
        for item in (None,KEY):
            e=BattleEngine(specs(['Suga'],item),specs(['Lev']),seed=1)
            a,d=e.team_a.units[0],e.team_b.units[0]
            d.base['jmp']=0;ctx=CombatContext(e,a,d,a,d)
            changes.append(e.combat_stats(ctx)[a.uid])
        self.assertEqual(changes[1]['spd']-changes[0]['spd'],5)
        self.assertEqual(changes[1]['spd'],character('Suga').stats['spd'])
        for stat in ('pow','def','rec'):self.assertEqual(changes[1][stat]-changes[0][stat],3)
        e=BattleEngine(specs(['Lev']),specs(['Suga'],KEY));a,d=e.team_a.units[0],e.team_b.units[0]
        self.assertEqual(e.combat_stats(CombatContext(e,a,d,a,d))[d.uid]['spd'],d.base['spd']-5)

    def test_mapo_torch_two_distinct_recipients_including_self(self):
        e=BattleEngine(specs(['Suga','Lev','Kai'],KEY),specs(['Iizuna']),seed=1)
        u,ally,other=e.team_a.units;e.rng=Mock(sample=Mock(return_value=[u,ally]))
        e._turn_start_owner(u)
        self.assertEqual(u.visible_bonus('spd'),6);self.assertEqual(ally.visible_bonus('pow'),6);self.assertFalse(other.visible_buffs)
        solo=AllForOneEngine(specs(['Suga','Lev'],KEY),specs(['Iizuna']))
        solo._turn_start_owner(solo.team_a.units[0]);self.assertEqual(solo.team_a.units[0].visible_bonus('pow'),6)

    def test_every_cpu_formation_runs(self):
        for mode,cls in [('tiny_titans',TinyTitansEngine),('all_for_one',AllForOneEngine)]:
            for i,row in enumerate(EVENT_TEAMS[mode]):
                with self.subTest(mode=mode,index=i):
                    a=cpu_specs(mode,i)
                    for card,c,_ in a:
                        if card.equipment_key:self.assertTrue(can_equip(ITEM_BY_KEY[card.equipment_key],c))
                    e=cls(a,cpu_specs(mode,0),seed=i);r=e.run()
                    self.assertIn(r.winner_side,('A','B','draw'))

class EventStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'events.sqlite3');await self.db.init()
        await self.db.ensure_user(1);self.store=EventStore(self.db)
    async def asyncTearDown(self):self.tmp.cleanup()
    async def issue(self,names):
        async with self.store.tx() as c:return [await self.store._issue_card(c,1,'Player',character(n)) for n in names]
    async def team(self):
        cards=await self.issue(['Suga','Lev','Kai','Kita','Daichi','Reon','Aran'])
        row=await self.store.team(1)
        for i,card in enumerate(cards):row=await self.store.set_entry(1,1,i,card.instance_id,'front',row['revision'])
        await self.store.use(1,1);return row,cards
    async def test_saved_team_restart_rows_and_stale_editor(self):
        row,cards=await self.team();again=await EventStore(self.db).team(1)
        self.assertEqual(again['entries'],row['entries'])
        await self.store.set_entry(1,1,0,cards[0].instance_id,'back',row['revision'])
        with self.assertRaisesRegex(ValueError,'changed'):await self.store.set_entry(1,1,1,None,'back',row['revision'])

    async def test_main_only_team_is_usable_with_optional_supports(self):
        card=(await self.issue(['Iizuna']))[0];row=await self.store.team(1)
        await self.store.set_entry(1,1,0,card.instance_id,'back',row['revision'])
        await self.store.use(1,1)
        run=await self.store.start(1,'all_for_one',0)
        self.assertEqual(len(run['state']['a']),1)
    async def test_run_resume_and_rewards_settle_once(self):
        cards=await self.issue(['Suga','Lev','Kai','Kita','Daichi','Reon']);await self.db.ensure_saved_team(1,1)
        await self.db.replace_saved_team(1,1,cards[0].instance_id,cards[3].instance_id,cards[4].instance_id,front2_id=cards[1].instance_id,front3_id=cards[2].instance_id,back3_id=cards[5].instance_id,team_format=6)
        with patch('hiq.event_store.secrets.randbelow',return_value=0):row=await self.store.start(1,'tiny_titans',0)
        resumed=await EventStore(self.db).start(1,'tiny_titans',3);self.assertEqual(row['id'],resumed['id'])
        with self.assertRaises(ValueError):await self.store.start(1,'all_for_one',0)
        results=await asyncio.gather(*[self.store.finish(1,row['id'],{'winner_side':'A'}) for _ in range(2)])
        self.assertEqual(sum(results),1);self.assertEqual((await self.store.wallet(1))['shards'][KEY],1)
        with self.assertRaisesRegex(ValueError,'Next event'):await self.store.start(1,'tiny_titans',0)
    async def test_unique_recipe_is_atomic_and_uses_normal_craft(self):
        await self.store.wallet(1)
        async with self.store.tx() as c:
            await c.execute('UPDATE users SET coins=499 WHERE discord_id=1')
            await credit(c,1,'item_shard',KEY,25);await credit(c,1,'character_shard',character('Suga').id,25)
        with self.assertRaisesRegex(ValueError,'500 coins'):await self.store.craft(1,KEY)
        self.assertEqual((await self.store.wallet(1))['shards'][KEY],25)
        async with self.db.connect() as c:self.assertEqual(await balance(c,1,'character_shard',character('Suga').id),25)
        async with self.store.tx() as c:await c.execute('UPDATE users SET coins=500 WHERE discord_id=1')
        iid=await RaidStore(self.db).craft(1,'Player',KEY);self.assertTrue(iid)
        self.assertEqual((await self.db.get_user(1)).coins,0)
        self.assertEqual((await self.store.wallet(1))['shards'][KEY],0)

    async def test_no_drop_loss_and_af1_never_pay_shards(self):
        await self.team()
        for mode,drop,winner in [('tiny_titans',False,'A'),('tiny_titans',True,'B'),('all_for_one',True,'A')]:
            state=dict(reward_policy='signature_drop_v2',signature_key=KEY,drop_roll=drop)
            sid=mode+str(drop)
            async with self.store.tx() as c:
                await c.execute("INSERT INTO event_runs VALUES (?,?,?,'running',?,NULL,0,'2026-01-01')",(sid,1,mode,json.dumps(state)))
            self.assertEqual(await self.store.finish(1,sid,dict(winner_side=winner)),0)
        self.assertEqual((await self.store.wallet(1))['shards'][KEY],0)

    async def test_legacy_signature_shards_migrate_once(self):
        async with self.store.tx() as c:
            await c.execute('INSERT INTO event_signature_shards VALUES (?,?,?)',(1,KEY,7))
        await self.db.init();await self.db.init()
        self.assertEqual((await self.store.wallet(1))['shards'][KEY],7)
    async def test_af1_merge_and_card_removal_references(self):
        row,cards=await self.team();donor=(await self.issue(['Suga']))[0]
        async with self.store.tx() as c:await self.db._replace_merged_card_tx(c,cards[0].instance_id,donor.instance_id)
        row=await self.store.team(1);self.assertEqual(row['entries'][0]['id'],donor.instance_id)
        async with self.store.tx() as c:await self.db._clear_card_from_teams_tx(c,donor.instance_id)
        self.assertIsNone((await self.store.team(1))['entries'][0])

    async def test_titans_saved_normal_team_snapshot_and_abandon(self):
        cards=await self.issue(['Suga','Lev','Kai','Kita','Daichi','Reon'])
        await self.db.ensure_saved_team(1,1)
        await self.db.replace_saved_team(1,1,cards[0].instance_id,cards[3].instance_id,cards[4].instance_id,
            front2_id=cards[1].instance_id,front3_id=cards[2].instance_id,back3_id=cards[5].instance_id,team_format=6)
        row=await self.store.start(1,'tiny_titans',0)
        self.assertEqual(len(row['state']['a']),6)
        self.assertEqual(row['state']['a'][0]['character']['stats']['pow'],47)
        self.assertEqual(await self.store.abandon(1),1)
        self.assertEqual(await self.store.finish(1,row['id'],{'winner_side':'A'}),0)
        self.assertEqual((await self.store.wallet(1))['tt_tokens'],0)

    async def test_editor_menus_fit_discord_and_enforce_owner(self):
        from hiq.cogs.events import AF1TeamView,EventMenu
        row,cards=await self.team()
        cog=SimpleNamespace(bot=SimpleNamespace(db=self.db),store=self.store)
        ctx=SimpleNamespace(author=SimpleNamespace(id=1))
        views=[AF1TeamView(cog,1,row,cards),EventMenu(cog,ctx,'tiny_titans'),EventMenu(cog,ctx,'all_for_one')]
        for view in views:
            self.assertLess(len(view.embed()),6000)
            self.assertLessEqual(len(view.children),25)
            for child in view.children:
                if hasattr(child,'options'):self.assertLessEqual(len(child.options),25)
            i=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
            self.assertFalse(await view.interaction_check(i))
            i.response.send_message.assert_awaited_once()

    async def test_signature_catalogue_and_craft_menu_show_full_recipe(self):
        from hiq.cogs.item_browser import ItemBrowser
        from hiq.cogs.crafting import CraftMenu
        bot=SimpleNamespace(db=self.db)
        browser=ItemBrowser(bot,1,[ITEM_BY_KEY[KEY]],{})
        self.assertIn('50% per clear',browser.embed().description)
        self.assertIn('500 coins',browser.embed().description)
        menu=await CraftMenu.create(bot,1,'Player');menu.owned_only=False;menu.target=KEY;menu.build()
        selected=menu.selected()
        self.assertEqual(selected['requirements']['Coins'][1],500)
        self.assertTrue(next(c for c in menu.children if getattr(c,'label',None)=='Craft').disabled)
        self.assertIn('Passing the Torch',str(menu.embed().to_dict()))
