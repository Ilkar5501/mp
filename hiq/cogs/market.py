"""Discord menus for the shared, cross-server auction house."""
import logging

import discord
from discord.ext import commands,tasks

from ..market_store import MarketStore,CURRENCIES
from ..ui import SafeView,EmbedPager,PURPLE

log=logging.getLogger(__name__)


def auction_embed(row):
    asset=row['asset'];e=discord.Embed(title=f'Market — {row["id"]}',color=PURPLE)
    nature=''
    if asset['kind']=='card':nature='\nNature: '+(f'+{asset["boon"].upper()}/−{asset["bane"].upper()}' if asset['boon'] and asset['bane'] else 'Neutral')
    e.description=f'**{discord.utils.escape_markdown(asset["label"])}**{nature}\nSeller: <@{row["seller_id"]}>\nStatus: **{row["status"].title()}**\nEnds: <t:{int(row["expires_at"])}:R>\nAsset: `{asset["reference"]}`'
    for currency,price in row['prices'].items():
        bid=next((b for b in row.get('bids',[]) if b['currency']==currency),None)
        text=f'Minimum: **{price["min"]:,}**\nBuyout: '+(f'**{price["buyout"]:,}**' if price['buyout'] else 'None')
        if bid:text+=f'\nTop bid: **{bid["amount"]:,}** by <@{bid["bidder_id"]}> ({bid["amount"]/price["min"]*100:.2f}% of minimum)'
        e.add_field(name=CURRENCIES[currency],value=text,inline=False)
    if row['winner_id']:e.add_field(name='Sold',value=f'<@{row["winner_id"]}> paid **{row["amount"]:,} {CURRENCIES[row["currency"]]}**.',inline=False)
    e.set_footer(text='Shared across all servers • At expiry, highest bid ÷ its currency’s minimum wins. Equal ratios: earlier bid wins.')
    return e


class OwnedView(SafeView):
    def __init__(self,uid,**kwargs):super().__init__(**kwargs);self.uid=uid;self.finished=False
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('This menu belongs to another player. Use ;market to open your own.',ephemeral=True);return False
        if self.finished:
            await i.response.send_message('This confirmation has already been used.',ephemeral=True);return False
        return True


class ActionConfirm(OwnedView):
    def __init__(self,store,uid,aid,currency=None,amount=None):
        super().__init__(uid,timeout=120);self.store=store;self.aid=aid;self.currency=currency;self.amount=amount
        self.confirm.label='Confirm Purchase' if currency else 'Cancel Auction & Refund Bids'
    @discord.ui.button(label='Confirm',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        await i.response.defer()
        row=await self.store.bid(self.uid,self.aid,self.currency,self.amount,buyout=True) if self.currency else await self.store.cancel(self.uid,self.aid)
        self.finished=True
        await i.edit_original_response(content=None,embed=auction_embed(row),view=None)
    @discord.ui.button(label='Go Back',style=discord.ButtonStyle.secondary)
    async def back(self,i,button):
        self.finished=True;await i.response.edit_message(content='No changes made.',embed=None,view=None)


class BidModal(discord.ui.Modal,title='Place an auction bid'):
    amount=discord.ui.TextInput(label='Amount',max_length=10)
    def __init__(self,store,aid,currency):super().__init__();self.store=store;self.aid=aid;self.currency=currency;self.amount.label=f'Bid in {CURRENCIES[currency]}'
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        try:
            try:amount=int(str(self.amount).strip())
            except ValueError:raise ValueError('Enter a positive whole number.')
            row=await self.store.bid(i.user.id,self.aid,self.currency,amount)
        except ValueError as exc:await i.followup.send(str(exc),ephemeral=True);return
        await i.followup.send(content='Purchase complete.' if row['status']=='sold' else 'Your bid is held in escrow. Outbid or cancelled bids are refunded automatically.',embed=auction_embed(row),view=AuctionView(self.store,row),ephemeral=True)


class AuctionView(SafeView):
    def __init__(self,store,row):
        super().__init__(timeout=600);self.store=store;self.aid=row['id']
        if row['status']=='active':
            for currency,price in row['prices'].items():
                bid=discord.ui.Button(label=f'Bid {CURRENCIES[currency]}',style=discord.ButtonStyle.primary,row=0)
                async def place(i,key=currency):await i.response.send_modal(BidModal(self.store,self.aid,key))
                bid.callback=place;self.add_item(bid)
                if price['buyout']:
                    buy=discord.ui.Button(label=f'Buy: {price["buyout"]:,} {CURRENCIES[currency]}',style=discord.ButtonStyle.success,row=1)
                    async def buy_now(i,key=currency,amount=price['buyout']):
                        await i.response.send_message(f'Buy this listing for **{amount:,} {CURRENCIES[key]}**? This completes the sale immediately.',view=ActionConfirm(self.store,i.user.id,self.aid,key,amount),ephemeral=True)
                    buy.callback=buy_now;self.add_item(buy)
            cancel=discord.ui.Button(label='Cancel Auction',style=discord.ButtonStyle.danger,row=2)
            async def cancel_auction(i):
                current=await self.store.auction(self.aid)
                if not current or current['seller_id']!=i.user.id:
                    await i.response.send_message('Only the seller can cancel this auction.',ephemeral=True);return
                await i.response.send_message('Cancel this auction? All bids will be refunded and the asset returned to your inventory. Cards are not restored to their old team slots.',view=ActionConfirm(self.store,i.user.id,self.aid),ephemeral=True)
            cancel.callback=cancel_auction;self.add_item(cancel)
        refresh=discord.ui.Button(label='Refresh',style=discord.ButtonStyle.secondary,row=2)
        async def reload(i):
            await i.response.defer()
            await self.store.settle();current=await self.store.auction(self.aid)
            await i.edit_original_response(embed=auction_embed(current),view=AuctionView(self.store,current))
        refresh.callback=reload;self.add_item(refresh)


class ListingConfirm(OwnedView):
    def __init__(self,store,uid,asset,prices,hours):
        super().__init__(uid,timeout=180);self.store=store;self.asset=asset;self.prices=prices;self.hours=hours
    @discord.ui.button(label='Create Auction',style=discord.ButtonStyle.success)
    async def confirm(self,i,button):
        await i.response.defer()
        row=await self.store.create(self.uid,self.asset['kind'],self.asset['reference'],self.asset['quantity'],self.prices,hours=self.hours,expected=self.asset)
        self.finished=True
        await i.edit_original_response(content='Auction created. Find it from any server with `;market` or `;auction '+row['id']+'`.',embed=auction_embed(row),view=AuctionView(self.store,row))
    @discord.ui.button(label='Never Mind',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):self.finished=True;await i.response.edit_message(content='Listing cancelled. Nothing was taken from your inventory.',embed=None,view=None)


def parse_price(raw):
    raw=raw.strip()
    if not raw or raw=='0':return None
    parts=raw.replace('/',' ').split()
    if len(parts) not in (1,2):raise ValueError('Enter a minimum, or minimum / buyout. Leave blank to disable a currency.')
    try:minimum=int(parts[0]);buyout=int(parts[1]) if len(parts)==2 else None
    except ValueError:raise ValueError('Prices must be positive whole numbers.')
    from ..market_store import whole
    whole(minimum,'Minimum')
    if buyout is not None:
        whole(buyout,'Buyout')
        if buyout<minimum:raise ValueError('Buyout cannot be below the minimum.')
    return dict(min=minimum,buyout=buyout)


class ListingModal(discord.ui.Modal,title='Create an auction'):
    reference=discord.ui.TextInput(label='Card/item ID, school, or character + title',max_length=150)
    quantity=discord.ui.TextInput(label='Quantity (cards and items: 1)',default='1',max_length=7)
    coins=discord.ui.TextInput(label='Coins: minimum / optional buyout',placeholder='100 / 500 — blank disables Coins',required=False,max_length=25)
    clan=discord.ui.TextInput(label='Clan Dollars: minimum / optional buyout',placeholder='100 / 500 — blank disables Clan Dollars',required=False,max_length=25)
    hours=discord.ui.TextInput(label='Duration in hours (1–24)',default='24',max_length=2)
    def __init__(self,store,uid,kind):super().__init__();self.store=store;self.uid=uid;self.kind=kind
    async def on_submit(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This listing belongs to another player.',ephemeral=True);return
        await i.response.defer(ephemeral=True)
        try:
            prices={key:price for key,raw in (('coins',str(self.coins)),('clancoins',str(self.clan))) if (price:=parse_price(raw)) is not None}
            if not prices:raise ValueError('Set a minimum for at least one currency.')
            try:quantity=int(str(self.quantity));hours=int(str(self.hours))
            except ValueError:raise ValueError('Quantity and hours must be whole numbers.')
            from ..market_store import whole
            whole(hours,'Duration in hours',maximum=24)
            asset=await self.store.preview_asset(self.uid,self.kind,str(self.reference).strip(),quantity)
        except ValueError as exc:await i.followup.send(str(exc),ephemeral=True);return
        e=discord.Embed(title='Review your auction',description=f'**{discord.utils.escape_markdown(asset["label"])}**\n`{asset["reference"]}`\nDuration: **{hours} hours**\n\n'+ '\n'.join(f'**{CURRENCIES[k]}:** minimum {p["min"]:,}; buyout '+(f'{p["buyout"]:,}' if p['buyout'] else 'none') for k,p in prices.items())+'\n\nThe asset is held until sale, cancellation or expiry. Cards leave saved teams; their equipment stays in your inventory. You can cancel and refund all bids. At expiry, the highest bid relative to its currency’s minimum wins. A buyout in either currency ends the auction immediately.',color=PURPLE)
        await i.followup.send(embed=e,view=ListingConfirm(self.store,self.uid,asset,prices,hours),ephemeral=True)


class ListingStart(OwnedView):
    def __init__(self,store,uid):super().__init__(uid,timeout=300);self.store=store
    @discord.ui.select(placeholder='What would you like to sell?',options=[discord.SelectOption(label=label,value=key) for key,label in [('card','Character card'),('item','Equipment or inventory item'),('school','School shards'),('character','Character shards')]])
    async def choose(self,i,select):await i.response.send_modal(ListingModal(self.store,self.uid,select.values[0]))


class MarketBrowse(OwnedView):
    def __init__(self,store,uid,rows,query):
        super().__init__(uid,timeout=600);self.store=store;self.rows=rows;self.query=query;self.page=0;self.rebuild()
    def embed(self):
        chunk=self.rows[self.page*10:(self.page+1)*10]
        e=discord.Embed(title='Match Point — Global Market',description='\n\n'.join(f'`{r["id"]}` **{discord.utils.escape_markdown(r["asset"]["label"])}**\n'+ ' · '.join(f'{CURRENCIES[k]}: {p["min"]:,} minimum' for k,p in r['prices'].items())+f' · <t:{int(r["expires_at"])}:R>' for r in chunk) or 'No active listings match your search.',color=PURPLE)
        e.add_field(name='Auction house',value='Shared across all servers. Use `;auction` to sell, `;auction <ID>` to inspect a listing, or `;myauctions` for your sale history. Item shards cannot be sold.',inline=False)
        e.set_footer(text=f'Page {self.page+1}/{max(1,(len(self.rows)+9)//10)} • Refresh to see the latest listings.')
        return e
    def rebuild(self):
        self.clear_items();self.page=min(self.page,max(0,(len(self.rows)-1)//10))
        chunk=self.rows[self.page*10:(self.page+1)*10]
        if chunk:
            select=discord.ui.Select(placeholder='Inspect an auction',options=[discord.SelectOption(label=r['asset']['label'][:100],value=r['id'],description=r['id']) for r in chunk])
            async def inspect(i):
                await i.response.defer(ephemeral=True);await self.store.settle();row=await self.store.auction(select.values[0])
                await i.followup.send(embed=auction_embed(row),view=AuctionView(self.store,row),ephemeral=True)
            select.callback=inspect;self.add_item(select)
        for label,delta in (('Previous',-1),('Next',1),('Refresh',0)):
            button=discord.ui.Button(label=label,row=1,disabled=(delta==-1 and self.page==0 or delta==1 and (self.page+1)*10>=len(self.rows)))
            async def navigate(i,move=delta):
                await i.response.defer()
                if move:self.page+=move
                else:self.rows=await self.store.browse(self.query)
                self.rebuild();await i.edit_original_response(embed=self.embed(),view=self)
            button.callback=navigate;self.add_item(button)


class MarketCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=MarketStore(bot.db);self.expiry.start()
    def cog_unload(self):self.expiry.cancel()
    @tasks.loop(seconds=30)
    async def expiry(self):
        try:await self.store.settle()
        except Exception:log.exception('Market settlement failed; will retry without releasing escrow twice.')
    @expiry.before_loop
    async def ready(self):await self.bot.wait_until_ready()
    @commands.hybrid_command(name='market',description='Browse the auction house shared across all servers.')
    async def market(self,ctx,*,search:str=''):
        await ctx.defer();rows=await self.store.browse(search)
        view=MarketBrowse(self.store,ctx.author.id,rows,search);await ctx.send(embed=view.embed(),view=view)
    @commands.hybrid_command(name='auction',description='Create an auction, or inspect an auction ID.')
    async def auction(self,ctx,auction_id:str=None):
        if auction_id:
            await ctx.defer();await self.store.settle();row=await self.store.auction(auction_id)
            if not row:await ctx.send('Auction not found. Browse `;market` for active listings.');return
            await ctx.send(embed=auction_embed(row),view=AuctionView(self.store,row));return
        await ctx.send('Choose what to sell. You’ll review the asset, minimum prices, optional buyouts and duration before anything leaves your inventory.',view=ListingStart(self.store,ctx.author.id))
    @commands.hybrid_command(name='myauctions',description='See your active auctions and completed sales.')
    async def myauctions(self,ctx):
        await ctx.defer();rows=await self.store.browse(owner=ctx.author.id)
        pages=[discord.Embed(title='Your Auctions',description='\n\n'.join(f'`{r["id"]}` — **{r["status"].title()}**\n{discord.utils.escape_markdown(r["asset"]["label"])}'+ (f'\nSold for {r["amount"]:,} {CURRENCIES[r["currency"]]}' if r['winner_id'] else '') for r in rows[n:n+10]) or 'You haven’t listed anything yet. Use `;auction` to get started.',color=PURPLE) for n in range(0,max(1,len(rows)),10)]
        view=EmbedPager(pages,ctx.author.id);await ctx.send(embed=view.embed(),view=view)


async def setup(bot):await bot.add_cog(MarketCog(bot))
