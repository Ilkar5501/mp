"""Inventory rows and forgiving name/ID search; no ownership mutations."""
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from .abbreviations import SCHOOLS
from .data import CHAR_BY_ID
from .items import ITEM_BY_KEY,inventory_label
from .shard_balances import label as shard_label

CATEGORIES=('Non-equip Items','School Shards','Character Shards','Equip Shards','Equip Items',
            'Unique Equip Shards','Unique Equip Items')
MODES={'toggle','view','stacked','compact','regular','ids'}
ITEM_NAMES={'mergecopy':'Merge Copy','blessing':'Blessing','starpack':'Star Pack',
            'pack_base':'Base Pack','pack_school':'School Pack','pack_position':'Position Pack'}


@dataclass(frozen=True)
class InventoryEntry:
    category:str
    label:str
    quantity:int=1
    reference:str=''
    equipped_on:str=''
    terms:tuple[str,...]=()


def parse_query(raw):
    parts=raw.strip().split(maxsplit=1)
    if parts and parts[0].casefold() in MODES:
        return parts[0].casefold(),parts[1] if len(parts)>1 else ''
    return '',raw.strip()


def normalized(raw):
    words=re.findall(r'[^\W_]+',unicodedata.normalize('NFKD',str(raw).casefold()),re.UNICODE)
    return ['shard' if word=='shards' else word for word in words]


def searchable_terms(kind,detail):
    item=ITEM_BY_KEY.get(detail,{})
    char=CHAR_BY_ID.get(detail)
    return tuple(str(t) for t in (detail,ITEM_NAMES.get(kind,kind),item.get('name',''),
                                  *item.get('aliases',[]),char.name if char else '',char.title if char else '') if t)


def inventory_entries(schools,balances,items,*,stacked=False):
    rows=[]
    for school,qty in schools.items():
        if qty>0:
            aliases=tuple(short for short,name in SCHOOLS.items() if name==school)
            rows.append(InventoryEntry('School Shards',school+' Shard',qty,terms=(school,*aliases)))
    for row in balances:
        if row['quantity']<=0:continue
        kind,detail=row['kind'],row['detail']
        category=('Unique Equip Shards' if ITEM_BY_KEY.get(detail,{}).get('signature') else 'Equip Shards') if kind=='item_shard' else 'Character Shards'
        rows.append(InventoryEntry(category,
                                   shard_label(kind,detail),row['quantity'],terms=searchable_terms(kind,detail)))
    groups=defaultdict(list)
    for item in items:
        groups[(item['kind'],item['stars'],item['detail'],'' if stacked else item['id'])].append(item)
    for (kind,stars,detail,_),copies in groups.items():
        name=inventory_label(copies[0]) or ((f'{stars}★ ' if stars else '')+ITEM_NAMES.get(kind,kind)+(f' · {detail}' if detail else ''))
        terms=searchable_terms(kind,detail)+tuple(item['id'] for item in copies)
        category=('Unique Equip Items' if ITEM_BY_KEY.get(detail,{}).get('signature') else 'Equip Items') if kind=='equipment' else 'Non-equip Items'
        rows.append(InventoryEntry(category,name,len(copies),'' if stacked else copies[0]['id'],
                                   '' if stacked else copies[0].get('equipped_on') or '',terms))
    return sorted(rows,key=lambda row:(CATEGORIES.index(row.category),row.label.casefold(),row.reference))


def search_entries(entries,query):
    words=normalized(query);compact=''.join(words)
    if not compact:return list(entries)
    strong=[];scores=[]
    for row in entries:
        terms=[normalized(t) for t in (row.label,*row.terms)]
        if any(compact in ''.join(t) for t in terms) or any(all(word in t for word in words) for t in terms):
            strong.append(row);continue
        candidates=[''.join(t) for t in terms]+[word for t in terms for word in t]
        score=max((SequenceMatcher(None,compact,t).ratio() for t in candidates if t),default=0)
        scores.append((score,row))
    if strong:return strong
    best=max((score for score,_ in scores),default=0)
    return [row for score,row in sorted(scores,key=lambda pair:-pair[0]) if score>=max(.72,best-.08)]
