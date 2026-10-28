from __future__ import annotations

import asyncio
import logging
import discord
from discord.ext import commands

from ..community_store import CommunityStore, CLAN_ICONS
from ..data import CHAR_BY_ID
from ..prefecture import play_series
from ..tournament import restore_specs
from ..ui import SafeView, EmbedPager, text_pages, PURPLE

log=logging.getLogger(__name__)


class ClanNameModal(discord.ui.Modal,title='Create your clan'):
    clan_name=discord.ui.TextInput(label='Clan name',min_length=2,max_length=40)
    def __init__(self,parent):super().__init__();self.parent=parent
    async def on_submit(self,i):
        try:clan=await self.parent.cog.store.create_clan(i.user.id,str(i.user),str(self.clan_name),self.parent.icon)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        self.parent.stop()
        await i.response.edit_message(content=f'{clan["icon"]} **{discord.utils.escape_markdown(clan["name"])}** created! Clan ID: `{clan["id"]}`. It is now on `;clans`.',view=None)


class CreateClanView(SafeView):
    def __init__(self,cog,uid):
        super().__init__(timeout=180);self.cog=cog;self.uid=uid;self.icon=CLAN_ICONS[0]
        select=discord.ui.Select(placeholder='Choose a standard Discord emoji',options=[discord.SelectOption(label=f'Icon {i+1}',emoji=e,value=e) for i,e in enumerate(CLAN_ICONS)])
        async def choose(i):self.icon=select.values[0];await i.response.edit_message(content=f'Clan icon: {self.icon}. Click **Name & Create**.',view=self)
        select.callback=choose;self.add_item(select)
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This clan builder belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Name & Create',style=discord.ButtonStyle.success,row=1)
    async def name_clan(self,i,button):await i.response.send_modal(ClanNameModal(self))


class ClanEditNameModal(discord.ui.Modal,title='Rename your clan'):
    def __init__(self,parent):
        super().__init__();self.parent=parent
        self.clan_name=discord.ui.TextInput(label='Clan name',default=parent.name,min_length=2,max_length=40)
        self.add_item(self.clan_name)
    async def on_submit(self,i):
        self.parent.name=str(self.clan_name).strip()
        await i.response.edit_message(content=self.parent.summary(),view=self.parent)


class ClanEditView(SafeView):
    def __init__(self,cog,uid,clan):
        super().__init__(timeout=180);self.cog=cog;self.uid=uid;self.name=clan['name'];self.icon=clan['icon']
        select=discord.ui.Select(placeholder='Choose your clan emoji',options=[discord.SelectOption(label=f'Icon {n+1}',emoji=icon,value=icon,default=icon==self.icon) for n,icon in enumerate(CLAN_ICONS)],row=0)
        async def choose(i):
            self.icon=select.values[0]
            for option in select.options:option.default=option.value==self.icon
            await i.response.edit_message(content=self.summary(),view=self)
        select.callback=choose;self.add_item(select)
    def summary(self):return f'Preview: {self.icon} **{discord.utils.escape_markdown(self.name)}**\nChoose an emoji or rename the clan, then Save Changes.'
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('This clan editor belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Rename',style=discord.ButtonStyle.secondary,row=1)
    async def rename(self,i,button):await i.response.send_modal(ClanEditNameModal(self))
    @discord.ui.button(label='Save Changes',style=discord.ButtonStyle.success,row=1)
    async def save(self,i,button):
        try:clan=await self.cog.store.edit_clan(i.user.id,self.name,self.icon)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        self.stop();await i.response.edit_message(content=f'Clan updated: {clan["icon"]} **{discord.utils.escape_markdown(clan["name"])}** • `{clan["id"]}`',view=None)
    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.danger,row=1)
    async def cancel(self,i,button):self.stop();await i.response.edit_message(content='Clan edit cancelled.',view=None)


class ApplicationView(SafeView):
    def __init__(self,cog,aid):
        super().__init__(timeout=None);self.cog=cog;self.aid=aid
        for item in self.children:item.custom_id=f'v8:application:{aid}:{item.custom_id}'
    async def decide(self,i,accept):
        app,clan=await self.cog.store.decide(i.user.id,self.aid,accept)
        await i.response.edit_message(content=f'Application {"accepted" if accept else "declined"} by {i.user.display_name}.',view=None)
        await self.cog.dm(app['user_id'],f'Your application to **{discord.utils.escape_markdown(clan["name"])}** was {"accepted" if accept else "declined"}.')
        for uid in {clan['leader_id'],clan['co_id']}-{None,i.user.id}:
            await self.cog.dm(uid,f'{i.user.display_name} {"accepted" if accept else "declined"} **{discord.utils.escape_markdown(app["username"])}**’s application to {discord.utils.escape_markdown(clan["name"])}.')
    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success,custom_id='accept')
    async def accept(self,i,button):await self.decide(i,True)
    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger,custom_id='decline')
    async def decline(self,i,button):await self.decide(i,False)


class WarBanView(SafeView):
    def __init__(self,cog,sid,actor):
        super().__init__(timeout=300);self.cog=cog;self.sid=sid;self.actor=actor;self.page=0
        self.units=sorted(CHAR_BY_ID.values(),key=lambda c:(c.name,c.title,c.id));self.rebuild()
    async def interaction_check(self,i):
        if i.user.id!=self.actor:await i.response.send_message('This ban menu belongs to another leader.',ephemeral=True);return False
        return True
    def rebuild(self):
        self.clear_items();chunk=self.units[self.page*20:(self.page+1)*20]
        select=discord.ui.Select(placeholder='Select one unit to lock your ban',options=[discord.SelectOption(label=f'{c.name} — {c.title}'[:100],value=c.id,description=f'{c.rarity}★ • {c.school} • {c.id}'[:100]) for c in chunk],row=0)
        async def choose(i):
            selected=select.values[0]
            await self.cog.store.ban_war_unit(self.sid,i.user.id,selected)
            unit=CHAR_BY_ID[selected];self.stop()
            await i.response.edit_message(content=f'Ban locked: **{unit.name} — {unit.title}**. Both bans are revealed together once both leaders finish.',view=None)
            await self.cog.update_board(self.sid)
        select.callback=choose;self.add_item(select)
        pages=(len(self.units)+19)//20
        for delta,emoji in ((-1,'◀️'),(1,'▶️')):
            button=discord.ui.Button(emoji=emoji,disabled=(self.page==0 if delta==-1 else self.page==pages-1),row=1)
            async def nav(i,d=delta):
                self.page+=d;self.rebuild()
                await i.response.edit_message(content=f'Choose one exact unit variant. Selection immediately locks your ban for both sides. Page {self.page+1}/{pages}.',view=self)
            button.callback=nav;self.add_item(button)


class WarSendView(SafeView):
    def __init__(self,cog,row,side,actor):
        super().__init__(timeout=180);self.cog=cog;self.actor=actor
        st=row['state'];members=[r for r in st['rosters'][side] if r['user_id'] not in st['eliminated'][side]]
        select=discord.ui.Select(placeholder='Send an eligible clan member',options=[discord.SelectOption(label=r['username'][:100],value=str(r['user_id'])) for r in sorted(members,key=lambda r:r['username'].casefold())])
        async def choose(i):
            selected=await cog.store.nominate(row['id'],i.user.id,int(select.values[0]),st['round'])
            await i.response.edit_message(content='Player sent. Opening sends remain hidden until both sides commit.' if st['round']==1 else 'Player sent and revealed on the war board.',view=None)
            await cog.update_board(row['id'])
            if selected['state']['stage']=='teams':await cog.notify_players(selected)
        select.callback=choose;self.add_item(select)
    async def interaction_check(self,i):
        if i.user.id!=self.actor:await i.response.send_message('This send selector belongs to another leader.',ephemeral=True);return False
        return True


class WarTeamView(SafeView):
    def __init__(self,cog,sid,uid,round_no,teams):
        super().__init__(timeout=300);self.uid=uid
        select=discord.ui.Select(placeholder='Choose your team for this series',options=[discord.SelectOption(label=f'{t.name} — Slot {t.slot}'[:100],value=str(t.slot)) for t in sorted(teams,key=lambda t:(t.name.casefold(),t.slot))])
        async def choose(i):
            row=await cog.store.select_war_team(sid,i.user.id,int(select.values[0]),round_no)
            await i.response.edit_message(content='Team locked for this best-of-three. You can choose a different team next round.',view=None)
            await cog.update_board(sid)
            if all(row['state']['teams']):cog.launch(sid)
        select.callback=choose;self.add_item(select)
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Only the selected player can choose this team.',ephemeral=True);return False
        return True


class ForfeitConfirm(SafeView):
    def __init__(self,cog,sid,uid):super().__init__(timeout=90);self.cog=cog;self.sid=sid;self.uid=uid
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This confirmation belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Confirm clan defeat',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        await self.cog.store.forfeit(self.sid,i.user.id)
        task=self.cog.jobs.get(self.sid)
        if task:task.cancel()
        await i.response.edit_message(content='Your clan forfeited. The loss and opponent’s win are recorded.',view=None)
        await self.cog.update_board(self.sid)


class WarView(SafeView):
    def __init__(self,cog,sid,row=None):
        super().__init__(timeout=None);self.cog=cog;self.sid=sid
        for item in self.children:item.custom_id=f'v8:war:{sid}:{item.custom_id}'
        if row:
            self.accept.disabled=self.decline.disabled=row['status']!='challenge'
            self.ban.disabled=not(row['status']=='active' and row['state']['stage']=='bans')
            self.send.disabled=not(row['status']=='active' and row['state']['stage'] in ('blind','winner_send','response_send'))
            self.team.disabled=not(row['status']=='active' and row['state']['stage']=='teams')
            self.forfeit.disabled=row['status'] not in ('active','running','broken')
    @discord.ui.button(label='Accept War',style=discord.ButtonStyle.success,custom_id='accept',row=0)
    async def accept(self,i,button):
        await self.cog.store.respond_war(self.sid,i.user.id,True)
        await i.response.defer();await self.cog.update_board(self.sid)
    @discord.ui.button(label='Decline War',style=discord.ButtonStyle.danger,custom_id='decline',row=0)
    async def decline(self,i,button):
        await self.cog.store.respond_war(self.sid,i.user.id,False)
        await i.response.defer();await self.cog.update_board(self.sid)
    @discord.ui.button(label='Ban Unit (leader)',style=discord.ButtonStyle.primary,custom_id='ban',row=0)
    async def ban(self,i,button):
        row=await self.cog.store.session(self.sid)
        clan=await self.cog.store.clan(user_id=i.user.id)
        if not row or row['status']!='active' or row['state']['stage']!='bans':raise ValueError('That war is not waiting for bans.')
        if not clan or clan['leader_id']!=i.user.id or clan['id'] not in row['state']['clans']:raise ValueError('Only the two participating clan leaders can ban units.')
        if row['state']['bans'][row['state']['clans'].index(clan['id'])] is not None:raise ValueError('Your clan’s ban is already locked.')
        view=WarBanView(self.cog,self.sid,i.user.id)
        await i.response.send_message(f'Choose one exact unit variant. Selection immediately locks your ban for both sides. Your choice stays hidden until both leaders finish. Page 1/{(len(view.units)+19)//20}.',view=view,ephemeral=True)
    @discord.ui.button(label='Send Player',style=discord.ButtonStyle.primary,custom_id='send',row=1)
    async def send(self,i,button):
        row=await self.cog.store.session(self.sid)
        if not row or row['status']!='active':raise ValueError('That war is not waiting for sends.')
        clan=await self.cog.store.clan(user_id=i.user.id)
        if not clan or i.user.id not in (clan['leader_id'],clan['co_id']) or clan['id'] not in row['state']['clans']:raise ValueError('Only a participating clan leader or co-leader can send players.')
        side=row['state']['clans'].index(clan['id']);st=row['state']
        if st['sends'][side] is not None:raise ValueError('Your clan already sent its player.')
        if st['stage']=='winner_send' and side!=st['initiative']:raise ValueError('The previous winning clan must send first.')
        if st['stage'] not in ('blind','winner_send','response_send'):raise ValueError('Sends are closed this round.')
        await i.response.send_message('Choose a player. This selector is private.',view=WarSendView(self.cog,row,side,i.user.id),ephemeral=True)
    @discord.ui.button(label='Choose My Team',style=discord.ButtonStyle.primary,custom_id='team',row=1)
    async def team(self,i,button):
        row=await self.cog.store.session(self.sid)
        view=await self.cog.team_view(row,i.user.id)
        await i.response.send_message('Select a complete saved team privately.',view=view,ephemeral=True)
    @discord.ui.button(label='Forfeit',style=discord.ButtonStyle.danger,custom_id='forfeit',row=1)
    async def forfeit(self,i,button):
        row=await self.cog.store.session(self.sid);clan=await self.cog.store.clan(user_id=i.user.id)
        if not row or row['status'] not in ('active','running','broken') or not clan or clan['id'] not in row['state']['clans'] or i.user.id not in (clan['leader_id'],clan['co_id']):raise ValueError('Only a participating clan leader or co-leader can forfeit an active war.')
        await i.response.send_message('This records a clan loss and an opponent win. Confirm?',view=ForfeitConfirm(self.cog,self.sid,i.user.id),ephemeral=True)


class ClanCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=CommunityStore(bot.db);self.jobs={};self.restore_task=None
    async def cog_load(self):self.restore_task=asyncio.create_task(self.restore())
    def cog_unload(self):
        if self.restore_task:self.restore_task.cancel()
        for task in self.jobs.values():task.cancel()
    async def dm(self,uid,content,**kwargs):
        try:
            user=self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send(content,allowed_mentions=discord.AllowedMentions.none(),**kwargs);return True
        except discord.HTTPException:return False
    async def restore(self):
        await self.bot.wait_until_ready()
        async with self.bot.db.connect() as conn:
            apps=await self.store.rows(conn,"SELECT id FROM clan_applications WHERE status='pending'")
        for app in apps:self.bot.add_view(ApplicationView(self,app['id']))
        for row in await self.store.sessions('war'):
            if row['status'] in ('challenge','active','running','broken'):
                self.bot.add_view(WarView(self,row['id'],row),message_id=row['message_id'])
                if row['status']=='running':self.launch(row['id'])
    async def team_view(self,row,uid):
        if not row or row['status']!='active' or row['state']['stage']!='teams' or uid not in row['state']['sends']:raise ValueError('You are not currently waiting to choose a war team.')
        if row['state']['teams'][row['state']['sends'].index(uid)] is not None:raise ValueError('Your team is already locked.')
        fmt=row['state']['format'];teams=[t for t in await self.bot.db.list_saved_teams(uid) if t.complete and t.team_format==fmt]
        banned=set(row['state'].get('bans',[]))-{None}
        if banned:
            cards={c.instance_id:c for c in await self.bot.db.get_cards(uid)}
            teams=[t for t in teams if all(iid in cards and cards[iid].character_id not in banned for _,iid,_ in t.entries)]
        if not teams:raise ValueError(f'Create a complete {fmt}v{fmt} team without banned units with ;teamcreate or ;teamedit, then return to Choose My Team.')
        return WarTeamView(self,row['id'],uid,row['state']['round'],teams)
    async def notify_players(self,row):
        for uid in row['state']['sends']:
            try:view=await self.team_view(row,uid)
            except ValueError:
                await self.dm(uid,f'You were sent in war `{row["id"]}`. Create a complete {row["state"]["format"]}v{row["state"]["format"]} team without banned units, then use **Choose My Team** on the war board.');continue
            await self.dm(uid,f'Your clan sent you for round {row["state"]["round"]}. Select your team below. If this menu expires, use **Choose My Team** on the war board.',view=view)
    async def board(self,row):
        st=row['state'];clans=[await self.store.clan(clan_id=cid) for cid in st['clans']]
        names=[discord.utils.escape_markdown(cl['name']) if cl else cid for cl,cid in zip(clans,st['clans'])]
        e=discord.Embed(title=f'Clan War • {st["format"]}v{st["format"]}',color=PURPLE)
        e.description=f'**{names[0]} vs {names[1]}** • {row["status"].title()}\n'
        if row['status']=='challenge':e.description+='Do you accept or decline this offer? Both clans need five members. No war cooldown.'
        elif row['status']=='complete':
            e.description+=f'🏆 **{names[st["winner"]]} wins!**'+(' Opponent forfeited.' if 'forfeit' in st else ' Five opposing players knocked out.')
            rewards=await self.store.rewards(row['id'])
            e.add_field(name='War rewards • daily caps applied',value='\n'.join(f'<@{r["user_id"]}>: **{st.get("gold_rewards",{}).get(str(r["user_id"]),0)} coins + {r["amount"]} clan coins**' for r in rewards) or 'No rewards.',inline=False)
        elif row['status'] in ('cancelled','declined'):e.description+='Closed without clan wins, losses or rewards.'
        else:
            e.description+=f'Round **{st["round"]}** • '
            if st['stage']=='bans':e.description+='Both clan leaders lock one blind unit ban before sends.'
            elif st['stage']=='blind':e.description+='Both clans send privately.'
            elif st['stage']=='winner_send':e.description+=f'**{names[st["initiative"]]}** sends first, publicly.'
            elif st['stage']=='response_send':e.description+='The other clan now responds with its send.'
            elif st['stage']=='teams':e.description+='Sent players choose a saved team privately.'
            elif row['status']=='broken':e.description+='Processing paused. Reopen to resume, or ;refresh to close without a clan result.'
            else:e.description+='Best-of-three in progress.'
        bans=st.get('bans',[])
        if bans:
            revealed=all(bans)
            ban_lines=[f'{names[side]}: '+(f'**{CHAR_BY_ID[cid].name} — {CHAR_BY_ID[cid].title}**' if revealed else '✅ hidden ban locked' if cid else 'Awaiting leader’s ban') for side,cid in enumerate(bans)]
            e.add_field(name='Banned units • apply to both sides',value='\n'.join(ban_lines),inline=False)
        if st['rosters']:
            for side,members in enumerate(st['rosters']):
                lines=[]
                for member in members:
                    uid=member['user_id'];wins=st['wins'].get(str(uid),0)
                    line=f'<@{uid}> **{wins} {"win" if wins==1 else "wins"}**'
                    if uid in st['eliminated'][side]:
                        last=next(r for r in reversed(st['series']) if r['players'][side]==uid and r['winner']!=side)
                        winner=last['players'][1-side];score=last['score']
                        line=f'~~{line}~~ — {score[1-side]}–{score[side]}’d by *[{names[1-side]}] <@{winner}>*'
                    lines.append(line)
                e.add_field(name=f'{names[side]} • {len(st["eliminated"][side])}/5 knocked out',value='\n'.join(lines),inline=False)
            sends=[]
            for side,uid in enumerate(st['sends']):
                visible=st['round']>1 or all(x is not None for x in st['sends'])
                sends.append(f'{names[side]}: '+(f'<@{uid}>' if uid and visible else '✅ hidden send locked' if uid else 'Awaiting send')+(' • team locked' if st['teams'][side] else ''))
            if row['status'] not in ('complete','cancelled'):e.add_field(name='Current sends',value='\n'.join(sends),inline=False)
        e.set_footer(text=f'War ID {row["id"]} • ;clanwarview {row["id"]} reopens controls. Individual combat statistics are not added to analytics.')
        return e
    async def update_board(self,sid):
        row=await self.store.session(sid)
        if not row or not row['message_id']:return
        try:
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
            message=await channel.fetch_message(row['message_id'])
            view=WarView(self,sid,row) if row['status'] in ('challenge','active','running','broken') else None
            await message.edit(embed=await self.board(row),view=view,allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:log.info('War board unavailable: %s; ;clanwarview can reopen it.',sid)
    def launch(self,sid):
        if sid in self.jobs and not self.jobs[sid].done():return
        task=asyncio.create_task(self.run(sid));self.jobs[sid]=task
        def finished(done):
            if self.jobs.get(sid) is done:self.jobs.pop(sid,None)
            if not done.cancelled():asyncio.create_task(self.resume_ready(sid))
        task.add_done_callback(finished)
    async def resume_ready(self,sid):
        row=await self.store.session(sid)
        if row and row['status']=='active' and all(row['state']['teams']):self.launch(sid)
    async def run(self,sid):
        try:
            row=await self.store.resolve_claim(sid);st=row['state'];a,b=[restore_specs(t) for t in st['teams']]
            names=[next(m['username'] for m in st['rosters'][side] if m['user_id']==st['sends'][side]) for side in (0,1)]
            winner,score,games=await asyncio.wait_for(asyncio.to_thread(play_series,sid,st['round'],a,b,*st['sends'],*names),timeout=60)
            await self.store.finish_series(sid,st['round'],winner,score)
            await self.update_board(sid)
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
            from .community import SeriesResultsView
            battle=self.bot.get_cog('BattleCog')
            view=SeriesResultsView(battle,games,a,b,*names,{m['user_id'] for roster in st['rosters'] for m in roster},f'Clan War Round {st["round"]} • {score[0]}–{score[1]}')
            await view.send(channel)
        except asyncio.CancelledError:raise
        except ValueError as exc:
            row=await self.store.session(sid)
            if row and row['status']=='running':await self.store.mark_broken(sid)
            log.info('War %s not resolved: %s',sid,exc)
        except Exception:
            log.exception('War %s paused',sid);await self.store.mark_broken(sid);await self.update_board(sid)

    @commands.hybrid_command(name='clanedit',description='Clan leaders can rename their clan and change its emoji.')
    async def clanedit(self,ctx):
        clan=await self.store.clan(user_id=ctx.author.id)
        if not clan or clan['leader_id']!=ctx.author.id:
            await ctx.send('Only your clan’s leader can edit its name and emoji.');return
        view=ClanEditView(self,ctx.author.id,clan)
        await ctx.send(view.summary(),view=view,allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name='createclan',description='Choose an icon and name to create a public clan.')
    async def createclan(self,ctx):
        if await self.store.clan(user_id=ctx.author.id):await ctx.send('You already belong to a clan.');return
        await ctx.send('Choose your clan’s emoji, then enter its name.',view=CreateClanView(self,ctx.author.id))
    @commands.hybrid_command(name='clans',description='Browse all public clans.')
    async def clans(self,ctx):
        clans=await self.store.all_clans();lines=[]
        for c in clans:
            total=c['wins']+c['losses'];rate=f' • {100*c["wins"]/total:.1f}% war wins ({c["wins"]}–{c["losses"]})' if total else ''
            lines.append(f'{c["icon"]} **{discord.utils.escape_markdown(c["name"])}** • {c["occupancy"]}/5 • `{c["id"]}`{rate}')
        pager=EmbedPager(text_pages('Public Clan Board',lines))
        await ctx.send(embed=pager.embed(),view=pager)
    @commands.hybrid_command(name='clanview',aliases=['clan'],description='View a clan profile by ID, or your own clan.')
    async def clanview(self,ctx,clan_id:str|None=None):
        clan=await self.store.clan(user_id=ctx.author.id,clan_id=clan_id)
        if not clan:await ctx.send('No clan found. Use ;clans.');return
        members=await self.store.members(clan['id'])
        total=clan['wins']+clan['losses'];e=discord.Embed(title=f'{clan["icon"]} {clan["name"]}',color=PURPLE)
        e.description=f'ID: `{clan["id"]}` • {len(members)}/5\nLeader: <@{clan["leader_id"]}>\nCo-leader: '+(f'<@{clan["co_id"]}>' if clan['co_id'] else 'None')
        lines=[]
        for member in members:
            uid=member['user_id'];role='Leader' if uid==clan['leader_id'] else 'Co-leader' if uid==clan['co_id'] else 'Member'
            lines.append(f'<@{uid}> • **{discord.utils.escape_markdown(member["username"])}** • {role}')
        e.add_field(name='Members',value='\n'.join(lines) or 'No members.',inline=False)
        e.add_field(name='Clan wars',value=f'**{clan["wins"]} wins / {clan["losses"]} losses**'+(f' • {100*clan["wins"]/total:.1f}% win rate' if total else ' • No wars played.'),inline=False)
        await ctx.send(embed=e,allowed_mentions=discord.AllowedMentions.none())
    @commands.hybrid_command(name='clanapply',description='Apply to a clan that has an open place.')
    async def clanapply(self,ctx,clan_id:str):
        try:aid,clan=await self.store.apply(ctx.author.id,str(ctx.author),clan_id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Application sent to **{discord.utils.escape_markdown(clan["name"])}**.')
        for uid in {clan['leader_id'],clan['co_id']}-{None}:
            await self.dm(uid,f'**{discord.utils.escape_markdown(str(ctx.author))}** applied to **{discord.utils.escape_markdown(clan["name"])}**. Applicant ID: {ctx.author.id}',view=ApplicationView(self,aid))
    @commands.hybrid_command(name='clanapplications',description='Review pending applications if DMs are unavailable.')
    async def clanapplications(self,ctx):
        clan=await self.store.clan(user_id=ctx.author.id)
        if not clan or ctx.author.id not in (clan['leader_id'],clan['co_id']):await ctx.send('Only clan leaders and co-leaders can review applications.');return
        async with self.bot.db.connect() as c:apps=await self.store.rows(c,"SELECT * FROM clan_applications WHERE clan_id=? AND status='pending'",(clan['id'],))
        if not apps:await ctx.send('No pending applications.');return
        for app in apps:await ctx.send(f'Applicant: **{discord.utils.escape_markdown(app["username"])}** • {app["user_id"]}',view=ApplicationView(self,app['id']),allowed_mentions=discord.AllowedMentions.none())
    @commands.hybrid_command(name='clanco',description='Appoint or remove your clan’s single co-leader.')
    async def clanco(self,ctx,user:discord.User):
        try:co=await self.store.appoint(ctx.author.id,user.id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Co-leader {"appointed: "+user.display_name if co else "removed"}.')
    @commands.hybrid_command(name='clanleave',description='Leave your clan while retaining your currency and items.')
    async def clanleave(self,ctx):
        try:await self.store.remove_member(ctx.author.id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send('You left the clan. Your clan coins and inventory are retained.')
    @commands.hybrid_command(name='clankick',description='Remove a member of your clan.')
    async def clankick(self,ctx,user:discord.User):
        try:await self.store.remove_member(ctx.author.id,user.id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'{user.display_name} was removed. Their clan coins and items are retained.')
    @commands.hybrid_command(name='clanlead',description='Transfer clan leadership to another member.')
    async def clanlead(self,ctx,user:discord.User):
        try:await self.store.transfer_leader(ctx.author.id,user.id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Leadership transferred to {user.display_name}.')
    @commands.hybrid_command(name='memberview',description='Show whether each clan member has checked in today.')
    async def memberview(self,ctx):
        if getattr(ctx,'interaction',None):await ctx.defer()
        clan=await self.store.clan(user_id=ctx.author.id)
        if not clan:await ctx.send('Join a clan to view today’s member check-ins.');return
        from ..economy import local_date_key
        today=local_date_key()
        lines=[]
        for member in await self.store.members(clan['id']):
            user=self.bot.get_user(member['user_id'])
            if not user:
                try:user=await self.bot.fetch_user(member['user_id'])
                except discord.HTTPException:pass
            lines.append(f'**{discord.utils.escape_markdown(str(user) if user else member["username"])}** • <@{member["user_id"]}> • {"✅" if member["last_checkin"]==today else "❌"}')
        await ctx.send(embed=discord.Embed(title=f'{clan["name"]} — Members',description='\n'.join(lines),color=PURPLE),allowed_mentions=discord.AllowedMentions.none())
    @commands.hybrid_command(name='clanwar',description='Challenge another full clan to a 3v3 or 6v6 war.')
    async def clanwar(self,ctx,clan_id:str,format:str):
        fmt={'3':3,'3v3':3,'6':6,'6v6':6}.get(format.casefold())
        if not ctx.guild:await ctx.send('Start clan wars from a server channel.');return
        try:row=await self.store.challenge(ctx.author.id,clan_id,fmt,ctx.channel.id)
        except ValueError as exc:await ctx.send(str(exc));return
        other=await self.store.clan(clan_id=clan_id);officers={other['leader_id'],other['co_id']}-{None}
        mentions=' / '.join(f'<@{uid}>' for uid in sorted(officers))
        msg=await ctx.send(f'{ctx.author.display_name} is challenging {mentions}’s **{discord.utils.escape_markdown(other["name"])}** to a **{fmt}v{fmt}** war! Do you accept or decline?',embed=await self.board(row),view=WarView(self,row['id'],row),allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=uid) for uid in officers],roles=False,everyone=False))
        await self.store.set_message(row['id'],msg.id)
    @commands.hybrid_command(name='clanwarview',description='Reopen your clan war controls or a public scoreboard.')
    async def clanwarview(self,ctx,war_id:str|None=None):
        if not war_id:
            clan=await self.store.clan(user_id=ctx.author.id)
            rows=[r for r in await self.store.sessions('war') if clan and clan['id'] in r['state']['clans'] and r['status'] in ('challenge','active','running','broken')]
            row=rows[-1] if rows else None
        else:row=await self.store.session(war_id)
        if not row or row['kind']!='war':await ctx.send('No matching clan war.');return
        if ctx.channel.id!=row['channel_id']:await ctx.send('Reopen this war from its original channel.');return
        view=WarView(self,row['id'],row) if row['status'] in ('challenge','active','running','broken') else None
        msg=await ctx.send(embed=await self.board(row),view=view,allowed_mentions=discord.AllowedMentions.none())
        await self.store.set_message(row['id'],msg.id)
        if row['status'] in ('running','broken') or (row['status']=='active' and all(row['state']['teams'])):self.launch(row['id'])


async def setup(bot):await bot.add_cog(ClanCog(bot))
