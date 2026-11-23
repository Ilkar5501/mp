import unittest
import test_v11
from hiq.contest import character
from hiq.raid_store import RaidStore
from hiq.pvp_store import PvPStore
from hiq.event_store import EventStore

class LockedLoadoutTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def setup_teams(self):
        raid=RaidStore(self.db)
        async with self.store.tx() as c:
            cards=[await self.store._issue_card(c,1,'One',character(name)) for name in ('Suga','Lev','Kai','Daichi')]
            items=[await self.store._issue_item(c,1,'equipment',detail='fish_cakes') for _ in range(3)]
        for slot in (1,2,3):
            await self.db.ensure_saved_team(1,slot)
            await self.db.replace_saved_team(1,slot,*(card.instance_id for card in cards[:3]))
        return raid,cards,items

    async def test_same_character_can_hold_distinct_copies_in_multiple_locked_teams(self):
        raid,cards,items=await self.setup_teams();main=cards[0].instance_id
        await raid.equip(1,main,items[0]);await self.db.set_team_lock(1,1,True)
        await self.db.set_team_lock(1,3,True)  # same character, same copy, two locks
        await raid.equip(1,main,items[1]);await self.db.set_team_lock(1,2,True)
        await raid.equip(1,main,items[2])
        self.assertEqual((await self.db.team_specs(1,1))[0][0].equipped_item_id,items[0])
        self.assertEqual((await self.db.team_specs(1,2))[0][0].equipped_item_id,items[1])
        with self.assertRaisesRegex(ValueError,'Team 1'):
            await raid.equip(1,cards[1].instance_id,items[0])
        with self.assertRaisesRegex(ValueError,'Team 1'):
            await raid.recycle_equipment(1,item_id=items[0])
        await self.db.set_team_lock(1,1,False)
        self.assertEqual((await self.db.team_specs(1,1))[0][0].equipped_item_id,items[2])
        with self.assertRaisesRegex(ValueError,'Team 3'):await raid.equip(1,cards[1].instance_id,items[0])
        await self.db.set_team_lock(1,3,False)
        await raid.equip(1,cards[1].instance_id,items[0])
        self.assertEqual((await self.db.get_card(cards[1].instance_id)).equipped_item_id,items[0])

    async def test_different_item_copies_can_be_reserved_by_different_characters(self):
        raid,cards,items=await self.setup_teams()
        await raid.equip(1,cards[0].instance_id,items[0]);await raid.equip(1,cards[1].instance_id,items[1])
        await self.db.set_team_lock(1,1,True)
        specs=await self.db.team_specs(1,1)
        self.assertEqual([card.equipped_item_id for card,_,_ in specs[:2]],items[:2])
        await self.db.clear_saved_team(1,1)
        self.assertFalse(await self.db.team_locked(1,1))

    async def test_merging_a_team_member_preserves_the_locked_item_on_the_survivor(self):
        raid,cards,items=await self.setup_teams()
        await raid.equip(1,cards[0].instance_id,items[0]);await self.db.set_team_lock(1,1,True)
        async with self.store.tx() as c:survivor=await self.store._issue_card(c,1,'One',character('Suga'))
        await self.db.merge_cards(1,survivor.instance_id,cards[0].instance_id)
        saved=(await self.db.team_specs(1,1))[0][0]
        self.assertEqual(saved.instance_id,survivor.instance_id)
        self.assertEqual(saved.equipped_item_id,items[0])
        with self.assertRaisesRegex(ValueError,'Team 1'):await raid.equip(1,cards[1].instance_id,items[0])

    async def test_pvp_unlock_refreshes_source_roster_and_current_items(self):
        raid,cards,items=await self.setup_teams();pvp=PvPStore(self.db)
        await raid.equip(1,cards[0].instance_id,items[0])
        await pvp.set_team(1,1);await pvp.set_lock(1,3,True)
        await raid.equip(1,cards[0].instance_id,items[1])
        await self.db.replace_saved_team(1,1,cards[0].instance_id,cards[1].instance_id,cards[3].instance_id)
        async with self.store.tx() as c:
            locked=await pvp.specs_tx(c,1,3)
        self.assertEqual(locked[2][0].instance_id,cards[2].instance_id)
        self.assertEqual(locked[0][0].equipped_item_id,items[0])
        await pvp.set_lock(1,3,False)
        async with self.store.tx() as c:unlocked=await pvp.specs_tx(c,1,3)
        self.assertEqual(unlocked[2][0].instance_id,cards[3].instance_id)
        self.assertEqual(unlocked[0][0].equipped_item_id,items[1])

    async def test_af1_uses_locked_equipment_and_unlock_releases_it(self):
        raid,cards,items=await self.setup_teams();af1=EventStore(self.db)
        await raid.equip(1,cards[0].instance_id,items[0])
        row=await af1.team(1,1)
        row=await af1.set_entry(1,1,0,cards[0].instance_id,'front',row['revision'])
        row=await af1.set_lock(1,1,True,row['revision'])
        await raid.equip(1,cards[0].instance_id,items[1])
        async with self.store.tx() as c:specs=await af1._specs(c,1,row['entries'],allow_empty_supports=True,slot=1)
        self.assertEqual(specs[0][0].equipped_item_id,items[0])
        row=await af1.set_lock(1,1,False,row['revision'])
        async with self.store.tx() as c:specs=await af1._specs(c,1,row['entries'],allow_empty_supports=True,slot=1)
        self.assertEqual(specs[0][0].equipped_item_id,items[1])
