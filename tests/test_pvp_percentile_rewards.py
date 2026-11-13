import asyncio
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from hiq.config import TIMEZONE
from hiq.database import Database
from hiq.pvp_store import PvPStore
from hiq.pvp_cycles import cycle_for,prize_stars,next_elo_reset


def at(day):return datetime.fromisoformat(day).replace(hour=12,tzinfo=TIMEZONE)


class PercentileTests(unittest.TestCase):
    def test_brackets_are_cumulative_and_round_up(self):
        self.assertEqual([prize_stars(i,5) for i in range(1,6)],[[1,2,3,4,5],[1,2,3,4],[1,2,3],[1,2],[1]])
        self.assertEqual(prize_stars(1,1),[1,2,3,4,5])
        for total in range(1,101):
            for stars in range(1,6):
                self.assertEqual(sum(stars in prize_stars(rank,total) for rank in range(1,total+1)),(total*(6-stars)+4)//5)
        self.assertEqual(prize_stars(0,5),[]);self.assertEqual(prize_stars(1,0),[])

    def test_season_spans_two_monday_resets_and_handles_year_and_dst(self):
        self.assertEqual(cycle_for('2026-11-15'),('fortnight-2026-11-09',1))
        self.assertEqual(cycle_for('2026-11-16'),('fortnight-2026-11-09',2))
        self.assertEqual(cycle_for('2026-11-23'),('fortnight-2026-11-23',1))
        self.assertEqual(next_elo_reset('2026-11-12'),datetime(2026,11,23,tzinfo=TIMEZONE))
        self.assertEqual(next_elo_reset('2026-11-22'),datetime(2026,11,23,tzinfo=TIMEZONE))
        reset=next_elo_reset('2027-03-07')
        self.assertEqual((reset.weekday(),reset.hour),(0,0))
        self.assertEqual(reset.tzinfo,TIMEZONE)


class WeeklyPacksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'pvp.sqlite3')
        await self.db.init();self.store=PvPStore(self.db)
        await self.store.settle(at('2026-11-12'))

    async def asyncTearDown(self):self.tmp.cleanup()

    async def seed(self,count=10,day='2026-11-12'):
        cycle,_=cycle_for(day)
        for uid in range(1,count+1):
            await self.db.ensure_user(uid)
            async with self.store.tx() as c:
                await c.execute('INSERT INTO pvp_ratings VALUES (?,?,?,1,0,0)',(cycle,uid,1500-uid))

    async def packs(self,uid):return sorted(it['stars'] for it in await self.store.items(uid))

    async def test_ten_player_brackets_and_no_weekly_coin_payout(self):
        await self.seed()
        await self.store.settle(at('2026-11-16'))
        for uid in range(1,11):
            self.assertEqual(await self.packs(uid),list(range(1,7-(uid+1)//2)))
            self.assertEqual((await self.db.get_user(uid)).coins,0)

    async def test_only_ranked_season_participants_count_including_losses_and_draws(self):
        await self.seed(4)
        async with self.store.tx() as c:
            await c.execute('UPDATE pvp_ratings SET wins=0,losses=1 WHERE owner_id=2')
            await c.execute('UPDATE pvp_ratings SET wins=0,draws=1 WHERE owner_id=3')
            await c.execute('UPDATE pvp_ratings SET wins=0,rating=9999 WHERE owner_id=4')
        await self.store.settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])
        self.assertEqual(await self.packs(2),[1,2,3,4])
        self.assertEqual(await self.packs(3),[1,2])
        self.assertEqual(await self.packs(4),[])

    async def test_timezone_boundary_exactly_at_monday_midnight(self):
        await self.seed(5)
        await self.store.settle(datetime(2026,11,16,4,59,59,tzinfo=timezone.utc))
        self.assertEqual(await self.packs(1),[])
        await self.store.settle(datetime(2026,11,16,5,0,tzinfo=timezone.utc))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])

    async def test_week_two_rewards_without_new_match_then_no_rewards_in_empty_new_season(self):
        await self.seed(5)
        await self.store.settle(at('2026-11-16'))
        self.assertEqual((await self.store.board(1,at('2026-11-16')))[2]['rating'],1499)
        await self.store.settle(at('2026-11-23'))
        self.assertEqual(await self.packs(1),[1,1,2,2,3,3,4,4,5,5])
        season,board,own=await self.store.board(1,at('2026-11-23'))
        self.assertEqual((own['rating'],own['wins'],own['losses'],own['draws']),(1000,0,0,0))
        self.assertEqual(board,[])
        await self.store.settle(at('2026-11-30'))
        self.assertEqual(await self.packs(1),[1,1,2,2,3,3,4,4,5,5])

    async def test_concurrent_retry_and_restart_do_not_duplicate_prizes(self):
        await self.seed(5)
        await asyncio.gather(*(self.store.settle(at('2026-11-16')) for _ in range(3)))
        await self.db.init();await PvPStore(self.db).settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])
        async with self.db.connect() as c:
            row=await self.store.one(c,'SELECT COUNT(*) n FROM pvp_monthly_awards')
        self.assertEqual(row['n'],5)

    async def test_partial_prize_delivery_failure_rolls_back_entire_week(self):
        await self.seed(5);original=self.store._issue_item;count=0
        async def fail(*args,**kwargs):
            nonlocal count
            count+=1
            if count==7:raise RuntimeError('Delivery failure')
            return await original(*args,**kwargs)
        with patch.object(self.store,'_issue_item',side_effect=fail):
            with self.assertRaises(RuntimeError):await self.store.settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[])
        async with self.db.connect() as c:
            self.assertEqual((await self.store.one(c,"SELECT closed FROM pvp_weeks WHERE week='2026-11-09'"))['closed'],0)
            self.assertEqual((await self.store.one(c,'SELECT COUNT(*) n FROM pvp_monthly_awards'))['n'],0)
        await self.store.settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])

    async def test_ties_follow_same_order_as_leaderboard(self):
        await self.seed(5)
        async with self.store.tx() as c:
            await c.execute('UPDATE pvp_ratings SET rating=1200,wins=2,losses=1')
            await c.execute('UPDATE pvp_ratings SET wins=3 WHERE owner_id=5')
            await c.execute('UPDATE pvp_ratings SET losses=0 WHERE owner_id=4')
        _,board,_=await self.store.board(1,at('2026-11-12'))
        self.assertEqual([r['owner_id'] for r in board],[5,4,1,2,3])
        await self.store.settle(at('2026-11-16'))
        async with self.db.connect() as c:
            rows=await self.store.rows(c,'SELECT owner_id FROM pvp_monthly_awards ORDER BY rank')
        self.assertEqual([r['owner_id'] for r in rows],[5,4,1,2,3])

    async def test_existing_maintenance_delivers_packs_without_user_command(self):
        from hiq.cogs.pvp import PvPCog
        await self.seed(5)
        cog=object.__new__(PvPCog);cog.store=self.store;cog.jobs={};cog.recoveries=set()
        with patch('hiq.pvp_store.calendar',return_value=(cycle_for('2026-11-16')[0],'2026-11-16')):
            await PvPCog.maintenance.coro(cog)
        self.assertEqual(await self.packs(1),[1,2,3,4,5])

    async def test_leaderboard_explains_stacked_rewards_and_separate_reset_dates(self):
        from hiq.cogs.pvp import PvPCog
        await self.seed(5)
        cog=object.__new__(PvPCog);cog.store=self.store
        ctx=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        with patch('hiq.economy.local_date_key',return_value='2026-11-16'):
            await PvPCog.pvpboard.callback(cog,ctx)
        embed=ctx.send.call_args.kwargs['embed']
        field=next(f for f in embed.fields if 'Weekly pack prizes' in f.name)
        self.assertIn('Week 2/2',field.name)
        self.assertIn('top 20% gets one pack of every rarity',field.value)
        self.assertIn('Weekly cutoff',field.value);self.assertIn('Elo resets',field.value)
        self.assertNotIn('First place',field.name)
        self.assertLess(len(embed),6000)

    async def test_pending_legacy_match_finishes_before_cutoff_prizes_and_blocks_new_matches(self):
        await self.seed(5)
        state=dict(day='2026-11-15',a=[],b=[])
        async with self.store.tx() as c:
            await c.execute("DELETE FROM economy_migrations WHERE version='v12-biweekly-percentile-pvp'")
            await c.execute("UPDATE pvp_ratings SET month='cycle-2026-11-09'")
            await c.execute("UPDATE pvp_weeks SET cycle='cycle-2026-11-09'")
            await c.execute("INSERT INTO pvp_runs VALUES ('late-ranked','cycle-2026-11-09',3,1,2,1,0,'running',?,NULL,NULL,'2026-11-15')",(json.dumps(state),))
        await self.store.settle(at('2026-11-16'))
        self.assertEqual((await self.store.run('late-ranked'))['month'],cycle_for('2026-11-15')[0])
        self.assertEqual(await self.packs(1),[])
        with self.assertRaisesRegex(ValueError,'previous week'):
            await self.store.start(3,at=at('2026-11-16'))
        await self.store.finish('late-ranked',dict(winner_side='A'),at=at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])
        self.assertGreater((await self.store.board(1,at('2026-11-16')))[2]['rating'],1499)
        await self.store.settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])

    async def test_legacy_live_ratings_migrate_once_and_paid_weeks_stay_paid(self):
        await self.db.ensure_user(1)
        async with self.store.tx() as c:
            await c.execute("DELETE FROM economy_migrations WHERE version='v12-biweekly-percentile-pvp'")
            await c.execute('DELETE FROM pvp_weeks')
            await c.execute("INSERT INTO pvp_ratings VALUES ('cycle-2026-11-09',1,1300,4,2,1)")
            await c.execute("INSERT INTO pvp_weeks VALUES ('2026-11-02','cycle-2026-10-05',5,1)")
            await c.execute("INSERT INTO pvp_monthly_awards VALUES ('week-2026-11-02',1,1,1250,'[]')")
            await c.execute("INSERT INTO pvp_weeks VALUES ('2026-11-09','cycle-2026-11-09',1,0)")
        await self.store.settle(at('2026-11-12'))
        self.assertEqual((await self.store.board(1,at('2026-11-12')))[2]['rating'],1300)
        self.assertEqual(await self.packs(1),[])
        await self.store.settle(at('2026-11-16'))
        self.assertEqual(await self.packs(1),[1,2,3,4,5])
        async with self.store.tx() as c:await c.execute('UPDATE pvp_ratings SET rating=1400 WHERE month=?',(cycle_for('2026-11-12')[0],))
        await self.store.settle(at('2026-11-16'))
        self.assertEqual((await self.store.board(1,at('2026-11-16')))[2]['rating'],1400)
        self.assertEqual(await self.packs(1),[1,2,3,4,5])
