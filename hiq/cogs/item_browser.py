"""Searchable three-item catalogue with per-player daily shard progress."""
import math
import discord
from ..items import ITEMS,find_items
from ..raid_store import RaidStore
from ..ui import SafeView,PURPLE


class ItemSearch(discord.ui.Modal,title='Search equipment'):
    query=discord.ui.TextInput(label='Item name',placeholder='Enter a name or part of a name',required=False,max_length=100)
    def __init__(self,view):super().__init__();self.view=view
    async def on_submit(self,i):
        self.view.matches=sorted(find_items(str(self.query)),key=lambda x:x['name'])
        self.view.page=0;self.view.menu_page=0
        await self.view.refresh(i)


class ItemBrowser(SafeView):
    def __init__(self,bot,uid,matches,progress):
        super().__init__(timeout=600)
        self.bot=bot;self.uid=uid;self.matches=matches;self.progress=progress;self.page=0;self.menu_page=0
        self.catalogue=sorted(ITEMS,key=lambda x:x['name'])
        self.build()

    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('This item browser belongs to another player.',ephemeral=True);return False
        return True

    async def refresh(self,i):
        self.progress=await RaidStore(self.bot.db).shard_progress(self.uid)
        self.build();await i.response.edit_message(embed=self.embed(),view=self)

    def embed(self):
        blocks=[]
        for it in self.matches[self.page*3:self.page*3+3]:
            cost=it['shard_cost'];cap=cost*2*self.progress.get('_days',1);earned=self.progress.get(it['key'],0)
            blocks.append(f'> **{it["name"]} — {it.get("emoji","🏐")}**\n> **`{it["effect"]}`**\n> -# Shard Cost: {cost}\n> -# Raid Type: *{it["theme"].title()}*\n> -# Who can equip: *{it["eligible"]}*\n> -# Accrued Shard Allowance: {cap} | {earned/cap:.0%} ({earned}/{cap}) • +{cost*2}/day; expires weekly')
        e=discord.Embed(title='Equipment Items',description='\n\n'.join(blocks) or 'No matching items. Try another search or browse all items.',color=PURPLE)
        e.set_footer(text=f'Page {self.page+1}/{max(1,math.ceil(len(self.matches)/3))} • {len(self.matches)} items • ;craft name • ;allcraft')
        return e

    def build(self):
        self.clear_items()
        search=discord.ui.Button(label='Search',emoji='🔎',row=0)
        async def search_cb(i):await i.response.send_modal(ItemSearch(self))
        search.callback=search_cb;self.add_item(search)
        all_button=discord.ui.Button(label='All items',row=0)
        async def show_all(i):self.matches=list(self.catalogue);self.page=0;await self.refresh(i)
        all_button.callback=show_all;self.add_item(all_button)
        pages=max(1,math.ceil(len(self.matches)/3))
        if pages>1:
            for delta,emoji in [(-1,'◀️'),(1,'▶️')]:
                button=discord.ui.Button(emoji=emoji,row=0,disabled=self.page==0 if delta<0 else self.page==pages-1)
                async def nav(i,d=delta):self.page+=d;await self.refresh(i)
                button.callback=nav;self.add_item(button)
        options=[discord.SelectOption(label=it['name'],value=it['key']) for it in self.catalogue[self.menu_page*23:self.menu_page*23+23]]
        if self.menu_page:options.append(discord.SelectOption(label='Previous item choices',value='__prev__'))
        if (self.menu_page+1)*23<len(self.catalogue):options.append(discord.SelectOption(label='More item choices',value='__next__'))
        select=discord.ui.Select(placeholder='Choose an item to view',options=options,row=1)
        async def choose(i):
            key=select.values[0]
            if key in ('__prev__','__next__'):self.menu_page+=1 if key=='__next__' else -1
            else:self.matches=[next(it for it in self.catalogue if it['key']==key)];self.page=0
            await self.refresh(i)
        select.callback=choose;self.add_item(select)
