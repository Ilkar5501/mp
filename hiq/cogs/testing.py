from __future__ import annotations

import math
import json
from datetime import datetime, timezone

import discord
from discord.ext import commands

from ..battle.engine import BattleEngine
from ..cards import display_title
from ..data import CHARACTERS, CHAR_BY_ID, character_search_score,match_roster_names
from ..models import CardInstance

def test_positions(size):
    if size==6:
        return [("Frontline 1","front_character_id","front"),("Frontline 2","front2_character_id","front"),("Frontline 3","front3_character_id","front"),("Backline 1","back1_character_id","back"),("Backline 2","back2_character_id","back"),("Backline 3","back3_character_id","back")]
    return [("Frontline","front_character_id","front"),("Backline 1","back1_character_id","back"),("Backline 2","back2_character_id","back")]


async def save_test_row(db,owner,row):
    await db.save_test_team(owner,row.get("front_character_id"),row.get("back1_character_id"),row.get("back2_character_id"),team_format=row.get("team_format",3),front2=row.get("front2_character_id"),front3=row.get("front3_character_id"),back3=row.get("back3_character_id"))


PURPLE=discord.Color.from_rgb(72,42,115)


def _npc_card(c, idx:int):
    now=datetime.now(timezone.utc)
    return CardInstance(
        instance_id=f'test-{c.id}-{idx}',full_instance_id=f'TEST:{c.id}:{idx}',card_id=c.card_id,character_id=c.id,
        owner_id=0,original_owner_id=0,original_owner_name='Test Team',serial_no=idx,merge_level=0,
        obtained_at=now,locked=True,boon_stat=None,bane_stat=None,
    )


class BotSearchModal(discord.ui.Modal,title='Search test-team roster'):
    query=discord.ui.TextInput(label='Player Name / Title',required=False,max_length=100)
    def __init__(self,parent:'BotSetView'):
        super().__init__(); self.parent_view=parent
    async def on_submit(self,interaction:discord.Interaction):
        self.parent_view.search=str(self.query.value or '').strip(); self.parent_view.page=0; self.parent_view.rebuild()
        await interaction.response.edit_message(embed=await self.parent_view.embed(),view=self.parent_view)


class BotRosterSelect(discord.ui.Select):
    def __init__(self,parent:'BotSetView'):
        self.parent_view=parent
        opts=[discord.SelectOption(label=display_title(c)[:100],value=c.id,description=f'{c.rarity}★ | {c.position} | {c.school}'[:100]) for c in parent.page_chars()]
        super().__init__(placeholder='Choose a roster player…',options=opts or [discord.SelectOption(label='No results',value='__none__')],disabled=not bool(opts),row=0)
    async def callback(self,interaction:discord.Interaction):
        if self.values[0]=='__none__':
            await interaction.response.send_message('No roster player is available on this page.',ephemeral=True); return
        await interaction.response.send_message(
            f'Place **{display_title(CHAR_BY_ID[self.values[0]])}**:',
            view=BotPlacementView(self.parent_view,self.values[0]),ephemeral=True,
        )


class BotPlacementView(discord.ui.View):
    def __init__(self,parent:'BotSetView',character_id:str):
        super().__init__(timeout=60); self.parent_view=parent; self.character_id=character_id
        self.team_format=parent.team_format
        select=discord.ui.Select(placeholder='Place or replace a specific slot',options=[discord.SelectOption(label=label,value=key) for label,key,_ in test_positions(self.team_format)],row=1)
        async def select_cb(i):
            key=select.values[0]
            lane=next(lane for _,slot,lane in test_positions(self.team_format) if slot==key)
            await self._place(i,lane,key)
        select.callback=select_cb; self.add_item(select)
    async def _place(self,interaction:discord.Interaction,lane:str,key:str|None=None):
        current=await self.parent_view.bot.db.get_test_team(self.parent_view.owner_id) or {}
        size=current.get('team_format',3)
        if size!=self.team_format:
            await interaction.response.edit_message(content='The test-team format changed. Choose the player again.',view=None); return
        positions=test_positions(size)
        newc=CHAR_BY_ID[self.character_id]
        if any(CHAR_BY_ID[current[slot]].name==newc.name and current[slot]!=self.character_id for _,slot,_ in positions if slot!=key and current.get(slot) in CHAR_BY_ID):
            await interaction.response.edit_message(content='A test team cannot contain two versions of the same character name.',view=None); return
        for _,slot,_ in positions:
            if current.get(slot)==self.character_id: current[slot]=None
        slots=[key for _,key,line in positions if line==lane]
        empty=key or next((slot for slot in slots if not current.get(slot)),None)
        if empty is None:
            if len(slots)==1: empty=slots[0]
            else:
                await interaction.response.edit_message(content='That line is full. Use a specific replacement slot in the builder or reset the team.',view=None); return
        current[empty]=self.character_id
        await save_test_row(self.parent_view.bot.db,self.parent_view.owner_id,current)
        await interaction.response.edit_message(content=f'Added **{display_title(newc)}** to the test team {"Frontline" if lane=="front" else "Backline"}.',view=None)
        await self.parent_view.refresh()
    @discord.ui.button(label='Frontline',style=discord.ButtonStyle.primary)
    async def front(self,interaction,button): await self._place(interaction,'front')
    @discord.ui.button(label='Backline',style=discord.ButtonStyle.primary)
    async def back(self,interaction,button): await self._place(interaction,'back')
    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button): await interaction.response.edit_message(content='Selection cancelled.',view=None)


class BotSetView(discord.ui.View):
    def __init__(self,bot,owner_id:int):
        super().__init__(timeout=600); self.bot=bot; self.owner_id=owner_id; self.page=0; self.per_page=25; self.search=''; self.message=None; self.team_format=3; self.rebuild()
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('This test-team builder belongs to another player.',ephemeral=True); return False
        return True
    def filtered(self):return sorted(match_roster_names(self.search),key=lambda c:(c.name.casefold(),c.title.casefold()))
    @property
    def pages(self): return max(1,math.ceil(len(self.filtered())/self.per_page))
    def page_chars(self):
        a=self.filtered(); s=self.page*self.per_page; return a[s:s+self.per_page]
    async def embed(self):
        row=await self.bot.db.get_test_team(self.owner_id) or {}
        e=discord.Embed(title='Personal Test Bot Team',description=f'Roster page **{self.page+1}/{self.pages}**'+(f' • Search: `{self.search}`' if self.search else '')+'\nCustomize virtual equipment, merges and natures below.',color=PURPLE)
        lines=[]
        size=row.get('team_format',3)
        self.team_format=size
        e.title=f'Personal Test Bot Team — {size}v{size}'
        for label,key,_ in test_positions(size):
            cid=row.get(key);config=json.loads(row.get('settings_json','{}')).get(key,{})
            from ..items import ITEM_BY_KEY
            details=f' +{config.get("merge_level",0)} • '+(f'+{config["boon_stat"].upper()}/−{config["bane_stat"].upper()}' if config.get('boon_stat') else 'Neutral')
            details+=' • '+ITEM_BY_KEY.get(config.get('equipment_key'),{}).get('name','No item')
            lines.append(f'**{label}:** '+(display_title(CHAR_BY_ID[cid])+details if cid in CHAR_BY_ID else '—'))
        e.add_field(name='Current Test Team',value='\n'.join(lines),inline=False)
        if all(row.get(k) for _,k,_ in test_positions(size)):
            e.add_field(name='Ready',value='Use `;test` or `/test` to fight this team with your active player team.',inline=False)
        return e
    async def refresh(self):
        if self.message:
            try: await self.message.edit(embed=await self.embed(),view=self)
            except discord.HTTPException: pass
    def rebuild(self):
        self.clear_items(); self.page=min(self.page,self.pages-1); self.add_item(BotRosterSelect(self))
        prev=discord.ui.Button(emoji='◀️',style=discord.ButtonStyle.secondary,row=1,disabled=self.page<=0)
        nxt=discord.ui.Button(emoji='▶️',style=discord.ButtonStyle.secondary,row=1,disabled=self.page>=self.pages-1)
        search=discord.ui.Button(label='Search Name/Title',style=discord.ButtonStyle.primary,row=1)
        reset=discord.ui.Button(label='Reset',style=discord.ButtonStyle.danger,row=1)
        async def prev_cb(i): self.page=max(0,self.page-1); self.rebuild(); await i.response.edit_message(embed=await self.embed(),view=self)
        async def next_cb(i): self.page=min(self.pages-1,self.page+1); self.rebuild(); await i.response.edit_message(embed=await self.embed(),view=self)
        async def search_cb(i): await i.response.send_modal(BotSearchModal(self))
        async def reset_cb(i): row=await self.bot.db.get_test_team(self.owner_id) or {}; await self.bot.db.save_test_team(self.owner_id,None,None,None,team_format=row.get('team_format',3)); self.search=''; self.page=0; self.rebuild(); await i.response.edit_message(embed=await self.embed(),view=self)
        prev.callback=prev_cb; nxt.callback=next_cb; search.callback=search_cb; reset.callback=reset_cb
        for x in (prev,nxt,search,reset): self.add_item(x)
        formats=discord.ui.Select(placeholder='Choose format (switching clears this test team)',options=[discord.SelectOption(label='3v3 — 1 front + 2 back',value='3'),discord.SelectOption(label='6v6 — 3 front + 3 back',value='6')],row=2)
        async def format_cb(i):
            row=await self.bot.db.get_test_team(self.owner_id) or {}
            size=int(formats.values[0])
            if row.get('team_format',3)!=size:
                await self.bot.db.save_test_team(self.owner_id,None,None,None,team_format=size)
            await i.response.edit_message(embed=await self.embed(),view=self)
        formats.callback=format_cb; self.add_item(formats)
        for kind,label in [('equipment','Add Item'),('merge','Set Merge'),('nature','Set Boon/Bane')]:
            button=discord.ui.Button(label=label,row=3)
            async def custom_cb(i,k=kind):
                from .test_customization import TestCustomization
                row=await self.bot.db.get_test_team(self.owner_id) or {}
                view=TestCustomization(self,k,row)
                await i.response.send_message(embed=view.embed(),view=view,ephemeral=False)
            button.callback=custom_cb;self.add_item(button)


class TestingCog(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def _active_specs(self,uid):
        team=await self.bot.db.active_team(uid)
        if not team or not team.complete: return None
        out=[]
        for iid,lane in [(iid,lane) for _,iid,lane in team.entries]:
            card=await self.bot.db.get_card(iid)
            if not card: return None
            out.append((card,CHAR_BY_ID[card.character_id],lane))
        return out

    @commands.hybrid_command(name='botset',description='Build and customize your personal CPU test team.')
    async def botset(self,ctx:commands.Context):
        view=BotSetView(self.bot,ctx.author.id); msg=await ctx.send(embed=await view.embed(),view=view); view.message=msg

    @commands.hybrid_command(name='test',description='Fight your active team against your saved test bot team.')
    async def test(self,ctx:commands.Context):
        player=await self._active_specs(ctx.author.id)
        if not player:
            await ctx.send('Select a complete 3v3 or 6v6 active team before testing.'); return
        row=await self.bot.db.get_test_team(ctx.author.id)
        size=len(player)
        if row and row.get('team_format',3)!=size:
            await ctx.send(f'Your active team is {size}v{size}; your test bot is {row.get("team_format",3)}v{row.get("team_format",3)}. Choose a matching format in `;botset`.'); return
        if not row or not all(row.get(k) in CHAR_BY_ID for _,k,_ in test_positions(size)):
            await ctx.send('Your test bot team is incomplete. Use `;botset` first.'); return
        enemy=[]
        for i,(_,key,lane) in enumerate(test_positions(size),1):
            c=CHAR_BY_ID[row[key]];card=_npc_card(c,i)
            config=json.loads(row.get('settings_json','{}')).get(key,{})
            if config.get('character_id')==c.id:
                card.merge_level=config.get('merge_level',0);card.boon_stat=config.get('boon_stat');card.bane_stat=config.get('bane_stat');card.equipment_key=config.get('equipment_key')
            enemy.append((card,c,lane))
        engine=BattleEngine(player,enemy,owner_a=ctx.author.id,owner_b=None,name_a=ctx.author.display_name,name_b='Test Bot',mode='test')
        battle_cog=self.bot.get_cog('BattleCog')
        _,e,view,file=await battle_cog._finish_battle(ctx,engine,player,enemy,None,ctx.author.display_name,'Test Bot',reward_regular_win=False)
        e.title='🧪 '+e.title
        e.set_footer(text=f'{size}v{size} • Saved test settings applied. No coins, quest wins, shards, or gauntlet progress are awarded.')
        await ctx.send(embed=e,view=view,file=file)



async def setup(bot): await bot.add_cog(TestingCog(bot))
