import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hiq.database import Database
from hiq.community_store import CommunityStore
from hiq.data import CHARACTERS


class MergeTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.store=CommunityStore(self.db)
        async with self.store.tx() as c:
            self.base=await self.store._issue_card(c,1,'One',CHARACTERS[0])
            self.donor=await self.store._issue_card(c,1,'One',CHARACTERS[0])
    async def asyncTearDown(self):self.temp.cleanup()
    async def test_merge_replaces_all_six_saved_positions_and_legacy_teams(self):
        keys=('front_id','front2_id','front3_id','back1_id','back2_id','back3_id')
        for slot,key in enumerate(keys,1):
            await self.db.ensure_saved_team(1,slot)
            async with self.store.tx() as c:
                await c.execute(f'UPDATE saved_teams SET team_format=6,{key}=? WHERE owner_id=1 AND slot=?',(self.donor.instance_id,slot))
        async with self.store.tx() as c:
            await c.execute('INSERT INTO teams VALUES (?,?,?,?,?,?)',(1,'attack',self.donor.instance_id,self.donor.instance_id,self.donor.instance_id,'now'))
        await self.store.merge_many(1,self.base.instance_id,[self.donor.instance_id])
        for slot,key in enumerate(keys,1):self.assertEqual(getattr(await self.db.get_saved_team(1,slot),key),self.base.instance_id)
        async with self.db.connect() as c:
            team=await self.store.one(c,'SELECT * FROM teams WHERE owner_id=1')
        self.assertEqual(team['front_id'],self.base.instance_id)
        self.assertIsNone(await self.db.get_card(self.donor.instance_id))
    async def test_direct_two_card_merge_also_replaces_team_slot(self):
        await self.db.ensure_saved_team(1,1)
        async with self.store.tx() as c:await c.execute('UPDATE saved_teams SET back2_id=? WHERE owner_id=1',(self.donor.instance_id,))
        await self.db.merge_cards(1,self.base.full_instance_id,self.donor.full_instance_id)
        self.assertEqual((await self.db.get_saved_team(1,1)).back2_id,self.base.instance_id)
    async def test_max_donor_requires_confirmation_and_keeps_target_nature(self):
        async with self.store.tx() as c:
            await c.execute("UPDATE cards SET merge_level=5,boon_stat='spd',bane_stat='hp' WHERE instance_id=?",(self.base.instance_id,))
            await c.execute("UPDATE cards SET merge_level=10,boon_stat='rec',bane_stat='jmp' WHERE instance_id=?",(self.donor.instance_id,))
        with self.assertRaises(ValueError):await self.store.merge_many(1,self.base.instance_id,[self.donor.instance_id])
        self.assertEqual((await self.db.get_card(self.donor.instance_id)).merge_level,10)
        merged=await self.store.merge_many(1,self.base.instance_id,[self.donor.instance_id],confirm_max_transfer=True)
        self.assertEqual((merged.merge_level,merged.boon_stat,merged.bane_stat),(10,'spd','hp'))
        self.assertEqual(merged.full_instance_id,self.base.full_instance_id)
    async def test_extra_materials_and_max_survivor_remain_rejected(self):
        async with self.store.tx() as c:
            await c.execute('UPDATE cards SET merge_level=10 WHERE instance_id=?',(self.donor.instance_id,))
            item=await self.store._issue_item(c,1,'mergecopy',CHARACTERS[0].rarity)
        with self.assertRaises(ValueError):await self.store.merge_many(1,self.base.instance_id,[self.donor.instance_id,item],confirm_max_transfer=True)
        with self.assertRaises(ValueError):await self.store.merge_many(1,self.donor.instance_id,[self.base.instance_id],confirm_max_transfer=True)
    async def test_ui_max_donor_needs_second_click(self):
        from hiq.cogs.merging import MultiMergeView
        async with self.store.tx() as c:await c.execute('UPDATE cards SET merge_level=10 WHERE instance_id=?',(self.donor.instance_id,))
        view=MultiMergeView(SimpleNamespace(db=self.db),1,await self.db.get_cards(1),[],base=self.base.instance_id,materials=[self.donor.instance_id])
        interaction=SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        button=next(b for b in view.children if getattr(b,'label',None)=='Merge')
        self.assertFalse(button.disabled);await button.callback(interaction)
        self.assertIsNotNone(await self.db.get_card(self.donor.instance_id))
        button=next(b for b in view.children if getattr(b,'label',None)=='Confirm +10 Transfer')
        await button.callback(interaction)
        self.assertIsNone(await self.db.get_card(self.donor.instance_id))
        self.assertEqual((await self.db.get_card(self.base.instance_id)).merge_level,10)
