import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from hiq.database import Database
from hiq.data import CHARACTERS
from hiq.pack_store import PackStore
from hiq.prefecture import PodBattleEngine


class PackTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/'packs.sqlite3');await self.db.init()
        await self.db.ensure_user(1);await self.db.set_user_fields(1,coins=2000)
        self.store=PackStore(self.db)

    async def asyncTearDown(self):self.tmp.cleanup()

    async def snapshot(self):
        async with self.db.connect() as c:
            return {table:[tuple(row) for row in await (await c.execute(f'SELECT * FROM {table} ORDER BY 1')).fetchall()]
                    for table in ('users','cards','pity','card_serials')}

    async def test_simultaneous_daily_commands_issue_only_one_pack(self):
        from hiq.cogs.economy import EconomyCog
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,name='Player'),send=AsyncMock())
        # Separate database objects also exercise SQLite's transaction lock.
        cogs=[EconomyCog(SimpleNamespace(db=Database(self.db.path))) for _ in range(2)]
        await asyncio.gather(*(EconomyCog.daily.callback(cog,ctx) for cog in cogs))
        cards=await self.db.get_cards(1)
        self.assertEqual(len(cards),3)
        self.assertEqual(sorted(next(c.rarity for c in CHARACTERS if c.id==card.character_id) for card in cards),[2,3,4])
        self.assertEqual(sum('embed' in call.kwargs for call in ctx.send.call_args_list),1)
        self.assertTrue((await self.db.get_user(1)).starter_daily_claimed)

    async def test_failed_paid_packs_roll_back_coins_cards_pity_and_serials(self):
        target=next(c for c in CHARACTERS if c.rarity==3 and not c.is_duo)
        other=next(c for c in CHARACTERS if c.rarity==3 and c.id!=target.id and not c.is_duo)
        await self.db.set_pity(1,target.id,armed=True)
        for kind,detail in [('base',{}),('position',{'position':target.position}),('school',{'school':target.school})]:
            with self.subTest(kind=kind):
                before=await self.snapshot();original=self.store._issue_card;calls=0
                async def fail_second(*args):
                    nonlocal calls
                    calls+=1
                    if calls==2:raise RuntimeError('Card persistence failed')
                    return await original(*args)
                with patch('hiq.pack_store.pull_pack',return_value=[other]*3),patch.object(self.store,'_issue_card',side_effect=fail_second):
                    with self.assertRaisesRegex(RuntimeError,'persistence'):
                        await self.store.open(1,'Player',kind,**detail)
                self.assertEqual(await self.snapshot(),before)

    async def test_failed_daily_can_retry_without_consuming_starter_or_pity(self):
        target=next(c for c in CHARACTERS if c.rarity==3)
        await self.db.set_pity(1,target.id,armed=True);before=await self.snapshot()
        original=self.store._issue_card;calls=0
        async def fail_second(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise asyncio.CancelledError()
            return await original(*args)
        with patch.object(self.store,'_issue_card',side_effect=fail_second):
            with self.assertRaises(asyncio.CancelledError):await self.store.open(1,'Player',daily=True)
        self.assertEqual(await self.snapshot(),before)
        opened=await self.store.open(1,'Player',daily=True)
        self.assertTrue(opened['starter']);self.assertEqual(len(opened['cards']),3)
        self.assertIsNone(await self.store.open(1,'Player',daily=True))

    async def test_paid_pack_commits_exact_payment_and_pity_together(self):
        target=next(c for c in CHARACTERS if c.rarity==3)
        other=next(c for c in CHARACTERS if c.rarity==3 and c.id!=target.id)
        await self.db.set_pity(1,target.id,armed=True)
        with patch('hiq.pack_store.pull_pack',return_value=[other]*3):opened=await self.store.open(1,'Player')
        self.assertEqual(opened['cards'][0].character_id,target.id)
        self.assertEqual(len(await self.db.get_cards(1)),3)
        self.assertEqual(opened['coins'],1700);self.assertEqual((await self.db.get_user(1)).coins,1700)
        self.assertIsNone(await self.db.get_pity(1))

    async def test_concurrent_paid_packs_cannot_overdraw_balance(self):
        await self.db.set_user_fields(1,coins=300)
        results=await asyncio.gather(*(PackStore(Database(self.db.path)).open(1,'Player') for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
        self.assertEqual((await self.db.get_user(1)).coins,0)
        self.assertEqual(len(await self.db.get_cards(1)),3)

    async def test_invalid_targeted_pack_and_insufficient_funds_leave_state_unchanged(self):
        for kind,detail in [('position',{'position':'invalid'}),('school',{'school':'invalid'})]:
            before=await self.snapshot()
            with self.assertRaises(ValueError):await self.store.open(1,'Player',kind,**detail)
            self.assertEqual(await self.snapshot(),before)
        await self.db.set_user_fields(1,coins=0);before=await self.snapshot()
        with self.assertRaisesRegex(ValueError,'300 coins'):await self.store.open(1,'Player')
        self.assertEqual(await self.snapshot(),before)

    async def test_each_paid_command_uses_transactional_pack_delivery(self):
        from hiq.cogs.economy import EconomyCog
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,name='Player'),send=AsyncMock())
        cog=EconomyCog(SimpleNamespace(db=self.db))
        for command,kwargs,cost in [(EconomyCog.pack_base,{},300),(EconomyCog.pack_position,{'position':'WS'},450),(EconomyCog.pack_school,{'school':'Karasuno'},450)]:
            before=len(await self.db.get_cards(1));coins=(await self.db.get_user(1)).coins
            await command.callback(cog,ctx,**kwargs)
            self.assertEqual(len(await self.db.get_cards(1)),before+3)
            self.assertEqual((await self.db.get_user(1)).coins,coins-cost)

    async def test_ineligible_position_pack_preserves_armed_pity(self):
        target=next(c for c in CHARACTERS if c.rarity==3 and c.position=='S')
        other=next(c for c in CHARACTERS if c.rarity==3 and c.position=='WS')
        await self.db.set_pity(1,target.id,armed=True)
        with patch('hiq.pack_store.pull_pack',return_value=[other]*3):
            opened=await self.store.open(1,'Player','position',position='WS')
        self.assertTrue(all(c.character_id==other.id for c in opened['cards']))
        self.assertEqual((await self.db.get_pity(1))['armed'],1)

    async def test_next_daily_is_normal_pack_and_balance_still_shows_pity(self):
        from hiq.cogs.economy import EconomyCog
        await self.store.open(1,'Player',daily=True,day='2026-11-12')
        with patch('hiq.pack_store.pull_pack',wraps=__import__('hiq.economy',fromlist=['pull_pack']).pull_pack) as pull:
            opened=await self.store.open(1,'Player',daily=True,day='2026-11-13')
        pull.assert_called_once();self.assertFalse(opened['starter'])
        target=next(c for c in CHARACTERS if c.rarity==3)
        await self.db.set_pity(1,target.id,armed=True)
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,display_name='Player'),send=AsyncMock())
        await EconomyCog.balance.callback(EconomyCog(SimpleNamespace(db=self.db)),ctx)
        self.assertTrue(any(f.name=='Pity Target' and target.name in f.value for f in ctx.send.call_args.kwargs['embed'].fields))


class CombatFixTests(unittest.TestCase):
    def pod(self):
        chars=list({c.name:c for c in CHARACTERS}.values())[:9]
        specs=[(None,c,'front' if i%3==0 else 'back') for i,c in enumerate(chars)]
        e=PodBattleEngine(specs,specs,seed=1)
        for u in e.units:u.character=replace(u.character,name='Test'+u.uid,title='Test');u.cd=99
        return e

    def test_prefecture_passive_saves_and_two_save_limit(self):
        for name,offense,lane in [('Matsukawa','qik','front'),('Maruyama','qik','back'),('Atema','pow','front')]:
            with self.subTest(name=name):
                e=self.pod();a=e.team_a.units[0];target=e.team_b.units[1];savior=e.team_b.units[0]
                a.character=replace(a.character,stats={**a.character.stats,'pow':60 if offense=='pow' else 0,'qik':60 if offense=='qik' else 0})
                savior.character=replace(savior.character,name=name);savior.lane=lane;e.rng=Mock(random=Mock(return_value=0))
                self.assertEqual(e.maybe_save(a,target),(savior,True))
                self.assertEqual(e.maybe_save(a,target),(savior,True))
                self.assertEqual(e.maybe_save(a,target),(target,False))

    def test_only_reluctant_titan_blocks_prefecture_saves(self):
        for title,blocked in [('Reluctant Titan',True),('Last Hope',False),('Unyielding Ace',False)]:
            e=self.pod();a=e.team_a.units[0];target=e.team_b.units[1];savior=e.team_b.units[0]
            a.character=replace(a.character,name='Kiryu',title=title)
            savior.character=replace(savior.character,name='Hirugami');savior.cd=0
            self.assertEqual(e.maybe_save(a,target),(target,False) if blocked else (savior,True))

    def test_prefecture_free_ball_prevents_passive_and_ultimate_saves(self):
        e=self.pod();a=e.team_a.units[0];target=e.team_b.units[1]
        target.add_status('Free Ball',False)
        with patch.object(e,'passive_save') as save:
            self.assertEqual(e.maybe_save(a,target),(target,False));save.assert_not_called()

    def test_local_pod_ultimate_save_keeps_priority(self):
        e=self.pod();a=e.team_a.units[0];target=e.team_b.units[1]
        local,remote=e.team_b.units[0],e.team_b.units[3]
        for u in (local,remote):u.character=replace(u.character,name='Hirugami');u.cd=0
        remote.base['spd']=999
        self.assertEqual(e.maybe_save(a,target),(local,True))

    def test_salts_does_not_increase_open_play_stat_scaling(self):
        import test_v11
        damage=[]
        for key in (None,'smelling_salts'):
            e,ctx=test_v11.PureTests().gear_engine(key)
            ctx.attacker.character=replace(ctx.attacker.character,name='Hinata')
            ctx.attacker.add_status('Open Play',True)
            e.resolve_hit(ctx,ctx.attacker,ctx.defender);damage.append(ctx.attacker.damage_dealt)
        self.assertEqual(damage[0],damage[1])

    def test_salts_still_grants_comparison_speed(self):
        import test_v11
        e,ctx=test_v11.PureTests().gear_engine('smelling_salts')
        stats=e.combat_stats(ctx)[ctx.attacker.uid]
        self.assertEqual(e.skill_spd(ctx.attacker,stats),stats['spd']+7)
