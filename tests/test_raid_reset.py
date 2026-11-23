"""Administrative resets restore allowances without removing owned rewards."""
import unittest
from datetime import date,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_v11
from hiq.allowances import calendar_days
from hiq.economy import local_date_key,week_key
from hiq.items import ITEMS,SIGNATURE_ITEMS
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance


class RaidResetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def seed(self):
        day=local_date_key();previous=(date.fromisoformat(calendar_days(day)[0])-timedelta(days=1)).isoformat()
        self.old_day=previous;self.key=ITEMS[0]['key'];self.signature=SIGNATURE_ITEMS[0]['key']
        async with self.store.tx() as c:
            for uid in (1,2):
                for item in ITEMS:
                    await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(uid,day,item['key'],item['daily_cap']*20))
                await credit(c,uid,'item_shard',self.key,48)
                await c.execute("INSERT INTO raid_shard_daily VALUES (?,?,'character_shard',50)",(uid,day))
                await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,25)',(uid,day,self.signature))
                await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,99)',(uid,previous,self.key))
                await c.execute('INSERT INTO raid_shard_weekly VALUES (?,?,350)',(uid,week_key()))
                await c.execute("INSERT INTO economy_daily VALUES (?,?,'raid',5,300,300)",(uid,day))
                await c.execute("INSERT INTO raid_theme_cursor VALUES (?,6,'defense',2)",(uid,))

    async def test_individual_reset_restores_bank_and_preserves_rewards_and_other_users(self):
        await self.seed();raid=RaidStore(self.db)
        before=await raid.shard_progress(1)
        self.assertEqual(raid.reward_pool('defense',before),[])
        result=await raid.reset_item_allowances(1)
        self.assertEqual((result['players'],result['rows']),(1,len(ITEMS)))
        progress=await raid.shard_progress(1)
        for theme in ('tactics','offense','defense','tempo','recovery'):
            pool=raid.reward_pool(theme,progress)
            self.assertEqual(len(pool),sum(i['theme']==theme for i in ITEMS))
            self.assertTrue(all(r['remaining']==r['cap'] for r in pool))
        self.assertEqual(progress['coins'],300)
        self.assertEqual(progress['clancoins'],300)
        self.assertEqual(progress['character_shard'],50)
        self.assertEqual(progress['character_shard_weekly'],350)
        self.assertEqual(progress[self.signature],25)
        self.assertGreater((await raid.shard_progress(2))[self.key],0)
        async with self.db.connect() as c:
            self.assertEqual(await balance(c,1,'item_shard',self.key),48)
            self.assertEqual((await raid.one(c,'SELECT amount FROM raid_shard_daily WHERE owner_id=1 AND day_key=? AND reward_key=?',(self.old_day,self.key)))['amount'],99)
            self.assertEqual((await raid.one(c,'SELECT next_index FROM raid_theme_cursor WHERE owner_id=1'))['next_index'],2)
        self.assertEqual((await raid.reset_item_allowances(1))['rows'],0)

    async def test_all_reset_targets_every_player_and_ignores_old_weeks(self):
        await self.seed();raid=RaidStore(self.db)
        result=await raid.reset_item_allowances()
        self.assertEqual((result['players'],result['rows']),(2,len(ITEMS)*2))
        for uid in (1,2):self.assertNotIn(self.key,await raid.shard_progress(uid))
        for invalid in (True,0,-1,'all'):
            with self.assertRaises(ValueError):await raid.reset_item_allowances(invalid)

    async def test_command_requires_owner_and_confirmation(self):
        from discord.ext import commands
        from hiq.cogs.raids import RaidCog,RaidResetConfirm
        ctx=SimpleNamespace(bot=SimpleNamespace(is_owner=AsyncMock(return_value=False)),author=SimpleNamespace(id=1),send=AsyncMock())
        with self.assertRaises(commands.NotOwner):await RaidCog.raidreset.checks[0](ctx)
        ctx.bot.is_owner.return_value=True
        self.assertTrue(await RaidCog.raidreset.checks[0](ctx))
        cog=RaidCog(SimpleNamespace(db=self.db))
        cog.store.reset_item_allowances=AsyncMock(return_value={'players':2,'rows':120})
        await RaidCog.raidreset.callback(cog,ctx,'all')
        cog.store.reset_item_allowances.assert_not_awaited()
        view=ctx.send.call_args.kwargs['view'];self.assertIsInstance(view,RaidResetConfirm)
        other=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(other))
        owner=SimpleNamespace(user=ctx.author,response=SimpleNamespace(defer=AsyncMock(),edit_message=AsyncMock()),edit_original_response=AsyncMock())
        await view.confirm.callback(owner)
        await view.confirm.callback(owner)
        cog.store.reset_item_allowances.assert_awaited_once_with(None)
        self.assertFalse(await view.interaction_check(other))
        self.assertNotIn('raidcaps',{cmd.name for cmd in cog.get_commands()})

    async def test_cancel_and_missing_target_do_not_reset(self):
        from hiq.cogs.raids import RaidCog,RaidResetConfirm
        store=SimpleNamespace(reset_item_allowances=AsyncMock())
        cog=RaidCog(SimpleNamespace(db=self.db));cog.store=store
        ctx=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        await RaidCog.raidreset.callback(cog,ctx)
        self.assertNotIn('view',ctx.send.call_args.kwargs)
        view=RaidResetConfirm(store,1,None)
        i=SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.cancel.callback(i)
        await view.confirm.callback(i)
        store.reset_item_allowances.assert_not_awaited()
