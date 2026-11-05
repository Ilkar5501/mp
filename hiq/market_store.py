"""Global auctions with asset/currency escrow and atomic, restart-safe settlement."""
from __future__ import annotations

import json
import time
import uuid
from fractions import Fraction

from .community_store import CommunityStore
from .data import CHAR_BY_ID,CHARACTERS
from .items import inventory_label
from .shard_balances import credit as credit_shards, consume as consume_shards

MAX_PRICE = 1_000_000_000
ESCROW_OWNER = 0  # Reserved internal account; Discord user IDs are strictly positive.
CURRENCIES = {'coins': 'Coins', 'clancoins': 'Clan Dollars'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS market_auctions (
 id TEXT PRIMARY KEY, seller_id INTEGER NOT NULL, status TEXT NOT NULL,
 asset_json TEXT NOT NULL, prices_json TEXT NOT NULL,
 created_at REAL NOT NULL, expires_at REAL NOT NULL,
 winner_id INTEGER, currency TEXT, amount INTEGER, closed_at REAL
);
CREATE INDEX IF NOT EXISTS market_open ON market_auctions(status,expires_at);
CREATE TABLE IF NOT EXISTS market_bids (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, auction_id TEXT NOT NULL,
 currency TEXT NOT NULL, bidder_id INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
 UNIQUE(auction_id,currency)
);
'''


def currency_key(raw):
    key=str(raw).casefold().replace('_','').replace(' ','')
    key={'coin':'coins','clandollars':'clancoins','clandollar':'clancoins','clancoin':'clancoins'}.get(key,key)
    if key not in CURRENCIES:raise ValueError('Choose Coins or Clan Dollars.')
    return key


def whole(value,label,minimum=1,maximum=MAX_PRICE):
    if isinstance(value,bool) or not isinstance(value,int) or not minimum<=value<=maximum:
        raise ValueError(f'{label} must be a whole number from {minimum:,} to {maximum:,}.')
    return value


class MarketStore(CommunityStore):
    @staticmethod
    def unpack_auction(row):
        if row:
            row=dict(row);row['asset']=json.loads(row.pop('asset_json'));row['prices']=json.loads(row.pop('prices_json'))
        return row

    async def auction(self,aid,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.auction(aid,c)
        row=self.unpack_auction(await self.one(conn,'SELECT * FROM market_auctions WHERE id=?',(aid,)))
        if row:row['bids']=await self.rows(conn,'SELECT * FROM market_bids WHERE auction_id=? ORDER BY sequence',(aid,))
        return row

    async def browse(self,query='',*,owner=None):
        await self.settle()
        async with self.db.connect() as c:
            rows=await self.rows(c,'SELECT * FROM market_auctions '+('WHERE seller_id=? ' if owner else "WHERE status='active' ")+'ORDER BY created_at DESC', (owner,) if owner else ())
        auctions=[self.unpack_auction(row) for row in rows]
        query=query.strip().casefold()
        return [row for row in auctions if not query or query in (row['asset']['label']+' '+row['id']).casefold()]

    async def preview_asset(self,uid,kind,reference,quantity=1,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.preview_asset(uid,kind,reference,quantity,c)
        whole(quantity,'Quantity',maximum=1_000_000)
        if kind=='card':
            if quantity!=1:raise ValueError('List one card instance at a time.')
            row=await self.one(conn,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,reference,reference))
            if not row:raise ValueError('That card is not in your collection.')
            if row['locked']:raise ValueError('Unlock the card before listing it.')
            char=CHAR_BY_ID[row['character_id']]
            return dict(kind=kind,reference=row['instance_id'],quantity=1,label=f'{char.name} — {char.title} +{row["merge_level"]}',
                        boon=row['boon_stat'],bane=row['bane_stat'],original_locked=row['locked'])
        if kind=='item':
            if quantity!=1:raise ValueError('List one item instance at a time.')
            row=await self.one(conn,'SELECT * FROM inventory_items WHERE owner_id=? AND id=?',(uid,reference))
            if not row or row['kind'] in ('item_shard','character_shard'):raise ValueError('Choose an item instance you own. Item shards cannot be sold.')
            if await self.one(conn,'SELECT 1 FROM cards WHERE equipped_item_id=?',(reference,)):
                raise ValueError('Unequip the item before listing it.')
            names={'mergecopy':'Merge Copy','blessing':'Blessing','starpack':'Star Pack','pack_base':'Base Pack','pack_school':'School Pack','pack_position':'Position Pack'}
            label=inventory_label(row) or f'{row["stars"] or ""} {names.get(row["kind"],row["kind"])} {row["detail"]}'.strip()
            return dict(kind=kind,reference=reference,quantity=1,label=label)
        if kind=='school':
            from .abbreviations import school_name
            reference=school_name(reference)
            reference=next((s for s in {char.school for char in CHARACTERS} if s.casefold()==reference.casefold()),None)
            if not reference:raise ValueError('Choose an existing character’s school.')
            row=await self.one(conn,'SELECT quantity FROM shards WHERE owner_id=? AND school=?',(uid,reference))
            if not row or row['quantity']<quantity:raise ValueError('You do not have enough matching school shards.')
            return dict(kind=kind,reference=reference,quantity=quantity,label=f'{reference} School Shards ×{quantity}')
        if kind=='character':
            from .data import find_character
            from .shard_balances import balance
            matches=find_character(reference)
            char=CHAR_BY_ID.get(reference)
            if char is None:
                exact=[c for c in matches if c.id.casefold()==reference.casefold() or c.card_id.casefold()==reference.casefold()]
                matches=exact or matches
                if len(matches)!=1:raise ValueError('Use the character’s full name and title for character shards.')
                char=matches[0]
            if await balance(conn,uid,'character_shard',char.id)<quantity:raise ValueError('You do not have enough matching character shards.')
            return dict(kind=kind,reference=char.id,quantity=quantity,label=f'{char.name} — {char.title} Shards ×{quantity}')
        raise ValueError('Asset type: card, item, school or character. Item shards cannot be sold.')

    async def create(self,uid,kind,reference,quantity,prices,*,hours=24,expected=None):
        whole(uid,'Player ID',maximum=2**63-1);whole(hours,'Duration in hours',maximum=24)
        if not prices:raise ValueError('Enable at least one currency with a minimum price.')
        clean={}
        for raw,values in prices.items():
            key=currency_key(raw);minimum=whole(values['min'],'Minimum price');buyout=values.get('buyout')
            if buyout is not None:
                whole(buyout,'Buyout price')
                if buyout<minimum:raise ValueError('Buyout cannot be below the minimum price.')
            clean[key]=dict(min=minimum,buyout=buyout)
        await self.db.ensure_user(ESCROW_OWNER)
        async with self.tx() as c:
            asset=await self.preview_asset(uid,kind,reference,quantity,c)
            if expected is not None and asset!=expected:raise ValueError('That asset changed. Open a fresh auction preview before confirming.')
            aid='au-'+uuid.uuid4().hex[:12];created=time.time()
            if kind=='card':
                await self.db._clear_card_from_teams_tx(c,asset['reference'])
                await c.execute('UPDATE cards SET owner_id=?,locked=1 WHERE instance_id=?',(ESCROW_OWNER,asset['reference']))
            elif kind=='item':await c.execute('UPDATE inventory_items SET owner_id=? WHERE id=?',(ESCROW_OWNER,asset['reference']))
            elif kind=='school':
                await c.execute('UPDATE shards SET quantity=quantity-? WHERE owner_id=? AND school=?',(quantity,uid,asset['reference']))
            else:await consume_shards(c,uid,'character_shard',asset['reference'],quantity)
            await c.execute("INSERT INTO market_auctions(id,seller_id,status,asset_json,prices_json,created_at,expires_at) VALUES (?,?,'active',?,?,?,?)",
                            (aid,uid,json.dumps(asset),json.dumps(clean),created,created+hours*3600))
            return await self.auction(aid,c)

    async def _money(self,c,uid,currency,amount):
        if currency=='coins':
            cur=await c.execute('UPDATE users SET coins=coins+? WHERE discord_id=? AND coins+?>=0',(amount,uid,amount))
        else:
            await c.execute('INSERT OR IGNORE INTO clan_wallets(user_id,coins) VALUES (?,0)',(uid,))
            cur=await c.execute('UPDATE clan_wallets SET coins=coins+? WHERE user_id=? AND coins+?>=0',(amount,uid,amount))
        if cur.rowcount!=1:raise ValueError(f'You do not have enough {CURRENCIES[currency]} available. Active bids are held in escrow.')

    async def _deliver(self,c,asset,uid):
        ref=asset['reference']
        if asset['kind']=='card':
            cur=await c.execute('UPDATE cards SET owner_id=?,locked=? WHERE instance_id=? AND owner_id=?',(uid,asset['original_locked'],ref,ESCROW_OWNER))
            if cur.rowcount!=1:raise RuntimeError('Auction card escrow is missing.')
        elif asset['kind']=='item':
            cur=await c.execute('UPDATE inventory_items SET owner_id=? WHERE id=? AND owner_id=?',(uid,ref,ESCROW_OWNER))
            if cur.rowcount!=1:raise RuntimeError('Auction item escrow is missing.')
        elif asset['kind']=='school':
            await c.execute('INSERT INTO shards VALUES (?,?,?) ON CONFLICT(owner_id,school) DO UPDATE SET quantity=quantity+excluded.quantity',(uid,ref,asset['quantity']))
        else:await credit_shards(c,uid,'character_shard',ref,asset['quantity'])

    async def _close(self,c,row,*,winner=None,cancelled=False):
        # Caller owns the write transaction. Asset delivery, refunds and seller
        # payment either all commit or all roll back; terminal rows cannot repeat.
        if row['status']!='active':return row
        for bid in row['bids']:
            if winner is None or bid['sequence']!=winner['sequence']:
                await self._money(c,bid['bidder_id'],bid['currency'],bid['amount'])
        recipient=winner['bidder_id'] if winner else row['seller_id']
        await self._deliver(c,row['asset'],recipient)
        if winner:await self._money(c,row['seller_id'],winner['currency'],winner['amount'])
        await c.execute('UPDATE market_auctions SET status=?,winner_id=?,currency=?,amount=?,closed_at=? WHERE id=?',
                        ('cancelled' if cancelled else 'sold' if winner else 'expired',winner['bidder_id'] if winner else None,
                         winner['currency'] if winner else None,winner['amount'] if winner else None,time.time(),row['id']))
        # Keep final bids for the receipt/history; escrow is released only here.
        return await self.auction(row['id'],c)

    async def settle(self,at=None):
        at=time.time() if at is None else at;done=[]
        async with self.tx() as c:
            rows=await self.rows(c,"SELECT id FROM market_auctions WHERE status='active' AND expires_at<=? ORDER BY expires_at,id",(at,))
            for item in rows:
                row=await self.auction(item['id'],c)
                # Equal relative bids go to the earlier currently leading bid.
                winner=max(row['bids'],key=lambda b:(Fraction(b['amount'],row['prices'][b['currency']]['min']),-b['sequence']),default=None)
                done.append(await self._close(c,row,winner=winner))
        return done

    async def bid(self,uid,aid,currency,amount,*,buyout=False):
        currency=currency_key(currency);whole(amount,'Bid')
        whole(uid,'Player ID',maximum=2**63-1)
        await self.db.ensure_user(uid);await self.settle()
        async with self.tx() as c:
            row=await self.auction(aid,c)
            if not row or row['status']!='active' or row['expires_at']<=time.time():raise ValueError('That auction has closed.')
            if row['seller_id']==uid:raise ValueError('You cannot bid on your own auction.')
            price=row['prices'].get(currency)
            if not price:raise ValueError('The seller is not accepting that currency.')
            if buyout:
                if not price['buyout']:raise ValueError('There is no buyout for that currency.')
                if amount!=price['buyout']:raise ValueError('The buyout changed. Reopen the auction.')
            # Bidding at or above the advertised buyout pays that price exactly.
            if price['buyout']:amount=min(amount,price['buyout'])
            previous=next((b for b in row['bids'] if b['currency']==currency),None)
            if amount<price['min'] or previous and amount<=previous['amount']:
                raise ValueError('Meet the minimum and bid more than the current top bid in that currency.')
            held=previous['amount'] if previous and previous['bidder_id']==uid else 0
            await self._money(c,uid,currency,-(amount-held))
            if previous:
                if not held:await self._money(c,previous['bidder_id'],currency,previous['amount'])
                await c.execute('DELETE FROM market_bids WHERE sequence=?',(previous['sequence'],))
            await c.execute('INSERT INTO market_bids(auction_id,currency,bidder_id,amount) VALUES (?,?,?,?)',(aid,currency,uid,amount))
            row=await self.auction(aid,c)
            if price['buyout'] and amount>=price['buyout']:
                winner=next(b for b in row['bids'] if b['currency']==currency)
                row=await self._close(c,row,winner=winner)
            return row

    async def cancel(self,uid,aid):
        await self.settle()
        async with self.tx() as c:
            row=await self.auction(aid,c)
            if not row or row['seller_id']!=uid:raise ValueError('Only the seller can cancel this auction.')
            if row['status']!='active' or row['expires_at']<=time.time():raise ValueError('That auction has already closed.')
            return await self._close(c,row,cancelled=True)
