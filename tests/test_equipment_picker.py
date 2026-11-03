import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import test_v11
from hiq.data import CHARACTERS
from hiq.items import ITEMS
from hiq.raid_store import RaidStore
from hiq.cogs.raids import EquipmentView


class EquipmentPickerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def setup_items(self):
        async with self.store.tx() as c:
            a=await self.store._issue_card(c,1,'One',CHARACTERS[0])
            b=await self.store._issue_card(c,1,'One',CHARACTERS[1])
            x=await self.store._issue_item(c,1,'equipment',detail='water_bottle')
            y=await self.store._issue_item(c,1,'equipment',detail='energy_drink')
        return a,b,x,y

    async def test_transfer_releases_both_previous_slots_without_deleting_items(self):
        a,b,x,y=await self.setup_items();store=RaidStore(self.db)
        await store.equip(1,a.instance_id,x);await store.equip(1,b.instance_id,y)
        rows=await store.equipment_options(1,b)
        self.assertEqual(next(r for r in rows if r['id']==x)['equipped_card_id'],a.instance_id)
        await store.equip(1,b.instance_id,x)
        self.assertIsNone((await self.db.get_card(a.instance_id)).equipped_item_id)
        self.assertEqual((await self.db.get_card(b.instance_id)).equipped_item_id,x)
        rows=await store.equipment_options(1,b)
        self.assertIsNone(next(r for r in rows if r['id']==y)['equipped_card_id'])
        self.assertEqual({r['id'] for r in rows},{x,y})

    async def test_ineligible_or_foreign_requests_do_not_detach_source(self):
        a,b,x,y=await self.setup_items();store=RaidStore(self.db)
        await store.equip(1,a.instance_id,x)
        with self.assertRaises(ValueError):await store.equip(2,b.instance_id,x)
        self.assertEqual((await self.db.get_card(a.instance_id)).equipped_item_id,x)
        async with self.store.tx() as c:
            restricted=await self.store._issue_item(c,1,'equipment',detail='captain_s_armband')
            await c.execute('UPDATE cards SET equipped_item_id=?,equipment_key=? WHERE instance_id=?',(restricted,'captain_s_armband',a.instance_id))
        with self.assertRaises(ValueError):await store.equip(1,b.instance_id,restricted)
        self.assertEqual((await self.db.get_card(a.instance_id)).equipped_item_id,restricted)

    async def test_concurrent_moves_keep_one_equipped_copy(self):
        a,b,x,y=await self.setup_items();store=RaidStore(self.db)
        await asyncio.gather(store.equip(1,a.instance_id,x),store.equip(1,b.instance_id,x))
        cards=[await self.db.get_card(c.instance_id) for c in (a,b)]
        self.assertEqual(sum(c.equipped_item_id==x for c in cards),1)

    async def test_grouped_types_and_instance_pages_with_holder_details(self):
        a,b,x,y=await self.setup_items()
        rows=[dict(id=f'copy-{i}',detail='water_bottle',equipped_card_id=a.instance_id if i==0 else None,equipped_character_id=a.character_id if i==0 else None) for i in range(27)]
        bot=SimpleNamespace(db=self.db)
        view=EquipmentView(bot,1,b,rows)
        self.assertEqual(len(view.children[0].options),2)
        self.assertIn('27x',view.children[0].options[1].label)
        view=EquipmentView(bot,1,b,rows,key='water_bottle')
        self.assertEqual(view.pages,3);self.assertEqual(len(view.children[0].options),10)
        embed=view.instance_embed()
        self.assertIn('copy-0',embed.description);self.assertIn(a.instance_id,embed.description)
        self.assertIn('Equipped to',embed.description);self.assertIn('-# ',embed.description)
        denied=SimpleNamespace(data={},user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(denied))

    async def test_type_list_paginates_without_exceeding_discord_limit(self):
        a,b,x,y=await self.setup_items()
        rows=[dict(id=f'i-{i}',detail=item['key']) for i,item in enumerate(ITEMS[:30])]
        view=EquipmentView(SimpleNamespace(db=self.db),1,b,rows)
        self.assertEqual(view.pages,2);self.assertEqual(len(view.children[0].options),25)
        view.page=1;view.build();self.assertEqual(len(view.children[0].options),7)
