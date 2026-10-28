from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..cards import nature_text
from ..abbreviations import school_name
from ..config import GAME_CONFIG
from ..economy import local_date_key, next_daily_at, pack_cost, pull_pack, pull_starter_daily
from ..data import CHAR_BY_ID
from ..progression import resolve_pity_sequence


POSITION_CHOICES = [
    app_commands.Choice(name="Libero (Li)", value="Li"),
    app_commands.Choice(name="Middle Blocker (MB)", value="MB"),
    app_commands.Choice(name="Opposite (OP)", value="OP"),
    app_commands.Choice(name="Setter (S)", value="S"),
    app_commands.Choice(name="Wing Spiker (WS)", value="WS"),
]
SCHOOL_CHOICES = [
    app_commands.Choice(name=school, value=school)
    for school in sorted(GAME_CONFIG["packs"]["school"]["eligible_schools"],key=str.casefold)
]


def pack_embed(title: str, cards, created, coins: int | None = None, *, starter: bool = False) -> discord.Embed:
    lines = []
    for c, inst in zip(cards, created):
        lines.append(
            f'**{c.name}{" - " + c.title if c.title else ""} {c.rarity}★** | **+0** | '
            f'{nature_text(inst, compact=True)} | **ID:** `{inst.instance_id}`'
        )
    e = discord.Embed(title=title, description='\n'.join(lines), color=discord.Color.from_rgb(109, 72, 170))
    if starter:
        e.add_field(
            name='Starter Daily',
            value='Your first-ever `;daily` is guaranteed to contain exactly **one 2★, one 3★, and one 4★** card. This can happen only once per account.',
            inline=False,
        )
    e.add_field(name='Next step', value='Use `;collection` to see your cards or `;cardview <ID>` to inspect one.', inline=False)
    if coins is not None:
        e.set_footer(text=f'Coins remaining: {coins}')
    return e


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _apply_pity(self, owner_id: int, chars, *, pack_type: str, position: str | None = None, school: str | None = None):
        state = await self.bot.db.get_pity(owner_id)
        if not state:
            return list(chars)
        target = CHAR_BY_ID.get(state['character_id'])
        if not target:
            await self.bot.db.clear_pity(owner_id)
            return list(chars)
        eligible = pack_type == 'base'
        if pack_type == 'position':
            eligible = bool(position and target.position.casefold() == position.casefold())
        elif pack_type == 'school':
            eligible = bool(school and target.school.casefold() == school.casefold())
        resolution=resolve_pity_sequence(chars,target,armed=bool(state.get('armed')),target_eligible=eligible)
        if resolution.cleared:
            await self.bot.db.clear_pity(owner_id)
        elif resolution.armed != bool(state.get('armed')):
            await self.bot.db.set_pity_armed(owner_id,resolution.armed)
        return resolution.cards

    async def _create_pack(self, ctx, chars):
        created = []
        for char in chars:
            created.append(await self.bot.db.create_card(ctx.author.id, ctx.author.name, char.id, char.card_id))
        return created

    @commands.hybrid_command(name='daily', aliases=['d'], description='Open your free daily Base Pack.')
    async def daily(self, ctx: commands.Context):
        user = await self.bot.db.get_user(ctx.author.id)
        today = local_date_key()
        if user.last_daily_date == today:
            ts = int(next_daily_at().timestamp())
            await ctx.send(f'You already opened today\'s free pack. Your next `;daily` is available **<t:{ts}:R>** (<t:{ts}:f>).')
            return
        starter = not user.starter_daily_claimed
        chars = pull_starter_daily() if starter else pull_pack('base')
        chars = await self._apply_pity(ctx.author.id, chars, pack_type='base')
        cards = await self._create_pack(ctx, chars)
        await self.bot.db.set_user_fields(
            ctx.author.id,
            last_daily_date=today,
            starter_daily_claimed=1 if starter else int(user.starter_daily_claimed),
        )
        ts = int(next_daily_at().timestamp())
        e = pack_embed('🎁 Starter Daily Pack' if starter else '🎁 Daily Base Pack', chars, cards, starter=starter)
        e.add_field(name='Next daily', value=f'Available <t:{ts}:R>.', inline=False)
        await ctx.send(embed=e)

    @commands.hybrid_command(name='balance', aliases=['bal'], description='Show your coins and how many packs you can afford.')
    async def balance(self, ctx: commands.Context):
        u = await self.bot.db.get_user(ctx.author.id)
        base_cost = pack_cost('base')
        targeted_cost = pack_cost('position')
        e = discord.Embed(title=f'{ctx.author.display_name} — Balance', color=discord.Color.from_rgb(72, 42, 115))
        e.description = f'🪙 **Coins:** {u.coins:,}'
        e.add_field(name='Packs you can buy', value=(
            f'**Base Packs:** {u.coins // base_cost}  (`{base_cost}` 🪙 each)\n'
            f'**Position Packs:** {u.coins // targeted_cost}  (`{targeted_cost}` 🪙 each)\n'
            f'**School Packs:** {u.coins // targeted_cost}  (`{targeted_cost}` 🪙 each)'
        ), inline=False)
        shards=await self.bot.db.get_shards(ctx.author.id)
        if shards:
            e.add_field(name='School Shards', value=' • '.join(f'**{school}:** {qty}' for school,qty in sorted(shards.items()) if qty) or 'None', inline=False)
        pity = await self.bot.db.get_pity(ctx.author.id)
        target = CHAR_BY_ID.get(pity['character_id']) if pity else None
        if target:
            state = ('**Active** — your next '+str(target.rarity)+'★ pull from an eligible pack is guaranteed to be this character.' if pity.get('armed') else '**Inactive** — a same-rarity miss will activate the guarantee.')
            e.add_field(name='Pity Target',value=f'**{target.name} — {target.title} ({target.rarity}★)**\n{state}',inline=False)
        else:
            e.add_field(name='Pity Target',value='**Inactive** — no target set • use `;pity <name>`.',inline=False)
        await ctx.send(embed=e)

    @commands.hybrid_command(name='shop', description='Show pack prices and rarity odds.')
    async def shop(self, ctx: commands.Context):
        p = GAME_CONFIG['packs']
        schools = ', '.join(p['school']['eligible_schools'])
        e = discord.Embed(title='HiQ Battler Pack Shop', color=discord.Color.from_rgb(109, 72, 170))
        e.add_field(name='Base Pack — 300 coins', value='3 random cards\n**30% 1★ • 25% 2★ • 20% 3★ • 15% 4★ • 10% 5★** per card', inline=False)
        e.add_field(name='Position Pack — 450 coins', value='3 cards from MB / S / OP / Li / WS\n**20% 1★ • 20% 2★ • 25% 3★ • 20% 4★ • 15% 5★** per card', inline=False)
        e.add_field(name='School Pack — 450 coins', value=f'3 cards from one eligible school\n**{schools}**\nSame targeted rarity odds as Position Packs.', inline=False)
        e.add_field(name='Boons / Banes', value='Each pull rolls HP, active POW/QIK, DEF, REC, SPD, and JMP independently for its boon and bane. Different rolls give **+3 / −3**; matching rolls cancel into **Neutral**.', inline=False)
        e.add_field(name='Free & earned', value='`;daily` gives one free Base Pack per day. Your first 5 `;battle` wins each day award **50 coins each**, then your next 45 award **4 each** (430 daily).', inline=False)
        await ctx.send(embed=e)

    @commands.hybrid_group(name='pack', aliases=['p'], description='Buy a Base, Position, or School Pack.', invoke_without_command=True)
    async def pack(self, ctx):
        schools = ', '.join(GAME_CONFIG['packs']['school']['eligible_schools'])
        await ctx.send(
            'Use `;pack base`, `;pack position <position>`, or `;pack school <school>`.\n'
            '**Positions:** Li, MB, OP, S, WS\n'
            f'**Schools:** {schools}'
        )

    @pack.command(name='base', aliases=['b'], description='Buy a 3-card Base Pack for 300 coins.')
    async def pack_base(self, ctx):
        cost = pack_cost('base')
        if not await self.bot.db.spend_coins(ctx.author.id, cost):
            await ctx.send(f'You need **{cost} coins** for a Base Pack.')
            return
        chars = pull_pack('base')
        chars = await self._apply_pity(ctx.author.id, chars, pack_type='base')
        cards = await self._create_pack(ctx, chars)
        coins = (await self.bot.db.get_user(ctx.author.id)).coins
        await ctx.send(embed=pack_embed('📦 Base Pack', chars, cards, coins))

    @pack.command(name='position', aliases=['p'], description='Buy a Position Pack for 450 coins.')
    @app_commands.choices(position=POSITION_CHOICES)
    async def pack_position(self, ctx, position: str):
        cost = pack_cost('position')
        try:
            chars = pull_pack('position', position=position)
        except ValueError as e:
            await ctx.send(str(e))
            return
        if not await self.bot.db.spend_coins(ctx.author.id, cost):
            await ctx.send(f'You need **{cost} coins** for a Position Pack.')
            return
        chars = await self._apply_pity(ctx.author.id, chars, pack_type='position', position=position)
        cards = await self._create_pack(ctx, chars)
        coins = (await self.bot.db.get_user(ctx.author.id)).coins
        await ctx.send(embed=pack_embed(f'🎯 {position.upper()} Position Pack', chars, cards, coins))

    @pack.command(name='school', aliases=['s'], description='Buy a School Pack for 450 coins.')
    @app_commands.choices(school=SCHOOL_CHOICES)
    async def pack_school(self, ctx, *, school: str):
        school=school_name(school)
        school=next((s for s in GAME_CONFIG['packs']['school']['eligible_schools'] if s.casefold()==school.casefold()),school)
        cost = pack_cost('school')
        try:
            chars = pull_pack('school', school=school)
        except ValueError as e:
            await ctx.send(str(e))
            return
        if not await self.bot.db.spend_coins(ctx.author.id, cost):
            await ctx.send(f'You need **{cost} coins** for a School Pack.')
            return
        chars = await self._apply_pity(ctx.author.id, chars, pack_type='school', school=school)
        cards = await self._create_pack(ctx, chars)
        coins = (await self.bot.db.get_user(ctx.author.id)).coins
        await ctx.send(embed=pack_embed(f'🏫 {school} School Pack', chars, cards, coins))


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
