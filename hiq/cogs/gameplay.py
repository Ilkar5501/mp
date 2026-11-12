import math
import discord
from discord.ext import commands
from ..gameplay_store import GameplayStore
from ..items import ITEM_BY_KEY
from ..ui import SafeView,PURPLE


class OwnedView(SafeView):
    def __init__(self,uid):super().__init__(timeout=300);self.uid=uid
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open your own menu to make changes.',ephemeral=True);return False
        return True


class DismantleConfirm(OwnedView):
    def __init__(self,store,uid,iid):super().__init__(uid);self.store=store;self.iid=iid
    @discord.ui.button(label='Dismantle permanently',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        char,amount=await self.store.dismantle_card(self.uid,self.iid,confirm=True)
        self.stop();await i.response.edit_message(content=f'Dismantled **{char.name} — {char.title}** for **{amount} character shards**.',embed=None,view=None)
    @discord.ui.button(label='Cancel')
    async def cancel(self,i,button):self.stop();await i.response.edit_message(content='Dismantle cancelled.',embed=None,view=None)


class AutocleanView(OwnedView):
    def __init__(self,store,uid,selected):
        super().__init__(uid);self.store=store;self.selected=selected;self.page=0
        self.items=sorted(ITEM_BY_KEY.values(),key=lambda x:x['name']);self.build()
    def embed(self):
        names=[i['name'] for i in self.items if i['key'] in self.selected]
        return discord.Embed(title='Automatic equipment recycling',description='Selected items are recycled for Clan Dollars whenever a new copy is created. Existing inventory and trade transfers are unchanged. Unselect an item to keep future copies.\n\n**Enabled:** '+(', '.join(names) or 'None'),color=PURPLE)
    def build(self):
        self.clear_items();page=self.items[self.page*25:self.page*25+25];keys=[i['key'] for i in page]
        select=discord.ui.Select(placeholder='Select equipment to recycle automatically',min_values=0,max_values=len(page),options=[discord.SelectOption(label=i['name'],value=i['key'],default=i['key'] in self.selected) for i in page])
        async def choose(i):
            await self.store.set_autoclean_page(self.uid,keys,select.values)
            self.selected.difference_update(keys);self.selected.update(select.values);self.build()
            await i.response.edit_message(embed=self.embed(),view=self)
        select.callback=choose;self.add_item(select)
        for label,d in [('Previous',-1),('Next',1)]:
            button=discord.ui.Button(label=label,row=1,disabled=not 0<=self.page+d<math.ceil(len(self.items)/25))
            async def nav(i,delta=d):self.page+=delta;self.build();await i.response.edit_message(embed=self.embed(),view=self)
            button.callback=nav;self.add_item(button)


class GameplayCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=GameplayStore(bot.db)
    @commands.hybrid_command(name='dismantle',description='Turn an unused, unlocked character card into character shards.')
    async def dismantle(self,ctx,card_id:str):
        try:char,n=await self.store.dismantle_card(ctx.author.id,card_id)
        except ValueError as error:await ctx.send(str(error));return
        await ctx.send(f'Permanently dismantle **{char.name} — {char.title}** for **{n} character shards**? This cannot be undone.',view=DismantleConfirm(self.store,ctx.author.id,card_id))
    @commands.hybrid_command(name='autoclean',aliases=['aclean'],description='Choose equipment to recycle automatically when created.')
    async def autoclean(self,ctx):
        view=AutocleanView(self.store,ctx.author.id,await self.store.autoclean(ctx.author.id))
        await ctx.send(embed=view.embed(),view=view)
    @commands.hybrid_command(name='use',description='Use an inventory consumable by its item ID.')
    async def use(self,ctx,item_id:str):
        try:
            item=next((it for it in await self.store.items(ctx.author.id) if it['id']==item_id),None)
            if not item:raise ValueError('That item is not in your inventory.')
            if item['kind']=='shiny_stone':
                from .youth_menus import StoneView
                from ..data import CHAR_BY_ID
                cards=[c for c in await self.bot.db.get_cards(ctx.author.id) if len(c.shiny_slots)<len(CHAR_BY_ID[c.character_id].shiny_arts)]
                if not cards:raise ValueError('None of your cards have another shiny artwork to unlock. Your stone was kept.')
                await ctx.send('Choose a card to unlock its next shiny artwork.',view=StoneView(self.store,ctx.author.id,item_id,cards));return
            if item['kind']!='pvpshield':raise ValueError('Use ;open for packs or ;bless for blessings. ;use accepts PvP Shields and Shiny Stones.')
            until,reusable=await self.store.use_shield(ctx.author.id,item_id)
            await ctx.send(f'PvP Shield activated until <t:{int(until)}:F>. You cannot initiate or be selected for PvP while protected. Your next shield is available <t:{int(reusable)}:F>.')
        except ValueError as error:await ctx.send(str(error))
    @commands.hybrid_command(name='fuse',description='Fuse matching Young Emperor and King of the Lost Court cards into their Duo.')
    async def fuse(self,ctx):
        from .youth_menus import FuseView
        cards=[c for c in await self.bot.db.get_cards(ctx.author.id) if c.character_id in ('Oikawa — Young Emperor','Kageyama — King of the Lost Court') and not c.locked and not c.equipped_item_id]
        if not any(c.character_id=='Oikawa — Young Emperor' for c in cards):await ctx.send('You need an unlocked, unequipped Young Emperor and King of the Lost Court at the same merge level. Earn AF1 Marks through ;af1, up to 25 per day.');return
        await ctx.send('Choose both ingredients. The Duo keeps their merge level. Cost: 100 coins + 25 AF1 Marks × max(1, merge level). Cards assigned to teams or active activities must be removed first.',view=FuseView(self.store,ctx.author.id,cards))
    @commands.hybrid_command(name='buy',description='Buy a PvP Shield or Shiny Stone with coins.')
    async def buy(self,ctx,item:str,quantity:int=1):
        key={'shield':'pvpshield','pvpshield':'pvpshield','stone':'shiny_stone','shinystone':'shiny_stone'}.get(item.casefold().replace('_',''))
        try:
            ids,cost=await self.store.buy_consumable(ctx.author.id,key,quantity)
            await ctx.send(f'Purchased {quantity} item(s) for {cost:,} coins. Use `;inv` for IDs and `;use ID` to activate.')
        except ValueError as error:await ctx.send(str(error))


async def setup(bot):await bot.add_cog(GameplayCog(bot))
