import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from hiq.database import Database
from hiq.data import CHARACTERS
from hiq.items import ITEMS,can_equip
from hiq.market_store import MarketStore,ESCROW_OWNER
from hiq.raid_store import RaidStore
from hiq.shard_balances import credit,balance


class MarketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'market.sqlite3')
        await self.db.init();self.store=MarketStore(self.db)
        for uid in (1,2,3,4):await self.db.ensure_user(uid)
        async with self.store.tx() as c:
            await c.execute('UPDATE users SET coins=1000')
            for uid in (1,2,3,4):await self.store._credit(c,uid,1000)
            self.card=await self.store._issue_card(c,1,'One',CHARACTERS[0])
            self.item=await self.store._issue_item(c,1,'equipment',detail=next(i['key'] for i in ITEMS if can_equip(i,CHARACTERS[0])))
            await c.execute('INSERT INTO shards VALUES (1,?,9)',('Karasuno',))
            await credit(c,1,'character_shard',CHARACTERS[0].id,50)
        self.prices={'coins':{'min':100,'buyout':500},'clancoins':{'min':200,'buyout':800}}
    async def asyncTearDown(self):self.temp.cleanup()
    async def listing(self,**kwargs):return await self.store.create(1,'card',self.card.instance_id,1,self.prices,**kwargs)
    async def gold(self,uid):return (await self.db.get_user(uid)).coins
    async def test_relative_bid_wins_and_settlement_is_exactly_once_after_restart(self):
        row=await self.listing()
        self.assertEqual((await self.db.get_card(self.card.instance_id)).owner_id,ESCROW_OWNER)
        await self.store.bid(2,row['id'],'coins',300)  # 3× minimum
        await self.store.bid(3,row['id'],'clancoins',500)  # 2.5× minimum
        self.assertEqual(await self.gold(2),700);self.assertEqual(await self.store.wallet(3),500)
        reopened=MarketStore(Database(self.db.path))
        done=await reopened.settle(at=row['expires_at'])
        self.assertEqual(done[0]['winner_id'],2)
        self.assertEqual((await self.db.get_card(self.card.instance_id)).owner_id,2)
        self.assertEqual(await self.gold(1),1300);self.assertEqual(await self.store.wallet(3),1000)
        self.assertEqual(await reopened.settle(at=row['expires_at']+100),[])
        self.assertEqual(await self.gold(1),1300)
    async def test_outbid_refund_self_increase_and_cancel_both_currencies(self):
        row=await self.listing();aid=row['id']
        await self.store.bid(2,aid,'coins',200)
        await self.store.bid(2,aid,'coins',250)
        self.assertEqual(await self.gold(2),750)
        await self.store.bid(3,aid,'coins',300)
        self.assertEqual(await self.gold(2),1000)
        await self.store.bid(4,aid,'clancoins',300)
        with self.assertRaises(ValueError):await self.store.cancel(3,aid)
        await self.store.cancel(1,aid)
        self.assertEqual(await self.gold(3),1000);self.assertEqual(await self.store.wallet(4),1000)
        self.assertEqual((await self.db.get_card(self.card.instance_id)).owner_id,1)
        with self.assertRaises(ValueError):await self.store.cancel(1,aid)
        self.assertEqual(await self.gold(3),1000)
    async def test_buyout_refunds_other_currency_and_preserves_card_history(self):
        row=await self.listing();await self.store.bid(2,row['id'],'coins',400)
        done=await self.store.bid(3,row['id'],'clancoins',800,buyout=True)
        self.assertEqual(done['status'],'sold');self.assertEqual(await self.gold(2),1000)
        self.assertEqual(await self.store.wallet(1),1800);self.assertEqual(await self.store.wallet(3),200)
        card=await self.db.get_card(self.card.instance_id)
        self.assertEqual(card.owner_id,3);self.assertEqual(card.original_owner_id,1)
        self.assertEqual(card.full_instance_id,self.card.full_instance_id)
        self.assertFalse(card.locked)
    async def test_no_bids_returns_asset_and_item_shards_cannot_be_listed(self):
        row=await self.store.create(1,'item',self.item,1,{'coins':{'min':1}})
        await self.store.settle(at=row['expires_at'])
        self.assertTrue(any(it['id']==self.item for it in await self.store.items(1)))
        with self.assertRaises(ValueError):await self.store.create(1,'item_shard',ITEMS[0]['key'],1,self.prices)
    async def test_shard_sales_transfer_exact_quantities_without_touching_daily_caps(self):
        for kind,ref,qty in [('school','kar',3),('character',CHARACTERS[0].id,12)]:
            row=await self.store.create(1,kind,ref,qty,{'coins':{'min':1,'buyout':10}})
            await self.store.bid(2,row['id'],'coins',10,buyout=True)
        async with self.db.connect() as c:
            rows=await self.store.rows(c,'SELECT * FROM shards ORDER BY owner_id')
            self.assertEqual([(r['owner_id'],r['quantity']) for r in rows],[(1,6),(2,3)])
            self.assertEqual(await balance(c,1,'character_shard',CHARACTERS[0].id),38)
            self.assertEqual(await balance(c,2,'character_shard',CHARACTERS[0].id),12)
            self.assertEqual(await self.store.rows(c,'SELECT * FROM raid_weekly'),[])
    async def test_insufficient_funds_and_invalid_bids_leave_escrow_unchanged(self):
        row=await self.listing();aid=row['id'];await self.store.bid(2,aid,'coins',400)
        for uid,currency,amount in [(1,'coins',450),(3,'coins',399),(3,'coins',-1)]:
            with self.assertRaises(ValueError):await self.store.bid(uid,aid,currency,amount)
        async with self.store.tx() as c:await c.execute('UPDATE users SET coins=0 WHERE discord_id=3')
        with self.assertRaises(ValueError):await self.store.bid(3,aid,'coins',450)
        self.assertEqual(await self.gold(2),600)
        self.assertEqual((await self.store.auction(aid))['bids'][0]['bidder_id'],2)
    async def test_two_simultaneous_buyouts_only_one_can_win(self):
        row=await self.listing()
        other=MarketStore(Database(self.db.path))
        outcomes=await asyncio.gather(self.store.bid(2,row['id'],'coins',500,buyout=True),other.bid(3,row['id'],'clancoins',800,buyout=True),return_exceptions=True)
        self.assertEqual(sum(isinstance(result,ValueError) for result in outcomes),1)
        sold=await self.store.auction(row['id']);self.assertEqual(sold['status'],'sold')
        self.assertEqual((await self.db.get_card(self.card.instance_id)).owner_id,sold['winner_id'])
        self.assertEqual(await self.gold(1)+await self.store.wallet(1),2000+sold['amount'])
        loser=3 if sold['winner_id']==2 else 2
        self.assertEqual(await self.gold(loser),1000);self.assertEqual(await self.store.wallet(loser),1000)
    async def test_equal_ratios_use_earliest_bid_and_disabled_currency_rejected(self):
        row=await self.listing()
        await self.store.bid(3,row['id'],'clancoins',400);await self.store.bid(2,row['id'],'coins',200)
        done=(await self.store.settle(at=row['expires_at']))[0];self.assertEqual(done['winner_id'],3)
        second=await self.store.create(1,'item',self.item,1,{'coins':{'min':100}})
        with self.assertRaises(ValueError):await self.store.bid(2,second['id'],'clancoins',400)
    async def test_listing_removes_card_from_teams_and_keeps_seller_equipment(self):
        await self.db.ensure_saved_team(1,1)
        async with self.store.tx() as c:
            await c.execute('UPDATE saved_teams SET front_id=? WHERE owner_id=1',(self.card.instance_id,))
        await RaidStore(self.db).equip(1,self.card.instance_id,self.item)
        with self.assertRaises(ValueError):await self.store.create(1,'item',self.item,1,self.prices)
        await self.listing()
        self.assertIsNone((await self.db.get_saved_team(1,1)).front_id)
        self.assertIsNone((await self.db.get_card(self.card.instance_id)).equipped_item_id)
        self.assertTrue(any(it['id']==self.item for it in await self.store.items(1)))
        with self.assertRaises(ValueError):await self.listing()
    async def test_delivery_error_rolls_back_all_refunds_and_status(self):
        from unittest.mock import patch,AsyncMock
        row=await self.listing();await self.store.bid(2,row['id'],'coins',300)
        await self.store.bid(3,row['id'],'clancoins',400)
        with patch.object(self.store,'_deliver',new=AsyncMock(side_effect=RuntimeError('disk error'))):
            with self.assertRaises(RuntimeError):await self.store.settle(at=row['expires_at'])
        self.assertEqual(await self.store.wallet(3),600)
        self.assertEqual((await self.store.auction(row['id']))['status'],'active')
        await self.store.settle(at=row['expires_at'])
        self.assertEqual(await self.store.wallet(3),1000)


class MarketMenus(unittest.IsolatedAsyncioTestCase):
    async def test_menus_and_price_parser(self):
        from hiq.cogs.market import ListingStart,ListingModal,parse_price
        self.assertIsNone(parse_price(''));self.assertEqual(parse_price('100 / 200'),dict(min=100,buyout=200))
        with self.assertRaises(ValueError):parse_price('300/200')
        for kind in ('card','item','school','character'):
            self.assertEqual(len(ListingModal(None,1,kind).children),5)
        self.assertEqual(len(ListingStart(None,1).children),1)
