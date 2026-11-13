"""Ranked automatic/targeted PvP, protected team slots and monthly standings."""
import asyncio
import logging
from dataclasses import asdict

import discord
from discord import app_commands
from discord.ext import commands,tasks

from ..battle.engine import BattleEngine
from ..battle_render import render_battle_summary
from ..data import CHAR_BY_ID
from ..pvp_store import PvPStore,PvPTeamUnavailable
from ..tournament import restore_specs,restore_result
from ..ui import SafeView,EmbedPager,PURPLE
from .battle import BattleLogLauncher

log=logging.getLogger(__name__)


def parse_format(raw):
    if not raw:return None
    fmt={'3':3,'3v3':3,'6':6,'6v6':6}.get(str(raw).casefold())
    if not fmt:raise ValueError('Choose 3v3 or 6v6.')
    return fmt


class PvPTeamView(SafeView):
    def __init__(self,cog,uid,prefs,teams,cards):
        super().__init__(timeout=600);self.cog=cog;self.uid=uid;self.prefs=prefs;self.teams=teams;self.cards=cards;self.build()

    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('Open ;pvpset for your own teams.',ephemeral=True);return False
        return True

    async def refresh(self,i):
        self.prefs=await self.cog.store.preferences(self.uid)
        self.teams=await self.cog.bot.db.list_saved_teams(self.uid)
        self.cards=await self.cog.bot.db.get_cards(self.uid)
        self.build();await i.response.edit_message(embed=self.embed(),view=self)

    def embed(self):
        fmt=self.prefs['profile']['default_format']
        by_id={c.instance_id:CHAR_BY_ID.get(c.character_id) for c in self.cards}
        e=discord.Embed(title='PvP Teams',description=f'Default: **{fmt}v{fmt}** · Choose from your saved teams.',color=PURPLE)
        for size in (3,6):
            row=self.prefs['teams'].get(size)
            text='Not set — create a team with ;teamcreate.'
            if row:
                lines=[]
                for entry in row['entries']:
                    char=by_id.get(entry['id'])
                    lines.append(('F' if entry['lane']=='front' else 'B')+': '+(char.name+' · '+char.title if char else 'Missing card'))
                text='\n'.join(lines)
                if not row['valid']:text+='\nUpdate this lineup before battling.'
            e.add_field(name=f'{"🔒" if row and row["locked"] else "🔓"} {size}v{size}'+(' · '+row['name'] if row else ''),value=text,inline=True)
        e.set_footer(text='Locks stop ;teamuse from replacing that PvP lineup. Edit normal teams with ;teamedit.')
        return e

    def build(self):
        self.clear_items()
        for index,fmt in enumerate((3,6)):
            row=self.prefs['teams'].get(fmt);revision=row['revision'] if row else -1
            available=[t for t in self.teams if t.team_format==fmt and t.complete]
            select=discord.ui.Select(placeholder=f'Choose a {fmt}v{fmt} team',options=[discord.SelectOption(label=f'{t.slot}. {t.name}'[:100],value=str(t.slot)) for t in available] or [discord.SelectOption(label='No complete teams',value='none')],disabled=not available,row=index)
            async def choose(i,s=select,f=fmt,r=revision):
                await self.cog.store.set_team(self.uid,int(s.values[0]),revision=r,expected_format=f);await self.refresh(i)
            select.callback=choose;self.add_item(select)
            locked=bool(row and row['locked'])
            lock=discord.ui.Button(label=f'{fmt}v{fmt}',emoji='🔒' if locked else '🔓',style=discord.ButtonStyle.primary if locked else discord.ButtonStyle.secondary,row=2,disabled=row is None)
            async def toggle(i,f=fmt,r=revision,value=locked):
                await self.cog.store.set_lock(self.uid,f,not value,r);await self.refresh(i)
            lock.callback=toggle;self.add_item(lock)
            default=discord.ui.Button(label=f'Use {fmt}v{fmt}',row=3,style=discord.ButtonStyle.primary if self.prefs['profile']['default_format']==fmt else discord.ButtonStyle.secondary,disabled=not row or not row['valid'])
            async def activate(i,f=fmt):await self.cog.store.set_default(self.uid,f);await self.refresh(i)
            default.callback=activate;self.add_item(default)


class PvPCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot;self.store=PvPStore(bot.db);self.jobs={};self.recoveries=set();self.maintenance.start()

    def cog_unload(self):
        self.maintenance.cancel()
        for task in (*self.jobs.values(),*self.recoveries):task.cancel()

    @tasks.loop(seconds=60)
    async def maintenance(self):
        try:
            await self.store.settle()
            for row in await self.store.pending():
                if row['id'] not in self.jobs:
                    task=self.launch(row['id'])
                    recovery=asyncio.create_task(self.recover(row['id'],task));self.recoveries.add(recovery)
                    recovery.add_done_callback(self.recoveries.discard)
        except Exception:log.exception('PvP maintenance will retry.')

    @maintenance.before_loop
    async def ready(self):await self.bot.wait_until_ready()

    def launch(self,sid):
        task=self.jobs.get(sid)
        if task is None:
            task=asyncio.create_task(self.simulate(sid));self.jobs[sid]=task
            task.add_done_callback(lambda done:self.jobs.pop(sid,None) if self.jobs.get(sid) is done else None)
        return task

    async def simulate(self,sid):
        row=await self.store.run(sid);state=row['state'];a=restore_specs(state['a']);b=restore_specs(state['b'])
        if row['status']=='done':return row,restore_result(row['result']),a,b
        engine=BattleEngine(a,b,owner_a=row['a_id'],owner_b=row['b_id'],name_a=state['name_a'],name_b=state['name_b'],mode='pvp',seed=state['seed'])
        result=await asyncio.to_thread(engine.run)
        row=await self.store.finish(sid,asdict(result))
        return row,result,a,b

    async def result_payload(self,row,result,a,b):
        state=row['state'];settlement=row['settlement']
        embed=self.bot.get_cog('BattleCog')._result_embed(result,state['name_a'],state['name_b'],a,b)
        embed.title=('Ranked PvP · ' if row['rated'] else 'Practice PvP · ')+embed.title
        if row['rated']:
            embed.add_field(name='Elo · '+row['month'],value=f'<@{row["a_id"]}>: **{settlement["a_before"]} → {settlement["a_after"]}**\n<@{row["b_id"]}>: **{settlement["b_before"]} → {settlement["b_after"]}**',inline=False)
        else:embed.add_field(name='Practice · Elo unchanged',value=state['reason'],inline=False)
        reward=settlement['reward']
        if reward:embed.add_field(name='PvP coins',value=f'<@{settlement["winner_id"]}>: **+{reward["coins"]} coins**',inline=False)
        if reward and not reward.get('duplicate') and reward.get('allowance_day') is None:
            embed.add_field(name='Reward allowance reached',value='The winner’s PvP reward allowance is capped.',inline=False)
        consolation=settlement.get('loss_reward')
        if consolation is not None:
            loser=row['b_id'] if settlement['winner_id']==row['a_id'] else row['a_id']
            note='Already rewarded against this opponent today.' if consolation.get('duplicate') else 'PvP reward allowance capped.' if consolation.get('allowance_day') is None else f'+{consolation["coins"]} coins. The loss and Elo result are unchanged.'
            embed.add_field(name='Never Give Up',value=f'<@{loser}>: {note}',inline=False)
        embed.set_footer(text=f'{row["format"]}v{row["format"]} · One shared five-week ladder · Existing daily coin limits apply')
        view=BattleLogLauncher({row['a_id'],row['b_id']},'PvP Battle Log',result.log)
        try:
            image=await render_battle_summary(result,a,b,state['name_a'],state['name_b'])
            file=discord.File(image,filename='pvp_summary.png');embed.set_image(url='attachment://pvp_summary.png')
            return dict(embed=embed,view=view,file=file,allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log.exception('PvP image unavailable; sending the settled result and log.')
            return dict(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())

    async def recover(self,sid,task):
        try:
            row,result,a,b=await task
            channel=self.bot.get_channel(row['state']['channel']) or await self.bot.fetch_channel(row['state']['channel'])
            await channel.send(**await self.result_payload(row,result,a,b))
        except asyncio.CancelledError:raise
        except Exception:log.exception('PvP %s recovery will retry if unsettled.',sid)

    @commands.hybrid_command(name='pvp',description='Match the closest eligible Elo opponent, or challenge a player.')
    @app_commands.choices(format=[app_commands.Choice(name='3v3',value='3v3'),app_commands.Choice(name='6v6',value='6v6')])
    @commands.cooldown(1,5.0,commands.BucketType.user)
    async def pvp(self,ctx,opponent:discord.Member|None=None,format:str=''):
        if opponent and (opponent.bot or opponent.id==ctx.author.id):await ctx.send('Choose another player.');return
        await ctx.defer()
        try:
            row=await self.store.start(ctx.author.id,target=opponent.id if opponent else None,fmt=parse_format(format),name=ctx.author.display_name,target_name=opponent.display_name if opponent else '',channel=ctx.channel.id)
            row,result,a,b=await asyncio.shield(self.launch(row['id']))
        except PvPTeamUnavailable as error:
            if getattr(ctx,'interaction',None):await ctx.send(str(error),ephemeral=True)
            else:
                try:await ctx.author.send(str(error))
                except discord.HTTPException:await ctx.send('I could not DM the format check. Use /pvp for a private check.',delete_after=10)
            return
        except ValueError as error:await ctx.send(str(error),allowed_mentions=discord.AllowedMentions.none());return
        await ctx.send(**await self.result_payload(row,result,a,b))

    @commands.hybrid_command(name='pvpset',aliases=['pvps'],description='Set and lock separate 3v3 and 6v6 PvP teams.')
    async def pvpset(self,ctx,slot:int|None=None):
        if slot is not None:
            fmt=await self.store.set_team(ctx.author.id,slot);await self.store.set_default(ctx.author.id,fmt)
        prefs=await self.store.preferences(ctx.author.id,ctx.author.display_name)
        view=PvPTeamView(self,ctx.author.id,prefs,await self.bot.db.list_saved_teams(ctx.author.id),await self.bot.db.get_cards(ctx.author.id))
        await ctx.send(embed=view.embed(),view=view)

    @commands.hybrid_command(name='pvpboard',aliases=['pvpb'],description='View the shared five-week PvP Elo leaderboard and weekly prize.')
    async def pvpboard(self,ctx):
        month,rows,own=await self.store.board(ctx.author.id);pages=[]
        for start in range(0,max(1,len(rows)),15):
            lines=[f'**{i}.** <@{r["owner_id"]}> · **{r["rating"]} Elo** · {r["wins"]}W/{r["losses"]}L/{r["draws"]}D' for i,r in enumerate(rows[start:start+15],start+1)]
            e=discord.Embed(title='PvP Ladder · '+month,description='\n'.join(lines) or 'No ranked matches yet. Use ;pvp to begin.',color=PURPLE)
            e.add_field(name='Your Elo',value=str(own['rating']),inline=True)
            from ..pvp_cycles import cycle_for
            from ..economy import local_date_key,next_week_reset
            _,phase=cycle_for(local_date_key())
            e.add_field(name=f'Week {phase}/5 · First place',value=f'{phase}★ pack'+(f' + {(5-phase)*100} coins' if phase<5 else '')+f'\nCutoff <t:{int(next_week_reset().timestamp())}:F>. Elo carries until week 5 ends, then resets.',inline=False)
            pages.append(e)
        view=EmbedPager(pages,ctx.author.id);await ctx.send(embed=view.embed(),view=view,allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):await bot.add_cog(PvPCog(bot))
