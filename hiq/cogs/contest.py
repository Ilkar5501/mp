import asyncio,hashlib,logging
from dataclasses import asdict
import discord
from discord.ext import commands,tasks
from ..contest import ContestEngine,PRESETS,preset_specs,presets_for,support_slots,support_note
from ..contest_store import ContestStore
from ..tournament import restore_specs
from ..ui import EmbedPager,PURPLE,text_pages
from .battle import BattleLogLauncher

log=logging.getLogger(__name__)


def damage_report_embed(stats,username,opponent):
    from ..data import CHAR_BY_ID
    embed=discord.Embed(title='Clan Contest — Damage Report',color=PURPLE)
    for side,label in (('A',username),('B',opponent)):
        rows=[];total=dict(damage_dealt=0,damage_taken=0,damage_mitigated=0)
        for uid,value in stats.items():
            if not uid.startswith(side) or 'character_id' not in value:continue
            if side=='B' and not uid.startswith('B0:'):continue
            char=CHAR_BY_ID.get(value['character_id'])
            name=f'{char.name} — {char.title}' if char else value['character_id']
            rows.append('**'+discord.utils.escape_markdown(name)+'**\n'+f"Dealt **{value.get('damage_dealt',0):,}** • Tanked **{value.get('damage_taken',0):,}** • Mitigated **{value.get('damage_mitigated',0):,}**")
            for key in total:total[key]+=value.get(key,0)
        misc=stats.get(side+':misc',{})
        for key in total:total[key]+=misc.get(key,0)
        rows.append('**Misc. sources**\n'+f"Dealt **{misc.get('damage_dealt',0):,}** • Tanked **{misc.get('damage_taken',0):,}** • Mitigated **0**")
        if misc.get('self_damage_taken'):rows.append(f"Includes {misc['self_damage_taken']:,} self-inflicted damage tanked.")
        rows.append(f"**Totals:** {total['damage_dealt']:,} dealt • {total['damage_taken']:,} tanked • {total['damage_mitigated']:,} mitigated")
        embed.add_field(name=discord.utils.escape_markdown(label)[:256],value='\n\n'.join(rows),inline=False)
    embed.set_footer(text='Misc. sources include support effects and damage outside combat. Self-damage is not credited to the opponent.')
    return embed


class ContestResultView(BattleLogLauncher):
    def __init__(self,owner_id,lines,stats,username,opponent):
        super().__init__({owner_id},'Clan Contest Battle Log',lines)
        self.stats=stats;self.username=username;self.opponent=opponent

    @discord.ui.button(label='Damage Report',emoji='📊',style=discord.ButtonStyle.secondary)
    async def damage_report(self,interaction,button):
        if interaction.user.id not in self.owner_ids:
            await interaction.response.send_message('This damage report belongs to the player who ran this contest.',ephemeral=True)
            return
        await interaction.response.send_message(embed=damage_report_embed(self.stats,self.username,self.opponent),ephemeral=True)


class ContestCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot;self.store=ContestStore(bot.db);self.jobs={};self.weekly.start()

    def cog_unload(self):
        self.weekly.cancel()
        for task in self.jobs.values():task.cancel()

    @tasks.loop(seconds=60)
    async def weekly(self):
        try:await self.store.settle()
        except Exception:log.exception('Clan Contest settlement will retry.')

    @weekly.before_loop
    async def ready(self):await self.bot.wait_until_ready()

    def launch(self,sid):
        if sid not in self.jobs or self.jobs[sid].done():
            task=asyncio.create_task(self.play(sid));self.jobs[sid]=task
            task.add_done_callback(lambda done:self.jobs.pop(sid,None) if self.jobs.get(sid) is done else None)

    async def play(self,sid):
        try:
            row=await self.store.run(sid)
            if not row or row['status']!='running':return
            state=row['state'];seed=int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big')
            rules=state.get('contest_rules',1)
            engine=ContestEngine(restore_specs(state['players']),state['index'],rules_version=rules,seed=seed,name_a=state['username'],name_b=presets_for(rules)[state['index']][0])
            result=await asyncio.wait_for(asyncio.to_thread(engine.run),60)
            row=await self.store.finish(sid,engine.score,asdict(result))
            channel=self.bot.get_channel(state['channel']) or await self.bot.fetch_channel(state['channel'])
            note='This week already closed, so this late result did not change the rankings.' if row['state']['week_closed'] else 'Your best damage today determines your daily percentage. The top player earns 100%, and daily percentages add up across the week. Retry any time.'
            embed=discord.Embed(title='Clan Contest — Result',description=f'<@{row["owner_id"]}> dealt **{engine.score:,} damage** over **{result.turns}/20 turns** and broke **{engine.bars} HP bars**.\n\n**+{row["state"]["participation"]} Clan Dollars** for participation.\n{note}\n\n`;contestboard` opens the standings.',color=PURPLE)
            await channel.send(embed=embed,view=ContestResultView(row['owner_id'],result.log,result.stats,state['username'],presets_for(rules)[state['index']][0]),allowed_mentions=discord.AllowedMentions.none())
        except asyncio.CancelledError:raise
        except Exception:log.exception('Contest %s paused; use ;contestresume.',sid)

    async def board_embed(self):
        from ..contest_render import render_contest
        index,_=await self.store.rotation();specs=preset_specs(index);feature=specs[0][1]
        supports="\n".join(f"**{char.name} — {char.title}** • {char.school} • "+" / ".join(slot+": "+char.abilities[slot].name for slot in support_slots(char))+(f'\n{support_note(index,char)}' if support_note(index,char) else '') for _,char,_ in specs[1:])
        bond=sum(char.school==feature.school for _,char,_ in specs[1:])
        embed=discord.Embed(title=f'Clan Contest — {PRESETS[index][0]}',description=f'**{feature.name} — {feature.title}** • {feature.school}\n+10 • Neutral • No equipment • {specs[0][2].title()} line • School Bond +{bond}\n\nBring a complete 6v6 team and deal as much damage as you can in 20 turns. The featured unit refills its HP bars; your team can be defeated. Only the featured unit fights.\n\n**Supports**\n{supports}\n\nThe best score from your retries counts each day. First participation pays **200 Clan Dollars**, once daily, with no rollover.\n\nUse `;clancontest <team slot>` to play, `;contestboard` for rankings.',color=PURPLE)
        file=discord.File(await render_contest(specs),filename='contest.png');embed.set_image(url='attachment://contest.png')
        return embed,file

    @commands.hybrid_command(name='clancontest',aliases=['cc'],description='View today’s featured unit or enter with a complete 6v6 team slot.')
    async def clancontest(self,ctx,slot:int|None=None):
        if not ctx.guild:await ctx.send('Play Clan Contest in a server channel.');return
        await ctx.defer();await self.store.settle()
        if slot is None:
            embed,file=await self.board_embed();await ctx.send(embed=embed,file=file);return
        row=await self.store.start(ctx.author.id,ctx.author.display_name,slot,ctx.channel.id)
        self.launch(row['id']);await ctx.send(f'Your contest attempt is running. Good luck! ID: `{row["id"]}`')

    @commands.hybrid_command(name='contestresume',description='Recover your interrupted Clan Contest attempt.')
    async def contestresume(self,ctx):
        async with self.bot.db.connect() as c:
            row=await self.store.one(c,"SELECT id FROM contest_runs WHERE owner_id=? AND status='running'",(ctx.author.id,))
        if not row:await ctx.send('You don’t have an interrupted contest attempt.');return
        self.launch(row['id']);await ctx.send('Your saved contest attempt is resuming in its original channel.')

    @commands.hybrid_command(name='contestboard',aliases=['ccboard','cb'],description='See daily percentages and weekly clan and player standings.')
    async def contestboard(self,ctx):
        await ctx.defer()
        await self.store.settle();clans,players=await self.store.leaderboard()
        from datetime import date,timedelta
        from ..economy import week_key
        days=[(date.fromisoformat(week_key())+timedelta(days=n)).isoformat() for n in range(7)]
        def breakdown(row):
            return ' · '.join(f'{date.fromisoformat(day):%a} {row["days"].get(day,{}).get("percent",0):.2f}%' for day in days)
        lines=[]
        for i,row in enumerate(clans,1):
            clan=await self.store.clan(clan_id=row['clan_id'])
            lines.append(f'**{i}. {discord.utils.escape_markdown(clan["name"] if clan else "Former clan")}** — **{row["score"]:.2f}%**\n{breakdown(row)}')
        pages=text_pages('Clan Contest — Weekly Clan Rankings',lines,limit=2800)
        pages+=text_pages('Clan Contest — Weekly Player Rankings',[f'**{i}.** <@{r["owner_id"]}> — **{r["score"]:.2f}%**\n{breakdown(r)}' for i,r in enumerate(players,1)],limit=2800)
        for embed in pages:
            embed.add_field(name='How scoring works',value='Each day: your best damage ÷ the top player’s best damage × 100. Clans compare their combined daily damage with the top clan’s total. The daily leader earns 100%; seven daily percentages add up to a maximum of 700%. Missing days earn 0%. Percentages update when a better score arrives; prizes lock at the weekly reset.',inline=False)
            embed.add_field(name='Weekly prizes',value='1st: 1,000 • 2nd: 750 • 3rd: 500. Clans earn Clan Dollars for all members; individual players earn Coins. Equal scores sort by clan/player ID. Rankings reset Monday.',inline=False)
        pager=EmbedPager(pages,ctx.author.id);await ctx.send(embed=pager.embed(),view=pager)


async def setup(bot):await bot.add_cog(ContestCog(bot))
