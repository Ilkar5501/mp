"""Limited Nisekyu pack pools and one persistent, restart-safe event window."""
from datetime import datetime,timedelta,timezone
import random
from .data import CHARACTERS
from .economy import _weighted_rarity
from .config import GAME_CONFIG

EVENT='nisekyu-thanksgiving-2026'
COST=500
CARD_COUNT=10
PACK_IMAGE='https://raw.githubusercontent.com/Ilkar5501/matchpointbattler/refs/heads/main/Nisekyuu.JPG.jpeg'
RETURNING_IDS={
    'Tanaka — Unbreakable Wing','Hinata — Wings of Dawn','Hinata — Miracle Maker',
    'Kageyama — Crown of Precision','Kageyama — Prodigious Talent',
    'Kageyama — King of the Lost Court','Nishinoya — Guardian Deity',
}

def utcnow():return datetime.now(timezone.utc)

async def activate(c,moment=None):
    moment=moment or utcnow()
    await c.execute('CREATE TABLE IF NOT EXISTS limited_events (event_key TEXT PRIMARY KEY, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL)')
    await c.execute('INSERT OR IGNORE INTO limited_events VALUES (?,?,?)',(EVENT,moment.isoformat(),(moment+timedelta(hours=96)).isoformat()))
    return await window(c)

async def window(c):
    exists=await (await c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='limited_events'")).fetchone()
    if not exists:return None
    row=await (await c.execute('SELECT starts_at,ends_at FROM limited_events WHERE event_key=?',(EVENT,))).fetchone()
    return (datetime.fromisoformat(row[0]),datetime.fromisoformat(row[1])) if row else None

async def active(c,moment=None):
    bounds=await window(c)
    return bool(bounds and bounds[0]<=(moment or utcnow())<bounds[1])

async def shard_characters(c):
    available=await active(c)
    return [ch for ch in CHARACTERS if not ch.is_duo and (not ch.event_pack or available)]

def pull_nisekyu(rng=None):
    rng=rng or random.Random()
    bonyari=[c for c in CHARACTERS if c.event_pack=='nisekyu']
    returning=[c for c in CHARACTERS if c.id in RETURNING_IDS]
    lower=[c for c in returning if c.rarity<5]
    five=[c for c in returning if c.rarity==5]
    other=[c for c in CHARACTERS if not c.event_pack and not c.is_duo and c.id not in RETURNING_IDS]
    if len(bonyari)!=4 or len(returning)!=7 or len(lower)!=4 or len(five)!=3:raise ValueError('The Nisekyu pack roster is incomplete.')
    results=[]
    for _ in range(CARD_COUNT):
        roll=rng.random()
        if roll<.25:pool=bonyari
        elif roll<.75:pool=lower if rng.random()<.60 else five
        else:
            rarity=_weighted_rarity(GAME_CONFIG['packs']['base']['rarity_odds'],{c.rarity for c in other},rng)
            pool=[c for c in other if c.rarity==rarity]
        results.append(rng.choice(pool))
    return results
