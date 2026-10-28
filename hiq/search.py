from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .abbreviations import school_name
from .data import character_search_score,match_roster_names
from .query_text import split_query
from .models import CardInstance, CharacterDef
from .stats import permanent_stats

STAT_ALIASES = {
    'hp': 'hp',
    'pow': 'pow',
    'qik': 'qik',
    'def': 'def',
    'defense': 'def',
    'rec': 'rec',
    'receive': 'rec',
    'spd': 'spd',
    'speed': 'spd',
    'jmp': 'jmp',
    'jump': 'jmp',
}


def stat_label(stat: str | None) -> str:
    if not stat:
        return '—'
    return stat.upper()


@dataclass(slots=True)
class NumericFilter:
    key: str
    op: str
    value: int

    def matches(self, actual: int) -> bool:
        if self.op in ('=', '=='):
            return actual == self.value
        if self.op == '>':
            return actual > self.value
        if self.op == '>=':
            return actual >= self.value
        if self.op == '<':
            return actual < self.value
        if self.op == '<=':
            return actual <= self.value
        return False


@dataclass(slots=True)
class SearchFilters:
    name: str = ''
    instance_id: str = ''
    school: str = ''
    position: str = ''
    role_class: str = ''
    effect: str = ''
    boon: str = ''
    bane: str = ''
    rarity: NumericFilter | None = None
    merge: NumericFilter | None = None
    stats: list[NumericFilter] = field(default_factory=list)


def _numeric_expr(key: str, raw: str) -> NumericFilter | None:
    m = re.fullmatch(r'\s*(<=|>=|==|=|<|>)?\s*(-?\d+)\s*', raw)
    if not m:
        return None
    return NumericFilter(key, m.group(1) or '=', int(m.group(2)))



SHORTHAND_KEYS = {
    '-n': 'name',
    '-pos': 'position',
    '-tm': 'school',
    '-class': 'class',
    '-cl': 'class',
    '-star': 'star',
    '-st': 'star',
    '-effect': 'effect',
    '-eff': 'effect',
    '-hp': 'hp',
    '-pow': 'pow',
    '-qik': 'qik',
    '-def': 'def',
    '-rec': 'rec',
    '-spd': 'spd',
    '-jmp': 'jmp',
}

def expand_shorthand(raw: str) -> str:
    """Translate user-friendly dash filters into the existing colon syntax.

    Examples: ``-n oikawa``, ``-spd > 30``, ``-tm Aoba Johsai``.
    The original colon-based parser remains authoritative after expansion.
    """
    if not raw.strip():
        return raw
    try:
        tokens=split_query(raw)
    except ValueError:
        tokens=raw.split()
    if not any(token.casefold() in SHORTHAND_KEYS for token in tokens):
        return raw
    out=[]
    i=0
    while i < len(tokens):
        tok=tokens[i]
        key=SHORTHAND_KEYS.get(tok.casefold())
        if not key:
            out.append(tok); i+=1; continue
        i+=1
        if key in STAT_ALIASES or key == 'star':
            if i>=len(tokens):
                out.append(tok); continue
            op='='
            if tokens[i] in ('>','<','>=','<=','='):
                op=tokens[i]; i+=1
            if i>=len(tokens):
                out.append(tok); continue
            value=tokens[i]; i+=1
            # Allow compact values such as >30 even if operator was omitted above.
            if re.match(r'^(<=|>=|=|<|>)', value):
                out.append(f'{key}:{value}')
            else:
                out.append(f'{key}:{op}{value}')
            continue
        # Text filters consume until the next recognized shorthand flag.
        vals=[]
        while i < len(tokens) and tokens[i].casefold() not in SHORTHAND_KEYS:
            vals.append(tokens[i]); i+=1
        if vals:
            # Quote multi-word text values so the second shlex pass keeps them together.
            value = " ".join(vals).replace('"', '\"')
            out.append(f'{key}:"{value}"')
    return ' '.join(out)

def parse_filters(raw: str, *, gallery: bool = False) -> SearchFilters:
    raw = expand_shorthand(raw)
    f = SearchFilters()
    if not raw.strip():
        return f
    try:
        tokens = split_query(raw)
    except ValueError:
        tokens = raw.split()
    bare: list[str] = []
    for token in tokens:
        if ':' not in token:
            bare.append(token)
            continue
        key, value = token.split(':', 1)
        key = key.casefold().strip()
        value = value.strip()
        if key in ('name', 'character', 'title'):
            f.name = value
        elif key in ('id', 'card', 'instance') and not gallery:
            f.instance_id = value
        elif key == 'school':
            f.school = value
        elif key in ('position', 'pos'):
            f.position = value.upper()
        elif key in ('class', 'cl', 'role'):
            f.role_class = 'support' if value.casefold() == 'sup' else value
        elif key in ('effect', 'eff'):
            f.effect = value
        elif key == 'boon' and not gallery:
            f.boon = STAT_ALIASES.get(value.casefold(), value.casefold())
        elif key == 'bane' and not gallery:
            f.bane = STAT_ALIASES.get(value.casefold(), value.casefold())
        elif key in ('star', 'stars', 'rarity'):
            f.rarity = _numeric_expr('rarity', value)
        elif key == 'merge' and not gallery:
            f.merge = _numeric_expr('merge', value)
        elif key == 'stat':
            m = re.fullmatch(r'([A-Za-z]+)\s*(<=|>=|==|=|<|>)\s*(-?\d+)', value)
            if m and m.group(1).casefold() in STAT_ALIASES:
                f.stats.append(NumericFilter(STAT_ALIASES[m.group(1).casefold()], m.group(2), int(m.group(3))))
        elif key in STAT_ALIASES:
            nf = _numeric_expr(STAT_ALIASES[key], value)
            if nf:
                f.stats.append(nf)
        else:
            bare.append(token)
    if bare and not f.name:
        f.name = ' '.join(bare)
    return f


def _contains_ci(value: str, query: str) -> bool:
    return query.casefold() in value.casefold()


def _role_classes(value: str) -> set[str]:
    """Return each independently searchable class from a role label.

    Values such as ``Tank/Support`` or ``DPS + Support`` therefore match either
    component without requiring the combined label verbatim.
    """
    return {
        part.strip().casefold()
        for part in re.split(r'[/,+&|]+', value or '')
        if part.strip()
    }


def _matches_role(c: CharacterDef, query: str) -> bool:
    wanted = 'support' if query.casefold().strip() == 'sup' else query.casefold().strip()
    return wanted in _role_classes(c.role_class)


def _matches_effect(c: CharacterDef, query: str) -> bool:
    # Ability names and extended tooltip definitions are intentionally excluded.
    effect_text = ' '.join(ability.text for ability in c.abilities.values())
    return _contains_ci(effect_text, query)


def filter_characters(characters: list[CharacterDef], filters: SearchFilters) -> list[CharacterDef]:
    out = []
    matched={c.id for c in match_roster_names(filters.name)} if filters.name else None
    for c in characters:
        if filters.school and not _contains_ci(c.school, school_name(filters.school)):
            continue
        if filters.position and c.position.casefold() != filters.position.casefold():
            continue
        if filters.role_class and not _matches_role(c, filters.role_class):
            continue
        if filters.effect and not _matches_effect(c, filters.effect):
            continue
        if filters.rarity and not filters.rarity.matches(c.rarity):
            continue
        if filters.stats and any(not sf.matches(int(c.stats.get(sf.key, 0))) for sf in filters.stats):
            continue
        if matched is not None and c.id not in matched:
            continue
        out.append(c)
    if filters.name:
        out.sort(key=lambda c: (c.name.casefold(),c.title.casefold()))
    else:
        out.sort(key=lambda c: (c.name.casefold(), c.title.casefold()))
    return out


def filter_cards(cards: list[CardInstance], char_by_id: dict[str, CharacterDef], filters: SearchFilters) -> list[CardInstance]:
    out = []
    matched={c.id for c in match_roster_names(filters.name)} if filters.name else None
    for card in cards:
        c = char_by_id[card.character_id]
        if filters.instance_id and filters.instance_id.casefold() not in card.instance_id.casefold() and filters.instance_id.casefold() not in card.full_instance_id.casefold():
            continue
        if filters.school and not _contains_ci(c.school, school_name(filters.school)):
            continue
        if filters.position and c.position.casefold() != filters.position.casefold():
            continue
        if filters.role_class and not _matches_role(c, filters.role_class):
            continue
        if filters.effect and not _matches_effect(c, filters.effect):
            continue
        if filters.boon and (card.boon_stat or '').casefold() != filters.boon.casefold():
            continue
        if filters.bane and (card.bane_stat or '').casefold() != filters.bane.casefold():
            continue
        if filters.rarity and not filters.rarity.matches(c.rarity):
            continue
        if filters.merge and not filters.merge.matches(card.merge_level):
            continue
        stats = permanent_stats(c, card.merge_level, card.boon_stat, card.bane_stat)
        if filters.stats and any(not sf.matches(stats.get(sf.key, 0)) for sf in filters.stats):
            continue
        if matched is not None and c.id not in matched:
            continue
        out.append(card)
    if filters.name:
        out.sort(key=lambda card: (char_by_id[card.character_id].name.casefold(),char_by_id[card.character_id].title.casefold(),card.instance_id))
    return out


def filters_summary(f: SearchFilters) -> str:
    parts = []
    if f.name:
        parts.append(f'name≈{f.name}')
    if f.instance_id:
        parts.append(f'id={f.instance_id}')
    if f.school:
        parts.append(f'school={f.school}')
    if f.position:
        parts.append(f'position={f.position}')
    if f.role_class:
        parts.append(f'class={f.role_class}')
    if f.effect:
        parts.append(f'effect~{f.effect}')
    if f.boon:
        parts.append(f'boon={stat_label(f.boon)}')
    if f.bane:
        parts.append(f'bane={stat_label(f.bane)}')
    for nf in [f.rarity, f.merge, *f.stats]:
        if nf:
            parts.append(f'{nf.key}{nf.op}{nf.value}')
    return ', '.join(parts) if parts else 'no filters'
