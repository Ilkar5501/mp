import math
import discord
from ..data import CHAR_BY_ID
from ..cards import card_embed,template_embed
from ..gameplay_store import GameplayStore
from ..ui import SafeView,PURPLE
from .gameplay import OwnedView


class StoneView(OwnedView):
    def __init__(self,store,uid,item_id,cards):
        super().__init__(uid);self.store=store;self.item_id=item_id;self.cards=cards;self.page=0;self.build()
    def build(self):
        self.clear_items();page=self.cards[self.page*25:self.page*25+25]
        select=discord.ui.Select(placeholder='Choose a card to unlock its next shiny artwork',options=[discord.SelectOption(label=(CHAR_BY_ID[c.character_id].name+' — '+CHAR_BY_ID[c.character_id].title)[:100],value=c.instance_id,description=f'+{c.merge_level} · {c.instance_id}') for c in page])
        async def choose(i):
            slot=await self.store.use_stone(self.uid,self.item_id,select.values[0]);self.stop()
            await i.response.edit_message(content=f'Unlocked **Shiny {slot}**. This card keeps its boon and loses its bane. View it with `;cv {select.values[0]}`.',view=None)
        select.callback=choose;self.add_item(select)
        for text,d in [('Previous',-1),('Next',1)]:
            b=discord.ui.Button(label=text,row=1,disabled=not 0<=self.page+d<math.ceil(len(self.cards)/25))
            async def nav(i,delta=d):self.page+=delta;self.build();await i.response.edit_message(view=self)
            b.callback=nav;self.add_item(b)


class FuseView(OwnedView):
    def __init__(self,store,uid,cards):
        super().__init__(uid);self.store=store;self.cards=cards;self.first=None;self.second=None;self.page=0;self.build()
    def build(self):
        self.clear_items()
        candidates=[c for c in self.cards if c.character_id=='Oikawa — Young Emperor'] if self.first is None else [c for c in self.cards if c.character_id=='Kageyama — King of the Lost Court' and c.merge_level==self.first.merge_level]
        if not candidates:
            b=discord.ui.Button(label='No matching ingredients',disabled=True);self.add_item(b)
        else:
            page=candidates[self.page*25:self.page*25+25]
            select=discord.ui.Select(placeholder='Choose Oikawa — Young Emperor' if self.first is None else 'Choose matching Kageyama',options=[discord.SelectOption(label=f'+{c.merge_level} · {CHAR_BY_ID[c.character_id].title}',description=c.instance_id,value=c.instance_id) for c in page])
            async def choose(i):
                selected=next(c for c in self.cards if c.instance_id==select.values[0])
                if self.first is None:self.first=selected;self.page=0;self.build();await i.response.edit_message(view=self)
                else:
                    self.second=selected;level,coins,marks=await self.store.fuse(self.uid,self.first.instance_id,self.second.instance_id)
                    await i.response.edit_message(content=f'Fuse these two cards into **Kings of the Playground +{level}** for **{coins:,} coins + {marks} AF1 Marks**? Both ingredients are consumed, including their shiny unlocks. The Duo has its own shiny slots.',view=FuseConfirm(self.store,self.uid,self.first.instance_id,self.second.instance_id))
            select.callback=choose;self.add_item(select)
            for text,d in [('Previous',-1),('Next',1)]:
                b=discord.ui.Button(label=text,row=1,disabled=not 0<=self.page+d<math.ceil(len(candidates)/25))
                async def nav(i,delta=d):self.page+=delta;self.build();await i.response.edit_message(view=self)
                b.callback=nav;self.add_item(b)
        if self.first:
            b=discord.ui.Button(label='Change Oikawa',row=2)
            async def back(i):self.first=None;self.page=0;self.build();await i.response.edit_message(view=self)
            b.callback=back;self.add_item(b)


class FuseConfirm(OwnedView):
    def __init__(self,store,uid,a,b):super().__init__(uid);self.store=store;self.a=a;self.b=b
    @discord.ui.button(label='Confirm fusion',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        card=await self.store.fuse(self.uid,self.a,self.b,confirm=True);self.stop()
        await i.response.edit_message(content=f'Created **Oikawa & Kageyama — Kings of the Playground +{card.merge_level}**. Card ID: `{card.instance_id}`.',view=None)
    @discord.ui.button(label='Cancel')
    async def cancel(self,i,button):self.stop();await i.response.edit_message(content='Fusion cancelled.',view=None)


class QuantityModal(discord.ui.Modal,title='Purchase clan supplies'):
    quantity=discord.ui.TextInput(label='Quantity',default='1',max_length=3)
    def __init__(self,store,uid,kind,stars):super().__init__();self.store=store;self.uid=uid;self.kind=kind;self.stars=stars
    async def on_submit(self,i):
        if i.user.id!=self.uid:return
        try:
            quantity=int(str(self.quantity));ids,cost=await self.store.buy_clan_supply(self.uid,self.kind,self.stars,quantity)
        except (ValueError,TypeError) as error:await i.response.send_message(str(error),ephemeral=True);return
        await i.response.send_message(f'Purchased **{quantity}** item(s) for **{cost:,} Clan Dollars**. Find their IDs in `;inv`.',ephemeral=True)


class BulkClanShop(OwnedView):
    def __init__(self,store,uid):
        super().__init__(uid)
        options=[]
        for kind,label,mult in [('mergecopy','Merge Copy',150),('blessing','Blessing',200),('starpack','Star Pack',100)]:
            for stars in range(1,6):options.append(discord.SelectOption(label=f'{stars}★ {label} — {stars*mult} Clan Dollars',value=f'{kind}|{stars}'))
        options.extend([discord.SelectOption(label='PvP Shield — 750 Clan Dollars',value='pvpshield|1'),discord.SelectOption(label='Shiny Stone — 15,000 Clan Dollars',value='shiny_stone|1')])
        select=discord.ui.Select(placeholder='Choose an item, then enter a quantity',options=options)
        async def choose(i):
            kind,stars=select.values[0].split('|');await i.response.send_modal(QuantityModal(store,uid,kind,int(stars)))
        select.callback=choose;self.add_item(select)


def add_art_buttons(view,bot,uid,card):
    char=CHAR_BY_ID[card.character_id]
    if not card.shiny_slots:return
    if not hasattr(view,'art_preview'):view.art_preview=card.art_slot
    slots=[0]+sorted(card.shiny_slots)
    for text,d in [('↑',-1),('↓',1)]:
        position=slots.index(view.art_preview) if view.art_preview in slots else 0
        b=discord.ui.Button(label=text,row=3,disabled=not 0<=position+d<len(slots))
        async def nav(i,delta=d):
            fresh=await bot.db.get_card(card.instance_id)
            if not fresh or fresh.owner_id!=i.user.id:raise ValueError('This card is no longer yours.')
            view.art_preview=slots[max(0,min(len(slots)-1,slots.index(view.art_preview)+delta))]
            view.build();await i.response.edit_message(embed=card_embed(bot,fresh,art_slot=view.art_preview),view=view)
        b.callback=nav;view.add_item(b)
    lock=discord.ui.Button(label='Lock displayed art',emoji='🔒',row=3)
    async def save(i):
        await GameplayStore(bot.db).lock_art(uid,card.instance_id,view.art_preview)
        await i.response.send_message('Selected artwork saved for this card.',ephemeral=True)
    lock.callback=save;view.add_item(lock)
