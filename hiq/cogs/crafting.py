"""Public crafting menus with separate item and character recipe lists."""
from __future__ import annotations
import discord
from ..data import CHARACTERS,CHAR_BY_ID,match_roster_names
from ..items import ITEMS,ITEM_BY_KEY,CHARACTER_SHARD_COST,find_items
from ..progression import TRAINING_SCHOOLS,craft_cost
from ..raid_store import RaidStore
from ..ui import SafeView,PURPLE


class CraftSearchModal(discord.ui.Modal,title='Search recipes'):
    query=discord.ui.TextInput(label='Name',required=False,max_length=100)
    def __init__(self,parent):
        super().__init__();self.parent=parent;self.query.default=parent.query
    async def on_submit(self,i):
        self.parent.query=str(self.query.value).strip();self.parent.page=0;self.parent.target=None;self.parent.source=None
        self.parent.build();await i.response.edit_message(embed=self.parent.embed(),view=self.parent)


class CraftMenu(SafeView):
    def __init__(self,bot,uid,username):
        super().__init__(timeout=600)
        self.bot=bot;self.uid=uid;self.username=username;self.store=RaidStore(bot.db)
        self.category='items';self.page=0;self.target=None;self.source=None;self.owned_only=True;self.query='';self.busy=False
        self.balances={};self.schools={};self.result=''
    @classmethod
    async def create(cls,bot,uid,username):
        view=cls(bot,uid,username);await view.reload();return view
    async def reload(self):
        self.balances={(r['kind'],r['detail']):r['quantity'] for r in await self.store.shard_balances(self.uid)}
        self.schools=await self.bot.db.get_shards(self.uid);self.build()
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open ;craft for your own recipes.',ephemeral=True);return False
        if self.busy:await i.response.send_message('Your craft is still processing.',ephemeral=True);return False
        return True
    def recipes(self):
        if self.category=='items':
            matching={it['key'] for it in find_items(self.query)}
            rows=[{'target':it['key'],'name':it['name'],'sources':{'item':(self.balances.get(('item_shard',it['key']),0),it['shard_cost'])}} for it in ITEMS if it['key'] in matching]
        else:
            matching={c.id for c in match_roster_names(self.query)}
            rows=[]
            for c in CHARACTERS:
                if c.id not in matching:continue
                sources={'character':(self.balances.get(('character_shard',c.id),0),CHARACTER_SHARD_COST[c.rarity])}
                if c.school in TRAINING_SCHOOLS:sources['school']=(self.schools.get(c.school,0),craft_cost(c.rarity))
                rows.append({'target':c.id,'name':c.id,'sources':sources})
        if self.owned_only:rows=[r for r in rows if any(n>0 for n,cost in r['sources'].values())]
        rows.sort(key=lambda r:r['name'].casefold())
        return rows
    def selected(self):return next((r for r in self.recipes() if r['target']==self.target),None)
    @staticmethod
    def counts(recipe):return ' • '.join(f'{source.title()} {n}/{cost}' for source,(n,cost) in recipe['sources'].items())
    def embed(self):
        recipes=self.recipes();pages=max(1,(len(recipes)+24)//25)
        e=discord.Embed(title='Craft — '+self.category.title(),description='Choose a recipe, then craft. Shards are counted by type.\n'+('Showing recipes you have shards for.' if self.owned_only else 'Showing all recipes.'),color=PURPLE)
        selected=self.selected()
        if selected:
            e.add_field(name=selected['name'],value=self.counts(selected)+' shards',inline=False)
            if self.category=='characters':
                e.add_field(name='Crafted card',value='Neutral +0 • choose character or school shards below.',inline=False)
        elif not recipes:e.add_field(name='No matching recipes',value='Choose another category, clear the search or select All recipes.',inline=False)
        if self.result:e.add_field(name='Last crafted',value=self.result,inline=False)
        e.set_footer(text=f'{len(recipes)} recipes • Page {self.page+1}/{pages}'+(f' • Search: {self.query}' if self.query else ''))
        return e
    def build(self):
        self.clear_items();recipes=self.recipes();pages=max(1,(len(recipes)+24)//25);self.page=min(self.page,pages-1)
        category=discord.ui.Select(placeholder='Items or Characters',options=[discord.SelectOption(label=name.title(),value=name,default=self.category==name) for name in ('characters','items')],row=0)
        async def choose_category(i):
            self.category=category.values[0];self.page=0;self.target=None;self.source=None;self.query=''
            await self.reload();await i.response.edit_message(embed=self.embed(),view=self)
        category.callback=choose_category;self.add_item(category)
        chunk=recipes[self.page*25:(self.page+1)*25]
        options=[discord.SelectOption(label=r['name'][:100],value=r['target'],description=self.counts(r)[:100],default=r['target']==self.target) for r in chunk]
        picker=discord.ui.Select(placeholder='Choose a recipe • owned / required shards',options=options or [discord.SelectOption(label='No matching recipes',value='__none__')],disabled=not bool(chunk),row=1)
        async def choose_recipe(i):
            self.target=picker.values[0];self.source=None;self.build();await i.response.edit_message(embed=self.embed(),view=self)
        picker.callback=choose_recipe;self.add_item(picker)
        selected=self.selected()
        if selected:
            sources=selected['sources']
            if self.source not in sources:
                self.source=next((s for s,(n,cost) in sources.items() if n>=cost),next(iter(sources)))
            if self.category=='characters':
                source_picker=discord.ui.Select(placeholder='Choose shard source',options=[discord.SelectOption(label=f'{s.title()} shards — {n}/{cost}',value=s,default=self.source==s) for s,(n,cost) in sources.items()],row=2)
                async def choose_source(i):self.source=source_picker.values[0];self.build();await i.response.edit_message(embed=self.embed(),view=self)
                source_picker.callback=choose_source;self.add_item(source_picker)
        can_craft=bool(selected and self.source in selected['sources'] and selected['sources'][self.source][0]>=selected['sources'][self.source][1])
        craft=discord.ui.Button(label='Craft',style=discord.ButtonStyle.success,disabled=not can_craft,row=3)
        async def commit(i):
            if self.busy:raise ValueError('Your craft is still processing.')
            selected=self.selected()
            if not selected:raise ValueError('Choose a recipe first.')
            self.busy=True
            try:
                made=await self.store.craft(self.uid,self.username,self.target,self.source)
                self.result=(f'**{selected["name"]}** • Item ID `{made}`' if self.category=='items' else f'**{selected["name"]}** • Card ID `{made.instance_id}` • Neutral +0')
                await self.reload();await i.response.edit_message(embed=self.embed(),view=self)
            finally:self.busy=False
        craft.callback=commit;self.add_item(craft)
        toggle=discord.ui.Button(label='All recipes' if self.owned_only else 'Have shards',row=3)
        async def toggle_cb(i):
            self.owned_only=not self.owned_only;self.page=0;self.target=None;self.source=None
            await self.reload();await i.response.edit_message(embed=self.embed(),view=self)
        toggle.callback=toggle_cb;self.add_item(toggle)
        search=discord.ui.Button(label='Search',row=3)
        async def search_cb(i):await i.response.send_modal(CraftSearchModal(self))
        search.callback=search_cb;self.add_item(search)
        for delta,emoji in ((-1,'◀️'),(1,'▶️')):
            button=discord.ui.Button(emoji=emoji,disabled=self.page==0 if delta==-1 else self.page==pages-1,row=4)
            async def nav(i,d=delta):self.page+=d;self.build();await i.response.edit_message(embed=self.embed(),view=self)
            button.callback=nav;self.add_item(button)
