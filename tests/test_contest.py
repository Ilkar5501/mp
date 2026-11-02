import asyncio,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from hiq.contest import ContestEngine,PRESETS,preset_specs,SUPPORT_SLOTS
from hiq.contest_store import ContestStore
from hiq.data import CHARACTERS
from hiq.sealed import team_specs
from hiq.database import Database
from hiq.economy import week_key,local_date_key,week_key_for


def players():return team_specs('contest-test',1,[c.id for c in list({c.name:c for c in CHARACTERS}.values())[:6]])


class ContestEngineTests(unittest.TestCase):
    def test_all_fourteen_formations_have_six_eligible_supports(self):
        self.assertEqual(len(PRESETS),14)
        for i in range(14):
            specs=preset_specs(i)
            self.assertEqual(len(specs),7)
            self.assertEqual(specs[0][0].merge_level,10)
            self.assertTrue(all(c.name in SUPPORT_SLOTS for _,c,_ in specs[1:]))
            self.assertNotIn(specs[0][1].name,[c.name for _,c,_ in specs[1:]])
            self.assertFalse(any(card.equipment_key or card.boon_stat or card.bane_stat for card,_,_ in specs))

    def test_cycling_preserves_overkill_and_thresholds(self):
        e=ContestEngine(players(),0,seed=1);bar=e.team_b.max_hp
        self.assertEqual(bar,e.feature.base['hp'])
        e.damage_team(e.team_b,bar*2+17)
        self.assertEqual((e.score,e.bars,e.team_b.hp),(bar*2+17,2,bar-17))
        e.damage_team(e.team_b,bar-17)
        self.assertEqual(e.team_b.hp,bar);self.assertEqual(e.bars,3)

    def test_supports_never_act_or_receive_attacks(self):
        for i in range(14):
            e=ContestEngine(players(),i,seed=i);result=e.run()
            self.assertLessEqual(result.turns,20)
            self.assertGreater(e.team_b.hp,0)
            self.assertEqual(len(e.enemy_team(e.team_a.units[0]).units),1)
            for u in e.supports:
                self.assertEqual(u.actions_taken,0);self.assertEqual(u.attacks_tanked,0)
                self.assertFalse(u.ult_triggered_this_combat)

    def test_twenty_turn_limit_when_player_cannot_die(self):
        e=ContestEngine(players(),0,seed=1)
        e.team_a.hp=e.team_a.max_hp=1000000
        self.assertEqual(e.run().turns,20)


class ContestStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();self.store=ContestStore(self.db)
        for uid in range(1,5):await self.db.ensure_user(uid)
        self.clan=await self.store.create_clan(1,'Tester','Contest Clan','🏐')

    async def asyncTearDown(self):self.temp.cleanup()

    async def run_row(self,score,day=None,uid=1):
        import json,uuid
        sid=uuid.uuid4().hex;day=day or local_date_key()
        async with self.store.tx() as c:
            await c.execute("INSERT INTO contest_runs VALUES (?,?,?,?,'running',?)",(sid,uid,day,self.clan['id'],json.dumps({})))
        return sid,await self.store.finish(sid,score,{})

    async def test_best_score_only_and_once_daily_participation(self):
        sid,first=await self.run_row(100)
        self.assertEqual(first['state']['participation'],200)
        again=await self.store.finish(sid,100,{})
        self.assertEqual(again['state']['participation'],200)
        _,second=await self.run_row(80)
        self.assertEqual(second['state']['participation'],0)
        await self.run_row(140)
        self.assertEqual(await self.store.wallet(1),200)
        clans,users=await self.store.leaderboard()
        self.assertEqual(users[0]['score'],140)
        self.assertEqual(clans[0]['score'],140)

    async def test_rotation_is_persistent_and_each_period_contains_all_teams(self):
        indices=[(await self.store.rotation(f'2026-11-{day:02}'))[0] for day in range(1,15)]
        self.assertEqual(set(indices),set(range(14)))
        self.assertEqual(await self.store.rotation('2026-11-01'),await ContestStore(self.db).rotation('2026-11-01'))
        self.assertNotEqual((await self.store.rotation('2026-11-14'))[1],(await self.store.rotation('2026-11-15'))[1])

    async def test_weekly_prizes_settle_exactly_once_after_restart(self):
        old=(datetime.fromisoformat(local_date_key())-timedelta(days=7)).date().isoformat()
        await self.run_row(500,old)
        before=(await self.db.get_user(1)).coins
        self.assertEqual(len(await self.store.settle()),1)
        self.assertEqual(await self.store.wallet(1),1200)
        self.assertEqual((await self.db.get_user(1)).coins,before+1000)

        self.assertEqual(await ContestStore(self.db).settle(),[])
        await self.run_row(999,old)
        self.assertEqual(await self.store.wallet(1),1200)
        self.assertEqual((await self.db.get_user(1)).coins,before+1000)

    async def test_top_three_clans_and_players_receive_distinct_prizes(self):
        from hiq.community_store import now
        old=(datetime.fromisoformat(local_date_key())-timedelta(days=7)).date().isoformat()
        second=await self.store.create_clan(2,'Two','Second Clan','🏐')
        third=await self.store.create_clan(3,'Three','Third Clan','🏐')
        async with self.store.tx() as c:
            await c.execute('INSERT INTO clan_members(user_id,clan_id,username,joined_at) VALUES (?,?,?,?)',(4,self.clan['id'],'Four',now()))
            for uid,cid,score in [(1,self.clan['id'],300),(2,second['id'],200),(3,third['id'],100)]:
                await c.execute('INSERT INTO contest_scores VALUES (?,?,?,?,?)',(uid,old,week_key_for(datetime.fromisoformat(old)),cid,score))
        before={uid:(await self.db.get_user(uid)).coins for uid in (1,2,3)}
        await self.store.settle()
        for uid,amount in ((1,1000),(2,750),(3,500),(4,1000)):
            self.assertEqual(await self.store.wallet(uid),amount)
            if uid in before:self.assertEqual((await self.db.get_user(uid)).coins,before[uid]+amount)
        self.assertEqual(await self.store.settle(),[])
