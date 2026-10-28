from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import CharacterDef

TRAINING_SCHOOLS = (
    'Inarizaki', 'Date Tech', 'Shiratorizawa', 'Karasuno',
    'Aoba Johsai', 'Kamomedai', 'Fukurodani', 'Nekoma', 'Mujinazaka', 'Tsubakihara',
)


def craft_cost(rarity: int) -> int:
    rarity=int(rarity)
    return 3 if rarity <= 2 else 6 if rarity <= 4 else 9


def recycle_payout(rarity: int, merge_level: int) -> int:
    return int(rarity) * 10 * (int(merge_level) + 1)


def training_difficulty(successes_this_week: int) -> tuple[str, int] | None:
    wins=int(successes_this_week)
    if wins >= 9:
        return None
    if wins < 3:
        return ('Easy', 0)
    if wins < 6:
        return ('Hard', 5)
    return ('Extreme', 10)


def post50_bonus_total(weekly_random_battle_wins: int) -> int:
    return 0


def post50_bonus_increment(new_weekly_random_battle_wins: int) -> int:
    return 0


@dataclass(slots=True)
class PityResolution:
    cards: list[CharacterDef]
    armed: bool
    cleared: bool


def resolve_pity_sequence(
    cards: Iterable[CharacterDef],
    target: CharacterDef,
    *,
    armed: bool,
    target_eligible: bool,
) -> PityResolution:
    """Resolve pity sequentially across one pack.

    A natural target clears pity. A same-rarity miss arms it. Once armed, the next
    same-rarity pull from a pack where the target is eligible is replaced by target.
    """
    out=[]
    active=True
    current_armed=bool(armed)
    cleared=False
    for card in cards:
        pick=card
        if active and card.id == target.id:
            active=False; cleared=True; current_armed=False
        elif active and card.rarity == target.rarity:
            if current_armed and target_eligible:
                pick=target; active=False; cleared=True; current_armed=False
            elif not current_armed:
                current_armed=True
        out.append(pick)
    return PityResolution(out,current_armed,cleared)
