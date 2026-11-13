import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hiq.database import Database
from hiq.event_store import EventStore
from hiq.gameplay_store import GameplayStore
from hiq.data import CHAR_BY_ID

DUO='Oikawa & Kageyama — Kings of the Playground'


class AF1SlotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'game.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.store=EventStore(self.db)
        self.cards=[]
        for slot,cid in enumerate(['Suga — Relentless Setter','Lev — Young Lion','Daichi — Steadfast Captain','Atsumu — Arrogant Genius','Akifumi — Supporting Teammate'],1):
            async with self.store.tx() as c:card=await self.store._issue_card(c,1,'Player',CHAR_BY_ID[cid])
            row=await self.store.team(1,slot)
            await self.store.set_entry(1,slot,0,card.instance_id,'front',row['revision'])
            self.cards.append(card)

    async def asyncTearDown(self):self.tmp.cleanup()

    async def cool(self):
        async with self.store.tx() as c:await c.execute("UPDATE event_runs SET created_at='2026-01-01T00:00:00+00:00'")

    async def test_all_five_slots_select_correct_cards_and_share_rotation(self):
        for slot,card in enumerate(self.cards,1):
            row=await self.store.start(1,'all_for_one',af1_slot=slot)
            self.assertEqual(row['state']['af1_slot'],slot)
            self.assertEqual(row['state']['a'][0]['card']['instance_id'],card.instance_id)
            self.assertEqual(row['state']['opponent'],slot-1)
            self.assertEqual((await self.store.wallet(1))['af1_slot'],slot)
            await self.store.finish(1,row['id'],{'winner_side':'A'});await self.cool()
        restarted=EventStore(self.db)
        row=await restarted.start(1,'all_for_one')
        self.assertEqual(row['state']['af1_slot'],5)
        self.assertEqual(row['state']['opponent'],5)

    async def test_pending_same_team_resumes_and_different_slot_cannot_replace_snapshot(self):
        row=await self.store.start(1,'all_for_one',af1_slot=2)
        self.assertEqual((await self.store.start(1,'all_for_one',af1_slot=2))['id'],row['id'])
        with self.assertRaisesRegex(ValueError,'pending AF1 battle'):
            await self.store.start(1,'all_for_one',af1_slot=3)
        self.assertEqual((await self.store.wallet(1))['af1_slot'],2)
        self.assertEqual((await self.store.start(1,'all_for_one'))['state'],row['state'])

    async def test_invalid_or_empty_slot_does_not_change_active_team_or_rotation(self):
        for slot in (0,6,-1):
            with self.assertRaisesRegex(ValueError,'slot 1–5'):await self.store.start(1,'all_for_one',af1_slot=slot)
        async with self.store.tx() as c:
            await c.execute('DELETE FROM af1_teams WHERE owner_id=1 AND slot=5')
        with self.assertRaisesRegex(ValueError,'af1team 5'):await self.store.start(1,'all_for_one',af1_slot=5)
        await self.store.team(1,5)
        with self.assertRaisesRegex(ValueError,'main first'):await self.store.start(1,'all_for_one',af1_slot=5)
        self.assertEqual((await self.store.wallet(1))['af1_slot'],1)
        self.assertEqual(await self.store.af1_opponent(1),0)

    async def test_af1_slot_command_auto_runs_and_no_argument_keeps_preview(self):
        from hiq.cogs.events import EventsCog
        ctx=SimpleNamespace(author=SimpleNamespace(id=1),defer=AsyncMock(),send=AsyncMock())
        cog=EventsCog(SimpleNamespace(db=self.db));cog.run_event=AsyncMock()
        await EventsCog.all_for_one.callback(cog,ctx,5)
        ctx.defer.assert_awaited_once()
        cog.run_event.assert_awaited_once_with(ctx,'all_for_one',0,af1_slot=5)
        ctx.send.assert_not_awaited();cog.run_event.reset_mock()
        await EventsCog.all_for_one.callback(cog,ctx)
        cog.run_event.assert_not_awaited();ctx.send.assert_awaited_once()


class DuoBlessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'game.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.store=GameplayStore(self.db)
        await self.store.create_clan(1,'Player','Duo Club','🏐')
        async with self.store.tx() as c:
            await self.store._credit(c,1,10000)
            self.duo=await self.store._issue_card(c,1,'Player',CHAR_BY_ID[DUO])
            self.regular=await self.store._issue_card(c,1,'Player',CHAR_BY_ID['Suga — Relentless Setter'])

    async def asyncTearDown(self):self.tmp.cleanup()

    async def test_bulk_and_direct_purchase_charge_six_star_price(self):
        ids,cost=await self.store.buy_clan_supply(1,'blessing',6,2)
        self.assertEqual(cost,2400);self.assertEqual(len(ids),2)
        iid,cost=await self.store.buy(1,'blessing',6)
        self.assertEqual(cost,1200);self.assertEqual(await self.store.wallet(1),6400)
        items=await self.store.items(1)
        self.assertTrue(all(it['kind']=='blessing' and it['stars']==6 for it in items))
        self.assertEqual({it['id'] for it in items},set(ids+[iid]))

    async def test_duo_blessing_changes_nature_and_consumes_once(self):
        ids,_=await self.store.buy_clan_supply(1,'blessing',6)
        card=await self.store.bless(1,ids[0],self.duo.instance_id,'pow','hp')
        self.assertEqual((card.boon_stat,card.bane_stat),('pow','hp'))
        self.assertFalse(await self.store.items(1))
        with self.assertRaises(ValueError):await self.store.bless(1,ids[0],self.duo.instance_id,'spd','hp')

    async def test_regular_blessings_and_duo_blessings_cannot_cross_rarities(self):
        ids,_=await self.store.buy_clan_supply(1,'blessing',6)
        with self.assertRaisesRegex(ValueError,'match'):await self.store.bless(1,ids[0],self.regular.instance_id,'spd','hp')
        normal,_=await self.store.buy_clan_supply(1,'blessing',1)
        with self.assertRaisesRegex(ValueError,'match'):await self.store.bless(1,normal[0],self.duo.instance_id,'spd','hp')
        self.assertEqual(len(await self.store.items(1)),2)

    async def test_only_blessings_allow_six_star_purchase_and_invalid_quantity_rolls_back(self):
        for kind,stars,qty in [('mergecopy',6,1),('starpack',6,1),('blessing',7,1),('blessing',6,0),('blessing',6,101)]:
            with self.assertRaises(ValueError):await self.store.buy_clan_supply(1,kind,stars,qty)
        for kind in ('mergecopy','starpack'):
            with self.assertRaises(ValueError):await self.store.buy(1,kind,6)
        with self.assertRaises(ValueError):await self.store.buy_clan_supply(1,'blessing',6,10)
        self.assertEqual(await self.store.wallet(1),10000);self.assertFalse(await self.store.items(1))

    async def test_shiny_duo_still_has_no_bane_after_blessing(self):
        async with self.store.tx() as c:await c.execute("UPDATE cards SET shiny_slots='[1]',bane_stat=NULL WHERE instance_id=?",(self.duo.instance_id,))
        ids,_=await self.store.buy_clan_supply(1,'blessing',6)
        card=await self.store.bless(1,ids[0],self.duo.instance_id,'spd','hp')
        self.assertEqual(card.boon_stat,'spd');self.assertIsNone(card.bane_stat)

    async def test_shop_inventory_and_blessing_picker_display_duo_label(self):
        from hiq.cogs.youth_menus import BulkClanShop
        from hiq.cogs.blessing_picker import BlessingPicker
        from hiq.cogs.community import item_label
        from hiq.inventory import inventory_entries,search_entries
        view=BulkClanShop(self.store,1)
        option=next(o for o in view.children[0].options if o.value=='blessing|6')
        self.assertEqual(option.label,'Duo Blessing — 1,200 Clan Dollars')
        ids,_=await self.store.buy_clan_supply(1,'blessing',6)
        items=await self.store.items(1)
        self.assertEqual(item_label(items[0]),'Duo Blessing')
        rows=inventory_entries({},[],items)
        self.assertEqual(search_entries(rows,'Duo Blessing')[0].label,'Duo Blessing')
        picker=BlessingPicker(SimpleNamespace(),1,items,[self.duo,self.regular],ids[0])
        self.assertEqual([c.instance_id for c in picker.matching_cards()],[self.duo.instance_id])
        self.assertEqual(picker.children[0].options[0].label,'Duo Blessing')
