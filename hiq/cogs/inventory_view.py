"""Owner-only inventory pages with search and the existing saved view preference."""
import discord
from ..inventory import inventory_entries,search_entries
from ..ui import SafeView,PURPLE


class InventorySearch(discord.ui.Modal,title='Search inventory'):
    query=discord.ui.TextInput(label='Item, shard name or ID',required=False,max_length=100)
    def __init__(self,parent):
        super().__init__();self.parent=parent;self.query.default=parent.query[:100]
    async def on_submit(self,i):
        self.parent.query=str(self.query.value).strip();self.parent.page=0;self.parent.build()
        await i.response.edit_message(embed=self.parent.embed(),view=self.parent)


class InventoryView(SafeView):
    PAGE_SIZE=25
    def __init__(self,store,uid,name,schools,balances,items,*,stacked=False,query=''):
        super().__init__(timeout=600)
        self.store=store;self.uid=uid;self.name=name;self.schools=schools;self.balances=balances;self.items=items
        self.stacked=stacked;self.query=query;self.page=0;self.build()

    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('Open ;inv for your own inventory.',ephemeral=True);return False
        return True

    def row_text(self,row):
        name=discord.utils.escape_markdown(row.label)
        if row.reference:
            return f'{name} · `{row.reference}`'+(f' · equipped: `{row.equipped_on}`' if row.equipped_on else '')
        return f'{name} · **{row.quantity}×**'

    def make_pages(self):
        rows=search_entries(inventory_entries(self.schools,self.balances,self.items,stacked=self.stacked),self.query)
        self.total=len(rows);pages=[];chunk=[];size=0
        for row in rows:
            # Keep 25 entries per page while respecting Discord's description limit.
            added=len(self.row_text(row))+len(row.category)+10
            if chunk and (len(chunk)>=self.PAGE_SIZE or size+added>3700):pages.append(chunk);chunk=[];size=0
            chunk.append(row);size+=added
        pages.append(chunk);self.pages=pages;self.page=min(self.page,len(pages)-1)

    def embed(self):
        lines=[];category=None
        if self.query:lines.append('Search: '+discord.utils.escape_markdown(self.query[:100])+'\n')
        for row in self.pages[self.page]:
            if row.category!=category:
                if category:lines.append('')
                lines.append('**'+row.category+'**');category=row.category
            lines.append(self.row_text(row))
        if not self.total:lines.append('No matching entries.' if self.query else 'Your inventory is empty.')
        e=discord.Embed(title=f'{self.name} — Inventory'[:256],description='\n'.join(lines),color=PURPLE)
        e.set_footer(text=f'Page {self.page+1}/{len(self.pages)} · {self.total} entries · '+('Stacked' if self.stacked else 'IDs'))
        return e

    def build(self):
        self.clear_items();self.make_pages()
        for step,emoji in ((-1,'◀️'),(1,'▶️')):
            b=discord.ui.Button(emoji=emoji,style=discord.ButtonStyle.secondary,disabled=self.page==0 if step<0 else self.page==len(self.pages)-1)
            async def turn(i,d=step):
                self.page=max(0,min(len(self.pages)-1,self.page+d));self.build()
                await i.response.edit_message(embed=self.embed(),view=self)
            b.callback=turn;self.add_item(b)
        search=discord.ui.Button(label='Search',style=discord.ButtonStyle.secondary)
        async def find(i):await i.response.send_modal(InventorySearch(self))
        search.callback=find;self.add_item(search)
        mode=discord.ui.Button(label='Show IDs' if self.stacked else 'Stack items',style=discord.ButtonStyle.secondary)
        async def toggle(i):
            stacked=not self.stacked
            async with self.store.tx() as c:
                await c.execute('INSERT INTO inventory_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET stacked=excluded.stacked',(self.uid,int(stacked)))
            self.stacked=stacked;self.page=0;self.build()
            await i.response.edit_message(embed=self.embed(),view=self)
        mode.callback=toggle;self.add_item(mode)
        if self.query:
            clear=discord.ui.Button(label='Clear search',style=discord.ButtonStyle.secondary)
            async def reset(i):
                self.query='';self.page=0;self.build();await i.response.edit_message(embed=self.embed(),view=self)
            clear.callback=reset;self.add_item(clear)
