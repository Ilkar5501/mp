from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import DATA_DIR
from .data import CHAR_BY_ID
from .models import CardInstance

GAUNTLETS = json.loads((DATA_DIR / 'gauntlets.json').read_text(encoding='utf-8'))


def school_order(mode: str) -> list[str]:
    return list(GAUNTLETS[mode]['order'])


def schools_for(mode: str) -> list[str]:
    return school_order(mode)


def games_for_school(mode: str, school: str) -> list[dict]:
    return list(GAUNTLETS[mode]['schools'][school])


def total_games(mode: str) -> int:
    return sum(len(games_for_school(mode, school)) for school in school_order(mode))


def game_count(mode: str, school: str) -> int:
    return len(games_for_school(mode, school))


def gauntlet_sequence(mode: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for school in school_order(mode):
        for game_no in range(1, game_count(mode, school) + 1):
            out.append((school, game_no))
    return out


def npc_specs(mode: str, school: str, game_no: int):
    row = games_for_school(mode, school)[game_no - 1]
    merge = int(row['merge'])
    ids = [row['front'], *row['back']]
    lanes = ['front', 'back', 'back']
    specs = []
    now = datetime.now(timezone.utc)
    for i, (cid, lane) in enumerate(zip(ids, lanes), 1):
        c = CHAR_BY_ID[cid]
        # Fixed gauntlet opponents are always neutral: no boon/bane.
        card = CardInstance(
            instance_id=f'npc-{mode}-{school}-{game_no}-{i}',
            full_instance_id=f'NPC:{mode}:{school}:{game_no}:{i}',
            card_id=c.card_id,
            character_id=c.id,
            owner_id=0,
            original_owner_id=0,
            original_owner_name=school,
            serial_no=i,
            merge_level=merge,
            obtained_at=now,
            locked=True,
            boon_stat=None,
            bane_stat=None,
        )
        specs.append((card, c, lane))
    return specs


def mode_for_school(school: str) -> str | None:
    for mode in ('regional','national'):
        if school in GAUNTLETS[mode]['schools']:
            return mode
    return None

def training_specs(school: str, merge: int, rng=None):
    import random
    rng = rng or random.Random()
    mode = mode_for_school(school)
    if school in GAUNTLETS.get('training', {}):
        games=GAUNTLETS['training'][school]
    elif mode:
        games=games_for_school(mode, school)
    else:
        raise ValueError(f'Unknown training school: {school}')
    row = rng.choice(games)
    ids = [row['front'], *row['back']]
    lanes = ['front','back','back']
    now = datetime.now(timezone.utc)
    specs=[]
    for i,(cid,lane) in enumerate(zip(ids,lanes),1):
        c=CHAR_BY_ID[cid]
        card=CardInstance(
            instance_id=f'training-{school}-{merge}-{i}',
            full_instance_id=f'TRAINING:{school}:{merge}:{i}',
            card_id=c.card_id, character_id=c.id, owner_id=0, original_owner_id=0,
            original_owner_name=f'{school} Training', serial_no=i, merge_level=int(merge),
            obtained_at=now, locked=True, boon_stat=None, bane_stat=None,
        )
        specs.append((card,c,lane))
    return specs
