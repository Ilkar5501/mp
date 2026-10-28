from __future__ import annotations
import asyncio
import json
import logging
import discord
from discord.ext import commands
from ..battle_render import render_battle_summary
from ..economy import local_date_key
from ..tournament import losses,next_match,play_match,restore_result,restore_specs
from ..tournament_store import TournamentStore
from ..tournament_cpu import is_cpu
from .battle import BattleLogLauncher,PURPLE
from .teams import TeamCreateView

log=logging.getLogger(__name__)


class EntrySelect(discord.ui.Select):
    def __init__(self,parent,teams):
        self.parent_view=parent
        super().__init__(placeholder='Enter with a complete 6v6 team',options=[discord.SelectOption(label=f'{t.name} — Slot {t.slot}'[:100],value=str(t.slot)) for t in sorted(teams,key=lambda t:(t.name.casefold(),t.slot))])

    async def callback(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            started=await self.parent_view.cog.store.join(self.parent_view.tid,interaction.user.id,interaction.user.display_name,int(self.values[0]))
        except ValueError as error:
            await interaction.followup.send(str(error),ephemeral=True);return
        if started:self.parent_view.cog.launch(self.parent_view.tid)
        await interaction.edit_original_response(content='✅ Your 6v6 lineup has been entered. Match results will arrive in your DMs. You can leave until all eight places are filled.',view=None)
        await self.parent_view.cog.refresh_lobby(self.parent_view.tid)


class TournamentTeamModal(discord.ui.Modal,title='Build your tournament team'):
    slot=discord.ui.TextInput(label='Team slot (1–15)',placeholder='Choose a new slot or expand an existing team',max_length=2)

    def __init__(self,parent):
        super().__init__();self.parent_view=parent

    async def on_submit(self,interaction):
        try:
            slot=int(str(self.slot.value))
            if not 1<=slot<=15:raise ValueError()
        except ValueError:
            await interaction.response.send_message('Choose a team slot from 1 to 15.',ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        cards=await self.parent_view.cog.bot.db.get_cards(interaction.user.id)
        from ..data import CHAR_BY_ID
        if len({CHAR_BY_ID[c.character_id].name for c in cards})<6:
            await interaction.followup.send('You need six different character names to build a 6v6 team.',ephemeral=True);return
        db=self.parent_view.cog.bot.db
        team=await db.ensure_saved_team(interaction.user.id,slot)
        team=await db.change_team_format(interaction.user.id,slot,6)
        from ..data import CHAR_BY_ID
        selected={CHAR_BY_ID[c.character_id].name for c in cards if c.instance_id in team.card_ids}
        view=TeamCreateView(self.parent_view.cog.bot,interaction.user.id,slot,team.name,cards,selected,6)
        view.message=await interaction.followup.send(content='Fill all six positions, then click **Join** on the tournament lobby to enter. Expanding preserves your existing lanes.',embed=await view.build_embed(),view=view,ephemeral=True,wait=True)


class EntryView(discord.ui.View):
    def __init__(self,cog,tid,owner,teams):
        super().__init__(timeout=300);self.cog=cog;self.tid=tid;self.owner=owner
        if teams:self.add_item(EntrySelect(self,teams))

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner:
            await interaction.response.send_message('This team selector belongs to another player.',ephemeral=True);return False
        return True

    @discord.ui.button(label='Create / expand a 6v6 team',style=discord.ButtonStyle.primary,row=1)
    async def create_team(self,interaction,button):
        await interaction.response.send_modal(TournamentTeamModal(self))


class LobbyView(discord.ui.View):
    def __init__(self,cog,tid):
        super().__init__(timeout=None);self.cog=cog;self.tid=tid
        for item in self.children:item.custom_id=f'hiq:to:{tid}:{item.custom_id}'

    @discord.ui.button(label='Join',style=discord.ButtonStyle.success,custom_id='join')
    async def join(self,interaction,button):
        user=await self.cog.bot.db.get_user(interaction.user.id)
        if not user.tutorial_started:
            await interaction.response.send_message('Complete `/start` before joining.',ephemeral=True);return
        t=await self.cog.store.get(self.tid)
        if not t or t['status']!='open':
            await interaction.response.send_message('This lobby is no longer open.',ephemeral=True);return
        teams=[t for t in await self.cog.bot.db.list_saved_teams(interaction.user.id) if t.team_format==6 and t.complete]
        await interaction.response.send_message('Select a complete 6v6 team, or create one below. Your lineup is saved when you enter. Eight entrants start the bracket automatically.',view=EntryView(self.cog,self.tid,interaction.user.id,teams),ephemeral=True)

    @discord.ui.button(label='Leave',style=discord.ButtonStyle.secondary,custom_id='leave')
    async def leave(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:await self.cog.store.leave(self.tid,interaction.user.id)
        except ValueError as error:
            await interaction.followup.send(str(error),ephemeral=True);return
        await interaction.followup.send('You are no longer entered in this lobby.',ephemeral=True)
        await self.cog.refresh_lobby(self.tid)

    @discord.ui.button(label='Auto-fill (host)',style=discord.ButtonStyle.primary,custom_id='autofill')
    async def auto_fill(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:added=await self.cog.store.auto_fill(self.tid,interaction.user.id)
        except ValueError as error:
            await interaction.followup.send(str(error),ephemeral=True);return
        self.cog.launch(self.tid)
        await interaction.followup.send(f'Added {added} CPU teams. The eight-team bracket is starting.',ephemeral=True)
        await self.cog.refresh_lobby(self.tid)

    @discord.ui.button(label='Cancel lobby (host)',style=discord.ButtonStyle.danger,custom_id='cancel')
    async def cancel(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:await self.cog.store.cancel(self.tid,interaction.user.id)
        except ValueError as error:
            await interaction.followup.send(str(error),ephemeral=True);return
        await interaction.followup.send('Lobby cancelled.',ephemeral=True)
        await self.cog.refresh_lobby(self.tid)


class MatchSelect(discord.ui.Select):
    def __init__(self,parent,matches):
        self.parent_view=parent
        super().__init__(placeholder='Choose one of your matches',options=[discord.SelectOption(label=f'{r["match_key"]} — {"Win" if r["winner_id"]==parent.owner else "Loss"}',value=r['match_key']) for r in matches])

    async def callback(self,interaction):
        await interaction.response.defer(ephemeral=True)
        await self.parent_view.cog.show_match(interaction.followup,self.parent_view.tid,self.values[0],self.parent_view.owner,ephemeral=True)


class MatchView(discord.ui.View):
    def __init__(self,cog,tid,owner,matches):
        super().__init__(timeout=300);self.cog=cog;self.tid=tid;self.owner=owner;self.add_item(MatchSelect(self,matches))

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner:
            await interaction.response.send_message('These matches belong to another player.',ephemeral=True);return False
        return True


class ResultsView(discord.ui.View):
    def __init__(self,cog,tid):
        super().__init__(timeout=None);self.cog=cog;self.tid=tid
        self.my_matches.custom_id=f'hiq:to:{tid}:matches'

    @discord.ui.button(label='View my matches',style=discord.ButtonStyle.primary,custom_id='matches')
    async def my_matches(self,interaction,button):
        matches=await self.cog.store.matches(self.tid)
        own=[r for r in matches.values() if interaction.user.id in (r['owner_a'],r['owner_b'])]
        if not own:
            await interaction.response.send_message('You did not play in this tournament.',ephemeral=True);return
        await interaction.response.send_message('Your tournament matches:',view=MatchView(self.cog,self.tid,interaction.user.id,own),ephemeral=True)


class TournamentCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot;self.store=TournamentStore(bot.db);self.jobs={};self.restore_task=None

    async def cog_load(self):self.restore_task=asyncio.create_task(self.restore())

    def cog_unload(self):
        if self.restore_task:self.restore_task.cancel()
        for task in self.jobs.values():task.cancel()

    async def restore(self):
        await self.bot.wait_until_ready()
        for t in await self.store.pending():
            if t['lobby_message_id'] and t['status'] in ('open','running'):
                self.bot.add_view(LobbyView(self,t['id']) if t['status']=='open' else ResultsView(self,t['id']),message_id=t['lobby_message_id'])
            if t['status'] in ('running','complete'):self.launch(t['id'])
        # Re-register historical result controls after restarts.
        async with self.bot.db.connect() as db:
            cur=await db.execute("SELECT id,results_message_id FROM tournaments WHERE status='complete' AND results_message_id IS NOT NULL")
            for t in await cur.fetchall():self.bot.add_view(ResultsView(self,t['id']),message_id=t['results_message_id'])

    def launch(self,tid):
        if tid not in self.jobs or self.jobs[tid].done():
            task=asyncio.create_task(self.run(tid));self.jobs[tid]=task
            def finished(done):
                if self.jobs.get(tid) is done:self.jobs.pop(tid,None)
            task.add_done_callback(finished)

    async def channel(self,t):
        channel=self.bot.get_channel(t['channel_id'])
        return channel or await self.bot.fetch_channel(t['channel_id'])

    async def board(self,t):
        entries=await self.store.entries(t['id']);matches=await self.store.matches(t['id'])
        e=discord.Embed(title=f'🏆 6v6 Tournament — {t["status"].title()}',color=PURPLE)
        names=lambda owner:discord.utils.escape_markdown(entries[owner]['display_name'][:32])
        e.description=(f'**{len(entries)}/8 entrants** • Double elimination • Two losses knock you out.\n'
            'Champion: **100 coins with CPUs / 150 with eight human players**.\nThree rewarded championship wins unlock per day, shared across both modes. Unused allowance banks until Monday weekly reset. Losses: 0 coins.\n'
            'Matches run automatically; human players receive results by DM. CPUs use Neutral +0 cards and earn no coins.\n')
        if t['status']=='open':e.description+='The host can use **Auto-fill** to fill empty places with CPU teams and start.\n'
        if t['status'] in ('open','cancelled'):e.add_field(name='Entrants',value='\n'.join(names(owner) for owner in entries) or 'No entrants yet.',inline=False)
        else:
            seeds=json.loads(t['seeds_json'])
            e.add_field(name='Seeds',value=' • '.join(f'{i+1}. {names(owner)}' for i,owner in enumerate(seeds)),inline=False)
            rounds=(('Winners — Round 1',('W1','W2','W3','W4')),
                ('Winners — Semifinals',('W5','W6')),('Winners — Final',('W7',)),
                ('Losers — Round 1',('L1','L2')),('Losers — Round 2',('L3','L4')),
                ('Losers — Semifinal',('L5',)),('Losers — Final',('L6',)),
                ('Grand final / reset',('G1','G2')))
            for title,keys in rounds:
                rows=[]
                for key in keys:
                    if key not in matches:continue
                    r=matches[key]
                    rows.append(f'**{key}:** {names(r["owner_a"])} vs {names(r["owner_b"])} → 🏆 {names(r["winner_id"])}')
                if rows:e.add_field(name=title,value='\n'.join(rows),inline=False)
        if t['champion_id']:
            rewards=await self.store.rewards(t['id'])
            winner=t['champion_id']
            reward_text='CPU team — no coins awarded.' if is_cpu(winner) else f'{rewards[winner]["amount"]} coins awarded (accrued reward allowance applied).'
            e.add_field(name=f'🏆 Champion — {names(winner)}',value=reward_text,inline=False)
            e.add_field(name='Other participants • 0 coins',value='\n'.join(f'{names(owner)}: {r["amount"]} coins' for owner,r in rewards.items() if owner!=t['champion_id']) or 'No other human participants.',inline=False)
        e.set_footer(text=f'ID {t["id"]} • ;to {t["id"]} reopens results • A losers-bracket finalist win triggers a grand-final reset.')
        return e

    async def refresh_lobby(self,tid):
        t=await self.store.get(tid)
        if not t or not t['lobby_message_id']:return
        try:
            channel=await self.channel(t);message=await channel.fetch_message(t['lobby_message_id'])
            await message.edit(embed=await self.board(t),view=LobbyView(self,tid) if t['status']=='open' else ResultsView(self,tid) if t['status']=='running' else None)
        except discord.HTTPException:log.warning('Could not update tournament lobby %s',tid)

    async def show_match(self,sender,tid,key,owner,*,ephemeral=False):
        entries=await self.store.entries(tid);matches=await self.store.matches(tid);r=matches[key]
        if owner not in (r['owner_a'],r['owner_b']):raise ValueError('This match belongs to another player.')
        a=entries[r['owner_a']];b=entries[r['owner_b']]
        a_specs=restore_specs(json.loads(a['snapshot_json']));b_specs=restore_specs(json.loads(b['snapshot_json']))
        result=restore_result(json.loads(r['result_json']))
        battle=self.bot.get_cog('BattleCog')
        e=battle._result_embed(result,a['display_name'],b['display_name'],a_specs,b_specs)
        e.title=f'Tournament {key} — {"Win" if r["winner_id"]==owner else "Loss"}'
        # Both recipients see the same neutral A/B layout.
        for i,label in [(0,'Team A'),(1,'Team B')]:
            field=e.fields[i];e.set_field_at(i,name=label+field.name[field.name.index(' —'):],value=field.value,inline=False)
        history={k:v for k,v in matches.items()}
        # Exclude later matches when replaying an earlier result.
        preceding={}
        for k,v in history.items():
            preceding[k]=v
            if k==key:break
        lost=losses(preceding,owner)
        status='You won this match.' if r['winner_id']==owner else 'You have been knocked out after your second loss.' if lost>=2 else 'Your first loss sends you to the losers bracket. You are still in the tournament.'
        e.description=f'{status}\nYour losses: **{lost}/2**.'
        image=await render_battle_summary(result,a_specs,b_specs,a['display_name'],b['display_name'])
        file=discord.File(image,filename='tournament_battle.png');e.set_image(url='attachment://tournament_battle.png')
        kwargs={'ephemeral':True} if ephemeral else {}
        await sender.send(embed=e,file=file,view=BattleLogLauncher({owner},f'Tournament {key} — Battle Log',result.log),allowed_mentions=discord.AllowedMentions.none(),**kwargs)

    async def notices(self,tid):
        for key,r in (await self.store.matches(tid)).items():
            for side,owner in [('A',r['owner_a']),('B',r['owner_b'])]:
                if r['notified_a' if side=='A' else 'notified_b']:continue
                if is_cpu(owner):
                    await self.store.mark_notice(tid,key,side,True);continue
                delivered=False
                try:
                    user=self.bot.get_user(owner) or await self.bot.fetch_user(owner)
                    await self.show_match(user,tid,key,owner);delivered=True
                except discord.HTTPException:log.info('Tournament DM unavailable: %s / %s',tid,owner)
                await self.store.mark_notice(tid,key,side,delivered)

    async def run(self,tid):
        try:
            t=await self.store.get(tid)
            if not t or t['status'] not in ('running','complete'):return
            entries=await self.store.entries(tid)
            # Catch up on a result committed immediately before a restart.
            await self.notices(tid)
            while t['status']=='running':
                matches=await self.store.matches(tid);pair=next_match(json.loads(t['seeds_json']),matches)
                if pair is None:
                    await self.store.finish(tid,local_date_key());break
                key,a,b=pair;ea=entries[a];eb=entries[b]
                result=await asyncio.to_thread(play_match,tid,key,restore_specs(json.loads(ea['snapshot_json'])),restore_specs(json.loads(eb['snapshot_json'])),a,b,ea['display_name'],eb['display_name'])
                await self.store.record_match(tid,key,a,b,result)
                await self.notices(tid)
                await self.refresh_lobby(tid)
            t=await self.store.get(tid)
            if t['status']=='complete' and not t['results_message_id']:
                channel=await self.channel(t)
                failed=any(r['notified_a']==-1 or r['notified_b']==-1 for r in (await self.store.matches(tid)).values())
                humans=[owner for owner in entries if not is_cpu(owner)]
                content=' '.join(f'<@{owner}>' for owner in humans)+'\nYour tournament has concluded.'
                if failed:content+=' Some DMs could not be delivered; use **View my matches** below for your results.'
                message=await channel.send(content,embed=await self.board(t),view=ResultsView(self,tid),allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=owner) for owner in humans],roles=False,everyone=False,replied_user=False))
                await self.store.message(tid,'results_message_id',message.id)
                await self.refresh_lobby(tid)
        except asyncio.CancelledError:raise
        except Exception:
            log.exception('Tournament %s paused; persisted bracket can resume on restart or ;to.',tid)
            try:
                t=await self.store.get(tid);channel=await self.channel(t)
                await channel.send('Tournament processing paused. Completed matches are saved. Run `;to` to resume it, or it will resume when the bot restarts.',allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:pass

    @commands.hybrid_command(name='tournament',aliases=['to'],description='Join an eight-player 6v6 double-elimination tournament.')
    async def tournament(self,ctx,tournament_id:str|None=None):
        if not ctx.guild:
            await ctx.send('Create or join tournaments from a server channel.');return
        if getattr(ctx,'interaction',None):await ctx.defer()
        if tournament_id:
            t=await self.store.get(tournament_id)
            if not t or t['guild_id']!=ctx.guild.id or t['channel_id']!=ctx.channel.id:
                await ctx.send('No tournament with that ID exists in this channel.');return
        else:t=await self.store.create_or_get(ctx.channel.id,ctx.guild.id,ctx.author.id)
        view=LobbyView(self,t['id']) if t['status']=='open' else ResultsView(self,t['id'])
        msg=await ctx.send(embed=await self.board(t),view=view)
        if t['status']=='open':await self.store.message(t['id'],'lobby_message_id',msg.id)
        if t['status'] in ('running','complete'):self.launch(t['id'])


async def setup(bot):await bot.add_cog(TournamentCog(bot))
