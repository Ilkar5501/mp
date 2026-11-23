import unittest
from collections import Counter
import test_v11
from hiq.items import ITEMS,THEMES
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance


class ItemCatalogueTests(unittest.TestCase):
    def test_all_four_price_tiers_and_caps(self):
        for item in ITEMS:
            cost=item['shard_cost']
            self.assertIn(cost,(2,4,6,8))
            self.assertEqual(item['recycle_value'],4*cost)
            self.assertEqual(item['daily_cap'],item['shard_cost'])
            self.assertEqual(item['weekly_cap'],7*item['shard_cost'])
            self.assertEqual(item['drop_rate'],{2:.04,4:.02,6:.01,8:.005}[cost])

    def test_new_items_have_separate_raid_themes(self):
        self.assertEqual(Counter(i['theme'] for i in ITEMS),Counter({theme:(13 if theme in ('recovery','tempo') else 12) for theme in THEMES}))
        for theme in THEMES:
            pool=RaidStore(None).reward_pool(theme,{},'hard')
            self.assertEqual(len([r for r in pool if r['kind']=='item_shard']),sum(i['theme']==theme for i in ITEMS))
            self.assertAlmostEqual(sum(r['weight'] for r in pool),1)

    def test_capped_items_leave_pool_and_increase_non_item_reward_chances(self):
        raid=RaidStore(None)
        for theme in THEMES:
            for difficulty in ('easy','hard','extreme'):
                initial=raid.reward_pool(theme,{},difficulty)
                earned={i['key']:i['daily_cap'] for i in ITEMS if i['theme']==theme}
                pool=raid.reward_pool(theme,earned,difficulty)
                self.assertEqual({r['kind'] for r in pool},{'coins','clancoins','character_shard'})
                self.assertAlmostEqual(sum(r['weight'] for r in pool),1)
                for reward in pool:
                    self.assertGreater(reward['weight'],next(r['weight'] for r in initial if r['key']==reward['key']))
                self.assertEqual({r['kind']:r['cap'] for r in pool},{'coins':300,'clancoins':300,'character_shard':50})


class ItemCraftingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def test_each_tier_crafts_and_bulkcraft_preserves_remainders(self):
        raid=RaidStore(self.db)
        selected=[next(i for i in ITEMS if i['shard_cost']==cost) for cost in (2,4,6,8)]
        async with self.store.tx() as c:
            for item in selected:await credit(c,1,'item_shard',item['key'],item['shard_cost']*2+1)
        for item in selected:await raid.craft(1,'Tester',item['key'])
        made=await raid.craft_all_items(1)
        self.assertEqual(sum(r['count'] for r in made),4)
        async with self.db.connect() as c:
            for item in selected:self.assertEqual(await balance(c,1,'item_shard',item['key']),1)

    async def test_caps_follow_one_recipe_per_day_and_recycle_pays_four_per_shard(self):
        raid=RaidStore(self.db);item=next(i for i in ITEMS if i['shard_cost']==8)
        caps=raid.reward_caps(item['theme'],{'_days':3})
        self.assertEqual(next(r for r in caps if r['key']==item['key'])['cap'],24)
        async with self.store.tx() as c:
            iid=await self.store._issue_item(c,1,'equipment',detail=item['key'])
        self.assertEqual((await raid.recycle_equipment(1,item_id=iid))['clan_coins'],32)

    async def test_upgrade_preserves_shards_equipment_and_already_earned_progress(self):
        from hiq.economy import local_date_key
        raid=RaidStore(self.db);item=ITEMS[0];earned=item['shard_cost']*3
        async with self.store.tx() as c:
            await credit(c,1,'item_shard',item['key'],17)
            iid=await self.store._issue_item(c,1,'equipment',detail=item['key'])
            await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(1,local_date_key(),item['key'],earned))
        await self.db.init()
        progress=await raid.shard_progress(1)
        self.assertEqual(progress[item['key']],earned)
        self.assertNotIn(item['key'],{r['key'] for r in raid.reward_pool(item['theme'],progress)})
        async with self.db.connect() as c:
            self.assertEqual(await balance(c,1,'item_shard',item['key']),17)
            self.assertIsNotNone(await (await c.execute('SELECT id FROM inventory_items WHERE id=?',(iid,))).fetchone())

    async def test_item_browser_shows_one_recipe_bank_and_four_times_recycle_value(self):
        from types import SimpleNamespace
        from hiq.cogs.item_browser import ItemBrowser
        item=ITEMS[0]
        text=ItemBrowser(SimpleNamespace(db=self.db),1,[item],{'_days':3}).embed().description
        self.assertIn(f'+{item["shard_cost"]}/day',text)
        self.assertIn(f'(0/{item["shard_cost"]*3})',text)
        self.assertIn(f'Recycle Value: {item["shard_cost"]*4} Clan Dollars',text)
