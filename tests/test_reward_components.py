import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from hiq.database import Database
from hiq.community_store import CommunityStore
from hiq.rewards import RewardStore
from hiq.data import CHARACTERS,CHAR_BY_ID
from hiq.youth_rewards import OIKAWA,DUO

MON='2026-11-09';SUN='2026-11-15'


class ComponentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.store=CommunityStore(self.db);self.rewards=RewardStore(self.db)
        async with self.store.tx() as c:await c.execute("UPDATE users SET created_at='2026-11-09T12:00:00+00:00'")
    async def asyncTearDown(self):self.temp.cleanup()
    def lineup(self,n=0):return [c.id for c in CHARACTERS[n:n+3]]
    async def own(self,cid,merge):
        async with self.store.tx() as c:
            card=await self.store._issue_card(c,1,'One',CHAR_BY_ID[cid])
            await c.execute('UPDATE cards SET merge_level=? WHERE instance_id=?',(merge,card.instance_id))
        return card
    async def claim(self,n=0,day=MON):return await self.rewards.award(1,'gauntlet_regional',day=day,lineup=self.lineup(n))

    async def test_fresh_bonus_pays_after_all_banked_clears_and_does_not_add_clear_count(self):
        for _ in range(7):await self.claim(day=SUN)
        before=(await self.rewards.daily(1,SUN))['gauntlet_regional']
        for n in range(1,7):
            payout=await self.claim(n,day=SUN)
            self.assertEqual((payout['coins'],payout['clear_coins'],payout['lineup_coins']),(50,0,50))
        self.assertEqual((await self.claim(7,day=SUN))['coins'],0)
        after=(await self.rewards.daily(1,SUN))['gauntlet_regional']
        self.assertEqual((before['wins'],after['wins'],after['coins']),(7,7,1750))

    async def test_oikawa_extra_clear_and_extra_lineup_have_independent_caps(self):
        await self.own(OIKAWA,10)
        self.assertEqual((await self.claim())['coins'],250)
        extra=await self.claim();self.assertEqual(extra['coins'],100)
        bonus=await self.claim(1);self.assertEqual((bonus['coins'],bonus['clear_coins'],bonus['lineup_coins']),(25,0,25))
        self.assertEqual((await self.claim(2))['coins'],0)
        self.assertEqual((await self.rewards.daily(1,MON))['gauntlet_regional']['coins'],375)

    async def test_duo_bonus_once_per_payout_and_no_duplicates_under_concurrency(self):
        await self.own(DUO,10)
        results=await asyncio.gather(self.claim(),self.claim())
        self.assertEqual(sorted(r['coins'] for r in results),[0,261])
        self.assertEqual((await self.db.get_user(1)).coins,261)

    async def test_existing_component_migration_is_idempotent_and_preserves_wallet(self):
        await self.own(OIKAWA,10)
        async with self.store.tx() as c:
            await c.execute("DELETE FROM economy_migrations WHERE version='12.1.13-reward-components'")
            await c.execute("INSERT INTO economy_daily VALUES(1,?,'gauntlet_regional',2,375,0)",(MON,))
            await c.execute("INSERT INTO youth_daily VALUES(1,?,'gauntlet_regional',1)",(MON,))
        await self.db.init();await self.db.init()
        self.assertEqual((await self.db.get_user(1)).coins,0)
        async with self.db.connect() as c:
            row=await (await c.execute('SELECT clear_coins,lineup_coins FROM gauntlet_rewards')).fetchone()
            self.assertEqual(tuple(row),(300,75))
        self.assertEqual((await self.claim(1))['coins'],0)

    async def test_raid_extra_is_recorded_and_shown_without_changing_normal_pool_cap(self):
        from hiq.raid_store import RaidStore
        from hiq.quest_pages import quest_pages
        raid=RaidStore(self.db);await self.own(OIKAWA,10);await self.own(DUO,0)
        async with self.store.tx() as c:
            await c.execute("INSERT INTO economy_daily VALUES(1,?,'raid',0,300,300)",(MON,))
        for kind in ('coins','clancoins'):
            sid='extra-'+kind
            async with self.store.tx() as c:
                st=dict(theme='tactics',difficulty='easy',format=3,rotation_version=2,a=[])
                await c.execute("INSERT INTO raid_runs VALUES(?,1,1,'running',?,?)",(sid,json.dumps(st),MON))
            reward=dict(kind=kind,key=kind,amount=100,weight=1,detail='')
            with patch('hiq.raid_store.local_date_key',return_value=MON),patch.object(raid,'reward_pool',return_value=[reward]),patch.object(raid,'reward_caps',return_value=[dict(remaining=1)]):
                row=await raid.finish(sid,dict(winner_side='A'))
            self.assertEqual(row['state']['currency']['coins' if kind=='coins' else 'clan_coins'],51)
        with patch('hiq.quest_pages.local_date_key',return_value=MON):pages=await quest_pages(self.db,1)
        self.assertIn('350 / 350',pages[0].description);self.assertIn('350 / 350',pages[1].description)
        async with self.db.connect() as c:
            base=await (await c.execute("SELECT coins,clan_coins FROM economy_daily WHERE reward_key='raid'")).fetchone()
            self.assertEqual(tuple(base),(300,300))
            self.assertEqual(len(await (await c.execute('SELECT * FROM raid_currency_extras')).fetchall()),2)

    async def test_tournament_all_humans_still_paid_when_champion_win_allowance_exhausted(self):
        from hiq.tournament_store import TournamentStore
        store=TournamentStore(self.db)
        for uid in (2,3,4):await self.db.ensure_user(uid)
        await self.rewards.award(1,'tournament',day=MON)
        async with self.store.tx() as c:
            await c.execute("INSERT INTO tournaments(id,channel_id,guild_id,creator_id,status,created_at) VALUES('cap-test',1,1,1,'running',?)",(MON,))
            for uid in (1,2,3,4):await c.execute("INSERT INTO tournament_entries VALUES('cap-test',?,?,'[]',?)",(uid,str(uid),MON))
        with patch('hiq.tournament_store.next_match',return_value=None),patch('hiq.tournament_store.champion',return_value=1):
            await store.finish('cap-test',MON);await store.finish('cap-test',MON)
        results=await store.rewards('cap-test')
        self.assertEqual({uid:r['amount'] for uid,r in results.items()},{1:150,2:150,3:150,4:150})
        self.assertEqual((await self.db.get_user(1)).coins,450)

    async def test_merge_selected_material_stays_visible_and_can_be_unselected(self):
        from hiq.cogs.merging import MultiMergeView
        async with self.store.tx() as c:
            base=await self.store._issue_card(c,1,'One',CHARACTERS[0]);donor=await self.store._issue_card(c,1,'One',CHARACTERS[0])
        view=MultiMergeView(SimpleNamespace(db=self.db),1,await self.db.get_cards(1),[],base=base.instance_id,materials=[donor.instance_id])
        select=view.children[0];self.assertEqual(select.min_values,0)
        self.assertTrue(select.options[0].default);self.assertIn('[Selected]',select.options[0].label)
        select._values=[]
        await select.callback(SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock())))
        self.assertEqual(view.materials,[]);self.assertFalse(view.children[0].options[0].default)
        self.assertIsNotNone(await self.db.get_card(donor.instance_id))

    async def test_unique_autoclean_uses_hundred_dollar_recycle_value(self):
        from hiq.gameplay_store import GameplayStore
        from hiq.items import SIGNATURE_ITEMS
        store=GameplayStore(self.db);key=SIGNATURE_ITEMS[0]['key']
        await store.set_autoclean_page(1,[key],[key])
        async with self.store.tx() as c:
            self.assertIsNone(await self.store._issue_item(c,1,'equipment',detail=key))
        self.assertEqual(await self.store.wallet(1),100)

    async def test_merge_page_changes_preserve_other_pages_and_search_selections(self):
        from hiq.cogs.merging import MultiMergeView
        async with self.store.tx() as c:
            cards=[await self.store._issue_card(c,1,'One',CHARACTERS[0]) for _ in range(23)]
        view=MultiMergeView(SimpleNamespace(db=self.db),1,cards,[],base=cards[0].instance_id)
        first=view.children[0];chosen=first.options[0].value;first._values=[chosen]
        interaction=SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await first.callback(interaction)
        view.page=1;view.rebuild();second=view.children[0];other=second.options[0].value;second._values=[other]
        await second.callback(interaction);self.assertEqual(set(view.materials),{chosen,other})
        view.query=chosen;view.page=0;view.rebuild();view.children[0]._values=[]
        await view.children[0].callback(interaction)
        self.assertEqual(view.materials,[other])
