"""Deterministic eight-player double elimination and immutable team snapshots."""
from __future__ import annotations
import hashlib
import random
from dataclasses import asdict, replace
from datetime import datetime
from .battle.engine import BattleEngine
from .battle.types import BattleResult
from .models import AbilityDef, CardInstance, CharacterDef

# Cross-seeded lower rounds avoid immediate rematches where possible.
BRACKET=(
    ('W1','S0','S1'),('W2','S2','S3'),('W3','S4','S5'),('W4','S6','S7'),
    ('L1','LW1','LW2'),('L2','LW3','LW4'),
    ('W5','WW1','WW2'),('W6','WW3','WW4'),
    ('L3','WL1','LW6'),('L4','WL2','LW5'),
    ('W7','WW5','WW6'),('L5','WL3','WL4'),('L6','WL5','LW7'),
    ('G1','WW7','WL6'),
)


def next_match(seeds,matches):
    """Return (key, owner A, owner B), with a grand-final reset when needed."""
    if len(seeds)!=8 or len(set(seeds))!=8:raise ValueError('Eight unique seeds are required.')
    def resolve(ref):
        if ref.startswith('S'):return seeds[int(ref[1:])]
        row=matches[ref[1:]]
        return row['winner_id'] if ref[0]=='W' else row['loser_id']
    for key,a,b in BRACKET:
        if key not in matches:return key,resolve(a),resolve(b)
    if matches['G1']['winner_id']==matches['L6']['winner_id'] and 'G2' not in matches:
        return 'G2',matches['W7']['winner_id'],matches['L6']['winner_id']
    return None


def champion(matches):
    return matches.get('G2',matches.get('G1',{})).get('winner_id')


def losses(matches,owner):
    return sum(row['loser_id']==owner for row in matches.values())


def snapshot_specs(specs):
    rows=[]
    for card,c,lane in specs:
        value=asdict(card);value['obtained_at']=card.obtained_at.isoformat()
        rows.append({'card':value,'character':asdict(c),'lane':lane})
    return rows


def restore_specs(snapshot):
    rows=[]
    for raw in snapshot:
        card=dict(raw['card']);card['obtained_at']=datetime.fromisoformat(card['obtained_at'])
        char=dict(raw['character']);char['abilities']={key:AbilityDef(**value) for key,value in char['abilities'].items()}
        rows.append((CardInstance(**card),CharacterDef(**char),raw['lane']))
    return rows


def play_match(tid,key,a_specs,b_specs,owner_a,owner_b,name_a,name_b):
    # Three fresh simulations precede the deterministic remaining-HP tiebreak.
    prior=[]
    for attempt in range(3):
        seed=int.from_bytes(hashlib.sha256(f'{tid}:{key}:{attempt}'.encode()).digest()[:8],'big')
        engine=BattleEngine(a_specs,b_specs,owner_a=owner_a,owner_b=owner_b,name_a=name_a,name_b=name_b,mode='tournament',seed=seed)
        result=engine.run()
        if result.winner_side in ('A','B'):
            if prior:result.log[:0]=prior
            return result
        prior.append(f'Tournament {key}: simulation {attempt+1} ended in a draw; rematching.' if attempt<2 else f'Tournament {key}: third draw; applying tiebreak.')
    a_fraction=result.team_a_hp/engine.team_a.max_hp
    b_fraction=result.team_b_hp/engine.team_b.max_hp
    winner='A' if a_fraction>b_fraction else 'B' if b_fraction>a_fraction else random.Random(seed).choice(['A','B'])
    result.log[:0]=prior
    result.log.append(f'Tiebreak winner: {name_a if winner=="A" else name_b}; remaining HP percentage, then seeded coin toss for an exact tie.')
    return replace(result,winner_side=winner)


def result_dict(result):return asdict(result)
def restore_result(value):return BattleResult(**value)
