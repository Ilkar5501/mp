import unittest
from collections import Counter
import test_v11
from hiq.items import ITEMS,THEMES
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance


class ItemCatalogueTests(unittest.TestCase):
    def test_all_four_price_tiers_and_caps(self):
        for item in ITEMS:
            old=item['recycle_value']
            self.assertEqual(item['shard_cost'],{5:3,10:6,20:9,25:12}[old])
            self.assertEqual(item['daily_cap'],2*item['shard_cost'])
            self.assertEqual(item['weekly_cap'],14*item['shard_cost'])
            self.assertEqual(item['drop_rate'],{5:.04,10:.02,20:.01,25:.005}[old])

    def test_exactly_twelve_items_per_raid(self):
        self.assertEqual(Counter(i['theme'] for i in ITEMS),Counter({theme:12 for theme in THEMES}))
        for theme in THEMES:
            pool=RaidStore(None).reward_pool(theme,{},'hard')
            self.assertEqual(len([r for r in pool if r['kind']=='item_shard']),12)
            self.assertAlmostEqual(sum(r['weight'] for r in pool),1)


class ItemCraftingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def test_each_tier_crafts_and_bulkcraft_preserves_remainders(self):
        raid=RaidStore(self.db)
        selected=[next(i for i in ITEMS if i['shard_cost']==cost) for cost in (3,6,9,12)]
        async with self.store.tx() as c:
            for item in selected:await credit(c,1,'item_shard',item['key'],item['shard_cost']*2+1)
        for item in selected:await raid.craft(1,'Tester',item['key'])
        made=await raid.craft_all_items(1)
        self.assertEqual(sum(r['count'] for r in made),4)
        async with self.db.connect() as c:
            for item in selected:self.assertEqual(await balance(c,1,'item_shard',item['key']),1)

    async def test_caps_follow_two_recipes_per_day_and_recycle_payout_is_unchanged(self):
        raid=RaidStore(self.db);item=next(i for i in ITEMS if i['shard_cost']==12)
        caps=raid.reward_caps(item['theme'],{'_days':3})
        self.assertEqual(next(r for r in caps if r['key']==item['key'])['cap'],72)
        async with self.store.tx() as c:
            iid=await self.store._issue_item(c,1,'equipment',detail=item['key'])
        self.assertEqual((await raid.recycle_equipment(1,item_id=iid))['clan_coins'],25)
