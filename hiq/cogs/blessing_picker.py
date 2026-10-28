"""Select an owned Blessing and matching-rarity card before choosing its nature."""
import discord
from ..data import CHAR_BY_ID
from ..cards import display_title
from ..community_store import CommunityStore
from ..ui import SafeView,PURPLE

class BlessingPicker(SafeView):
    def __init__(self,cog,uid,blessings,cards,item_id=None):
        super().__init__(timeout=600);self.cog=cog;self.uid=uid
        self.blessings=sorted(blessings,key=lambda it:(it['stars'],it['id']))
        self.cards=sorted(cards,key=lambda c:(CHAR_BY_ID[c.character_id].name.casefold(),CHAR_BY_ID[c.character_id].title.casefold(),c.instance_id))
        self.item_id=item_id;self.card_id=None;self.item_page=0;self.card_page=0;self.build()
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open ;bless for your own cards.',ephemeral=True);return False
        return True
    def matching_cards(self):
        item=next((it for it in self.blessings if it['id']==self.item_id),None)
        return [c for c in self.cards if item and CHAR_BY_ID[c.character_id].rarity==item['stars']]
    def embed(self):
        e=discord.Embed(title='Blessing — Choose Item & Card',description='Choose a Blessing and a card with the same star rating. Then choose the boon and bane. The Blessing is consumed only when you confirm the new nature.',color=PURPLE)
        if self.item_id:e.add_field(name='Blessing',value=self.item_id)
        if self.card_id:e.add_field(name='Card',value=display_title(CHAR_BY_ID[next(c for c in self.cards if c.instance_id==self.card_id).character_id])+f' • `{self.card_id}`',inline=False)
        return e
    def build(self):
        self.clear_items();ipages=max(1,(len(self.blessings)+24)//25);self.item_page=min(self.item_page,ipages-1)
        items=self.blessings[self.item_page*25:(self.item_page+1)*25]
        picker=discord.ui.Select(placeholder='Choose a Blessing',options=[discord.SelectOption(label=f'{it["stars"]}★ Blessing',description=it['id'],value=it['id'],default=it['id']==self.item_id) for it in items] or [discord.SelectOption(label='No owned Blessings',value='__none__')],disabled=not bool(items),row=0)
        async def item_cb(i):self.item_id=picker.values[0];self.card_id=None;self.card_page=0;self.build();await i.response.edit_message(embed=self.embed(),view=self)
        picker.callback=item_cb;self.add_item(picker)
        cards=self.matching_cards();cpages=max(1,(len(cards)+24)//25);self.card_page=min(self.card_page,cpages-1)
        card_picker=discord.ui.Select(placeholder='Choose a matching-rarity card',options=[discord.SelectOption(label=display_title(CHAR_BY_ID[c.character_id])[:100],value=c.instance_id,description=f'+{c.merge_level} • {c.instance_id}',default=c.instance_id==self.card_id) for c in cards[self.card_page*25:(self.card_page+1)*25]] or [discord.SelectOption(label='Choose a Blessing with a matching owned card',value='__none__')],disabled=not bool(cards),row=1)
        async def card_cb(i):self.card_id=card_picker.values[0];self.build();await i.response.edit_message(embed=self.embed(),view=self)
        card_picker.callback=card_cb;self.add_item(card_picker)
        proceed=discord.ui.Button(label='Choose Boon/Bane',style=discord.ButtonStyle.primary,row=2,disabled=not bool(self.item_id and self.card_id))
        async def proceed_cb(i):
            from .community import BlessingView
            item=next((it for it in await self.cog.store.items(self.uid) if it['id']==self.item_id and it['kind']=='blessing'),None)
            card=await self.cog.bot.db.get_card(self.card_id)
            if not item or not card or card.owner_id!=self.uid or CHAR_BY_ID[card.character_id].rarity!=item['stars']:raise ValueError('The item or card changed. Open ;bless again.')
            view=BlessingView(self.cog,self.uid,self.item_id,card)
            await i.response.edit_message(content=view.summary(),embed=None,view=view);self.stop()
        proceed.callback=proceed_cb;self.add_item(proceed)
        for kind,pages,current in (('Blessings',ipages,self.item_page),('Cards',cpages,self.card_page)):
            for delta,emoji in ((-1,'◀️'),(1,'▶️')):
                button=discord.ui.Button(label=kind,emoji=emoji,row=3,disabled=current==0 if delta==-1 else current==pages-1)
                async def nav(i,d=delta,k=kind):
                    if k=='Blessings':self.item_page+=d
                    else:self.card_page+=d
                    self.build();await i.response.edit_message(embed=self.embed(),view=self)
                button.callback=nav;self.add_item(button)
