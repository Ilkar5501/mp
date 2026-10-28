from __future__ import annotations

import hashlib
import random

from .config import GAME_CONFIG
from .models import CharacterDef

STAT_KEYS = ('hp', 'pow', 'qik', 'def', 'rec', 'spd', 'jmp')



def roll_nature(character: CharacterDef, seed_text: str) -> tuple[str | None, str | None]:
    """Roll independent, equally likely boon/bane stats for one card instance.

    The legal pool is HP, the character's non-zero offense stat, DEF, REC, SPD, JMP.
    Matching boon/bane rolls cancel into a Neutral card. ``seed_text`` makes a card's
    roll stable across restarts and migrations.
    """
    configured = GAME_CONFIG.get('boon_bane', {}).get(
        'eligible_stats', ['hp', 'offense', 'def', 'rec', 'spd', 'jmp']
    )
    eligible = [character.offense_key if x == 'offense' else x for x in configured]
    zero_offense = 'qik' if character.offense_key == 'pow' else 'pow'
    eligible = [x for x in dict.fromkeys(eligible) if x in STAT_KEYS and x != zero_offense]
    if not eligible:
        return None, None
    digest = hashlib.sha256(seed_text.encode('utf-8')).digest()
    rng = random.Random(int.from_bytes(digest, 'big'))
    boon = rng.choice(eligible)
    bane = rng.choice(eligible)
    if boon == bane and GAME_CONFIG.get('boon_bane', {}).get('neutral_when_same', True):
        return None, None
    return boon, bane

def merge_bonus(character: CharacterDef, merge_level: int) -> dict[str, int]:
    """Return permanent stat gains from merges.

    Merge progression follows the requested five-stat cycle twice:
      +1 offense, +2 DEF, +3 REC, +4 SPD, +5 JMP + HP
      +6 offense, +7 DEF, +8 REC, +9 SPD, +10 JMP + HP

    "offense" always means the character's non-zero POW/QIK stat. The zero offense
    stat is never modified.
    """
    cfg = GAME_CONFIG.get('merge', {})
    max_level = int(cfg.get('max_level', 10))
    level = max(0, min(int(merge_level), max_level))
    cycle = list(cfg.get('stat_cycle', ['offense', 'def', 'rec', 'spd', 'jmp']))
    hp_milestones = {int(x) for x in cfg.get('hp_milestones', [5, 10])}

    bonus = {k: 0 for k in STAT_KEYS}
    if not cycle:
        return bonus

    for step in range(1, level + 1):
        stat = cycle[(step - 1) % len(cycle)]
        if stat == 'offense':
            bonus[character.offense_key] += 1
        elif stat in bonus and stat != ('qik' if character.offense_key == 'pow' else 'pow'):
            bonus[stat] += 1
        if step in hp_milestones:
            bonus['hp'] += 1
    return bonus


def nature_bonus(character: CharacterDef, boon_stat: str | None, bane_stat: str | None) -> dict[str, int]:
    cfg = GAME_CONFIG.get('boon_bane', {})
    amount = int(cfg.get('magnitude', 0))
    bonus = {k: 0 for k in STAT_KEYS}
    if amount <= 0 or not boon_stat or not bane_stat:
        return bonus

    # A character's zero offensive stat is never a legal nature stat. Keep this
    # guard here as well as in generation so stale/manual DB data cannot modify it.
    zero_offense = 'qik' if character.offense_key == 'pow' else 'pow'
    if boon_stat in bonus and boon_stat != zero_offense:
        bonus[boon_stat] += amount
    if bane_stat in bonus and bane_stat != zero_offense:
        bonus[bane_stat] -= amount
    return bonus


def permanent_stats(
    character: CharacterDef,
    merge_level: int,
    boon_stat: str | None = None,
    bane_stat: str | None = None,
) -> dict[str, int]:
    out = {k: int(character.stats.get(k, 0)) for k in STAT_KEYS}
    for source in (merge_bonus(character, merge_level), nature_bonus(character, boon_stat, bane_stat)):
        for k, v in source.items():
            out[k] += v
    return out


def card_bst(character: CharacterDef, merge_level: int, boon_stat: str | None = None, bane_stat: str | None = None) -> int:
    return sum(permanent_stats(character, merge_level, boon_stat, bane_stat).values())
