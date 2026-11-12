import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from hiq.character_notes import explanations, CharacterNotesView
from hiq.data import CHARACTERS
from hiq.cards import ability_block
from hiq.database import Database
from hiq.rewards import RewardStore
from hiq.quest_pages import quest_pages
from hiq.items import find_items
from hiq.tutorial import PAGES


class NotesTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_effects_are_separate_private_pages(self):
        character=next(c for c in CHARACTERS if c.name=='Iizuna')
        notes=dict(explanations(character))
        self.assertIn('[Clean Receive]',notes)
        self.assertIn('[Guiding Break]',notes)
        self.assertNotIn('Positive [Bonus] status',ability_block(character,'C'))
        view=CharacterNotesView(character)
        interaction=SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(send_message=AsyncMock()))
        await view.children[0].callback(interaction)
        payload=interaction.response.send_message.call_args.kwargs
        self.assertTrue(payload['ephemeral'])
        self.assertEqual(len(payload['view'].pages),len(notes))
        self.assertTrue(all(len(p.description)<=4096 for p in payload['view'].pages))

    async def test_shared_effect_is_available_on_other_character(self):
        character=next(c for c in CHARACTERS if c.name=='Yamamoto')
        self.assertIn('[Fatigue]',dict(explanations(character)))

    async def test_names_keep_old_searches_and_inventory_keys(self):
        for old,new,key in [('Loaded Approach',"Ace's Approach",'loaded_approach'),('Specialist Badge','Signature Move','specialist_badge'),('Recovery Denial','Conditioning Circuit','recovery_denial')]:
            self.assertEqual(find_items(old)[0]['key'],key)
            self.assertEqual(find_items(new)[0]['key'],key)

    async def test_tutorial_is_readable_and_does_not_invent_three_card_star_packs(self):
        self.assertGreater(len(PAGES),9)
        self.assertTrue(all(len(text)<4096 for _,text in PAGES))
        self.assertIn('Star Packs contain one card',' '.join(text for _,text in PAGES))


class RewardChangesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.rewards=RewardStore(self.db)

    async def asyncTearDown(self):self.temp.cleanup()

    async def test_prefecture_three_wins_three_hundred(self):
        values=[(await self.rewards.award(1,'prefecture',day='2026-11-02'))['coins'] for _ in range(4)]
        self.assertEqual(values,[100,100,100,0])

    async def test_cpu_repeat_does_not_fill_tournament_human_bonus(self):
        values=[]
        for human in (False,False,True,True):
            values.append((await self.rewards.award(1,'tournament',day='2026-11-02',human_tournament=human))['coins'])
        self.assertEqual(values,[300,0,150,0])

    async def test_qualifying_tournament_win_pays_four_fifty_once(self):
        self.assertEqual((await self.rewards.award(1,'tournament',day='2026-11-02',human_tournament=True))['coins'],450)
        self.assertEqual((await self.rewards.award(1,'tournament',day='2026-11-02',human_tournament=True))['coins'],0)

    async def test_five_quest_pages_and_all_training_schools(self):
        pages=await quest_pages(self.db,1)
        self.assertEqual(len(pages),5)
        self.assertIn('Coin Dailies',pages[0].title)
        for school in ('Karasuno','Tsubakihara','Mujinazaka'):self.assertIn(school,pages[2].description)
        self.assertTrue(all(len(p.description)<4096 for p in pages))

    async def test_unique_gauntlet_lineups_ignore_order_and_reset_separately(self):
        from hiq.community_store import CommunityStore
        async with CommunityStore(self.db).tx() as c:
            await c.execute("UPDATE users SET created_at='2026-10-19T12:00:00+00:00'")
        chars=list({c.name:c for c in CHARACTERS}.values())
        lineup=[c.id for c in chars[:3]];fresh=[c.id for c in chars[1:4]]
        async def award(day,key='gauntlet_regional',team=None):
            return (await self.rewards.award(1,key,day=day,lineup=team or lineup))['coins']
        self.assertEqual(await award('2026-10-26'),250)
        self.assertEqual(await award('2026-10-27',team=list(reversed(lineup))),200)
        self.assertEqual(await award('2026-10-27',team=fresh),50)
        self.assertEqual(await award('2026-10-27',team=fresh),0)
        self.assertEqual(await award('2026-10-27','gauntlet_national'),250)
        self.assertEqual(await award('2026-11-02'),250)
