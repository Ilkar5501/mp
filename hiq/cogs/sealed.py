"""Private asynchronous Sealed Draft and automatic 6v6 tournament."""
from __future__ import annotations
import asyncio,io,logging,re
from dataclasses import asdict
import aiohttp,discord
from discord.ext import commands
from PIL import Image,ImageDraw
from ..data import CHAR_BY_ID
from ..prefecture import play_series
from ..sealed import POSITIONS
from ..sealed_store import SealedStore,LIVE
from ..team_render import _fetch_square,_font
from ..tournament import restore_specs,restore_result
from ..ui import SafeView,PURPLE

log=logging.getLogger(__name__)


async def pack_gallery(cids):
    chars=[CHAR_BY_ID[cid] for cid in cids]
    async with aiohttp.ClientSession() as session:
        icons=await asyncio.gather(*[_fetch_square(session,c.character_icon,150) for c in chars])
    img=Image.new('RGB',(1000,540),(20,24,40));draw=ImageDraw.Draw(img)
    draw.text((24,16),'SEALED PACK — CHOOSE ONE',font=_font(24,True),fill='white')
    for index,(char,icon) in enumerate(zip(chars,icons)):
        x=20+(index%4)*245;y=65+(index//4)*235
        if icon:img.paste(icon,(x,y))
        else:draw.rectangle((x,y,x+150,y+150),fill=(48,53,76));draw.text((x+10,y+60),char.name[:16],font=_font(16),fill='white')
        draw.text((x,y+155),f'{index+1}. {char.name}',font=_font(16,True),fill='white')
        draw.text((x,y+180),char.title[:29],font=_font(13),fill=(205,207,230))
    out=io.BytesIO();img.save(out,format='PNG');out.seek(0);return out


class PackView(SafeView):
    def __init__(self,cog,row,uid):
        super().__init__(timeout=600);self.cog=cog;self.sid=row['id'];self.uid=uid;st=row['state'];key=str(uid)
        queue=st['queues'][key]
        if queue:
            pack_id=queue[0];cards=st['packs'][pack_id];count=len(cards)
            picker=discord.ui.Select(placeholder='Choose one character to keep',options=[discord.SelectOption(label=cid[:100],value=cid) for cid in cards])
            async def choose(i):
                await i.response.defer()
                fresh,recipient,wake=await cog.store.choose(self.sid,uid,pack_id,count,picker.values[0])
                await cog.show_pack(i,self.sid,uid,edit=True)
                await cog.update_board(self.sid)
                if fresh['status']=='building':await cog.channel_notice(fresh,'Draft complete. Everyone drafted 8 characters. Use **Edit Team** to build your private 6v6 team.')
                elif wake:await cog.pack_notice(fresh,recipient)
            picker.callback=choose;self.add_item(picker)
        refresh=discord.ui.Button(label='Refresh / Open Next Pack',style=discord.ButtonStyle.secondary,row=1)
        async def reopen(i):await i.response.defer();await cog.show_pack(i,self.sid,uid,edit=True)
        refresh.callback=reopen;self.add_item(refresh)

    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This pack belongs to another player.',ephemeral=True);return False
        await self.cog.store.read_active(self.sid,i.user.id)
        return True


class SealedTeamView(SafeView):
    def __init__(self,cog,row,uid,position=0):
        super().__init__(timeout=600);self.cog=cog;self.sid=row['id'];self.uid=uid;st=row['state'];key=str(uid)
        version=st['team_versions'].get(key,0);team=st['draft_teams'].get(key,[None]*6)
        positions=discord.ui.Select(placeholder='Choose formation position',options=[discord.SelectOption(label=p,value=str(i),default=i==position) for i,p in enumerate(POSITIONS)],row=0)
        async def choose_position(i):
            fresh=await cog.store.read_active(self.sid,uid,'building')
            await i.response.edit_message(embed=self.embed(fresh,uid),view=SealedTeamView(cog,fresh,uid,int(positions.values[0])))
        positions.callback=choose_position;self.add_item(positions)
        units=discord.ui.Select(placeholder=f'Place a character: {POSITIONS[position]}',options=[discord.SelectOption(label=cid[:100],value=cid,default=cid==team[position]) for cid in st['picks'][key]],row=1)
        async def place(i):
            fresh=await cog.store.place(self.sid,uid,position,units.values[0],version)
            await i.response.edit_message(embed=self.embed(fresh,uid),view=SealedTeamView(cog,fresh,uid,position))
            await cog.update_board(self.sid)
        units.callback=place;self.add_item(units)
        submit=discord.ui.Button(label='Submit Team',style=discord.ButtonStyle.success,disabled=not all(team),row=2)
        async def commit(i):
            await i.response.defer()
            fresh=await cog.store.submit(self.sid,uid,version)
            await i.edit_original_response(content='Team submitted. '+('All teams locked; the tournament starts now.' if fresh['status']=='running' else 'You may edit it until everyone submits.'),embed=None,view=None)
            await cog.update_board(self.sid)
            if fresh['status']=='running':cog.launch(self.sid)
        submit.callback=commit;self.add_item(submit)

    @staticmethod
    def embed(row,uid):
        team=row['state']['draft_teams'].get(str(uid),[None]*6)
        e=discord.Embed(title='Sealed • Private 6v6 Team',description='Choose a position, then one of your eight drafted characters. One variant per player name; replacing a variant clears that player’s previous slot.\nTeams remain private. Editing a submitted team removes its submitted status until you submit again.',color=PURPLE)
        for label,slots in (('FRONT ROW',range(3)),('BACK ROW',range(3,6))):
            e.add_field(name=label,value='\n'.join(f'**{POSITIONS[i]}:** {team[i] or "Empty"}' for i in slots),inline=False)
        return e

    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This team editor belongs to another player.',ephemeral=True);return False
        await self.cog.store.read_active(self.sid,i.user.id,'building');return True


class SealedResultsView(SafeView):
    def __init__(self,cog,row):
        super().__init__(timeout=600);self.owners=set(row['state']['order'])
        matches=[m for m in row['state']['matches'] if m['games']]
        picker=discord.ui.Select(placeholder='View a match and battle logs',options=[discord.SelectOption(label=f'Round {m["round"]} • Match {m["index"]+1} • {m["score"][0]}–{m["score"][1]}',value=str(n)) for n,m in enumerate(matches)])
        async def choose(i):
            await i.response.defer(ephemeral=True);m=matches[int(picker.values[0])];st=row['state'];a,b=m['players']
            from .community import SeriesResultsView
            view=SeriesResultsView(cog.bot.get_cog('BattleCog'),[restore_result(g) for g in m['games']],restore_specs(st['teams'][str(a)]),restore_specs(st['teams'][str(b)]),st['names'][str(a)],st['names'][str(b)],self.owners,f'Sealed Round {m["round"]} • {m["score"][0]}–{m["score"][1]}')
            await view.send(i.followup)
        picker.callback=choose;self.add_item(picker)

    async def interaction_check(self,i):
        if i.user.id not in self.owners:await i.response.send_message('These replays belong to the draft’s participants.',ephemeral=True);return False
        return True


class SealedView(SafeView):
    def __init__(self,cog,sid,row):
        super().__init__(timeout=None);self.cog=cog;self.sid=sid
        for item in self.children:item.custom_id=f'v11:sealed:{sid}:{item.custom_id}'
        self.open_pack.disabled=row['status']!='drafting';self.edit_team.disabled=row['status']!='building'
        self.results.disabled=not any(m['games'] for m in row['state']['matches'])

    @discord.ui.button(label='Open Pack',style=discord.ButtonStyle.primary,custom_id='open')
    async def open_pack(self,i,button):
        await self.cog.store.read_active(self.sid,i.user.id)
        await i.response.defer(ephemeral=True);await self.cog.show_pack(i,self.sid,i.user.id)

    @discord.ui.button(label='Edit Team',style=discord.ButtonStyle.success,custom_id='team')
    async def edit_team(self,i,button):
        row=await self.cog.store.read_active(self.sid,i.user.id,'building')
        await i.response.send_message(embed=SealedTeamView.embed(row,i.user.id),view=SealedTeamView(self.cog,row,i.user.id),ephemeral=True)

    @discord.ui.button(label='Match Results / Logs',style=discord.ButtonStyle.secondary,custom_id='results')
    async def results(self,i,button):
        row=await self.cog.store.session(self.sid)
        if i.user.id not in row['state']['order']:raise ValueError('You are not a participant in this draft.')
        if not any(m['games'] for m in row['state']['matches']):raise ValueError('No played matches yet.')
        await i.response.send_message('Choose a completed match.',view=SealedResultsView(self.cog,row),ephemeral=True)


class SealedCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=SealedStore(bot.db);self.jobs={};self.monitor=None
    async def cog_load(self):self.monitor=asyncio.create_task(self.watch())
    def cog_unload(self):
        if self.monitor:self.monitor.cancel()
        for task in self.jobs.values():task.cancel()

    async def channel(self,row):return self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
    async def channel_notice(self,row,text):await (await self.channel(row)).send(text,allowed_mentions=discord.AllowedMentions.none())

    def board(self,row):
        st=row['state'];e=discord.Embed(title='Sealed Draft • '+row['status'].title(),color=PURPLE)
        e.description='**Passing order**\n'+' → '.join(f'<@{uid}>' for uid in st['order'])
        if row['status'] in LIVE:e.description+=f'\nExpires <t:{int(st["expires_at"])}:R> • <t:{int(st["expires_at"])}:f>'
        if row['status']=='drafting':e.add_field(name='Draft progress',value='\n'.join(f'<@{uid}> — {len(st["picks"][str(uid)])}/8 picks' for uid in st['order']),inline=False)
        if row['status']=='building':e.add_field(name='SEALED TEAM SUBMISSIONS',value='\n'.join(f'{"✅" if str(uid) in st["teams"] else "⏳"} <@{uid}> — {"Submitted" if str(uid) in st["teams"] else "Building Team"}' for uid in st['order']),inline=False)
        if row['status']=='complete':e.description+=f'\n🏆 **<@{st["champion"]}> is the Sealed champion!**'
        if row['status']=='broken':e.description+='\nTournament paused. Use ;sealedview to resume.'
        if row['status']=='expired':e.description+='\n'+st.get('expiration_reason','Draft expired.')
        if st['matches']:
            lines=[]
            for m in st['matches']:
                a,b=m['players'];lines.append(f'Round {m["round"]}: <@{a}>'+(' — BYE' if b is None else f' vs <@{b}> • {m["score"][0]}–{m["score"][1]} • <@{m["winner"]}> advances'))
            e.add_field(name='Tournament results',value='\n'.join(lines),inline=False)
        e.set_footer(text=f'{row["id"]} • 8 picks → private 6v6 → best-of-three single elimination • ;sealedview reopens')
        return e

    async def update_board(self,sid):
        row=await self.store.session(sid)
        if not row or not row['message_id']:return
        try:
            message=await (await self.channel(row)).fetch_message(row['message_id'])
            await message.edit(embed=self.board(row),view=SealedView(self,sid,row),allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:log.info('Sealed board unavailable: %s',sid)

    async def show_pack(self,i,sid,uid,edit=False):
        row=await self.store.read_active(sid,uid);st=row['state'];key=str(uid)
        if row['status']!='drafting':
            await i.edit_original_response(content='Drafting finished. Use **Edit Team** on the channel’s draft board.',embed=None,attachments=[],view=None);return
        queue=st['queues'][key];payload=dict(content=f'You drafted **{len(st["picks"][key])}/8** characters. '+(f'Packs queued after this: {len(queue)-1}.' if queue else 'Waiting for another pack to arrive. Refresh or use Open Pack on the board.'),view=PackView(self,row,uid))
        if queue:
            cids=st['packs'][queue[0]]
            e=discord.Embed(title=f'Your Sealed Pack • {len(cids)} remaining',description='\n'.join(f'{n+1}. {cid}' for n,cid in enumerate(cids)),color=PURPLE)
            e.set_image(url='attachment://sealed-pack.png');payload['embed']=e
            file=discord.File(await pack_gallery(cids),filename='sealed-pack.png')
            if edit:payload['attachments']=[file]
            else:payload['file']=file
        elif edit:payload.update(embed=None,attachments=[])
        if edit:await i.edit_original_response(**payload)
        else:await i.followup.send(**payload,ephemeral=True)

    async def pack_notice(self,row,uid):
        try:
            user=self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send('Your Sealed pack is ready. Use **Open Pack** below or on the channel’s draft board.',view=SealedView(self,row['id'],row),allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:log.info('Sealed pack DM unavailable for %s; channel controls remain available.',uid)

    async def watch(self):
        await self.bot.wait_until_ready()
        for row in await self.store.sessions('sealed'):
            if row['status'] in (*LIVE,'complete'):self.bot.add_view(SealedView(self,row['id'],row))
            if row['status']=='running':self.launch(row['id'])
        while True:
            try:
                await self.store.expire()
                async with self.store.db.connect() as c:
                    notices=await self.store.rows(c,"SELECT * FROM community_sessions WHERE kind='sealed' AND status='expired' AND json_extract(state_json,'$.expiration_notified') IS NOT 1")
                for raw in notices:
                    row=self.store.unpack(raw)
                    if row['status']=='expired' and not row['state'].get('expiration_notified'):
                        await self.update_board(row['id'])
                        await self.channel_notice(row,f'Sealed Draft `{row["id"]}` expired: '+row['state'].get('expiration_reason','30 minutes elapsed.'))
                        await self.store.mark_notice(row['id'],'expiration_notified')
            except asyncio.CancelledError:raise
            except Exception:log.exception('Sealed expiry check failed.')
            await asyncio.sleep(20)

    def launch(self,sid):
        if sid in self.jobs and not self.jobs[sid].done():return
        task=asyncio.create_task(self.run(sid));self.jobs[sid]=task
        task.add_done_callback(lambda done:self.jobs.pop(sid,None) if self.jobs.get(sid) is done else None)

    async def run(self,sid):
        try:
            while True:
                row=await self.store.session(sid)
                if not row or row['status']!='running':return
                st=row['state'];index=st['match_index'];round_no=st['bracket_round'];a,b=st['bracket'][index]
                score=[];games=[];winner=a
                if b is not None:
                    specs=[restore_specs(st['teams'][str(uid)]) for uid in (a,b)]
                    side,score,results=await asyncio.wait_for(asyncio.to_thread(play_series,sid,f'{round_no}:{index}',*specs,a,b,st['names'][str(a)],st['names'][str(b)],'sealed'),60)
                    winner=(a,b)[side];games=[asdict(result) for result in results]
                fresh=await self.store.record(sid,round_no,index,winner,score,games)
                await self.update_board(sid)
                try:await self.channel_notice(fresh,f'Sealed Round {round_no}: <@{a}>'+(' receives a bye.' if b is None else f' vs <@{b}> — **{score[0]}–{score[1]}**, <@{winner}> advances. Results and battle logs are available on the draft board.'))
                except discord.HTTPException:log.info('Sealed result announcement unavailable: %s',sid)
                if fresh['status']=='complete':
                    await self.channel_notice(fresh,f'🏆 <@{winner}> is the **Sealed Draft champion!**');return
        except asyncio.CancelledError:raise
        except Exception:
            log.exception('Sealed tournament paused: %s',sid);await self.store.mark_broken(sid);await self.update_board(sid)

    @commands.hybrid_command(name='sealed',description='Start a private 6v6 Sealed Draft with 2–7 mentioned players.')
    async def sealed(self,ctx,*,players:str):
        if not ctx.guild:await ctx.send('Start Sealed Drafts in a server channel.');return
        ids=[int(uid) for uid in re.findall(r'<@!?(\d+)>',players)]
        if not 2<=len(ids)<=7 or len(set(ids))!=len(ids):await ctx.send('Mention 2–7 distinct human players: ;sealed @User1 @User2 …');return
        users=[]
        for uid in ids:
            user=ctx.guild.get_member(uid)
            if user is None:
                try:user=await ctx.guild.fetch_member(uid)
                except discord.HTTPException:await ctx.send('Every participant must be a member of this server.');return
            if user.bot:await ctx.send('Sealed participants must be human players.');return
            users.append(dict(id=uid,name=user.display_name))
        await ctx.defer()
        row=await self.store.create(users,ctx.channel.id)
        message=await ctx.send(embed=self.board(row),view=SealedView(self,row['id'],row),allowed_mentions=discord.AllowedMentions.none())
        await self.store.set_message(row['id'],message.id);self.bot.add_view(SealedView(self,row['id'],row))
        await asyncio.gather(*(self.pack_notice(row,uid) for uid in row['state']['order']))

    @commands.hybrid_command(name='sealedview',description='Reopen a Sealed Draft or resume its paused tournament.')
    async def sealedview(self,ctx,draft_id:str=''):
        if not ctx.guild:await ctx.send('Reopen Sealed Drafts in their server channel.');return
        await self.store.expire()
        rows=[r for r in await self.store.sessions('sealed') if r['channel_id']==ctx.channel.id]
        row=next((r for r in reversed(rows) if not draft_id or r['id']==draft_id),None)
        if not row:await ctx.send('No Sealed Draft found in this channel.');return
        if row['status']=='broken' and ctx.author.id in row['state']['order']:
            async with self.store.tx() as c:
                row=await self.store.current(c,row['id'],ctx.author.id,'broken');row['status']='running';row['state']['stage']='running';await self.store._save(c,row)
        message=await ctx.send(embed=self.board(row),view=SealedView(self,row['id'],row),allowed_mentions=discord.AllowedMentions.none())
        await self.store.set_message(row['id'],message.id)
        if row['status']=='running':self.launch(row['id'])


async def setup(bot):await bot.add_cog(SealedCog(bot))
