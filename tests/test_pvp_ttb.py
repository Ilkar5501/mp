import unittest
from datetime import date,datetime,timedelta,timezone
from unittest.mock import patch
import test_ranked_pvp
from hiq.pvp_store import PvPStore
from hiq.ttb_store import PvPTTBStore
from hiq.ttb_rotation import rotation,selected_ids
from hiq.data import CHAR_BY_ID

NOW=test_ranked_pvp.NOW

class PvPTTBTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_ranked_pvp.PvPStoreTests.asyncSetUp
    asyncTearDown=test_ranked_pvp.PvPStoreTests.asyncTearDown
    team=test_ranked_pvp.PvPStoreTests.team
    users=test_ranked_pvp.PvPStoreTests.users

    async def test_ttb_has_separate_ratings_daily_coins_and_last_rated_winners(self):
        await self.users(1,2);normal=self.store;ttb=PvPTTBStore(self.db)
        first=await normal.start(1,target=2,at=NOW)
        await normal.finish(first['id'],{'winner_side':'A'},at=NOW)
        first=await ttb.start(1,target=2,at=NOW)
        self.assertTrue(first['rated']);self.assertTrue(first['month'].startswith('ttb:'))
        paid=await ttb.finish(first['id'],{'winner_side':'A'},at=NOW)
        self.assertEqual(paid['settlement']['reward']['coins'],100)
        self.assertEqual((await self.db.get_user(1)).coins,200)
        self.assertEqual((await normal.board(1,at=NOW))[2]['rating'],1016)
        self.assertEqual((await ttb.board(1,at=NOW))[2]['rating'],1016)
        repeat=await ttb.start(1,at=NOW);self.assertFalse(repeat['rated'])
        await ttb.finish(repeat['id'],{'winner_side':'B'},at=NOW)
        repeat=await ttb.start(1,at=NOW);self.assertFalse(repeat['rated'])
        await ttb.finish(repeat['id'],{'winner_side':'A'},at=NOW)
        self.assertFalse((await normal.start(1,target=2,at=NOW))['rated'])

    async def test_weekly_prizes_stack_as_clan_dollars_and_do_not_pay_twice(self):
        ttb=PvPTTBStore(self.db);await ttb.settle(at=NOW)
        cycle=ttb.window(NOW)[0]
        async with ttb.tx() as c:
            for uid in range(1,6):
                await c.execute('INSERT OR IGNORE INTO users(discord_id,created_at) VALUES (?,?)',(uid,NOW.isoformat()))
                await c.execute('INSERT INTO pvp_ratings(month,owner_id,rating,wins) VALUES (?,?,?,1)',(cycle,uid,1200-uid*10))
        next_week=datetime(2026,11,16,17,tzinfo=timezone.utc)
        await ttb.settle(at=next_week);await ttb.settle(at=next_week)
        self.assertEqual([await ttb.wallet(uid) for uid in range(1,6)],[1500,1000,600,300,100])
        self.assertEqual((await ttb.board(1,at=next_week))[2]['rating'],1190)
        reset=datetime(2026,11,23,17,tzinfo=timezone.utc)
        self.assertEqual((await ttb.board(1,at=reset))[2]['rating'],1000)

    async def test_rotation_is_stable_avoids_consecutive_picks_and_keeps_four_weeks(self):
        previous=set();ttb=PvPTTBStore(self.db)
        for week in range(9):
            day=(date(2026,11,9)+timedelta(weeks=week)).isoformat()
            async with ttb.tx() as c:
                state=await rotation(c,day)
                self.assertEqual(state,await rotation(c,day))
                count=await ttb.one(c,'SELECT COUNT(*) n FROM ttb_rotations')
                self.assertLessEqual(count['n'],4)
            picks=selected_ids(state)
            self.assertEqual(len(picks),16);self.assertFalse(picks&previous)
            for rarity,ids in state['picks'].items():
                self.assertEqual(len(ids),4)
                self.assertTrue(all(CHAR_BY_ID[cid].rarity==int(rarity) and not CHAR_BY_ID[cid].is_duo for cid in ids))
            previous=picks
