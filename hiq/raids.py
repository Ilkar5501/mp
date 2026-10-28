from __future__ import annotations
import json
from datetime import datetime,timezone
from .config import DATA_DIR
from .data import CHAR_BY_ID
from .models import CardInstance
from .items import THEMES

RAID_TEAMS=json.loads((DATA_DIR/'raids.json').read_text(encoding='utf-8'))
RAID_POOLS={theme:{fmt:[i for i,t in enumerate(RAID_TEAMS[str(fmt)]) if t['theme']==theme] for fmt in (3,6)} for theme in THEMES}
DIFFICULTIES=('easy','hard','extreme')
RARITY_WEIGHTS={'easy':{1:.40,2:.30,3:.25,4:.04,5:.01},
                'hard':{1:.30,2:.25,3:.20,4:.15,5:.10},
                'extreme':{3:.27,4:.40,5:.33}}
DROP_WEIGHTS={'easy':{1:1.0},'hard':{1:.70,2:.30},'extreme':{1:.05,2:.40,3:.55}}
EASY_RARITY_WEIGHTS=RARITY_WEIGHTS['easy']

def team_index(fmt,theme,index,difficulty='hard'):
    if difficulty=='extreme':
        if fmt!=6:raise ValueError('Extreme raids are 6v6 only.')
        return next(i for i,t in enumerate(RAID_TEAMS['extreme']) if t['theme']==theme)
    return RAID_POOLS[theme][fmt][index % len(RAID_POOLS[theme][fmt])]

def raid_team(fmt,index,difficulty='hard'):
    if difficulty=='extreme' and fmt!=6:raise ValueError('Extreme raids are 6v6 only.')
    return RAID_TEAMS['extreme' if difficulty=='extreme' else str(fmt)][index]

def drop_count(rng,difficulty):
    weights=DROP_WEIGHTS[difficulty]
    return rng.choices(list(weights),weights=list(weights.values()),k=1)[0]

def cpu_specs(fmt,index,difficulty='hard'):
    if difficulty not in DIFFICULTIES:raise ValueError('Choose Easy, Hard or Extreme.')
    team=raid_team(fmt,index,difficulty);out=[]
    for i,raw in enumerate(team['units']):
        c=CHAR_BY_ID[raw['character_id']]
        card=CardInstance(f'raid-cpu-{fmt}-{index}-{i}',f'RAID:{fmt}:{index}:{i}',c.card_id,c.id,0,0,team['name'],i,raw['merge'] if difficulty!='easy' else 0,datetime(2026,1,1,tzinfo=timezone.utc),True,raw['boon'],raw['bane'],equipment_key=raw.get('equipment_key'))
        out.append((card,c,raw['lane']))
    return out

def choose_character(rng,characters,difficulty):
    weights=RARITY_WEIGHTS[difficulty]
    rarities=[r for r in weights if any(c.rarity==r for c in characters)]
    rarity=rng.choices(rarities,weights=[weights[r] for r in rarities],k=1)[0]
    return rng.choice([c for c in characters if c.rarity==rarity])
