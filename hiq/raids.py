from __future__ import annotations
import json
from datetime import datetime,timezone
from .config import DATA_DIR
from .data import CHAR_BY_ID
from .models import CardInstance
from .items import THEMES

RAID_TEAMS=json.loads((DATA_DIR/'raids.json').read_text(encoding='utf-8'))
RAID_POOLS={theme:{fmt:[i for i,t in enumerate(RAID_TEAMS[str(fmt)]) if t['theme']==theme] for fmt in (3,6)} for theme in THEMES}
DIFFICULTIES=('easy','hard')
EASY_RARITY_WEIGHTS={1:.30,2:.30,3:.25,4:.10,5:.05}

def team_index(fmt,theme,index):
    return RAID_POOLS[theme][fmt][index % len(RAID_POOLS[theme][fmt])]

def cpu_specs(fmt,index,difficulty='hard'):
    if difficulty not in DIFFICULTIES:raise ValueError('Choose Easy or Hard.')
    team=RAID_TEAMS[str(fmt)][index];out=[]
    for i,raw in enumerate(team['units']):
        c=CHAR_BY_ID[raw['character_id']]
        card=CardInstance(f'raid-cpu-{fmt}-{index}-{i}',f'RAID:{fmt}:{index}:{i}',c.card_id,c.id,0,0,team['name'],i,raw['merge'] if difficulty=='hard' else 0,datetime(2026,1,1,tzinfo=timezone.utc),True,raw['boon'],raw['bane'])
        out.append((card,c,raw['lane']))
    return out

def choose_character(rng,characters,difficulty):
    if difficulty=='easy':
        rarities=[r for r in EASY_RARITY_WEIGHTS if any(c.rarity==r for c in characters)]
        rarity=rng.choices(rarities,weights=[EASY_RARITY_WEIGHTS[r] for r in rarities],k=1)[0]
        return rng.choice([c for c in characters if c.rarity==rarity])
    return rng.choice(characters)
