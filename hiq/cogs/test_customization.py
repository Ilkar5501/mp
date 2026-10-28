"""Virtual equipment, merge and nature controls for personal test teams."""
import json
import discord
from ..data import CHAR_BY_ID
from ..items import ITEM_BY_KEY,can_equip
from ..ui import SafeView,PURPLE

class TestCustomization(SafeView):
    def __init__(self,parent,kind,row):
        super().__init__(timeout=300);self.parent_view=parent;self.kind=kind;self.row=row;self.slot=None;self.character_id=None;self.value=None;self.boon=None;self.bane=None;self.page=0;self.build()
    async def interaction_check(self,i):
        if i.user.id!=self.parent_view.owner_id:await i.response.send_message('This builder belongs to another player.',ephemeral=True);return False
        return True
    def embed(self):
        e=discord.Embed(title='Test Team — '+self.kind.title(),description='Select a unit, choose its setting and save. Test settings use virtual items and do not consume your inventory.',color=PURPLE)
        if self.character_id:
            e.add_field(name='Selected unit',value=CHAR_BY_ID[self.character_id].id,inline=False)
            if self.kind=='equipment' and self.value is not None:e.add_field(name='Item',value=ITEM_BY_KEY.get(self.value,{}).get('name','Empty slot'))
            elif self.kind=='merge' and self.value is not None:e.add_field(name='Merge',value=f'+{self.value}')
        return e
    def build(self):
        from .testing import test_positions
        self.clear_items();entries=[(label,key,self.row[key]) for label,key,_ in test_positions(self.row.get('team_format',3)) if self.row.get(key) in CHAR_BY_ID]
        entries.sort(key=lambda r:CHAR_BY_ID[r[2]].id.casefold())
        units=discord.ui.Select(placeholder='Choose a test-team unit',options=[discord.SelectOption(label=CHAR_BY_ID[cid].id[:100],description=label,value=key,default=key==self.slot) for label,key,cid in entries] or [discord.SelectOption(label='Add a unit first',value='__none__')],disabled=not bool(entries),row=0)
        async def unit_cb(i):
            self.slot=units.values[0];self.character_id=self.row[self.slot];self.value=None;self.boon=self.bane=None;self.page=0;self.build();await i.response.edit_message(embed=self.embed(),view=self)
        units.callback=unit_cb;self.add_item(units)
        ready=False
        if self.character_id:
            char=CHAR_BY_ID[self.character_id]
            if self.kind=='equipment':
                items=sorted((it for it in ITEM_BY_KEY.values() if can_equip(it,char)),key=lambda it:it['name'].casefold());pages=max(1,(len(items)+23)//24);self.page=min(self.page,pages-1)
                options=[discord.SelectOption(label='Empty slot / Unequip',value='__empty__')]+[discord.SelectOption(label=it['name'],value=it['key']) for it in items[self.page*24:(self.page+1)*24]]
                picker=discord.ui.Select(placeholder='Choose an item',options=options,row=1)
                async def equip_cb(i):self.value=picker.values[0];self.build();await i.response.edit_message(embed=self.embed(),view=self)
                picker.callback=equip_cb;self.add_item(picker);ready=self.value is not None
                for delta,emoji in ((-1,'◀️'),(1,'▶️')):
                    button=discord.ui.Button(emoji=emoji,row=4,disabled=self.page==0 if delta==-1 else self.page==pages-1)
                    async def nav(i,d=delta):self.page+=d;self.build();await i.response.edit_message(embed=self.embed(),view=self)
                    button.callback=nav;self.add_item(button)
            elif self.kind=='merge':
                picker=discord.ui.Select(placeholder='Choose merge level',options=[discord.SelectOption(label=f'+{n}',value=str(n),default=self.value==n) for n in range(11)],row=1)
                async def merge_cb(i):self.value=int(picker.values[0]);self.build();await i.response.edit_message(embed=self.embed(),view=self)
                picker.callback=merge_cb;self.add_item(picker);ready=self.value is not None
            else:
                for row,kind in ((1,'boon'),(2,'bane')):
                    stats=sorted(['hp',char.offense_key,'def','rec','spd','jmp'])
                    picker=discord.ui.Select(placeholder='Choose '+kind,options=[discord.SelectOption(label=s.upper(),value=s,default=getattr(self,kind)==s) for s in stats],row=row)
                    async def nature_cb(i,s=picker,k=kind):setattr(self,k,s.values[0]);self.build();await i.response.edit_message(embed=self.embed(),view=self)
                    picker.callback=nature_cb;self.add_item(picker)
                ready=bool(self.boon and self.bane)
        save=discord.ui.Button(label='Save',row=3,style=discord.ButtonStyle.success,disabled=not ready)
        async def save_cb(i):
            value=(self.boon,self.bane) if self.kind=='nature' else (None if self.value=='__empty__' else self.value)
            await self.parent_view.bot.db.set_test_modifiers(self.parent_view.owner_id,self.slot,self.character_id,self.kind,value)
            await i.response.edit_message(content='Test-team setting saved.',embed=None,view=None);await self.parent_view.refresh();self.stop()
        save.callback=save_cb;self.add_item(save)
