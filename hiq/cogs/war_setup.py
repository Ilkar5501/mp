"""Officer-owned clan, battle-format and war-length selector."""
import discord
from ..ui import SafeView,PURPLE


class WarSetupView(SafeView):
    def __init__(self,cog,ctx,clans,clan_id=None,fmt=3,size=3):
        super().__init__(timeout=300)
        self.cog=cog;self.ctx=ctx;self.uid=ctx.author.id;self.clans=clans
        self.clan_id=clan_id;self.fmt=fmt;self.size=size;self.page=0;self.sent=False
        self.build()

    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open ;clanwar for your own challenge.',ephemeral=True);return False
        if self.sent:await i.response.send_message('This challenge menu is closed.',ephemeral=True);return False
        return True

    def embed(self):
        return discord.Embed(title='Challenge a clan',description=f'Choose an opponent, battle format, and war length.\n**{self.fmt}v{self.fmt} • {"Quick: 3" if self.size==3 else "Long: 5"} players per clan**\nQuick Wars use the first three distinct players sent.',color=PURPLE)

    def build(self):
        self.clear_items();pages=max(1,(len(self.clans)+24)//25)
        chunk=self.clans[self.page*25:(self.page+1)*25]
        picker=discord.ui.Select(placeholder='Choose opposing clan',options=[discord.SelectOption(label=c['name'][:100],value=c['id'],description=f'{c["occupancy"]} members',default=c['id']==self.clan_id) for c in chunk] or [discord.SelectOption(label='No other clans',value='none')],disabled=not bool(chunk),row=0)
        async def choose(i):self.clan_id=picker.values[0];self.build();await i.response.edit_message(embed=self.embed(),view=self)
        picker.callback=choose;self.add_item(picker)
        for row,key,choices in ((1,'fmt',[(3,'3v3'),(6,'6v6')]),(2,'size',[(3,'Quick — 3 players'),(5,'Long — 5 players')])):
            select=discord.ui.Select(placeholder='Battle format' if key=='fmt' else 'War length',options=[discord.SelectOption(label=label,value=str(v),default=getattr(self,key)==v) for v,label in choices],row=row)
            async def change(i,s=select,k=key):setattr(self,k,int(s.values[0]));self.build();await i.response.edit_message(embed=self.embed(),view=self)
            select.callback=change;self.add_item(select)
        for delta,label in ((-1,'Previous clans'),(1,'Next clans')):
            b=discord.ui.Button(label=label,disabled=self.page==0 if delta==-1 else self.page>=pages-1,row=3)
            async def nav(i,d=delta):self.page+=d;self.build();await i.response.edit_message(view=self)
            b.callback=nav;self.add_item(b)
        send=discord.ui.Button(label='Send War Offer',style=discord.ButtonStyle.success,disabled=self.clan_id is None,row=4)
        async def challenge(i):
            self.sent=True
            await i.response.defer()
            try:offer=await self.cog.send_war_offer(self.ctx,self.clan_id,self.fmt,self.size)
            except Exception:self.sent=False;raise
            if offer is None:
                self.sent=False;await i.edit_original_response(content='The offer was not sent. Check the error above and choose again.',embed=self.embed(),view=self);return
            await i.edit_original_response(content='War offer sent.',embed=None,view=None);self.stop()
        send.callback=challenge;self.add_item(send)
