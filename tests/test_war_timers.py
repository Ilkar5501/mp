import unittest,time
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import test_v11
from hiq.data import CHARACTERS
from hiq.cogs.clans import ClanCog
from hiq.war_activity import WAR_WINDOW


class WarTimerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown
    war=test_v11.DatabaseTests.war
    save=test_v11.DatabaseTests.save
    ready_teams=test_v11.DatabaseTests.ready_teams

    async def test_opening_ban_does_not_reset_shared_send_clock(self):
        row=await self.war(bans=False);deadline=row['state']['deadlines'][0]
        with patch('time.time',return_value=deadline-60):
            row=await self.store.ban_war_unit(row['id'],1,CHARACTERS[-1].id)
            row=await self.store.nominate(row['id'],1,2,1)
            self.assertEqual(row['state']['deadlines'],[deadline,deadline])
            row=await self.store.ban_war_unit(row['id'],6,CHARACTERS[-1].id)
            self.assertEqual(row['state']['deadlines'],[deadline,deadline])
        await self.store.expire_war(row['id'],at=deadline+1)
        row=await self.store.session(row['id'])
        self.assertEqual(row['state']['winner'],0)

    async def test_both_banned_but_neither_sent_cancels(self):
        row=await self.war();await self.store.expire_war(row['id'],at=row['state']['deadlines'][0]+1)
        self.assertEqual((await self.store.session(row['id']))['status'],'cancelled')

    async def test_team_window_starts_at_second_send_even_in_round_one(self):
        row=await self.war();deadline=row['state']['deadlines'][0]
        with patch('time.time',return_value=deadline-120):
            row=await self.store.nominate(row['id'],1,2,1)
            with self.assertRaises(ValueError):await self.store.select_war_team(row['id'],2,1,1)
        with patch('time.time',return_value=deadline-30):
            row=await self.store.nominate(row['id'],6,7,1)
        self.assertEqual(row['state']['deadlines'],[deadline-30+WAR_WINDOW]*2)
        self.assertFalse(await self.store.expire_war(row['id'],at=deadline+1))

    async def test_later_winner_response_and_team_deadlines_are_independent(self):
        row=await self.war();st=row['state'];st['sends']=[2,7];row['status']='running';await self.save(row)
        row=await self.store.finish_series(row['id'],1,0,[2,0]);deadline=row['state']['deadlines'][0]
        with patch('time.time',return_value=deadline-60):
            row=await self.store.nominate(row['id'],1,2,2)
            response=row['state']['deadlines'][1]
            self.assertEqual(response,deadline-60+WAR_WINDOW)
        self.assertFalse(await self.store.expire_war(row['id'],at=deadline+1))
        with patch('time.time',return_value=response-60):
            row=await self.store.nominate(row['id'],6,8,2)
        self.assertEqual(row['state']['deadlines'],[response-60+WAR_WINDOW]*2)
        self.assertFalse(await self.store.expire_war(row['id'],at=response+1))

    async def test_missing_team_eliminates_player_not_clan(self):
        row=await self.war();await self.store.nominate(row['id'],1,2,1)
        row=await self.store.nominate(row['id'],6,7,1);row=await self.ready_teams(row)
        row['state']['teams'][0]=None;await self.save(row)
        await self.store.expire_war(row['id'],at=row['state']['deadlines'][0]+1)
        row=await self.store.session(row['id'])
        self.assertEqual(row['status'],'active');self.assertEqual(row['state']['eliminated'][0],[2])
        self.assertEqual(row['state']['stage'],'winner_send')
        self.assertEqual(row['state']['initiative'],1)

    async def test_officer_dms_are_persisted_and_only_sent_when_side_is_due(self):
        row=await self.war(bans=False);cog=ClanCog(SimpleNamespace(db=self.db));cog.dm=AsyncMock(return_value=True)
        await cog.notify_officers(row['id'])
        self.assertEqual({call.args[0] for call in cog.dm.call_args_list},{1,2,6,7})
        await cog.notify_officers(row['id']);self.assertEqual(cog.dm.await_count,4)
        restored=ClanCog(SimpleNamespace(db=self.db));restored.dm=AsyncMock(return_value=True)
        await restored.notify_officers(row['id']);restored.dm.assert_not_awaited()
        row=await self.store.session(row['id']);st=row['state'];st['bans']=[CHARACTERS[-1].id]*2
        st['sends']=[2,7];row['status']='running';await self.save(row)
        row=await self.store.finish_series(row['id'],1,0,[2,0])
        await restored.notify_officers(row['id'])
        self.assertEqual({c.args[0] for c in restored.dm.call_args_list},{1,2})
        row=await self.store.nominate(row['id'],1,2,2)
        await restored.notify_officers(row['id'])
        self.assertEqual({c.args[0] for c in restored.dm.call_args_list},{1,2,6,7})

    async def test_failed_dm_is_retried(self):
        row=await self.war(bans=False);cog=ClanCog(SimpleNamespace(db=self.db));cog.dm=AsyncMock(return_value=False)
        await cog.notify_officers(row['id']);await cog.notify_officers(row['id'])
        self.assertEqual(cog.dm.await_count,8)

    async def test_invite_is_in_tutorial(self):
        from hiq.tutorial import PAGES
        self.assertIn('https://discord.gg/pnAhQaKUNN',PAGES[-1][1])

    async def test_legacy_team_window_migrates_once_without_shortening(self):
        row=await self.war();st=row['state'];st['sends']=[2,7];st['stage']='teams'
        st.pop('clock_version');st.pop('clock_phase')
        st['deadlines']=[time.time()+60]*2;await self.save(row)
        before=time.time()
        self.assertTrue(await self.store.expire_war(row['id']))
        row=await self.store.session(row['id']);deadline=row['state']['deadlines'][0]
        self.assertGreaterEqual(deadline,before+WAR_WINDOW)
        self.assertFalse(await self.store.expire_war(row['id']))
        self.assertEqual((await self.store.session(row['id']))['state']['deadlines'][0],deadline)
