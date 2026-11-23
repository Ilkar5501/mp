"""Owned item copies reserved by saved, AF1 and PvP team loadouts."""
import json
from dataclasses import replace
from .data import CHAR_BY_ID
from .items import ITEM_BY_KEY,can_equip

SCHEMA='''
CREATE TABLE IF NOT EXISTS locked_loadouts (
 owner_id INTEGER NOT NULL,kind TEXT NOT NULL,slot INTEGER NOT NULL,
 entries_json TEXT NOT NULL,PRIMARY KEY(owner_id,kind,slot));
CREATE TABLE IF NOT EXISTS locked_items (
 owner_id INTEGER NOT NULL,kind TEXT NOT NULL,slot INTEGER NOT NULL,
 card_id TEXT NOT NULL,item_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,kind,slot,card_id));
CREATE INDEX IF NOT EXISTS locked_items_copy ON locked_items(item_id,card_id);
CREATE TABLE IF NOT EXISTS pvp_team_sources (
 owner_id INTEGER NOT NULL,format INTEGER NOT NULL,slot INTEGER NOT NULL,
 PRIMARY KEY(owner_id,format));
CREATE TRIGGER IF NOT EXISTS protect_locked_item_delete BEFORE DELETE ON inventory_items
WHEN EXISTS(SELECT 1 FROM locked_items l JOIN cards c ON c.instance_id=l.card_id AND c.owner_id=l.owner_id
 WHERE l.item_id=OLD.id AND l.owner_id=OLD.owner_id)
BEGIN SELECT RAISE(ABORT,'Item is reserved by a locked team. Unlock the team first.'); END;
CREATE TRIGGER IF NOT EXISTS protect_locked_item_trade BEFORE UPDATE OF owner_id ON inventory_items
WHEN NEW.owner_id<>OLD.owner_id AND EXISTS(SELECT 1 FROM locked_items l JOIN cards c ON c.instance_id=l.card_id AND c.owner_id=l.owner_id
 WHERE l.item_id=OLD.id AND l.owner_id=OLD.owner_id)
BEGIN SELECT RAISE(ABORT,'Item is reserved by a locked team. Unlock the team first.'); END;
'''

def label(kind,slot):
    return f'Team {slot}' if kind=='saved' else f'AF1 Team {slot}' if kind=='af1' else f'{slot}v{slot} PvP'

async def locked(c,uid,kind,slot):
    row=await (await c.execute('SELECT entries_json FROM locked_loadouts WHERE owner_id=? AND kind=? AND slot=?',(uid,kind,slot))).fetchone()
    return json.loads(row['entries_json']) if row else None

async def holders(c,uid,item_id):
    rows=await (await c.execute('''SELECT l.*,c.character_id FROM locked_items l JOIN cards c ON c.instance_id=l.card_id AND c.owner_id=l.owner_id
        WHERE l.owner_id=? AND l.item_id=? ORDER BY l.kind,l.slot''',(uid,item_id))).fetchall()
    return [dict(r,team=label(r['kind'],r['slot'])) for r in rows]

async def available(c,uid,item_id,card_id=None):
    rows=await holders(c,uid,item_id)
    conflicts=[r for r in rows if r['card_id']!=card_id]
    if conflicts:
        raise ValueError('That item copy is held by '+', '.join(sorted({r['team'] for r in conflicts}))+'. Unlock those teams before moving it to another character.')

async def snapshot(c,uid,kind,slot,entries):
    """One actual copy may be shared by the same character across many teams."""
    if not isinstance(entries,list) or any(e is not None and (not isinstance(e,dict) or not isinstance(e.get('id'),str) or e.get('lane') not in ('front','back')) for e in entries):
        raise ValueError('This team has an invalid slot. Update it before locking.')
    ids=[e['id'] for e in entries if e]
    if len(ids)!=len(set(ids)):raise ValueError('A team cannot lock the same card into more than one slot.')
    old=await locked(c,uid,kind,slot)
    prior={e['id']:e for e in old or [] if e}
    saved=[]
    for entry in entries:
        if not entry:saved.append(None);continue
        raw=await (await c.execute('SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))).fetchone()
        if not raw:raise ValueError('A team card is no longer yours. Update the team before locking it.')
        item_id=prior.get(entry['id'],{}).get('item_id',entry.get('item_id',raw['equipped_item_id']))
        if item_id:
            item=await (await c.execute("SELECT * FROM inventory_items WHERE owner_id=? AND id=? AND kind='equipment'",(uid,item_id))).fetchone()
            if not item or item['detail'] not in ITEM_BY_KEY or not can_equip(ITEM_BY_KEY[item['detail']],CHAR_BY_ID[raw['character_id']]):raise ValueError('A saved item is missing or cannot be equipped by this character.')
            await available(c,uid,item_id,entry['id'])
            normal=await (await c.execute('SELECT instance_id FROM cards WHERE owner_id=? AND equipped_item_id=? AND instance_id<>?',(uid,item_id,entry['id']))).fetchone()
            if normal:raise ValueError('That item copy is currently equipped to another character.')
        saved.append(dict(id=entry['id'],lane=entry['lane'],item_id=item_id))
    await c.execute('DELETE FROM locked_items WHERE owner_id=? AND kind=? AND slot=?',(uid,kind,slot))
    await c.execute('INSERT INTO locked_loadouts VALUES (?,?,?,?) ON CONFLICT(owner_id,kind,slot) DO UPDATE SET entries_json=excluded.entries_json',(uid,kind,slot,json.dumps(saved)))
    for entry in filter(None,saved):
        if entry['item_id']:await c.execute('INSERT INTO locked_items VALUES (?,?,?,?,?)',(uid,kind,slot,entry['id'],entry['item_id']))
    return saved

async def unlock(c,uid,kind,slot):
    await c.execute('DELETE FROM locked_items WHERE owner_id=? AND kind=? AND slot=?',(uid,kind,slot))
    await c.execute('DELETE FROM locked_loadouts WHERE owner_id=? AND kind=? AND slot=?',(uid,kind,slot))

async def resolve(c,db,uid,kind,slot,entries):
    saved=await locked(c,uid,kind,slot)
    specs=[]
    for entry in saved if saved is not None else entries:
        if not entry:continue
        row=await (await c.execute('SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))).fetchone()
        if not row:raise ValueError('A saved team character is no longer yours. Update the team.')
        card=db._card(row);char=CHAR_BY_ID[card.character_id]
        if saved is not None:
            item_id=entry.get('item_id');item=None
            if item_id:
                item=await (await c.execute("SELECT * FROM inventory_items WHERE owner_id=? AND id=? AND kind='equipment'",(uid,item_id))).fetchone()
                if not item or item['detail'] not in ITEM_BY_KEY or not can_equip(ITEM_BY_KEY[item['detail']],char):raise ValueError('A locked team item is missing or incompatible. Update the team.')
            card=replace(card,equipped_item_id=item_id,equipment_key=item['detail'] if item else None)
        specs.append((card,char,entry['lane']))
    return specs

async def refresh(c,uid,kind,slot,entries):
    if await locked(c,uid,kind,slot) is not None:await snapshot(c,uid,kind,slot,entries)

async def migrate_existing(c):
    rows=await (await c.execute("SELECT * FROM pvp_teams p WHERE locked=1 AND NOT EXISTS(SELECT 1 FROM locked_loadouts l WHERE l.owner_id=p.owner_id AND l.kind='pvp' AND l.slot=p.format)")).fetchall()
    for row in rows:
        try:await snapshot(c,row['owner_id'],'pvp',row['format'],json.loads(row['entries_json']))
        except ValueError:continue
    await c.commit()

async def replace_card_refs(c,consumed,survivor):
    rows=await (await c.execute('SELECT * FROM locked_loadouts WHERE instr(entries_json,?)>0',(consumed,))).fetchall()
    for row in rows:
        entries=json.loads(row['entries_json'])
        already=any(e and e['id']==survivor for e in entries) if survivor else False
        for index,entry in enumerate(entries):
            if entry and entry['id']==consumed:
                if not survivor or already:entries[index]=None
                else:entry['id']=survivor
        await c.execute('UPDATE locked_loadouts SET entries_json=? WHERE owner_id=? AND kind=? AND slot=?',(json.dumps(entries),row['owner_id'],row['kind'],row['slot']))
        await c.execute('DELETE FROM locked_items WHERE owner_id=? AND kind=? AND slot=?',(row['owner_id'],row['kind'],row['slot']))
        for entry in filter(None,entries):
            if entry.get('item_id'):await c.execute('INSERT INTO locked_items VALUES (?,?,?,?,?)',(row['owner_id'],row['kind'],row['slot'],entry['id'],entry['item_id']))
