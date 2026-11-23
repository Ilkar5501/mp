"""Saved weekly Tiny Titans boosts with a four-week history bound."""
import hashlib,json,random
from datetime import date,timedelta
from .data import CHARACTERS
from .pvp_cycles import week_start

SCHEMA='''CREATE TABLE IF NOT EXISTS ttb_rotations (
 week TEXT PRIMARY KEY,state_json TEXT NOT NULL);'''

def draw(week,previous=None):
    previous=previous or {};picks={};pools={};known={}
    rng=random.Random(int.from_bytes(hashlib.sha256(('ttb:'+week).encode()).digest()[:8],'big'))
    for rarity in range(1,5):
        key=str(rarity);all_ids=sorted(c.id for c in CHARACTERS if c.rarity==rarity and not c.is_duo)
        last=set(previous.get('picks',{}).get(key,[]))
        old_known=set(previous.get('known',{}).get(key,[]))
        pool=[cid for cid in previous.get('pools',{}).get(key,[]) if cid in all_ids and cid not in last]
        additions=[cid for cid in all_ids if cid not in old_known and cid not in pool and cid not in last]
        rng.shuffle(additions);pool+=additions
        selected=[]
        while len(selected)<min(4,len(all_ids)):
            if not pool:
                pool=[cid for cid in all_ids if cid not in last and cid not in selected]
                if not pool:raise ValueError('Not enough cards to avoid consecutive weekly boosts.')
                rng.shuffle(pool)
            selected.append(pool.pop(0))
        picks[key]=selected;pools[key]=pool;known[key]=all_ids
    return dict(picks=picks,pools=pools,known=known)

async def rotation(c,day):
    current=week_start(day).isoformat()
    row=await (await c.execute('SELECT state_json FROM ttb_rotations WHERE week=?',(current,))).fetchone()
    if row:return json.loads(row['state_json'])
    prior=await (await c.execute('SELECT * FROM ttb_rotations WHERE week<? ORDER BY week DESC LIMIT 1',(current,))).fetchone()
    previous=json.loads(prior['state_json']) if prior else None
    cursor=date.fromisoformat(prior['week'])+timedelta(weeks=1) if prior else date.fromisoformat(current)
    while cursor<=date.fromisoformat(current):
        key=cursor.isoformat();previous=draw(key,previous)
        await c.execute('INSERT INTO ttb_rotations VALUES (?,?)',(key,json.dumps(previous)))
        cursor+=timedelta(weeks=1)
    cutoff=(date.fromisoformat(current)-timedelta(weeks=3)).isoformat()
    await c.execute('DELETE FROM ttb_rotations WHERE week<?',(cutoff,))
    return previous

def selected_ids(state):return {cid for ids in state['picks'].values() for cid in ids}
