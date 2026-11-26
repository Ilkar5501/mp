import asyncio
import random
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from test_event_modes import specs
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.battle.nisekyu import CHITOGE,TSUGUMI,KOSAKI,MARIKA,KEY
from hiq.data import CHARACTERS,CHAR_BY_ID
from hiq.database import Database
from hiq.economy import pull_pack,pull_starter_daily
from hiq.event_modes import TinyTitansEngine,cpu_specs,EVENT_TEAMS,AllForOneEngine
from hiq.items import ITEM_BY_KEY,can_equip
from hiq.nisekyu import activate,active,window,shard_characters,pull_nisekyu,RETURNING_IDS
from hiq.pack_store import PackStore
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance

FOUR=[CHITOGE,TSUGUMI,KOSAKI,MARIKA]
START=datetime(2026,11,26,18,tzinfo=timezone.utc)

def duel(own,foe='Kai',**kwargs):
    e=BattleEngine(specs([own]),specs([foe]),seed=1,**kwargs)
    a,d=e.team_a.units[0],e.team_b.units[0]
    return e,a,d,CombatContext(e,a,d,a,d)


class NisekyuCombatTests(unittest.TestCase):
    def test_four_unit_opening_excludes_kosaki_and_enforces_team_cap(self):
        e=BattleEngine(specs(FOUR),specs(['Kai']),seed=1);e.turn=1
        kosaki=e.team_a.units[2];e.nisekyu_turn_start(kosaki)
        self.assertEqual([u.char_id for u in e.team_a.units if e.has_key(u)],[CHITOGE,TSUGUMI,MARIKA])
        e.nisekyu_turn_start(kosaki)
        self.assertFalse(e.has_key(kosaki))
        self.assertFalse(e.grant_key(kosaki,kosaki,'B'))

    def test_three_unit_opening_includes_kosaki(self):
        e=BattleEngine(specs([CHITOGE,TSUGUMI,KOSAKI]),specs(['Kai']),seed=1);e.turn=1
        e.nisekyu_turn_start(e.team_a.units[2])
        self.assertTrue(all(e.has_key(u) for u in e.team_a.units))

    def test_regular_key_prioritizes_chitoge_then_highest_active_offense(self):
        e=BattleEngine(specs([KOSAKI,CHITOGE,'Ushijima']),specs(['Kai']),seed=1);e.turn=2
        k,c,u=e.team_a.units;e.nisekyu_turn_start(k);self.assertTrue(e.has_key(c))
        e.nisekyu_turn_start(k);self.assertTrue(e.has_key(u));self.assertFalse(e.has_key(k))

    def test_existing_keys_count_against_opening_grant(self):
        e=BattleEngine(specs(FOUR+['Kai']),specs(['Kai']),seed=1);e.turn=1
        e.grant_key(e.team_a.units[0],e.team_a.units[-1],'C')
        e.nisekyu_turn_start(e.team_a.units[2])
        self.assertEqual(sum(e.has_key(u) for u in e.team_a.units),3)

    def test_key_is_not_cleansed_and_is_consumed_once_after_initiating(self):
        e,a,d,ctx=duel(CHITOGE);e.grant_key(a,a,'C');a.add_debuff('spd',-3);a.clear_debuffs()
        self.assertTrue(e.has_key(a));self.assertTrue(e.nisekyu_promise(ctx,a))
        e.nisekyu_after_combat(ctx);self.assertFalse(e.has_key(a))
        e.grant_key(a,a,'C');e.nisekyu_after_combat(ctx);self.assertTrue(e.has_key(a))

    def test_defending_does_not_consume_key(self):
        e,a,d,ctx=duel('Kai',CHITOGE);e.grant_key(d,d,'C');e.nisekyu_after_combat(ctx)
        self.assertTrue(e.has_key(d));self.assertFalse(e.nisekyu_promise(ctx,d))

    def test_promise_ribbon_checks_key_at_initiation_and_lasts_for_combat(self):
        e,a,d,ctx=duel(CHITOGE);e.grant_key(a,a,'C');e.nisekyu_combat_start(ctx)
        a.statuses.pop(KEY);self.assertTrue(e.nisekyu_promise(ctx,a))
        e,a,d,ctx=duel(CHITOGE);e.nisekyu_combat_start(ctx);e.grant_key(a,a,'C')
        self.assertFalse(e.nisekyu_promise(ctx,a))

    def test_tsugumi_guaranteed_save_has_once_per_turn_limit_and_front_requirement(self):
        e=BattleEngine(specs(['Kai']),specs([CHITOGE,TSUGUMI]),seed=1);e.turn=1
        a=e.team_a.units[0];c,t=e.team_b.units;t.lane='front'
        e.rng=Mock(random=Mock(return_value=.99))
        self.assertEqual(e.maybe_save(a,c),(t,True));self.assertEqual(e.maybe_save(a,c),(c,False))
        e.turn=2;t.lane='back';self.assertEqual(e.maybe_save(a,c),(c,False))
        t.lane='front';self.assertEqual(e.maybe_save(a,c),(t,True))

    def test_tsugumi_keyed_ally_save_uses_fifty_percent_and_does_not_spend_failed_roll(self):
        e=BattleEngine(specs(['Kai']),specs(['Aran',TSUGUMI]),seed=1);e.turn=1
        a=e.team_a.units[0];ally,t=e.team_b.units;t.lane='front';e.grant_key(t,ally,'B')
        e.rng=Mock(random=Mock(side_effect=[.50,.49]))
        self.assertEqual(e.maybe_save(a,ally),(ally,False));self.assertEqual(e.maybe_save(a,ally),(t,True))

    def test_anti_save_prevents_bodyguard(self):
        for foe in ('Kiryu — Reluctant Titan','Fukunaga'):
            e=BattleEngine(specs([foe]),specs([CHITOGE,TSUGUMI]),seed=1)
            c,t=e.team_b.units;t.lane='front'
            self.assertEqual(e.maybe_save(e.team_a.units[0],c),(c,False))
        e=BattleEngine(specs(['Kai']),specs([CHITOGE,TSUGUMI]),seed=1)
        c,t=e.team_b.units;t.lane='front';c.add_status('Free Ball',False)
        self.assertEqual(e.maybe_save(e.team_a.units[0],c),(c,False))

    def test_save_grants_chitoge_key_bodyguard_stats_counter_and_postcombat_buffs(self):
        e=BattleEngine(specs(['Kai']),specs([CHITOGE,TSUGUMI]),seed=1)
        a=e.team_a.units[0];c,t=e.team_b.units;t.lane='front'
        ctx=CombatContext(e,a,t,a,c,save_triggered=True)
        e.nisekyu_combat_start(ctx);self.assertTrue(e.has_key(c));self.assertTrue(e.nisekyu_counter(ctx,t))
        mods={s:0 for s in ('pow','qik','def','rec','spd','jmp')};e.nisekyu_mods(ctx,t,mods)
        self.assertEqual((mods['qik'],mods['def'],mods['spd']),(5,5,5))
        e.nisekyu_after_combat(ctx)
        for s in ('pow','qik','spd'):self.assertEqual(c.visible_bonus(s),4)
        self.assertFalse(t.visible_buffs)

    def test_bodyguard_does_not_get_save_stats_in_ordinary_combat(self):
        e,a,d,ctx=duel(TSUGUMI);mods={s:0 for s in ('pow','qik','def','rec','spd','jmp')}
        e.nisekyu_mods(ctx,a,mods);self.assertEqual(sum(mods.values()),0)
        self.assertFalse(e.nisekyu_counter(ctx,a))

    def test_chitoge_key_removes_percentage_reduction_but_keeps_flat_reduction(self):
        results=[]
        for keyed in (False,True):
            e,a,d,ctx=duel(CHITOGE,MARIKA);a.cd=99;d.cd=0
            if keyed:e.grant_key(a,a,'C')
            e.rng=Mock(random=Mock(return_value=.999))
            stats={a.uid:{'pow':100,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':0,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0}}
            with patch.object(e,'combat_stats',return_value=stats),patch.object(e,'expansion_flat_reduction',return_value=5):e.resolve_hit(ctx,a,d)
            results.append(a.damage_dealt)
        self.assertEqual(results,[55,95])

    def test_chitoge_ultimate_damage_and_key_penetration(self):
        for keyed,expected in ((False,90),(True,108)):
            e,a,d,ctx=duel(CHITOGE);a.cd=0;d.cd=99
            if keyed:e.grant_key(a,a,'C')
            e.rng=Mock(random=Mock(return_value=.999))
            stats={a.uid:{'pow':100,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':0,'qik':0,'def':40,'rec':40,'spd':0,'jmp':0}}
            with patch.object(e,'combat_stats',return_value=stats):e.resolve_hit(ctx,a,d)
            self.assertEqual(a.damage_dealt,expected)

    def test_tsugumi_ultimate_uses_actual_combat_speed(self):
        e,a,d,ctx=duel(TSUGUMI);a.cd=0;d.cd=99;e.rng=Mock(random=Mock(return_value=.999))
        stats={a.uid:{'pow':0,'qik':50,'def':0,'rec':0,'spd':50,'jmp':0},d.uid:{'pow':0,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0}}
        with patch.object(e,'combat_stats',return_value=stats):e.resolve_hit(ctx,a,d)
        self.assertEqual(a.damage_dealt,70)

    def test_kosaki_heals_after_ultimate_and_self_penalty_lasts_through_next_action(self):
        e,a,d,ctx=duel(KOSAKI);a.cd=0;d.cd=99;e.team_a.hp=10;a.action_in_progress=True
        e.rng=Mock(random=Mock(return_value=.999))
        stats={a.uid:{'pow':0,'qik':50,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':0,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0}}
        with patch.object(e,'combat_stats',return_value=stats):e.resolve_hit(ctx,a,d)
        self.assertEqual(e.team_a.hp,35);e.nisekyu_after_combat(ctx);self.assertEqual(e.team_a.hp,43)
        a.actions_taken+=1;a.action_in_progress=False;a.expire_after_action();self.assertEqual(a.visible_penalty('spd'),-3)
        a.actions_taken+=1;a.expire_after_action();self.assertEqual(a.visible_penalty('spd'),0)

    def test_marika_counter_ultimate_and_new_postcombat_key_survives(self):
        e,a,d,ctx=duel(MARIKA);a.ult_triggered_this_combat=True
        self.assertTrue(e.nisekyu_counter(ctx,a));self.assertFalse(e.offensive_ult_ready(a,d,ctx))
        a.cd=0;self.assertTrue(e.defensive_ult_ready(a,d,ctx))
        e.grant_key(a,a,'B');e.nisekyu_after_combat(ctx);self.assertTrue(e.has_key(a))

    def test_marika_defense_scales_to_three_keyed_allies(self):
        e=BattleEngine(specs(FOUR),specs(['Kai']),seed=1);m=e.team_a.units[3]
        for u in e.team_a.units[:3]:e.grant_key(m,u,'B')
        a=e.team_b.units[0];ctx=CombatContext(e,a,m,a,m);mods={s:0 for s in ('pow','qik','def','rec','spd','jmp')}
        e.nisekyu_mods(ctx,m,mods);self.assertEqual(mods['def'],6);self.assertEqual(mods['rec'],9)

    def test_marika_end_turn_key_obeys_hp_threshold_and_team_limit(self):
        e=BattleEngine(specs([MARIKA,'Kai']),specs(['Kai']),seed=1);m,ally=e.team_a.units
        e.nisekyu_end_turn();self.assertFalse(e.has_key(ally))
        e.team_a.hp=e.team_a.max_hp*3//4;e.nisekyu_end_turn();self.assertTrue(e.has_key(ally));self.assertFalse(e.has_key(m))

    def test_marika_halves_semis_each_precombat_hit_without_stacking(self):
        damage=[]
        for names in (['Kai'],['Kai',MARIKA]):
            e=BattleEngine(specs(['Semi']),specs(names),seed=1);a=e.team_a.units[0];a.base['qik']=100;a.cd=0
            for u in e.team_b.units:u.base['rec']=0
            d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d);e.semi_precombat(ctx)
            damage.append(d.damage_taken)
        self.assertEqual(damage,[70,35])

    def test_locket_doubles_actual_bond_at_every_team_size_and_never_stacks(self):
        for size in (1,2,3,4):
            for count in (1,2):
                team=specs(FOUR[:size])
                from dataclasses import replace
                team=[(replace(card,equipment_key='raku_s_locket' if i<count else None),c,lane) for i,(card,c,lane) in enumerate(team)]
                e=BattleEngine(team,specs(['Kai']),seed=1)
                for u in e.team_a.units:
                    # specs uses +0 cards; only same-school bond changes these stats.
                    self.assertEqual(u.base['def']-u.character.stats['def'],2*(size-1))

    def test_locket_only_cleanses_wearer_on_each_bonyari_action_and_keeps_key(self):
        e=BattleEngine(specs([CHITOGE,TSUGUMI,'Kai'],item='raku_s_locket'),specs(['Kai']),seed=1)
        wearer,ally,outsider=e.team_a.units
        # Only the first member wears it in this test.
        ally.card.equipment_key=None;outsider.card.equipment_key=None
        wearer.add_debuff('spd',-3);ally.add_debuff('spd',-3);e.grant_key(wearer,wearer,'C')
        e.rng=Mock(random=Mock(return_value=0));e.nisekyu_action_start(outsider);self.assertEqual(e.rng.random.call_count,0)
        e.nisekyu_action_start(ally);self.assertFalse(wearer.visible_debuffs);self.assertTrue(ally.visible_debuffs);self.assertTrue(e.has_key(wearer))
        wearer.add_debuff('spd',-3);e.rng.random.return_value=2/3;e.nisekyu_action_start(wearer);self.assertTrue(wearer.visible_debuffs)

    def test_new_challenge_is_legal_all_ten_merges_and_runs(self):
        idx=len(EVENT_TEAMS['tiny_titans'])-1;team=cpu_specs('tiny_titans',idx)
        self.assertEqual([lane for _,_,lane in team],['front']*3+['back']*3)
        for card,char,_ in team:
            self.assertEqual(card.merge_level,10);self.assertTrue(can_equip(ITEM_BY_KEY[card.equipment_key],char))
        result=TinyTitansEngine(team,team,seed=8).run()
        self.assertGreater(result.turns,0)

    def test_kosaki_af1_support_keys_active_main_instead_of_inactive_supports(self):
        e=AllForOneEngine(specs(['Kai',KOSAKI,CHITOGE,MARIKA]),specs(['Kai']),seed=1);e.turn=1
        e.support_turn(e.team_a.units[1]);self.assertTrue(e.has_key(e.team_a.units[0]))
        self.assertFalse(any(e.has_key(u) for u in e.team_a.units[1:]))


class NisekyuPackTests(unittest.TestCase):
    def test_pack_probabilities_and_all_ten_independent_draws(self):
        rng=random.Random(22);counts={'bonyari':0,'returning':0,'other':0,'lower':0,'five':0}
        for _ in range(5000):
            cards=pull_nisekyu(rng);self.assertEqual(len(cards),10)
            for c in cards:
                category='bonyari' if c.event_pack else 'returning' if c.id in RETURNING_IDS else 'other'
                counts[category]+=1
                if category=='returning':counts['five' if c.rarity==5 else 'lower']+=1
                if category=='other':self.assertFalse(c.is_duo or c.event_pack)
        for k,expected in (('bonyari',.25),('returning',.50),('other',.25)):
            self.assertAlmostEqual(counts[k]/50000,expected,delta=.012)
        self.assertAlmostEqual(counts['lower']/counts['returning'],.6,delta=.015)

    def test_normal_and_starter_packs_cannot_pull_event_cards(self):
        rng=random.Random(1)
        for _ in range(200):
            for c in pull_pack('base',rng=rng)+pull_pack('position',position='S',rng=rng)+pull_starter_daily(rng):self.assertFalse(c.event_pack)


class NisekyuStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'event.sqlite3');await self.db.init()
        await self.db.ensure_user(1);await self.db.set_user_fields(1,coins=2000)
        self.store=PackStore(self.db)
        async with self.db.connect() as c:await activate(c,START);await c.commit()

    async def asyncTearDown(self):self.tmp.cleanup()

    async def test_window_lasts_exactly_96_hours_and_restarts_do_not_extend_it(self):
        async with self.db.connect() as c:
            before=await window(c);await activate(c,START+timedelta(days=2));after=await window(c)
            self.assertEqual(before,after);self.assertEqual(after[1]-after[0],timedelta(hours=96))
            self.assertTrue(await active(c,START));self.assertTrue(await active(c,after[1]-timedelta(microseconds=1)))
            self.assertFalse(await active(c,after[1]));self.assertFalse(await active(c,START-timedelta(microseconds=1)))

    async def test_pack_issues_ten_cards_charges_five_hundred_and_does_not_touch_pity(self):
        await self.db.set_pity(1,CHITOGE,armed=True)
        with patch('hiq.nisekyu.utcnow',return_value=START):opened=await self.store.open_nisekyu(1,'Player')
        self.assertEqual(len(opened['cards']),10);self.assertEqual(opened['coins'],1500)
        self.assertTrue((await self.db.get_pity(1))['armed'])

    async def test_closed_sale_cannot_charge_or_issue_anything(self):
        with patch('hiq.nisekyu.utcnow',return_value=START+timedelta(hours=96)):
            with self.assertRaisesRegex(ValueError,'closed'):await self.store.open_nisekyu(1,'Player')
        self.assertEqual((await self.db.get_user(1)).coins,2000);self.assertFalse(await self.db.get_cards(1))

    async def test_partial_issue_failure_rolls_back_payment_serials_and_all_cards(self):
        original=self.store._issue_card;calls=0
        async def fail(*args):
            nonlocal calls
            calls+=1
            if calls==3:raise RuntimeError('injected')
            return await original(*args)
        with patch('hiq.nisekyu.utcnow',return_value=START),patch.object(self.store,'_issue_card',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'injected'):await self.store.open_nisekyu(1,'Player')
        self.assertEqual((await self.db.get_user(1)).coins,2000);self.assertFalse(await self.db.get_cards(1))
        async with self.db.connect() as c:self.assertEqual((await (await c.execute('SELECT count(*) FROM card_serials')).fetchone())[0],0)

    async def test_concurrent_purchases_cannot_overspend(self):
        await self.db.set_user_fields(1,coins=500)
        with patch('hiq.nisekyu.utcnow',return_value=START):
            results=await asyncio.gather(self.store.open_nisekyu(1,'Player'),self.store.open_nisekyu(1,'Player'),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertEqual(len(await self.db.get_cards(1)),10)
        self.assertEqual((await self.db.get_user(1)).coins,0)

    async def test_character_shards_stop_at_exact_deadline(self):
        async with self.db.connect() as c:
            with patch('hiq.nisekyu.utcnow',return_value=START):during=await shard_characters(c)
            with patch('hiq.nisekyu.utcnow',return_value=START+timedelta(hours=96)):after=await shard_characters(c)
        self.assertEqual(sum(bool(ch.event_pack) for ch in during),4);self.assertFalse(any(ch.event_pack for ch in after))

    async def test_event_character_crafting_is_blocked_even_with_shards(self):
        store=RaidStore(self.db)
        async with self.db.connect() as c:await credit(c,1,'character_shard',CHITOGE,100);await c.commit()
        with self.assertRaisesRegex(ValueError,'exclusive'):await store.craft(1,'Player',CHITOGE,'character')
        with self.assertRaisesRegex(ValueError,'exclusive'):await store.craft_sources(1,CHAR_BY_ID[CHITOGE])
        await store.craft_all_items(1,'Player');self.assertFalse(await self.db.get_cards(1))

    async def test_locket_combines_bonyari_shards_and_recycles_for_one_hundred(self):
        async with self.db.connect() as c:
            await credit(c,1,'item_shard','raku_s_locket',25)
            await credit(c,1,'character_shard',TSUGUMI,10);await credit(c,1,'character_shard',MARIKA,15);await c.commit()
        iid=await RaidStore(self.db).craft(1,'Player','raku_s_locket')
        self.assertTrue(iid);self.assertEqual((await self.db.get_user(1)).coins,1500)
        async with self.db.connect() as c:
            self.assertEqual(await balance(c,1,'character_shard',TSUGUMI),0);self.assertEqual(await balance(c,1,'character_shard',MARIKA),0)
        self.assertEqual(ITEM_BY_KEY['raku_s_locket']['recycle_value'],100)

    async def test_locket_missing_character_shards_preserves_item_shards_and_coins(self):
        async with self.db.connect() as c:await credit(c,1,'item_shard','raku_s_locket',25);await c.commit()
        with self.assertRaisesRegex(ValueError,'Bonyari High'):await RaidStore(self.db).craft(1,'Player','raku_s_locket')
        async with self.db.connect() as c:self.assertEqual(await balance(c,1,'item_shard','raku_s_locket'),25)
        self.assertEqual((await self.db.get_user(1)).coins,2000)

    async def test_standard_starpack_and_old_pity_cannot_bypass_event_exclusivity(self):
        await self.db.set_pity(1,CHITOGE,armed=True)
        async with self.store.tx() as c:iid=await self.store._issue_item(c,1,'starpack',stars=3)
        with patch('hiq.community_store.random.choice',side_effect=lambda pool:next(ch for ch in pool if not ch.event_pack)) as choose:
            cards=await self.store.open_item(1,'Player',iid)
        self.assertFalse(CHAR_BY_ID[cards[0].character_id].event_pack)
        self.assertFalse(any(ch.event_pack for ch in choose.call_args.args[0]))

    async def test_item_browser_and_pooled_crafting_ui_fit_discord_limits(self):
        from hiq.cogs.item_browser import ItemBrowser
        from hiq.cogs.crafting import CraftMenu
        from hiq.items import ITEMS,SIGNATURE_ITEMS
        browser=ItemBrowser(SimpleNamespace(db=self.db),1,ITEMS+SIGNATURE_ITEMS,{})
        for page in range(len(browser.result_pages)):
            browser.page=page;self.assertLessEqual(len(browser.embed().description),4096)
        async with self.db.connect() as c:
            await credit(c,1,'character_shard',TSUGUMI,10);await credit(c,1,'character_shard',MARIKA,15);await c.commit()
        menu=await CraftMenu.create(SimpleNamespace(db=self.db),1,'Player');menu.owned_only=False;menu.build()
        recipe=next(r for r in menu.recipes() if r['target']=='raku_s_locket')
        self.assertEqual(recipe['requirements']['Character'],(25,25))
        menu.category='characters';self.assertFalse(any(r['target'] in FOUR for r in menu.recipes()))

    async def test_event_pack_embed_uses_small_thumbnail_and_lists_all_ten_cards(self):
        from hiq.cogs.economy import EconomyCog
        from hiq.nisekyu import PACK_IMAGE
        from unittest.mock import AsyncMock
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,name='Player'),send=AsyncMock())
        cog=EconomyCog(SimpleNamespace(db=self.db))
        with patch('hiq.nisekyu.utcnow',return_value=START):await EconomyCog.pack_nisekyu.callback(cog,ctx)
        embed=ctx.send.call_args.kwargs['embed']
        self.assertEqual(embed.thumbnail.url,PACK_IMAGE);self.assertFalse(embed.image.url)
        self.assertEqual(embed.description.count('**ID:**'),10)
        self.assertLessEqual(len(embed.description),4096);self.assertLessEqual(len(embed),6000)
