import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from hiq.inventory import inventory_entries,search_entries,parse_query
from hiq.cogs.inventory_view import InventoryView
from hiq.cogs.community import CommunityCog
from hiq.cogs.utility import UtilityCog
from hiq.community_store import CommunityStore
from hiq.database import Database
from hiq.shard_balances import credit


def item(i,key='captain_s_armband',**extra):
    return dict(id=f'it-{i:06}',kind='equipment',stars=0,detail=key,**extra)


class InventorySearchTests(unittest.TestCase):
    def setUp(self):
        self.rows=inventory_entries({'Karasuno':7},[
            dict(kind='character_shard',detail='Suga — Relentless Setter',quantity=9),
            dict(kind='item_shard',detail='captain_s_armband',quantity=4),
            dict(kind='item_shard',detail='loaded_approach',quantity=3),
            dict(kind='character_shard',detail='Sakusa — Coiled Precision',quantity=0),
        ],[item(1),item(2,'suga_mapo_tofu_bowls'),item(3,'loaded_approach')])

    def test_case_punctuation_typos_aliases_and_ids(self):
        for query in ('CAPTAIN’S ARMBAND','captainsarmband','captans armband'):
            matches=search_entries(self.rows,query)
            self.assertEqual({r.category for r in matches},{'Equip Items','Equip Shards'})
            self.assertTrue(all("Captain's Armband" in r.label for r in matches))
        self.assertEqual(search_entries(self.rows,'IT000001')[0].reference,'it-000001')
        self.assertEqual(len(search_entries(self.rows,'loaded approach')),2)
        self.assertEqual(search_entries(self.rows,'SUGA shards')[0].category,'Character Shards')
        self.assertEqual(search_entries(self.rows,'KAR')[0].category,'School Shards')
        self.assertEqual(search_entries(self.rows,'Mapo Tofu Bowls')[0].label,'Tactics Playbook 3rd-Year Edition')
        self.assertEqual(search_entries(self.rows,'zzzzzxxyy'),[])
        self.assertFalse(search_entries(self.rows,'Sakusa'))

    def test_stacking_retains_all_instances_and_search_by_any_id(self):
        rows=inventory_entries({},[],[item(1),item(2,equipped_on='card-1')],stacked=True)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].quantity,2)
        self.assertEqual(search_entries(rows,'it000002'),rows)

    def test_mode_commands_and_multiword_queries(self):
        self.assertEqual(parse_query('REGULAR Captain’s Armband'),('regular','Captain’s Armband'))
        self.assertEqual(parse_query('suga relentless setter'),('','suga relentless setter'))
        self.assertEqual(parse_query('toggle'),('toggle',''))

    def test_all_inventory_kinds_are_separated_including_unique_event_shards(self):
        rows=inventory_entries({'Karasuno':7},[
            dict(kind='character_shard',detail='Suga — Relentless Setter',quantity=9),
            dict(kind='item_shard',detail='captain_s_armband',quantity=4),
            dict(kind='item_shard',detail='suga_mapo_tofu_bowls',quantity=25),
        ],[item(1),item(2,'suga_mapo_tofu_bowls'),dict(id='it-pack',kind='starpack',stars=5,detail=''),
           dict(id='it-blessing',kind='blessing',stars=3,detail=''),dict(id='it-merge',kind='mergecopy',stars=3,detail='')])
        from hiq.inventory import CATEGORIES
        self.assertEqual({r.category for r in rows},set(CATEGORIES))
        self.assertEqual([r.quantity for r in rows if r.category=='Unique Equip Shards'],[25])
        self.assertEqual(len([r for r in rows if r.category=='Non-equip Items']),3)


class InventoryMenuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'inventory.sqlite3')
        await self.db.init();await self.db.ensure_user(1);await self.db.ensure_user(2)
        self.store=CommunityStore(self.db)

    async def asyncTearDown(self):self.tmp.cleanup()

    async def test_25_rows_page_arrows_search_clear_and_owner(self):
        view=InventoryView(self.store,1,'Player',{},[],[item(i) for i in range(51)])
        self.assertEqual([len(p) for p in view.pages],[25,25,1])
        self.assertLess(len(view.embed()),6000);self.assertLessEqual(len(view.embed().description),4096)
        other=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(other));other.response.send_message.assert_awaited_once()
        i=SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(edit_message=AsyncMock()))
        await next(c for c in view.children if str(getattr(c,'emoji',''))=='▶️').callback(i);self.assertEqual(view.page,1)
        await next(c for c in view.children if str(getattr(c,'emoji',''))=='▶️').callback(i);self.assertEqual(view.page,2)
        self.assertTrue(next(c for c in view.children if str(getattr(c,'emoji',''))=='▶️').disabled)
        view.query='IT000050';view.page=0;view.build()
        self.assertEqual(view.total,1);self.assertIn('it-000050',view.embed().description)
        clear=next(c for c in view.children if getattr(c,'label',None)=='Clear search')
        await clear.callback(i);self.assertEqual(view.total,51)

    async def test_stacked_button_persists_preference_without_spending(self):
        async with self.store.tx() as c:
            await self.store._issue_item(c,1,'equipment',detail='captain_s_armband')
            await self.store._issue_item(c,1,'equipment',detail='captain_s_armband')
        items=await self.store.items(1)
        view=InventoryView(self.store,1,'Player',{},[],items)
        i=SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(edit_message=AsyncMock()))
        await next(c for c in view.children if getattr(c,'label',None)=='Stack items').callback(i)
        self.assertEqual(view.total,1);self.assertIn('2×',view.embed().description)
        async with self.db.connect() as c:
            self.assertEqual((await self.store.one(c,'SELECT stacked FROM inventory_preferences WHERE user_id=1'))['stacked'],1)
        self.assertEqual(len(await self.store.items(1)),2)

    async def test_command_searches_only_owner_and_preserves_existing_modes(self):
        async with self.store.tx() as c:
            await credit(c,1,'character_shard','Suga — Relentless Setter',7)
            await self.store._issue_item(c,1,'equipment',detail='captain_s_armband')
            await self.store._issue_item(c,2,'equipment',detail='suga_mapo_tofu_bowls')
        cog=CommunityCog(SimpleNamespace(db=self.db))
        ctx=SimpleNamespace(author=SimpleNamespace(id=1,display_name='Player'),send=AsyncMock())
        await CommunityCog.inventory.callback(cog,ctx,query='SUGA')
        text=ctx.send.call_args.kwargs['embed'].description
        self.assertIn('Relentless Setter',text);self.assertNotIn('Tactics Playbook',text)
        await CommunityCog.inventory.callback(cog,ctx,query='stacked CAPTAIN’S ARMBAND')
        self.assertTrue(ctx.send.call_args.kwargs['view'].stacked)
        await CommunityCog.inventory.callback(cog,ctx,query='toggle')
        self.assertFalse(ctx.send.call_args.kwargs['view'].stacked)

    async def test_links_include_server_and_all_website_pages(self):
        cog=UtilityCog(SimpleNamespace(db=self.db));ctx=SimpleNamespace(send=AsyncMock())
        with patch('hiq.cogs.utility.COMMUNITY_SERVER_URL','https://discord.gg/example'),patch('hiq.cogs.utility.BOT_WEBSITE_URL','https://example.org/mpgallery'):
            await UtilityCog.links.callback(cog,ctx)
        text=ctx.send.call_args.kwargs['embed'].description
        for url in ('https://discord.gg/example','https://example.org/mpgallery/','https://example.org/mpgallery/items.html','https://example.org/mpgallery/tierlist.html'):
            self.assertIn(url,text)

    async def test_category_selection_preserves_filter_with_search_and_stacking(self):
        balances=[dict(kind='item_shard',detail='suga_mapo_tofu_bowls',quantity=25),dict(kind='item_shard',detail='captain_s_armband',quantity=8),dict(kind='character_shard',detail='Suga — Relentless Setter',quantity=6)]
        items=[item(i,'suga_mapo_tofu_bowls') for i in range(51)]+[item(100)]
        view=InventoryView(self.store,1,'Player',{'Karasuno':3},balances,items,category='Unique Equip Items')
        self.assertEqual([len(p) for p in view.pages],[25,25,1])
        self.assertTrue(all(row.category=='Unique Equip Items' for p in view.pages for row in p))
        i=SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(edit_message=AsyncMock()))
        await next(c for c in view.children if getattr(c,'label',None)=='Stack items').callback(i)
        self.assertEqual(view.category,'Unique Equip Items');self.assertEqual(view.total,1)
        self.assertIn('51×',view.embed().description)
        menu=next(c for c in view.children if hasattr(c,'options'));menu._values=['Unique Equip Shards']
        await menu.callback(i)
        self.assertEqual(view.category,'Unique Equip Shards');self.assertEqual(view.total,1)
        self.assertIn('25×',view.embed().description);self.assertNotIn("Captain",view.embed().description)
        view.query='MAPO TOFU';view.build();self.assertEqual(view.total,1)
        menu=next(c for c in view.children if hasattr(c,'options'));menu._values=['Equip Items']
        await menu.callback(i);self.assertEqual(view.total,0)
        self.assertEqual(view.query,'MAPO TOFU')

    async def test_plain_inventory_defaults_to_one_category_and_name_search_spans_categories(self):
        items=[item(1),dict(id='it-pack',kind='starpack',stars=5,detail='')]
        view=InventoryView(self.store,1,'Player',{},[],items)
        self.assertEqual(view.category,'Non-equip Items');self.assertEqual(view.total,1)
        searched=InventoryView(self.store,1,'Player',{},[dict(kind='item_shard',detail='captain_s_armband',quantity=4)],items,query='captain')
        self.assertEqual(searched.category,'All');self.assertEqual(searched.total,2)
        self.assertLessEqual(len(next(c for c in searched.children if hasattr(c,'options')).options),25)
