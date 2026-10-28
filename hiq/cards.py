from __future__ import annotations

from datetime import timezone
import re
import discord

from .config import POW_EMOJI_ID, QIK_EMOJI_ID, POW_EMOJI_NAME, QIK_EMOJI_NAME, GAME_CONFIG
from .data import CHAR_BY_ID
from .models import CardInstance, CharacterDef
from .search import stat_label
from .stats import permanent_stats
from .battle.equipment import add_base_equipment,equipment_key
from .items import ITEM_BY_KEY

DIVIDER = '────────────────────────'


def resolve_custom_emoji(bot: discord.Client, kind: str) -> str:
    if kind == 'pow':
        emoji_id, emoji_name, fallback = POW_EMOJI_ID, POW_EMOJI_NAME, 'POW'
    else:
        emoji_id, emoji_name, fallback = QIK_EMOJI_ID, QIK_EMOJI_NAME, 'QIK'
    # A custom emoji can be rendered directly from its configured name/id even when
    # it is not yet present in discord.py's local emoji cache. This avoids falling
    # back to the old sword/diamond glyphs on startup or in DMs.
    if emoji_id:
        return f'<:{emoji_name}:{emoji_id}>'
    emoji = discord.utils.get(bot.emojis, name=emoji_name)
    return str(emoji) if emoji else fallback


def display_title(c: CharacterDef) -> str:
    return f'{c.name} - {c.title}' if c.title else c.name


def nature_text(card: CardInstance, *, compact: bool = False) -> str:
    amount = int(GAME_CONFIG.get('boon_bane', {}).get('magnitude', 3))
    if not card.boon_stat and not card.bane_stat:
        return '⚪ **Neutral**' if compact else '**Neutral**'
    boon = stat_label(card.boon_stat)
    bane = stat_label(card.bane_stat)
    if compact:
        return f'🟢 **{boon}** / 🔴 **{bane}**'
    return f'🟢 +{amount} {boon} / 🔴 −{amount} {bane}'


def compact_card_line(card: CardInstance) -> str:
    c = CHAR_BY_ID[card.character_id]
    return (
        f'**{display_title(c)} {c.rarity}★** | **+{card.merge_level}** | '
        f'{nature_text(card, compact=True)} | **ID:** `{card.instance_id}`'
    )


def _ability_line(kind: str, name: str, text: str) -> str:
    safe_name = name or 'Unnamed Ability'
    return f'[`{kind}`] **{safe_name}:** *{text or "—"}*'


def ability_block(c: CharacterDef, slot: str) -> str:
    ability = c.abilities[slot]
    kind = 'Ultimate' if slot == 'U' else 'Skill'
    lines = [_ability_line(kind, ability.name, ability.text)]
    notes = []
    tags = set(re.findall(r'\[([^\]]+)\]', ability.text))
    for source_slot, explanation in c.extended.items():
        if not explanation:
            continue
        definition = re.match(r'\[([^\]]+)\]', explanation)
        relevant = definition.group(1) in tags if definition else source_slot == slot
        if relevant and explanation not in notes:
            notes.append(explanation)
    lines.extend('-# ' + note.replace('\n', '\n-# ') for note in notes)
    return '\n'.join(lines)


def card_embed(bot: discord.Client, card: CardInstance) -> discord.Embed:
    c = CHAR_BY_ID[card.character_id]
    stats = add_base_equipment(permanent_stats(c, card.merge_level, card.boon_stat, card.bane_stat),equipment_key(card))
    title = display_title(c)
    pow_emoji = resolve_custom_emoji(bot, 'pow')
    qik_emoji = resolve_custom_emoji(bot, 'qik')
    offense = f'{pow_emoji} **POW:** {stats["pow"]}' if c.offense_key == 'pow' else f'{qik_emoji} **QIK:** {stats["qik"]}'

    u = c.abilities['U']
    body = [
        '### Battle Stats',
        f'❤️ **HP:** {stats["hp"]}',
        offense,
        f'🛡️ **DEF:** {stats["def"]}',
        f'🤲 **REC:** {stats["rec"]}',
        f'💨 **SPD:** {stats["spd"]}',
        f'⬆️ **JMP:** {stats["jmp"]}',
        f'**Merge:** +{card.merge_level}',
        f'**Boon/Bane:** {nature_text(card)}',
        f'**Position:** {c.position}',
        f'**School:** {c.school}',
        DIVIDER,
        '### Abilities',
        ability_block(c, 'U'),
        f'-# `CD`: {c.ult_cd}',
        '',
        ability_block(c, 'A'),
        '',
        ability_block(c, 'B'),
        '',
        ability_block(c, 'C'),
        DIVIDER,
        f'**Original Owner:** {card.original_owner_name}',
        f'**Full Instance ID:** `{card.full_instance_id}`',
        f'**Card ID:** `{card.card_id}`',
        f'**Obtained:** <t:{int(card.obtained_at.replace(tzinfo=timezone.utc).timestamp())}:F>',
    ]
    embed = discord.Embed(
        title=f'{c.rarity}★  {title}',
        description='\n'.join(body)[:4096],
        color=discord.Color.from_rgb(72, 42, 115),
    )
    if c.character_icon:
        embed.set_thumbnail(url=c.character_icon)
    if c.card_art:
        embed.set_image(url=c.card_art)
    role = f' • Class: {c.role_class}' if c.role_class else ''
    item=ITEM_BY_KEY.get(equipment_key(card))
    embed.add_field(name='Equipment',value=(f'**{item["name"]}** • `{card.equipped_item_id}`\n{item["effect"]}' if item else 'Empty'),inline=False)
    embed.set_footer(text=f'Instance ID: {card.instance_id}{role} • Stats include merges, nature and permanent equipment bonuses.')
    return embed


def template_embed(bot: discord.Client, c: CharacterDef) -> discord.Embed:
    stats = {k: int(c.stats.get(k, 0)) for k in ('hp', 'pow', 'qik', 'def', 'rec', 'spd', 'jmp')}
    pow_emoji = resolve_custom_emoji(bot, 'pow')
    qik_emoji = resolve_custom_emoji(bot, 'qik')
    offense = f'{pow_emoji} **POW:** {stats["pow"]}' if c.offense_key == 'pow' else f'{qik_emoji} **QIK:** {stats["qik"]}'
    u = c.abilities['U']
    body = [
        '### Battle Stats',
        f'❤️ **HP:** {stats["hp"]}',
        offense,
        f'🛡️ **DEF:** {stats["def"]}',
        f'🤲 **REC:** {stats["rec"]}',
        f'💨 **SPD:** {stats["spd"]}',
        f'⬆️ **JMP:** {stats["jmp"]}',
        f'**Position:** {c.position}',
        f'**School:** {c.school}',
        DIVIDER,
        '### Abilities',
        ability_block(c, 'U'),
        f'-# `CD`: {c.ult_cd}',
        '',
        ability_block(c, 'A'),
        '',
        ability_block(c, 'B'),
        '',
        ability_block(c, 'C'),
    ]
    embed = discord.Embed(
        title=f'{c.rarity}★  {display_title(c)}',
        description='\n'.join(body)[:4096],
        color=discord.Color.from_rgb(72, 42, 115),
    )
    if c.character_icon:
        embed.set_thumbnail(url=c.character_icon)
    if c.card_art:
        embed.set_image(url=c.card_art)
    role = f' • Class: {c.role_class}' if c.role_class else ''
    embed.set_footer(text=f'Gallery template • Base stats only; no ownership, merges, boons, or banes.{role}')
    return embed
