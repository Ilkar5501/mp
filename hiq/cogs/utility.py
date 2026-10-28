from __future__ import annotations
import discord
from discord.ext import commands

from ..abbreviations import SCHOOLS, school_name
from ..community_store import CommunityStore
from ..config import GAME_CONFIG
from ..data import CHARACTERS
from ..ui import EmbedPager, text_pages


class UtilityCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=CommunityStore(bot.db)

    @commands.hybrid_command(name='abbreviations',aliases=['ab'],description='Show command, school and filter abbreviations.')
    async def abbreviations(self,ctx):
        lines=['**Schools**']+[f'`{short}` → {school}' for short,school in SCHOOLS.items()]
        lines+=['\n**Commands**','`;d` daily • `;bal` balance • `;co` / `;col` collection • `;cv` cardview','`;qv` questview • `;to` tournament • `;pref` prefecture • `;inv` inventory','`;gn r` Regional Gauntlet • `;gn n` National Gauntlet','`;p b` Base Pack • `;p s kar` Karasuno Pack • `;p p ws` WS Pack','`;info p b` • `;info p s kar` • `;info p p ws` — pack pools and odds','\n**Filters**','`-n` name • `-pos` position • `-tm` school','Gallery: `-class` / `-cl` class • `-star` / `-st` rarity • `-effect` / `-eff` ability text','Classes: `dps`, `tank`, `support` or `sup`. Star ratings: `1` through `5`.','`-hp`, `-pow`, `-qik`, `-def`, `-rec`, `-spd`, `-jmp` — stat comparisons','Colon filters also work: `name:`, `school:`, `position:`, `class:`, `star:`, `effect:`, `boon:`, `bane:`, `merge:`, `id:`.','Examples: `;gallery -cl sup -st 5`, `;gallery -eff critical hit`, `;co -tm kar -pos MB -spd >= 30`','Positions: `MB` middle blocker • `WS` wing spiker • `OP` opposite • `S` setter • `Li` libero.']
        lines+=['\n**Raids and equipment**','`;raid 3v3 tactics 1` / `;raid 6v6 offense 2` — format, theme, saved team slot','Themes: `tactics`, `offense`, `defense`, `tempo`, `recovery`.','`;items -n name` / `;items -t effect` — equipment name/effect filters; `name:` and `text:` also work.','`;craft item name` • `;equip cardID itemID` • `;unequip cardID` • `;raidresume [raidID]`.']
        pager=EmbedPager(text_pages('Abbreviations',lines),ctx.author.id);await ctx.send(embed=pager.embed(),view=pager)

    @commands.hybrid_command(name='info',description='Inspect a pack’s units and individual first-draw drop rates.')
    async def info(self,ctx,*,query:str):
        parts=query.split()
        if not parts or parts[0].casefold() not in ('p','pack'):await ctx.send('Use ;info p b, ;info p s <school>, or ;info p p <position>.');return
        kind={'b':'base','base':'base','s':'school','school':'school','p':'position','pos':'position','position':'position'}.get(parts[1].casefold() if len(parts)>1 else '')
        if not kind:await ctx.send('Choose base, school or position.');return
        pool=list(CHARACTERS);detail=''
        if kind=='school':
            raw=school_name(' '.join(parts[2:]));detail=next((s for s in GAME_CONFIG['packs']['school']['eligible_schools'] if s.casefold()==raw.casefold()),None)
            if not detail:await ctx.send('That school does not have a School Pack. Use ;shop for eligible schools.');return
            pool=[c for c in pool if c.school==detail]
        elif kind=='position':
            detail=' '.join(parts[2:]).upper();pool=[c for c in pool if c.position.upper()==detail]
            if not pool:await ctx.send('Choose MB, WS, OP, S or Li.');return
        odds=GAME_CONFIG['packs'][kind]['rarity_odds'];available={c.rarity for c in pool}
        total=sum(odds.get(str(r),0) for r in available)
        lines=['Rates are **per first draw**, before pity. Rarity odds normalize over available rarities. Later draws exclude already-drawn character names where possible, so their rates change.\n']
        for stars in sorted(available,reverse=True):
            units=[c for c in pool if c.rarity==stars];rarity_rate=100*odds.get(str(stars),0)/total
            lines.append(f'\n**{stars}★: {rarity_rate:.2f}% rarity rate • {len(units)} options • {rarity_rate/len(units):.2f}% per unit**')
            lines += [f'• {c.name} — {c.title}: **{rarity_rate/len(units):.2f}%**' for c in sorted(units,key=lambda c:(c.name,c.title))]
        pager=EmbedPager(text_pages(f'{detail or "Base"} {kind.title()} Pack — Units & Odds',lines),ctx.author.id)
        await ctx.send(embed=pager.embed(),view=pager)

    async def _message_missing(self,row):
        if not row['message_id']:return True
        try:
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
            await channel.fetch_message(row['message_id']);return False
        except discord.NotFound:return True
        except discord.HTTPException:return False

    @commands.hybrid_command(name='refresh',description='Leave pending or broken activities without conceding a healthy war.')
    async def refresh(self,ctx):
        if getattr(ctx,'interaction',None):await ctx.defer(ephemeral=True)
        counts={};registry=getattr(self.bot,'session_registry',None)
        if registry:
            pending=registry.refresh_views(ctx.author.id)
            if pending:counts['pending trades/views']=pending
            failed=registry.clear_failed(ctx.author.id)
            if failed:
                for kind in set(failed):
                    command=self.bot.get_command(kind)
                    if command:command.reset_cooldown(ctx)
                counts['failed battle/PvP/training runs']=len(failed)
        clan=await self.store.clan(user_id=ctx.author.id)
        for row in await self.store.sessions():
            st=row['state'];kind=row['kind'];cog=self.bot.get_cog('ClanCog' if kind=='war' else 'CommunityCog')
            belongs=bool(clan and clan['id'] in st['clans']) if kind=='war' else (ctx.author.id==st['host'] or ctx.author.id in [p['user_id'] for p in st['players']])
            if not belongs or row['status'] in ('complete','cancelled','declined'):continue
            task=cog.jobs.get(row['id']) if cog else None
            live=bool(task and not task.done())
            # Awaiting sends/team choices is a healthy war, regardless of idle duration.
            missing=await self._message_missing(row)
            broken=row['status']=='broken' or (row['status']=='running' and not live) or missing
            if row['status']=='running' and live:continue
            try:await self.store.refresh_session(row['id'],ctx.author.id,broken=broken)
            except ValueError:continue
            counts[kind]=counts.get(kind,0)+1
            if cog:await cog.update_board(row['id'])
        raids=self.bot.get_cog('RaidCog')
        if raids:
            pending=await raids.store.pending(ctx.author.id)
            if pending and not any(row['id'] in raids.jobs and not raids.jobs[row['id']].done() for row in pending):
                counts['broken raids']=await raids.store.cancel_pending(ctx.author.id)
        tournament=self.bot.get_cog('TournamentCog')
        if tournament:
            for row in await tournament.store.pending():
                entries=await tournament.store.entries(row['id'])
                if ctx.author.id not in entries and row['creator_id']!=ctx.author.id:continue
                if row['status']=='open':
                    if row['creator_id']==ctx.author.id:await tournament.store.cancel(row['id'],ctx.author.id)
                    else:await tournament.store.leave(row['id'],ctx.author.id)
                elif row['status']=='running':
                    task=tournament.jobs.get(row['id'])
                    if task and not task.done():continue
                    async with self.store.tx() as conn:await conn.execute("UPDATE tournaments SET status='cancelled' WHERE id=? AND status='running'",(row['id'],))
                else:continue
                await tournament.refresh_lobby(row['id']);counts['tournaments']=counts.get('tournaments',0)+1
        # Legacy incomplete gauntlet state can be abandoned; live runs are preserved.
        if not registry or not registry.healthy(ctx.author.id,'gauntlet'):
            async with self.store.tx() as conn:
                cur=await conn.execute("DELETE FROM gauntlet_runs WHERE owner_id=? AND status NOT IN ('complete','claimed')",(ctx.author.id,))
                if cur.rowcount:counts['incomplete gauntlets']=cur.rowcount
        text='Refreshed: '+', '.join(f'{name}: {n}' for name,n in counts.items())+'.' if counts else 'No pending or broken activities to leave.'
        await ctx.send(text+' Functioning wars and matches remain active. Closed broken wars record no win, loss or reward.')


async def setup(bot):await bot.add_cog(UtilityCog(bot))
