from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..cards import nature_text
from ..abbreviations import school_name
from ..config import GAME_CONFIG
from ..economy import local_date_key, next_daily_at, pack_cost
from ..data import CHAR_BY_ID
from ..pack_store import PackStore


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

    @commands.hybrid_command(name='daily', aliases=['d'], description='Open your free daily Base Pack.')
    async def daily(self, ctx: commands.Context):
        opened = await PackStore(self.bot.db).open(ctx.author.id,ctx.author.name,daily=True,day=local_date_key())
        if opened is None:
            ts = int(next_daily_at().timestamp())
            await ctx.send(f'You already opened today\'s free pack. Your next `;daily` is available **<t:{ts}:R>** (<t:{ts}:f>).')
            return
        starter,chars,cards=opened['starter'],opened['chars'],opened['cards']
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
        from ..event_store import EventStore
        wallet=await EventStore(self.bot.db).wallet(ctx.author.id)
        e.add_field(name='AF1 Marks',value=f'{wallet["af1_marks"]:,} · Used by ;fuse. Earn up to 25 per day.',inline=False)
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
        e.add_field(name='Free & earned', value='`;daily` gives one free Base Pack per day. Battle wins award **50 coins each**, up to **450 coins per daily allowance**. Unused Battle allowances roll over within the week.', inline=False)
        e.add_field(name='Consumables',value='**PvP Shield — 500 coins:** `;buy pvpshield [quantity]`\n**Shiny Stone — 10,000 coins:** `;buy shinystone [quantity]`\nItems can be traded. Activate them with `;use ID`.',inline=False)
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
        try:opened=await PackStore(self.bot.db).open(ctx.author.id,ctx.author.name,'base')
        except ValueError as e:await ctx.send(str(e));return
        await ctx.send(embed=pack_embed('📦 Base Pack',opened['chars'],opened['cards'],opened['coins']))

    @pack.command(name='position', aliases=['p'], description='Buy a Position Pack for 450 coins.')
    @app_commands.choices(position=POSITION_CHOICES)
    async def pack_position(self, ctx, position: str):
        try:
            opened=await PackStore(self.bot.db).open(ctx.author.id,ctx.author.name,'position',position=position)
        except ValueError as e:
            await ctx.send(str(e))
            return
        await ctx.send(embed=pack_embed(f'🎯 {position.upper()} Position Pack',opened['chars'],opened['cards'],opened['coins']))

    @pack.command(name='school', aliases=['s'], description='Buy a School Pack for 450 coins.')
    @app_commands.choices(school=SCHOOL_CHOICES)
    async def pack_school(self, ctx, *, school: str):
        school=school_name(school)
        school=next((s for s in GAME_CONFIG['packs']['school']['eligible_schools'] if s.casefold()==school.casefold()),school)
        try:
            opened=await PackStore(self.bot.db).open(ctx.author.id,ctx.author.name,'school',school=school)
        except ValueError as e:
            await ctx.send(str(e))
            return
        await ctx.send(embed=pack_embed(f'🏫 {school} School Pack',opened['chars'],opened['cards'],opened['coins']))


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
