from __future__ import annotations

import random
from datetime import datetime, timedelta

from .config import GAME_CONFIG, TIMEZONE
from .data import CHARACTERS
from .models import CharacterDef
from .abbreviations import school_name


def local_now() -> datetime:
    return datetime.now(TIMEZONE)


def local_date_key() -> str:
    return local_now().date().isoformat()


def next_daily_at() -> datetime:
    now = local_now()
    tomorrow = now.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=TIMEZONE)


def week_key_for(moment: datetime) -> str:
    d = moment.astimezone(TIMEZONE).date() if moment.tzinfo else moment.replace(tzinfo=TIMEZONE).date()
    iso = d.isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def week_key() -> str:
    return week_key_for(local_now())


def next_week_reset_from(moment: datetime) -> datetime:
    now = moment.astimezone(TIMEZONE) if moment.tzinfo else moment.replace(tzinfo=TIMEZONE)
    days = (7 - now.weekday()) % 7
    if days == 0:
        days = 7
    target = now.date() + timedelta(days=days)
    return datetime(target.year, target.month, target.day, tzinfo=TIMEZONE)


def next_week_reset() -> datetime:
    return next_week_reset_from(local_now())


def _weighted_rarity(odds: dict[str, int], available: set[int], rng: random.Random) -> int:
    pairs = [(int(r), w) for r, w in odds.items() if int(r) in available and w > 0]
    if not pairs:
        raise ValueError('No eligible rarity exists for this pack.')
    total = sum(w for _, w in pairs)
    roll = rng.uniform(0, total)
    upto = 0.0
    for rarity, weight in pairs:
        upto += weight
        if roll <= upto:
            return rarity
    return pairs[-1][0]


def _draw_from_pool(pool: list[CharacterDef], rarities: list[int], rng: random.Random) -> list[CharacterDef]:
    results: list[CharacterDef] = []
    used_names: set[str] = set()
    for rarity in rarities:
        candidates = [c for c in pool if c.rarity == rarity and c.name not in used_names]
        if not candidates:
            candidates = [c for c in pool if c.rarity == rarity]
        if not candidates:
            raise ValueError(f'No {rarity}★ card exists in this pool.')
        pick = rng.choice(candidates)
        results.append(pick)
        used_names.add(pick.name)
    return results


def pull_starter_daily(rng: random.Random | None = None) -> list[CharacterDef]:
    """First-ever daily: exactly one 2★, one 3★, and one 4★ card."""
    rng = rng or random.Random()
    results = _draw_from_pool(list(CHARACTERS), [2, 3, 4], rng)
    rng.shuffle(results)
    return results


def pull_pack(
    pack_type: str,
    *,
    position: str | None = None,
    school: str | None = None,
    rng: random.Random | None = None,
) -> list[CharacterDef]:
    rng = rng or random.Random()
    pack_type = pack_type.casefold()
    if pack_type not in GAME_CONFIG['packs']:
        raise ValueError('Unknown pack type.')
    cfg = GAME_CONFIG['packs'][pack_type]
    pool = list(CHARACTERS)
    if pack_type == 'position':
        if not position:
            raise ValueError('A position is required for a Position Pack.')
        valid = sorted({c.position for c in CHARACTERS})
        position = next((p for p in valid if p.casefold() == position.casefold()), None)
        if position is None:
            raise ValueError(f'Unknown position. Choose one of: {", ".join(valid)}')
        pool = [c for c in pool if c.position == position]
    elif pack_type == 'school':
        if not school:
            raise ValueError('A school is required for a School Pack.')
        school = school_name(school)
        eligible = cfg.get('eligible_schools', [])
        match = next((s for s in eligible if s.casefold() == school.casefold()), None)
        if not match:
            raise ValueError(f'School Packs are currently available for: {", ".join(eligible)}')
        pool = [c for c in pool if c.school == match]

    results: list[CharacterDef] = []
    used_names: set[str] = set()
    for _ in range(int(cfg.get('cards', 3))):
        candidate_pool = [c for c in pool if c.name not in used_names] or pool
        available = {c.rarity for c in candidate_pool}
        rarity = _weighted_rarity(cfg['rarity_odds'], available, rng)
        rarity_pool = [c for c in candidate_pool if c.rarity == rarity]
        pick = rng.choice(rarity_pool)
        results.append(pick)
        used_names.add(pick.name)
    return results


def pack_cost(pack_type: str) -> int:
    return int(GAME_CONFIG['packs'][pack_type]['cost'])


def daily_win_reward(win_number: int) -> int:
    return 30 if 1 <= win_number <= 5 else 4 if 6 <= win_number <= 50 else 0


def weekly_milestone_rewards(old_wins: int, new_wins: int, already_claimed: set[int]) -> tuple[int, set[int]]:
    return 0, set(already_claimed)
