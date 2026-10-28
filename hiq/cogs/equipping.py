"""Choose an owned card before opening its public equipment controls."""
import discord
from ..cards import card_embed,display_title
from ..data import CHAR_BY_ID,match_roster_names
from ..ui import SafeView,PURPLE

class EquipmentSearchModal(discord.ui.Modal,title='Find a card to equip'):
    query=discord.ui.TextInput(label='Character name',required=False,max_length=100)
    def __init__(self,parent):super().__init__();self.parent=parent;self.query.default=parent.query
    async def on_submit(self,i):
        self.parent.query=str(self.query.value).strip();self.parent.page=0;self.parent.build()
        await i.response.edit_message(embed=self.parent.embed(),view=self.parent)

class EquipmentCardPicker(SafeView):
    def __init__(self,bot,uid,cards):
        super().__init__(timeout=600);self.bot=bot;self.uid=uid;self.cards=cards;self.page=0;self.query='';self.build()
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open ;equip to select your own cards.',ephemeral=True);return False
        return True
    def matching(self):
        wanted={c.id for c in match_roster_names(self.query)}
        return sorted((c for c in self.cards if c.character_id in wanted),key=lambda c:(CHAR_BY_ID[c.character_id].name,c.character_id,-c.merge_level,c.instance_id))
    def embed(self):
        cards=self.matching();pages=max(1,(len(cards)+24)//25)
        e=discord.Embed(title='Equip — Choose a Card',description='Select an owned card, then choose an eligible item from its equipment dropdown.',color=PURPLE)
        e.set_footer(text=f'{len(cards)} cards • Page {self.page+1}/{pages}'+(f' • Search: {self.query}' if self.query else ''))
        return e
    def build(self):
        self.clear_items();cards=self.matching();pages=max(1,(len(cards)+24)//25);self.page=min(self.page,pages-1)
        options=[discord.SelectOption(label=display_title(CHAR_BY_ID[c.character_id])[:100],value=c.instance_id,description=f'+{c.merge_level} • ID {c.instance_id}') for c in cards[self.page*25:(self.page+1)*25]]
        picker=discord.ui.Select(placeholder='Choose an owned card',options=options or [discord.SelectOption(label='No matching owned cards',value='__none__')],disabled=not bool(options),row=0)
        async def choose(i):
            from .raids import EquipmentView
            card=await self.bot.db.get_card(picker.values[0])
            if not card or card.owner_id!=self.uid:raise ValueError('That card is no longer yours. Open ;equip again.')
            await i.response.send_message(embed=card_embed(self.bot,card),view=await EquipmentView.create(self.bot,self.uid,card),ephemeral=False)
        picker.callback=choose;self.add_item(picker)
        for delta,emoji in ((-1,'◀️'),(1,'▶️')):
            button=discord.ui.Button(emoji=emoji,disabled=self.page==0 if delta==-1 else self.page==pages-1,row=1)
            async def nav(i,d=delta):self.page+=d;self.build();await i.response.edit_message(embed=self.embed(),view=self)
            button.callback=nav;self.add_item(button)
        search=discord.ui.Button(label='Search',row=1)
        async def search_cb(i):await i.response.send_modal(EquipmentSearchModal(self))
        search.callback=search_cb;self.add_item(search)
