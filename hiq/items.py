"""Raid item catalogue. Effects are implemented by battle.equipment."""
from __future__ import annotations
import json,re
from difflib import SequenceMatcher
from .config import DATA_DIR

ITEMS=json.loads((DATA_DIR/'items.json').read_text(encoding='utf-8'))
ITEM_BY_KEY={i['key']:i for i in ITEMS}
THEMES=('tactics','offense','defense','tempo','recovery')
CHARACTER_SHARD_COST={1:1,2:3,3:5,4:7,5:9}

def normalize(text):return re.sub(r'[^a-z0-9]+',' ',text.casefold().replace("'","").replace('’','')).strip()

def find_items(text=''):
    q=normalize(text)
    if not q:return list(ITEMS)
    exact=[i for i in ITEMS if q in (normalize(i['name']),normalize(i['key']))]
    if exact:return exact
    partial=[i for i in ITEMS if q in normalize(i['name'])]
    if partial:return partial
    scores=[(SequenceMatcher(None,q,normalize(i['name'])).ratio(),i) for i in ITEMS]
    best=max(score for score,_ in scores)
    return [i for score,i in sorted(scores,key=lambda pair:-pair[0]) if score>=max(.68,best-.06)]

def can_equip(item,char):
    if item['eligible']=='Anyone':return True
    names=[normalize(n) for n in item['eligible'].split(',')]
    return normalize(char.name) in names or normalize(f'{char.name} {char.title}') in names

def inventory_label(item):
    key=item.get('detail','');kind=item['kind']
    if kind=='equipment':return ITEM_BY_KEY.get(key,{}).get('name',key)
    if kind=='item_shard':return ITEM_BY_KEY.get(key,{}).get('name',key)+' Shard'
    if kind=='character_shard':return key+' Shard'
    return None
