import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from hiq.database import Database
from hiq.event_store import EventStore
from hiq.event_modes import EVENT_TEAMS,cpu_specs
from hiq.data import CHAR_BY_ID


class AF1RotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/'rotation.sqlite3')
        await self.db.init();await self.db.ensure_user(1)
        self.store=EventStore(self.db)
        async with self.store.tx() as c:
            card=await self.store._issue_card(c,1,'Player',CHAR_BY_ID['Suga — Relentless Setter'])
        self.team=await self.store.team(1)
        self.team=await self.store.set_entry(1,1,0,card.instance_id,'back',self.team['revision'])
        await self.store.use(1,1)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def cool(self):
        async with self.store.tx() as c:
            await c.execute("UPDATE event_runs SET created_at='2026-01-01T00:00:00+00:00'")

    async def test_order_wraps_and_ignores_requested_cpu_index(self):
        for index in range(len(EVENT_TEAMS['all_for_one'])+1):
            expected=index%len(EVENT_TEAMS['all_for_one'])
            self.assertEqual(await self.store.af1_opponent(1),expected)
            row=await self.store.start(1,'all_for_one',11)
            self.assertEqual(row['state']['opponent'],expected)
            self.assertEqual(row['state']['label'],EVENT_TEAMS['all_for_one'][expected]['name'])
            await self.store.finish(1,row['id'],{'winner_side':'A'});await self.cool()

    async def test_loss_reward_draw_and_abandon_do_not_advance(self):
        for outcome in ('B','draw','abandon'):
            row=await self.store.start(1,'all_for_one')
            if outcome=='abandon':await self.store.abandon(1)
            else:
                with patch('hiq.youth_rewards.loss_reward',return_value=True):
                    earned=await self.store.finish(1,row['id'],{'winner_side':outcome})
                self.assertEqual(earned,1 if outcome=='B' else 0)
            self.assertEqual(await self.store.af1_opponent(1),0)
            await self.cool()

    async def test_settles_once_and_persists_without_changing_team(self):
        row=await self.store.start(1,'all_for_one')
        earned=await asyncio.gather(*[self.store.finish(1,row['id'],{'winner_side':'A'}) for _ in range(2)])
        self.assertEqual(sum(earned),1)
        await self.db.init()
        restarted=EventStore(Database(self.db.path))
        self.assertEqual(await restarted.af1_opponent(1),1)
        self.assertEqual(await restarted.af1_opponent(2),0)
        self.assertEqual((await restarted.team(1))['entries'],self.team['entries'])

    async def test_win_at_daily_cap_still_advances(self):
        from hiq.economy import local_date_key
        async with self.store.tx() as c:
            await c.execute('INSERT INTO af1_daily VALUES (?,?,25)',(1,local_date_key()))
        row=await self.store.start(1,'all_for_one')
        self.assertEqual(await self.store.finish(1,row['id'],{'winner_side':'A'}),0)
        self.assertEqual(await self.store.reward_status(1,row['id']),'capped')
        self.assertEqual(await self.store.af1_opponent(1),1)

    async def test_stale_menu_cannot_start_a_different_opponent(self):
        row=await self.store.start(1,'all_for_one',expected_index=0)
        await self.store.finish(1,row['id'],{'winner_side':'A'});await self.cool()
        with self.assertRaisesRegex(ValueError,'rotation changed'):
            await self.store.start(1,'all_for_one',expected_index=0)
        row=await self.store.start(1,'all_for_one',expected_index=1)
        self.assertEqual(row['state']['opponent'],1)

    async def test_legacy_pending_battle_resumes_its_original_opponent(self):
        row=await self.store.start(1,'all_for_one');state=row['state']
        state['opponent']=5;state['label']=EVENT_TEAMS['all_for_one'][5]['name']
        async with self.store.tx() as c:
            await c.execute('UPDATE event_runs SET state_json=? WHERE id=?',(json.dumps(state),row['id']))
        self.assertEqual(await self.store.af1_opponent(1),5)
        resumed=await self.store.start(1,'all_for_one',expected_index=0)
        self.assertEqual(resumed['id'],row['id']);self.assertEqual(resumed['state'],state)
        await self.store.finish(1,row['id'],{'winner_side':'A'})
        self.assertEqual(await self.store.af1_opponent(1),6)

    async def test_af1_menu_has_no_cpu_picker_and_refreshes_after_battle(self):
        import discord
        from hiq.cogs.events import EventMenu,EventsCog
        ctx=SimpleNamespace(author=SimpleNamespace(id=1))
        store=SimpleNamespace(af1_opponent=AsyncMock(return_value=1))
        cog=SimpleNamespace(store=store,run_event=AsyncMock())
        view=EventMenu(cog,ctx,'all_for_one',0)
        self.assertFalse(any(isinstance(child,discord.ui.Select) for child in view.children))
        self.assertTrue(any(isinstance(child,discord.ui.Select) for child in EventMenu(cog,ctx,'tiny_titans').children))
        interaction=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
        await next(c for c in view.children if c.label=='Battle').callback(interaction)
        self.assertEqual(view.index,1);interaction.edit_original_response.assert_awaited_once()
        self.assertEqual(set(EventsCog.all_for_one.clean_params),set())


class TakeruRosterTests(unittest.TestCase):
    def test_exact_requested_roster(self):
        index=next(i for i,r in enumerate(EVENT_TEAMS['tiny_titans']) if r['signature_key']=='takeru_zunda_mochi_bento')
        actual={char.name:(lane,card.merge_level,card.boon_stat,card.bane_stat,card.equipment_key) for card,char,lane in cpu_specs('tiny_titans',index)}
        self.assertEqual(actual,{
            'Akifumi':('back',10,'spd','hp','rally_footwork'),
            'Oikawa & Kageyama':('front',10,'pow','hp','open_guard'),
            'Hinata':('back',10,'spd','hp','smelling_salts'),
            'Takeru':('front',10,'spd','hp','burden_of_expectation'),
            'Atsumu':('back',10,'spd','hp','smelling_salts'),
            'Daichi':('front',10,'pow','hp','manager_s_good_luck_charm'),
        })
        self.assertIn('Hinata — Miracle Maker',{char.id for _,char,_ in cpu_specs('tiny_titans',index)})
