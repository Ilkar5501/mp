from __future__ import annotations

import asyncio
import json
import random

import discord
from discord import app_commands
from discord.ext import commands

from ..battle.engine import BattleEngine
from ..battle_render import render_battle_summary
from ..config import GAME_CONFIG
from ..abbreviations import mode_name
from ..community_store import CommunityStore, PREFECTURE_DAILY_CAP
from ..data import CHAR_BY_ID
from ..economy import daily_win_reward, local_date_key, next_daily_at, next_week_reset, week_key, weekly_milestone_rewards
from ..gauntlet import gauntlet_sequence, npc_specs, school_order, total_games
from ..progression import TRAINING_SCHOOLS, post50_bonus_increment, post50_bonus_total
from ..ui import EmbedPager

PURPLE = discord.Color.from_rgb(72, 42, 115)
LOG_PAGE_LIMIT = 3600


def _chunk_log(lines: list[str], limit: int = LOG_PAGE_LIMIT) -> list[str]:
    """Split a battle log into Discord-safe embed pages without cutting normal lines."""
    if not lines:
        return ['No battle-log entries were recorded.']
    pages: list[str] = []
    current: list[str] = []
    size = 0
    for raw in lines:
        line = str(raw)
        # Extremely long custom text still needs a hard split to stay below embed limits.
        pieces = [line[i:i + limit - 8] for i in range(0, max(1, len(line)), limit - 8)] or ['']
        for piece in pieces:
            needed = len(piece) + (1 if current else 0)
            if current and size + needed > limit:
                pages.append('\n'.join(current))
                current = []
                size = 0
            current.append(piece)
            size += len(piece) + (1 if len(current) > 1 else 0)
    if current:
        pages.append('\n'.join(current))
    return pages or ['No battle-log entries were recorded.']


class BattleLogPager(discord.ui.View):
    def __init__(self, owner_ids: set[int], title: str, lines: list[str]):
        super().__init__(timeout=600)
        self.owner_ids = set(owner_ids)
        self.title = title
        self.pages = _chunk_log(lines)
        self.page = 0
        self._sync()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.owner_ids:
            await interaction.response.send_message('This battle log belongs to the players in that match.', ephemeral=True)
            return False
        return True

    def embed(self) -> discord.Embed:
        e = discord.Embed(
            title=self.title,
            description=self.pages[self.page],
            color=PURPLE,
        )
        e.set_footer(text=f'Battle log • Page {self.page + 1}/{len(self.pages)}')
        return e

    def _sync(self):
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= len(self.pages) - 1

    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji='▶️', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(self.pages) - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='Close', style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content='Battle log closed.', embed=None, view=None)


class BattleLogLauncher(discord.ui.View):
    def __init__(self, owner_ids: set[int], title: str, lines: list[str], *, label: str = 'View Full Battle Log'):
        super().__init__(timeout=900)
        self.owner_ids = set(owner_ids)
        self.log_title = title
        self.lines = list(lines)
        self.open_log.label = label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.owner_ids:
            await interaction.response.send_message('This battle log belongs to the players in that match.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='View Full Battle Log', emoji='📜', style=discord.ButtonStyle.primary)
    async def open_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        pager = BattleLogPager(self.owner_ids, self.log_title, self.lines)
        await interaction.response.send_message(embed=pager.embed(), view=pager, ephemeral=True)


class GauntletSchoolSelect(discord.ui.Select):
    def __init__(self, parent: 'GauntletPerformanceView'):
        self.parent_view=parent
        schools=[]
        for record in parent.records:
            if record['school'] not in schools:
                schools.append(record['school'])
        options=[discord.SelectOption(label=school,value=school,default=(school==parent.school)) for school in sorted(schools,key=str.casefold)]
        super().__init__(placeholder='Choose a school…',options=options,row=0)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.school=self.values[0]; self.parent_view.game_index=0; self.parent_view.rebuild()
        await self.parent_view.update(interaction)


class GauntletPerformanceView(discord.ui.View):
    def __init__(self, owner_id: int, records: list[dict], cog: 'BattleCog', mode_title: str):
        super().__init__(timeout=900); self.owner_id=owner_id; self.records=records; self.cog=cog; self.mode_title=mode_title
        self.school=records[0]['school'] if records else ''; self.game_index=0; self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('This gauntlet performance panel belongs to the player who ran it.',ephemeral=True); return False
        return True

    def school_records(self): return [r for r in self.records if r['school']==self.school]

    def rebuild(self):
        self.clear_items(); self.add_item(GauntletSchoolSelect(self))
        games=self.school_records(); self.game_index=max(0,min(self.game_index,len(games)-1))
        prev=discord.ui.Button(emoji='◀️',label='Previous Game',style=discord.ButtonStyle.secondary,row=1,disabled=self.game_index<=0)
        nxt=discord.ui.Button(emoji='▶️',label='Next Game',style=discord.ButtonStyle.secondary,row=1,disabled=self.game_index>=len(games)-1)
        async def pcb(i): self.game_index=max(0,self.game_index-1); self.rebuild(); await self.update(i)
        async def ncb(i): self.game_index=min(len(self.school_records())-1,self.game_index+1); self.rebuild(); await self.update(i)
        prev.callback=pcb; nxt.callback=ncb; self.add_item(prev); self.add_item(nxt)
        logs=discord.ui.Button(label='View Full Battle Log',emoji='📜',style=discord.ButtonStyle.primary,row=2)
        async def logcb(i):
            r=self.school_records()[self.game_index]
            pager=BattleLogPager({self.owner_id},f'{self.mode_title} — {self.school} G{r["game_no"]}',r['result'].log)
            await i.response.send_message(embed=pager.embed(),view=pager,ephemeral=True)
        logs.callback=logcb;self.add_item(logs)

    def embed(self):
        games=self.school_records()
        if not games: return discord.Embed(title='No gauntlet game data',color=PURPLE)
        r=games[self.game_index]
        e=self.cog._result_embed(r['result'],'You',r['school'],r['a_specs'],r['b_specs'])
        e.title=f'{self.mode_title} • {r["school"]} G{r["game_no"]} • {r["result_word"]}'
        e.set_footer(text='Use the school menu, game arrows and full battle log.')
        return e

    async def payload(self):
        r=self.school_records()[self.game_index]
        image=await render_battle_summary(r['result'],r['a_specs'],r['b_specs'],'You',r['school'])
        file=discord.File(image,filename='gauntlet.png');e=self.embed();e.set_image(url='attachment://gauntlet.png');return e,file

    async def update(self,interaction):
        await interaction.response.defer();e,file=await self.payload()
        await interaction.edit_original_response(embed=e,attachments=[file],view=self)


class BattleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _saved_team_specs(self, owner_id: int, slot: int | None = None):
        team = await (self.bot.db.get_saved_team(owner_id, slot) if slot else self.bot.db.active_team(owner_id))
        if not team or not team.complete:
            return None, team
        specs = []
        for iid, lane in [(iid,lane) for _,iid,lane in team.entries]:
            card = await self.bot.db.get_card(iid)
            if not card:
                return None, team
            specs.append((card, CHAR_BY_ID[card.character_id], lane))
        return specs, team

    async def _award_win(self, user_id: int) -> tuple[int, int, int, int]:
        from ..rewards import RewardStore
        reward=await RewardStore(self.bot.db).award(user_id,'battle')
        return reward['coins'],reward['coins'],0,0

    @staticmethod
    def _uid(side: str, index: int, char_id: str) -> str:
        return f'{side}{index}:{char_id}'

    @staticmethod
    def _lane_label(lane: str) -> str:
        return 'Frontline' if lane == 'front' else 'Backline'

    def _team_stat_lines(self, specs, side: str, result) -> str:
        lines = []
        for i, (card, c, lane) in enumerate(specs):
            s = result.stats.get(self._uid(side, i, c.id), {})
            merge = f' +{card.merge_level}' if card else ''
            lines.append(
                f'**{self._lane_label(lane)} • {c.name}{merge}**\n'
                f'↳ {s.get("attacks_made", 0)} attacks • {s.get("attacks_tanked", 0)} attacks tanked • '
                f'{s.get("damage_dealt", 0)} damage dealt • {s.get("damage_taken", 0)} damage tanked • '
                f'{s.get("damage_mitigated", 0)} mitigated'
            )
        return '\n'.join(lines)

    def _award_candidates(self, result, a_specs, b_specs):
        rows = []
        for side, specs in (('A', a_specs), ('B', b_specs)):
            for i, (_, c, lane) in enumerate(specs):
                s = result.stats.get(self._uid(side, i, c.id), {})
                rows.append((side, c, lane, s))
        return rows

    def _opom(self, result, a_specs, b_specs):
        # Offensive Player of the Match is shared across both teams.
        return max(
            self._award_candidates(result, a_specs, b_specs),
            key=lambda row: (
                row[3].get('damage_dealt', 0),
                row[3].get('crits', 0),
                -row[3].get('attacks_made', 0),
                row[1].name,
            ),
        )

    def _dpom(self, result, a_specs, b_specs):
        # Pressure absorbed = real damage taken + damage prevented by defense/DR/barriers.
        # Separating this from OPOM avoids the old bruiser-biased single-MVP score.
        return max(
            self._award_candidates(result, a_specs, b_specs),
            key=lambda row: (
                row[3].get('damage_taken', 0) + row[3].get('damage_mitigated', 0),
                row[3].get('damage_mitigated', 0),
                row[3].get('attacks_tanked', 0),
                row[1].name,
            ),
        )

    def _result_embed(self, result, name_a, name_b, a_specs, b_specs, reward=0, daily_reward=0, weekly_reward=0, bonus_reward=0):
        if result.winner_side == 'A':
            winner = name_a
        elif result.winner_side == 'B':
            winner = name_b
        else:
            winner = 'Draw'
        e = discord.Embed(title=f'🏐 Battle Result — {winner}', color=PURPLE)
        e.add_field(
            name=f'Your Team — {"🏆 " if result.winner_side=="A" else ""}{name_a} • {result.team_a_hp} HP',
            value='\u200b',
            inline=False,
        )
        e.add_field(
            name=f'Opponent — {"🏆 " if result.winner_side=="B" else ""}{name_b} • {result.team_b_hp} HP',
            value='\u200b',
            inline=False,
        )

        op_side, op_char, op_lane, op_stats = self._opom(result, a_specs, b_specs)
        dp_side, dp_char, dp_lane, dp_stats = self._dpom(result, a_specs, b_specs)
        op_owner = name_a if op_side == 'A' else name_b
        dp_owner = name_a if dp_side == 'A' else name_b
        e.add_field(
            name='⚔️ OPOM — Offensive Player of the Match',
            value=(
                f'**{op_char.name}** • {op_owner} • {self._lane_label(op_lane)}\n'
                f'**{op_stats.get("damage_dealt", 0)}** damage dealt across {op_stats.get("attacks_made", 0)} attacks'
            ),
            inline=False,
        )
        defensive_pressure = dp_stats.get('damage_taken', 0) + dp_stats.get('damage_mitigated', 0)
        e.add_field(
            name='🛡️ DPOM — Defensive Player of the Match',
            value=(
                f'**{dp_char.name}** • {dp_owner} • {self._lane_label(dp_lane)}\n'
                f'**{defensive_pressure}** defensive pressure absorbed = '
                f'{dp_stats.get("damage_taken", 0)} damage tanked + {dp_stats.get("damage_mitigated", 0)} mitigated'
            ),
            inline=False,
        )
        e.add_field(name='Turns', value=str(result.turns), inline=True)
        if reward:
            parts = []
            if daily_reward:
                parts.append(f'{daily_reward} daily')
            if weekly_reward:
                parts.append(f'{weekly_reward} weekly milestone')
            if bonus_reward:
                parts.append(f'{bonus_reward} post-50 bonus')
            e.add_field(name='Coins earned', value=f'🪙 **{reward}** ({" + ".join(parts)})', inline=False)
        return e

    async def _finish_battle(self, ctx, engine, a_specs, b_specs, owner_b, name_a, name_b, *, reward_regular_win: bool):
        if getattr(ctx,'interaction',None) and not ctx.interaction.response.is_done():
            await ctx.defer()
        result = await asyncio.to_thread(engine.run)
        reward = daily = weekly = bonus = 0
        if reward_regular_win and result.winner_side == 'A':
            reward, daily, weekly, bonus = await self._award_win(ctx.author.id)
        e = self._result_embed(result, name_a, name_b, a_specs, b_specs, reward, daily, weekly, bonus)
        allowed = {ctx.author.id}
        if owner_b:
            allowed.add(owner_b)
        view = BattleLogLauncher(allowed, f'{name_a} vs {name_b} — Full Battle Log', result.log)
        image = await render_battle_summary(result, a_specs, b_specs, name_a, name_b)
        file = discord.File(image, filename='battle_summary.png')
        e.set_image(url='attachment://battle_summary.png')
        return result, e, view, file

    @commands.hybrid_command(name='battle', aliases=['fight'], description='Fight a fresh random neutral CPU team.')
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    async def battle(self, ctx, slot: int | None = None):
        a_specs, _ = await self._saved_team_specs(ctx.author.id, slot)
        if not a_specs:
            await ctx.send('You need a complete team first. Use `;teamcreate 1`, choose **3v3 (1 front + 2 back)** or **6v6 (3 front + 3 back)** and fill every slot.')
            return
        rng = random.Random()
        b_specs = BattleEngine.cpu_team(rng,size=len(a_specs))
        engine = BattleEngine(
            a_specs,
            b_specs,
            owner_a=ctx.author.id,
            owner_b=None,
            name_a=ctx.author.display_name,
            name_b='CPU',
            mode='battle',
        )
        _, e, view, file = await self._finish_battle(
            ctx, engine, a_specs, b_specs, None, ctx.author.display_name, 'CPU', reward_regular_win=True
        )
        e.set_footer(
            text=f'{len(a_specs)}v{len(a_specs)} • CPU cards are neutral. Selected Tanks lead the frontline, followed by the best DEF+REC profiles.'
        )
        await ctx.send(embed=e, view=view, file=file)

    @commands.hybrid_command(name='pvp', description='Fight another player using both active teams.')
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    async def pvp(self, ctx, opponent: discord.Member):
        if opponent.id == ctx.author.id or opponent.bot:
            await ctx.send('Choose another player.')
            return
        a_specs, _ = await self._saved_team_specs(ctx.author.id, None)
        b_specs, _ = await self._saved_team_specs(opponent.id, None)
        if not a_specs:
            await ctx.send('You need a complete team first. Use `;teamcreate`.')
            return
        if not b_specs:
            await ctx.send(f'**{opponent.display_name}** does not have a complete active team.')
            return
        if len(a_specs)!=len(b_specs):
            notice=f'Team formats conflict: your team is {len(a_specs)}v{len(a_specs)}, while {opponent.display_name} uses {len(b_specs)}v{len(b_specs)}. Both teams must use the same format.'
            if getattr(ctx,'interaction',None):
                await ctx.send(notice,ephemeral=True)
            else:
                try: await ctx.author.send(notice)
                except discord.HTTPException:
                    await ctx.send('I could not send you a DM. Use `/pvp` for a private format check.',delete_after=10)
            return
        engine = BattleEngine(
            a_specs,
            b_specs,
            owner_a=ctx.author.id,
            owner_b=opponent.id,
            name_a=ctx.author.display_name,
            name_b=opponent.display_name,
            mode='pvp',
        )
        result, e, view, file = await self._finish_battle(
            ctx, engine, a_specs, b_specs, opponent.id, ctx.author.display_name, opponent.display_name, reward_regular_win=False
        )
        from ..rewards import RewardStore
        if result.winner_side in ('A','B'):
            winner,loser=(ctx.author.id,opponent.id) if result.winner_side=='A' else (opponent.id,ctx.author.id)
            reward=await RewardStore(self.bot.db).award(winner,'pvp',opponent=loser)
            e.add_field(name='PvP Reward',value=f'<@{winner}>: **+{reward["coins"]} coins**'+(' • already defeated this player today.' if reward['duplicate'] else ' • daily cap: 400 coins.'),inline=False)
        e.set_footer(text='50 coins per unique opponent defeated today; maximum 400. Repeated opponents give no reward.')
        await ctx.send(embed=e, view=view, file=file)

    @commands.hybrid_command(name='questview', aliases=['quests', 'quest','qv'], description='View daily currency rewards and weekly shard progress.')
    async def questview(self, ctx):
        from ..rewards import RewardStore
        from ..raid_store import RaidStore
        today=local_date_key();uid=ctx.author.id
        progress=await RewardStore(self.bot.db).daily(uid,today)
        e=discord.Embed(title=f'{ctx.author.display_name} — Daily Rewards',color=PURPLE)
        rules=[('battle','Battle',50,330,'First 5 wins: 30 each; next 45: 4 each.'),
               ('tournament','Tournament championships',3,450,'100 with CPUs / 150 with eight humans; shared three-win allowance.'),
               ('gauntlet_regional','Regional clears',2,150,'First clear: 100; second: 50.'),
               ('gauntlet_national','National clears',2,150,'First clear: 100; second: 50.'),
               ('raid','Raid',6,300,'One eligible reward per win: shards, 50 coins, or 25 clan coins. Currency caps: 300 coins and 100 clan coins/day.'),
               ('pvp','PvP',8,400,'50 per distinct opponent defeated today; repeat opponents give 0.'),
               ('prefecture','Prefecture',6,300,'50 per win.'),
               ('war','Clan-war victories',1,300,'300 coins + 100 clan coins for one victory; losses give 0.')]
        for key,label,limit,cap,text in rules:
            row=progress.get(key,{})
            if key=='raid':
                e.add_field(name=label,value=f'**{row.get("coins",0)}/300 coins • {row.get("clan_coins",0)}/100 clan coins**\n'+text,inline=False)
                continue
            e.add_field(name=label,value=f'**{min(limit,row.get("wins",0))}/{limit} wins • {row.get("coins",0)} coins**'+(' (maximum 300–450)' if key=='tournament' else f' / {cap}')+'\n'+text,inline=False)
        nxt=int(next_daily_at().timestamp())
        e.set_footer(text='Weekly battle milestones and post-50 weekly bonuses have been replaced by these daily rewards.')
        e.description=f'Daily rewards reset <t:{nxt}:R>. All game formats share each mode’s allowance.'
        extra=discord.Embed(title=f'{ctx.author.display_name} — Clan Coins & Shards',color=PURPLE)
        user=await self.bot.db.get_user(uid)
        extra.add_field(name='Daily Pack',value='Claimed today.' if user.last_daily_date==today else 'Available with ;daily.',inline=False)
        clan=await CommunityStore(self.bot.db).progress(uid)
        extra.add_field(name='Clan Coins',value=f'Check-in: 100; all five members checking in grants another 100.\nCheck-in today: {"done" if clan["checked_in"] else "available if in a clan"}.\nRaid coins: **{progress.get("raid",{}).get("clan_coins",0)}/100** • War coins: **{clan["war"]}/100** today.\nDonations: **{clan["donations"]}/3** today.',inline=False)
        shards=await RaidStore(self.bot.db).shard_progress(uid)
        extra.add_field(name='Raid Shards',value=f'Character shards: **{shards.get("character_shard",0)}/50 today** and **{shards.get("character_shard_weekly",0)}/350 this week**.\nEach item: up to twice its recipe cost in shards per day; see ;items.',inline=False)
        training=[];balances=await self.bot.db.get_shards(uid)
        for school in TRAINING_SCHOOLS:
            wins=await self.bot.db.training_wins(uid,school,week_key())
            if wins or balances.get(school,0):training.append(f'**{school}:** {wins}/9 weekly • {balances.get(school,0)} shards')
        if training:extra.add_field(name='Training / School Shards',value='\n'.join(training),inline=False)
        pager=EmbedPager([e,extra],uid)
        await ctx.send(embed=pager.embed(),view=pager)

    @commands.hybrid_command(name='gauntlet', aliases=['gn'], description='Run the Regional or National school gauntlet.')
    @app_commands.choices(mode=[app_commands.Choice(name='Regional',value='regional'),app_commands.Choice(name='National',value='national')])
    @commands.cooldown(1, 10.0, commands.BucketType.user)
    async def gauntlet(self, ctx, mode: str):
        mode = mode_name(mode)
        if mode in ('regionals', 'region'):
            mode = 'regional'
        elif mode in ('nationals', 'nation'):
            mode = 'national'
        if mode not in ('regional', 'national'):
            await ctx.send('Use `;gauntlet regional` or `;gauntlet national`.')
            return

        wk=week_key()
        a_specs, team = await self._saved_team_specs(ctx.author.id, None)
        if not a_specs or not team:
            await ctx.send('Your **active team** must be complete before entering this gauntlet. Use `;teamuse <slot>` to choose a complete team first.')
            return

        if team.team_format != 3:
            await ctx.send('Regional and National Gauntlets are **3v3**. Select a complete 3v3 team with `;teamuse <slot>` first.'); return
        sequence = gauntlet_sequence(mode)
        total = total_games(mode)
        mode_title = 'Regional' if mode == 'regional' else 'National'
        progress = discord.Embed(
            title=f'🏆 {mode_title} Gauntlet — Run Started',
            description=(
                f'**Team:** Slot {team.slot} — {team.name}\n'
                f'You must win **all {total} matches**. One loss or draw ends the run.\n'
                f'Order: **{" → ".join(school_order(mode))}**'
            ),
            color=PURPLE,
        )
        status_message = await ctx.send(embed=progress)

        records=[]
        wins=0
        failed_at=None
        for index,(school,game_no) in enumerate(sequence,start=1):
            b_specs=npc_specs(mode,school,game_no)
            opponent_name=f'{school} — G{game_no}'
            engine=BattleEngine(a_specs,b_specs,owner_a=ctx.author.id,owner_b=None,name_a=ctx.author.display_name,name_b=opponent_name,mode=f'gauntlet_{mode}')
            result=await asyncio.to_thread(engine.run)
            won=result.winner_side=='A'
            result_word='WIN' if won else ('DRAW' if result.winner_side=='draw' else 'LOSS')
            records.append({'school':school,'game_no':game_no,'won':won,'result_word':result_word,'result':result,'a_specs':a_specs,'b_specs':b_specs})
            if won: wins+=1
            else:
                failed_at=(school,game_no); break
            next_is_new_school=index==total or sequence[index][0]!=school
            if next_is_new_school:
                progress.description=f'**Team:** Slot {team.slot} — {team.name}\nProgress: **{wins}/{total} wins**\nCleared through **{school} G{game_no}**…'
                try: await status_message.edit(embed=progress)
                except discord.HTTPException: pass

        completed=wins==total and failed_at is None
        reward=0
        if completed:
            from ..rewards import RewardStore
            reward=(await RewardStore(self.bot.db).award(ctx.author.id,'gauntlet_'+mode))['coins']

        color=discord.Color.gold() if completed else discord.Color.red()
        final=discord.Embed(
            title=f'🏆 {mode_title} Champion — {ctx.author.display_name}' if completed else f'❌ {mode_title} Gauntlet Ended',
            description=f'**Final record: {wins}-{len(records)-wins}** • {wins}/{total} required wins\n'+('You defeated every team in the gauntlet.' if completed else f'Your run ended at **{failed_at[0]} G{failed_at[1]}**. You may retry immediately.'),
            color=color,
        )
        for school in school_order(mode):
            rows=[r for r in records if r['school']==school]
            if not rows: value='Not reached.'
            else: value='\n'.join(f'{"✅" if r["won"] else "❌"} **G{r["game_no"]}:** {r["result_word"]} • {r["result"].team_a_hp}-{r["result"].team_b_hp} HP • {r["result"].turns}T' for r in rows)
            final.add_field(name=school,value=value,inline=True)
        if completed:
            reset=int(next_daily_at().timestamp())
            final.add_field(name='Champion Reward',value=(f'🪙 **+{reward} coins**' if reward else 'Daily rewards already claimed • **0 additional coins**') + f'\nYou can replay immediately. Next daily reward <t:{reset}:R>.',inline=False)
        final.set_footer(text='Use the school selector and game arrows below to inspect per-game player performance. Gauntlet opponents are Neutral.')
        view=GauntletPerformanceView(ctx.author.id,records,self,mode_title) if records else None
        try: await status_message.edit(embed=final,view=None)
        except discord.HTTPException: await ctx.send(embed=final)
        if view:
            embed,file=await view.payload()
            await ctx.send(embed=embed,file=file,view=view)

async def setup(bot):
    await bot.add_cog(BattleCog(bot))
