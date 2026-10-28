from __future__ import annotations

import asyncio
import hashlib
import random
import re
import logging
from dataclasses import asdict

import discord
from discord.ext import commands

from ..battle_render import render_battle_summary
from ..cards import compact_card_line, nature_text,display_title
from ..data import CHAR_BY_ID
from ..community_store import CommunityStore, ADMIN_ID, PREFECTURE_DAILY_CAP, PREFECTURE_WIN_REWARD
from ..prefecture import FORMATIONS, PodBattleEngine, cpu_specs
from ..tournament import restore_specs, restore_result
from ..ui import SafeView, EmbedPager, text_pages, PURPLE

log=logging.getLogger(__name__)


def item_label(item):
    from ..items import inventory_label
    special=inventory_label(item)
    if special:
        if item.get('equipped_on'):
            char=CHAR_BY_ID.get(item.get('equipped_character'))
            special+=f' • equipped to {display_title(char) if char else "card"} (`{item["equipped_on"]}`)'
        return special
    names={'mergecopy':'Merge Copy','blessing':'Blessing','starpack':'Star Pack','pack_base':'Base Pack','pack_school':'School Pack','pack_position':'Position Pack'}
    return (f'{item["stars"]}★ ' if item.get('stars') else '')+names.get(item['kind'],item['kind'])+(f' • {item["detail"]}' if item.get('detail') else '')


class DonateConfirm(SafeView):
    def __init__(self,cog,uid,iid):super().__init__(timeout=120);self.cog=cog;self.uid=uid;self.iid=iid
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This donation belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Confirm Donation',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        payout,count=await self.cog.store.donate(i.user.id,self.iid)
        await i.response.edit_message(content=f'Donated card deleted. **{count} clan members each received {payout} clan coins**. Any team slot using this card is now empty.',embed=None,view=None)
    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):await i.response.edit_message(content='Donation cancelled.',embed=None,view=None)


class BlessingView(SafeView):
    def __init__(self,cog,uid,item_id,card):
        super().__init__(timeout=300);self.cog=cog;self.uid=uid;self.item_id=item_id;self.card=card;self.boon=None;self.bane=None
        from ..data import CHAR_BY_ID
        char=CHAR_BY_ID[card.character_id];stats=sorted(['hp',char.offense_key,'def','rec','spd','jmp'])
        for row,kind in enumerate(('boon','bane')):
            select=discord.ui.Select(placeholder=f'Choose your {kind}',options=[discord.SelectOption(label=s.upper(),value=s) for s in stats],row=row)
            async def choose(i,s=select,k=kind):
                setattr(self,k,s.values[0]);self.confirm.disabled=not(self.boon and self.bane)
                await i.response.edit_message(content=self.summary(),view=self)
            select.callback=choose;self.add_item(select)
        self.confirm.disabled=True
    def summary(self):
        return f'Blessing `{self.item_id}` → card `{self.card.instance_id}`\nBoon: **{(self.boon or "choose").upper()}** • Bane: **{(self.bane or "choose").upper()}**\nChoosing the same stat makes the card Neutral. Confirm consumes the Blessing.'
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This Blessing belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Confirm Nature',style=discord.ButtonStyle.success,row=2)
    async def confirm(self,i,button):
        if not self.boon or not self.bane:raise ValueError('Choose both stats first.')
        card=await self.cog.store.bless(self.uid,self.item_id,self.card.instance_id,self.boon,self.bane)
        await i.response.edit_message(content=f'Blessing consumed. `{card.instance_id}` now has {nature_text(card)}.',view=None)


class ClanShopView(SafeView):
    def __init__(self,cog,uid):
        super().__init__(timeout=300);self.cog=cog;self.uid=uid
        options=[discord.SelectOption(label=f'{s}★ {label} — {s*mult} clan coins',value=f'{kind}:{s}') for kind,label,mult in [('mergecopy','Merge Copy',150),('blessing','Blessing',200),('starpack','Star Pack',100)] for s in range(1,6)]
        select=discord.ui.Select(placeholder='Buy one item',options=sorted(options,key=lambda o:o.label.split('★ ',1)[-1].casefold()))
        async def buy(i):
            kind,stars=select.values[0].split(':');iid,cost=await cog.store.buy(i.user.id,kind,int(stars))
            await i.response.send_message(f'Bought **{item_label({"kind":kind,"stars":int(stars)})}** for {cost} clan coins. Item ID: `{iid}`. Use ;inv.',ephemeral=True)
        select.callback=buy;self.add_item(select)
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open ;clanshop for your own purchases.',ephemeral=True);return False
        return True


class RedeemLimitView(SafeView):
    def __init__(self,cog,uid,code,prize,expiry):super().__init__(timeout=180);self.cog=cog;self.uid=uid;self.code=code;self.prize=prize;self.expiry=expiry
    async def interaction_check(self,i):
        if i.user.id!=ADMIN_ID:await i.response.send_message('Only the designated administrator can create redeem codes.',ephemeral=True);return False
        return True
    async def create(self,i,scope):
        code,prize=await self.cog.store.create_redeem(i.user.id,self.code,self.prize,scope,self.expiry)
        await i.response.edit_message(content=f'Code **{code}** created: `{self.prize}` • {"once per player" if scope=="player" else "once overall"}.',view=None)
    @discord.ui.button(label='Once Per Player',style=discord.ButtonStyle.primary)
    async def player(self,i,button):await self.create(i,'player')
    @discord.ui.button(label='Once Overall',style=discord.ButtonStyle.secondary)
    async def overall(self,i,button):await self.create(i,'overall')


class SeriesResultsView(SafeView):
    """The regular result image and log controls, plus game navigation."""
    def __init__(self,battle,games,a,b,name_a,name_b,owners,title):
        super().__init__(timeout=900);self.battle=battle;self.games=games;self.a=a;self.b=b;self.names=(name_a,name_b);self.owners=owners;self.title=title;self.index=0
        self.sync()
    def sync(self):self.previous.disabled=self.index==0;self.next.disabled=self.index==len(self.games)-1
    async def interaction_check(self,i):
        if i.user.id not in self.owners:await i.response.send_message('These controls belong to the participating players.',ephemeral=True);return False
        return True
    async def payload(self):
        result=self.games[self.index];e=self.battle._result_embed(result,*self.names,self.a,self.b)
        e.title=f'{self.title} • Game {self.index+1}/{len(self.games)}'
        image=await render_battle_summary(result,self.a,self.b,*self.names)
        file=discord.File(image,filename='battle.png');e.set_image(url='attachment://battle.png');return e,file
    async def send(self,sender):
        e,file=await self.payload();await sender.send(embed=e,file=file,view=self,allowed_mentions=discord.AllowedMentions.none())
    async def update(self,i):
        await i.response.defer();self.sync();e,file=await self.payload()
        await i.edit_original_response(embed=e,attachments=[file],view=self)
    @discord.ui.button(emoji='◀️',label='Previous Game',style=discord.ButtonStyle.secondary)
    async def previous(self,i,button):self.index=max(0,self.index-1);await self.update(i)
    @discord.ui.button(emoji='▶️',label='Next Game',style=discord.ButtonStyle.secondary)
    async def next(self,i,button):self.index=min(len(self.games)-1,self.index+1);await self.update(i)
    @discord.ui.button(label='View Full Battle Log',emoji='📜',style=discord.ButtonStyle.primary)
    async def logs(self,i,button):
        from .battle import BattleLogPager
        await i.response.send_message(embed=(p:=BattleLogPager(self.owners,self.title,self.games[self.index].log)).embed(),view=p,ephemeral=True)


class PrefTeamView(SafeView):
    def __init__(self,cog,sid,uid,teams):
        super().__init__(timeout=300);self.uid=uid
        select=discord.ui.Select(placeholder='Choose your 3v3 pod',options=[discord.SelectOption(label=f'{t.name} — Slot {t.slot}'[:100],value=str(t.slot)) for t in sorted(teams,key=lambda t:(t.name.casefold(),t.slot))])
        async def choose(i):
            await cog.store.join_prefecture(sid,i.user.id,str(i.user),int(select.values[0]))
            await i.response.edit_message(content='Pod submitted. The host starts once three players join.',view=None)
            await cog.update_board(sid)
        select.callback=choose;self.add_item(select)
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This pod selector belongs to another player.',ephemeral=True);return False
        return True


class PrefectureView(SafeView):
    def __init__(self,cog,sid,row=None):
        super().__init__(timeout=None);self.cog=cog;self.sid=sid
        for item in self.children:item.custom_id=f'v8:pref:{sid}:{item.custom_id}'
        if row:
            self.join.disabled=row['status']!='open' or len(row['state']['players'])>=3
            self.start.disabled=row['status']!='open' or len(row['state']['players'])!=3
            self.leave.disabled=row['status']!='open'
            self.result.disabled=row['status']!='complete'
    @discord.ui.button(label='Join / Choose Pod',style=discord.ButtonStyle.success,custom_id='join')
    async def join(self,i,button):
        row=await self.cog.store.session(self.sid)
        if not row or row['status']!='open':raise ValueError('That lobby is no longer open.')
        teams=[t for t in await self.cog.bot.db.list_saved_teams(i.user.id) if t.complete and t.team_format==3]
        if not teams:raise ValueError('Create a complete 3v3 saved team first.')
        await i.response.send_message('Choose a 3v3 pod. Your characters cannot duplicate another pod’s characters.',view=PrefTeamView(self.cog,self.sid,i.user.id,teams),ephemeral=True)
    @discord.ui.button(label='Start (host)',style=discord.ButtonStyle.primary,custom_id='start')
    async def start(self,i,button):
        await self.cog.store.start_prefecture(self.sid,i.user.id)
        await i.response.defer();await self.cog.update_board(self.sid);self.cog.launch(self.sid)
    @discord.ui.button(label='Leave',style=discord.ButtonStyle.secondary,custom_id='leave')
    async def leave(self,i,button):
        await self.cog.store.refresh_session(self.sid,i.user.id)
        await i.response.defer();await self.cog.update_board(self.sid)
    @discord.ui.button(label='View Result',style=discord.ButtonStyle.secondary,custom_id='result')
    async def result(self,i,button):
        row=await self.cog.store.session(self.sid)
        if not row or row['status']!='complete':raise ValueError('No completed result is available yet.')
        owners={p['user_id'] for p in row['state']['players']}
        if i.user.id not in owners:raise ValueError('That result belongs to the participating players.')
        await i.response.defer(ephemeral=True)
        await self.cog.show_result(row,i.followup,ephemeral=True)


class CommunityCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=CommunityStore(bot.db);self.jobs={};self.restore_task=None
    async def cog_load(self):self.restore_task=asyncio.create_task(self.restore())
    def cog_unload(self):
        if self.restore_task:self.restore_task.cancel()
        for task in self.jobs.values():task.cancel()
    async def restore(self):
        await self.bot.wait_until_ready()
        for row in await self.store.sessions('prefecture'):
            if row['status'] in ('open','running','broken','complete'):
                self.bot.add_view(PrefectureView(self,row['id'],row),message_id=row['message_id'])
                if row['status']=='running':self.launch(row['id'])
    def launch(self,sid):
        if sid not in self.jobs or self.jobs[sid].done():self.jobs[sid]=asyncio.create_task(self.run(sid))
    async def board(self,row):
        st=row['state'];e=discord.Embed(title=f'Prefecture • 9v9 • {row["status"].title()}',color=PURPLE)
        e.description=f'Host: <@{st["host"]}> • **{len(st["players"])}/3 pods**\nCPU: **{FORMATIONS[st["cpu_index"]]["name"]}**\nEach win grants {PREFECTURE_WIN_REWARD} coins per player. Each day unlocks {PREFECTURE_DAILY_CAP} coins of allowance; unused portions bank until Monday weekly reset. See ;questview.'
        for i,player in enumerate(st['players']):
            lines=[f'{"F" if raw["lane"]=="front" else "B"}: {raw["character"]["name"]} — {raw["character"]["title"]}' for raw in player['snapshot']]
            e.add_field(name=f'Pod {i+1} — {player["username"]}',value='\n'.join(lines),inline=False)
        if row['status']=='complete':
            result=st['result'];e.description+=f'\n**{"Victory" if result["winner_side"]=="A" else "Draw" if result["winner_side"]=="draw" else "Defeat"}** • HP {result["team_a_hp"]}–{result["team_b_hp"]}'
            e.add_field(name='Rewards',value='\n'.join(f'<@{r["user_id"]}>: {r["amount"]} coins' for r in await self.store.rewards(row['id'])),inline=False)
        if row['status']=='broken':e.description+='\nProcessing paused. ;pref <ID> resumes; ;refresh closes without rewards.'
        e.set_footer(text=f'ID {row["id"]} • ;pref {row["id"]} reopens • Shared team HP and team-wide skills.')
        return e
    async def update_board(self,sid):
        row=await self.store.session(sid)
        if not row or not row['message_id']:return
        try:
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id']);message=await channel.fetch_message(row['message_id'])
            await message.edit(embed=await self.board(row),view=PrefectureView(self,sid,row) if row['status']!='cancelled' else None)
        except discord.HTTPException:log.info('Prefecture board unavailable: %s',sid)
    def specs(self,row):
        return [spec for p in row['state']['players'] for spec in restore_specs(p['snapshot'])],cpu_specs(row['state']['cpu_index'])
    async def show_result(self,row,sender,*,ephemeral=False):
        a,b=self.specs(row);result=restore_result(row['state']['result']);battle=self.bot.get_cog('BattleCog')
        e=battle._result_embed(result,'Player Alliance','Prefecture CPU',a,b)
        e.title='Prefecture 9v9 — '+('Victory' if result.winner_side=='A' else 'Draw' if result.winner_side=='draw' else 'Defeat')
        file=discord.File(await render_battle_summary(result,a,b,'Player Alliance','Prefecture CPU'),filename='prefecture.png');e.set_image(url='attachment://prefecture.png')
        from .battle import BattleLogLauncher
        await sender.send(embed=e,file=file,view=BattleLogLauncher({p['user_id'] for p in row['state']['players']},'Prefecture Battle Log',result.log),allowed_mentions=discord.AllowedMentions.none(),**({'ephemeral':True} if ephemeral else {}))
    async def run(self,sid):
        try:
            row=await self.store.resolve_claim(sid);a,b=self.specs(row)
            seed=int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big')
            engine=PodBattleEngine(a,b,name_a='Player Alliance',name_b='Prefecture CPU',mode='prefecture',seed=seed)
            result=await asyncio.wait_for(asyncio.to_thread(engine.run),timeout=60)
            row=await self.store.finish_prefecture(sid,asdict(result));await self.update_board(sid)
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id']);await self.show_result(row,channel)
        except asyncio.CancelledError:raise
        except ValueError as exc:
            row=await self.store.session(sid)
            if row and row['status']=='running':await self.store.mark_broken(sid)
            log.info('Prefecture %s not resolved: %s',sid,exc)
        except Exception:log.exception('Prefecture %s paused',sid);await self.store.mark_broken(sid);await self.update_board(sid)

    @commands.hybrid_command(name='donate',description='Donate one unlocked card to grant clan coins to all members.')
    async def donate(self,ctx,card_id:str):
        clan=await self.store.clan(user_id=ctx.author.id);card=await self.bot.db.get_card(card_id)
        if not clan:await ctx.send('Join a clan before donating.');return
        if not card or card.owner_id!=ctx.author.id:await ctx.send('That card is not in your collection.');return
        if card.locked:await ctx.send('Unlock that card first.');return
        from ..data import CHAR_BY_ID
        payout=CHAR_BY_ID[card.character_id].rarity*10*(card.merge_level+1)
        await ctx.send(f'Permanently delete `{card.instance_id}` to give **{payout} clan coins to each current clan member**? Limit: six donations per day.',view=DonateConfirm(self,ctx.author.id,card.instance_id))
    @commands.hybrid_command(name='checkin',description='Earn 150 daily clan coins; all five check-ins grant a clan bonus.')
    async def checkin(self,ctx):
        try:bonus,count=await self.store.checkin(ctx.author.id,str(ctx.author))
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Checked in: **+150 clan coins**. Clan progress: **{count}/5** today.'+(' All five checked in: **+50 additional clan coins for every member!**' if bonus else ''))
    @commands.hybrid_command(name='clanshop',description='View and purchase clan items with clan coins.')
    async def clanshop(self,ctx):
        if not await self.store.clan(user_id=ctx.author.id):await ctx.send('Join a clan to use its shop.');return
        e=discord.Embed(title='Clan Shop',description=f'Balance: **{await self.store.wallet(ctx.author.id)} clan coins**',color=PURPLE)
        for kind,mult,desc in [('Merge Copy',150,'Universal +1 merge material for a card of matching rarity. Cannot be a survivor or battle unit.'),('Blessing',200,'Choose a new boon/bane on a matching-rarity card. Same stat = Neutral.'),('Star Pack',100,'One random card of the chosen rarity, with a normal rolled nature.')]:
            e.add_field(name=kind,value=desc+'\n'+' • '.join(f'{s}★: {s*mult}' for s in range(1,6)),inline=False)
        e.set_footer(text='All items have unique IDs, are stored in ;inv, and can be traded. Donations/check-ins are separate from the war reward cap.')
        await ctx.send(embed=e,view=ClanShopView(self,ctx.author.id))
    @commands.hybrid_command(name='clanbuy',description='Buy a mergecopy, blessing or starpack with a star rating.')
    async def clanbuy(self,ctx,item:str,stars:int):
        item=item.casefold().replace('_','')
        try:iid,cost=await self.store.buy(ctx.author.id,item,stars)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Bought {stars}★ {item} for {cost} clan coins. Item ID: `{iid}`.')
    @commands.hybrid_command(name='inventory',aliases=['inv'],description='Browse your school shards and inventory items.')
    async def inventory(self,ctx,view:str=''):
        async with self.store.tx() as c:
            preference=await self.store.one(c,'SELECT stacked FROM inventory_preferences WHERE user_id=?',(ctx.author.id,))
            stacked=bool(preference and preference['stacked'])
            if view:
                mode=view.casefold()
                if mode not in ('view','toggle','stacked','compact','regular','ids'):raise ValueError('Use ;inv [toggle|stacked|regular].')
                stacked=not stacked if mode in ('view','toggle') else mode in ('stacked','compact')
                await c.execute('INSERT INTO inventory_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET stacked=excluded.stacked',(ctx.author.id,int(stacked)))
        shards=await self.bot.db.get_shards(ctx.author.id);items=await self.store.items(ctx.author.id)
        balances=await self.store.shard_balances(ctx.author.id)
        from ..shard_balances import label
        lines=['**School shards**']+[f'{school} Shard — **{qty}x**' for school,qty in sorted(shards.items()) if qty]
        for kind,title in (('item_shard','Item shards'),('character_shard','Character shards')):
            groups=sorted([r for r in balances if r['kind']==kind],key=lambda r:label(kind,r['detail']).casefold())
            if groups:
                lines.append('\n**'+title+'**')
                lines.extend(f'{label(kind,r["detail"])} — **{r["quantity"]}x**' for r in groups)
        lines+=['\n**Items** — `;craft` • `;equip` • packs: `;open ID` • Blessings: `;bless itemID cardID` • Merge Copies: `;merge`']
        if stacked:
            from collections import Counter
            from ..items import ITEM_BY_KEY
            groups=Counter((item['kind'],item['stars'],item['detail']) for item in items)
            lines += [f'**{ITEM_BY_KEY[detail]["name"] if kind=="equipment" and detail in ITEM_BY_KEY else item_label(dict(kind=kind,stars=stars,detail=detail))} — {count}x**' for (kind,stars,detail),count in sorted(groups.items())]
        else:lines+=[f'**{item_label(item)}** • Item ID `{item["id"]}`' for item in items]
        lines+=['\n`;inv toggle` switches stacked / regular view. Use `;inv regular` to see IDs for ;equip.']
        pager=EmbedPager(text_pages(f'{ctx.author.display_name} — Inventory',lines),ctx.author.id)
        await ctx.send(embed=pager.embed(),view=pager)
    @commands.hybrid_command(name='open',description='Open an owned pack item by its unique inventory ID.')
    async def open(self,ctx,pack_id:str):
        try:cards=await self.store.open_item(ctx.author.id,str(ctx.author),pack_id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(embed=discord.Embed(title='Pack Opened',description='\n\n'.join(compact_card_line(card) for card in cards),color=PURPLE))
    @commands.hybrid_command(name='bless',description='Use an owned matching-rarity Blessing on an owned card.')
    async def bless(self,ctx,item_id:str|None=None,card_id:str|None=None):
        if card_id is None:
            from .blessing_picker import BlessingPicker
            blessings=[it for it in await self.store.items(ctx.author.id) if it['kind']=='blessing']
            if item_id and not any(it['id']==item_id for it in blessings):await ctx.send('Choose a Blessing you own.');return
            view=BlessingPicker(self,ctx.author.id,blessings,await self.bot.db.get_cards(ctx.author.id),item_id)
            await ctx.send(embed=view.embed(),view=view);return
        card=await self.bot.db.get_card(card_id);items=await self.store.items(ctx.author.id)
        item=next((it for it in items if it['id']==item_id and it['kind']=='blessing'),None)
        if not card or card.owner_id!=ctx.author.id or not item:await ctx.send('Choose an owned card and an owned Blessing.');return
        from ..data import CHAR_BY_ID
        if item['stars']!=CHAR_BY_ID[card.character_id].rarity:await ctx.send('The Blessing must match the card’s star rating.');return
        view=BlessingView(self,ctx.author.id,item_id,card);await ctx.send(view.summary(),view=view)
    @commands.command(name='createredeem',hidden=True)
    async def createredeem(self,ctx,code:str,*,prize:str):
        if ctx.author.id!=ADMIN_ID:await ctx.send('Only the designated administrator can create redeem codes.');return
        from ..community_store import parse_prize,parse_expiry
        try:
            match=re.fullmatch(r'(.+?)\s+<t:(\d+):([fF])>',prize.strip())
            if not match:raise ValueError('Use ;createredeem code prize [count] player|overall <t:UNIX:f> (or :F>).')
            expiry=f'<t:{match[2]}:{match[3]}>';parse_expiry(expiry)
            parts=match[1].rsplit(' ',1)
            if len(parts)!=2 or parts[1].casefold() not in ('player','overall'):raise ValueError('Specify player or overall before the expiry timestamp.')
            prize,scope=parts[0],parts[1].casefold();parse_prize(prize)
            code,_=await self.store.create_redeem(ctx.author.id,code,prize,scope,expiry)
            await ctx.send(f'Created **{code}**: `{prize}` • {"once per player" if scope=="player" else "once overall"} • Expires {expiry}.')
        except ValueError as exc:await ctx.send(str(exc))
    @commands.hybrid_command(name='redeem',description='Claim a prize from a redeem code.')
    async def redeem(self,ctx,code:str):
        try:prize,iid=await self.store.redeem(ctx.author.id,code)
        except ValueError as exc:await ctx.send(str(exc));return
        if isinstance(iid,list):
            await ctx.send(f'Redeemed: **{len(iid)} × {item_label(prize)}**. Items are in ;inv.');return
        await ctx.send('Redeemed: '+(f'**{prize["amount"]} {"clan coins" if prize["kind"]=="clancoins" else "coins"}**.' if not iid else f'**{item_label(prize)}** • ID `{iid}`. Use ;inv.'))
    @commands.hybrid_command(name='prefecture',aliases=['pref'],description='Host or reopen a three-player cooperative 9v9 battle.')
    async def prefecture(self,ctx,lobby_id:str|None=None):
        if not ctx.guild:await ctx.send('Open Prefecture lobbies in a server channel.');return
        try:
            if lobby_id:
                row=await self.store.session(lobby_id)
                if not row or row['kind']!='prefecture' or row['channel_id']!=ctx.channel.id:raise ValueError('No Prefecture lobby with that ID exists in this channel.')
            else:row=await self.store.prefecture(ctx.author.id,str(ctx.author),ctx.channel.id,random.randrange(len(FORMATIONS)))
        except ValueError as exc:await ctx.send(str(exc));return
        msg=await ctx.send(embed=await self.board(row),view=PrefectureView(self,row['id'],row) if row['status']!='cancelled' else None,allowed_mentions=discord.AllowedMentions.none())
        await self.store.set_message(row['id'],msg.id)
        if row['status'] in ('running','broken'):self.launch(row['id'])


async def setup(bot):await bot.add_cog(CommunityCog(bot))
