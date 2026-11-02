import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import test_v11
from hiq.data import CHARACTERS
from hiq.items import ITEMS
from hiq.raid_store import RaidStore
from hiq.cogs.raids import RaidCog


class AllRecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def test_bulk_recycles_only_owned_unequipped_equipment(self):
        raid=RaidStore(self.db);keys=[i['key'] for i in ITEMS[:2]]
        async with self.store.tx() as c:
            equipped=await self.store._issue_item(c,1,'equipment',detail=keys[0])
            for key in keys:await self.store._issue_item(c,1,'equipment',detail=key)
            other=await self.store._issue_item(c,2,'equipment',detail=keys[0])
            preserved=[await self.store._issue_item(c,1,kind,3) for kind in ('blessing','mergecopy','starpack')]
            card=await self.store._issue_card(c,1,'One',CHARACTERS[0])
            await c.execute('UPDATE cards SET equipped_item_id=?,equipment_key=? WHERE instance_id=?',(equipped,keys[0],card.instance_id))
        result=await raid.recycle_equipment(1,all_items=True)
        self.assertEqual(result,dict(count=2,clan_coins=sum(i['shard_cost'] for i in ITEMS[:2])))
        self.assertEqual({i['id'] for i in await self.store.items(1)},set(preserved+[equipped]))
        self.assertEqual({i['id'] for i in await self.store.items(2)},{other})
        self.assertEqual((await self.db.get_card(card.instance_id)).equipped_item_id,equipped)
        self.assertEqual(await self.store.wallet(1),result['clan_coins'])

    async def test_concurrent_bulk_requests_cannot_pay_twice(self):
        async with self.store.tx() as c:await self.store._issue_item(c,1,'equipment',detail=ITEMS[0]['key'])
        raid=RaidStore(self.db)
        results=await asyncio.gather(raid.recycle_equipment(1,all_items=True),raid.recycle_equipment(1,all_items=True),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(await self.store.wallet(1),ITEMS[0]['shard_cost'])

    async def test_command_without_name_uses_bulk_path(self):
        store=SimpleNamespace(recycle_equipment=AsyncMock(return_value=dict(count=3,clan_coins=20)))
        context=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        await RaidCog.allrecycle.callback(SimpleNamespace(store=store),context)
        store.recycle_equipment.assert_awaited_once_with(1,all_items=True)
        self.assertIn('3 unequipped',context.send.call_args.args[0])
