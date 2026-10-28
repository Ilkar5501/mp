"""Sealed pack passing, private teams, and single-elimination brackets."""
from __future__ import annotations
import random
from collections import Counter
from datetime import datetime,timezone
from .data import CHAR_BY_ID
from .models import CardInstance
from .tournament import snapshot_specs

SEALED_LIFETIME=30*60
POSITIONS=('Front Left','Front Middle','Front Right','Back Left','Back Middle','Back Right')


def make_draft(users,roster,rng=None):
    rng=rng or random.SystemRandom()
    if not 2<=len(users)<=7 or len({u['id'] for u in users})!=len(users):raise ValueError('Choose 2–7 distinct human players.')
    if len(roster)<8*len(users):raise ValueError('The roster is too small for unique packs.')
    order=[u['id'] for u in users];rng.shuffle(order)
    pool=rng.sample(list(roster),8*len(users))
    # With at most two extra variants in the entire pool, every possible
    # eight-card draft contains at least six distinct player names. All variants
    # remain eligible, but an unlucky draft can never make a legal team impossible.
    while len(pool)-len({CHAR_BY_ID[cid].name for cid in pool})>2:
        counts=Counter(CHAR_BY_ID[cid].name for cid in pool)
        redundant=rng.choice([cid for cid in pool if counts[CHAR_BY_ID[cid].name]>1])
        replacements=[cid for cid in roster if CHAR_BY_ID[cid].name not in counts]
        if not replacements:raise ValueError('The roster cannot guarantee six distinct player names per draft.')
        pool[pool.index(redundant)]=rng.choice(replacements)
    rng.shuffle(pool)
    return dict(order=order,names={str(u['id']):u['name'] for u in users},
                packs={str(i):pool[8*i:8*(i+1)] for i in range(len(users))},
                queues={str(uid):[str(i)] for i,uid in enumerate(order)},
                picks={str(uid):[] for uid in order},teams={},draft_teams={},team_versions={},matches=[],stage='drafting')


def pick(st,uid,pack_id,expected_count,character):
    key=str(uid);queue=st['queues'].get(key,[])
    if not queue or queue[0]!=pack_id:raise ValueError('This pack has already passed. Reopen your active pack.')
    cards=st['packs'][pack_id]
    if len(cards)!=expected_count or character not in cards:raise ValueError('That selection is stale. Reopen your active pack.')
    cards.remove(character);st['picks'][key].append(character);queue.pop(0)
    recipient=st['order'][(st['order'].index(uid)+1)%len(st['order'])]
    wake=bool(cards) and not st['queues'][str(recipient)]
    if cards:st['queues'][str(recipient)].append(pack_id)
    if all(not pack for pack in st['packs'].values()):
        if any(len(v)!=8 for v in st['picks'].values()):raise RuntimeError('Unequal Sealed draft totals.')
        st['stage']='building'
    return recipient,wake


def bracket(order,rng=None):
    rng=rng or random.SystemRandom();players=list(order);rng.shuffle(players)
    size=1<<(len(players)-1).bit_length();byes=size-len(players)
    # Spread the byes across matches so no empty-vs-empty match is generated.
    pairs=[(players[i],None) for i in range(byes)]
    rest=players[byes:];pairs.extend(zip(rest[::2],rest[1::2]));rng.shuffle(pairs)
    return [list(pair) for pair in pairs]


def team_specs(sid,uid,cids):
    return [(CardInstance(f'{sid}:{uid}:{i}',f'{sid}:{uid}:{i}',CHAR_BY_ID[cid].card_id,cid,uid,uid,'Sealed Draft',i,0,
                          datetime(2026,1,1,tzinfo=timezone.utc),True),CHAR_BY_ID[cid],'front' if i<3 else 'back') for i,cid in enumerate(cids)]


def validate_team(picks,cids):
    if len(cids)!=6 or len(set(cids))!=6 or not set(cids)<=set(picks):raise ValueError('Place six distinct drafted characters in all six positions.')
    if len({CHAR_BY_ID[cid].name for cid in cids})!=6:raise ValueError('Only one variant of each player name may be used on a team.')
