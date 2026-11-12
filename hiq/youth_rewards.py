"""Collection passives. Call from reward settlements, never purchases or transfers."""
import hashlib,random

OIKAWA='Oikawa — Young Emperor'
KAGEYAMA='Kageyama — King of the Lost Court'
HINATA='Hinata — Furious Upstart'
DUO='Oikawa & Kageyama — Kings of the Playground'

SCHEMA='''CREATE TABLE IF NOT EXISTS youth_daily (
 owner_id INTEGER NOT NULL,day TEXT NOT NULL,reward_key TEXT NOT NULL,claims INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,day,reward_key));'''


async def highest(conn,uid,cid):
    row=await (await conn.execute('SELECT MAX(merge_level) FROM cards WHERE owner_id=? AND character_id=?',(uid,cid))).fetchone()
    return row[0] if row and row[0] is not None else None


async def currency(conn,uid,amount,*,clan=False,qualifies=False):
    """Return the credited amount; cap ledgers must record the base amount only."""
    merge=await highest(conn,uid,DUO) if amount>0 or qualifies else None
    total=amount+(merge+1 if merge is not None else 0)
    if total:
        if clan:await conn.execute('INSERT INTO clan_wallets VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET coins=coins+excluded.coins',(uid,total))
        else:await conn.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(total,uid))
    return total


def seeded(key):
    return random.Random(int.from_bytes(hashlib.sha256(str(key).encode()).digest()[:8],'big'))


def loss_reward(specs,*,key=None,rng=None):
    """Specs may be live tuples or immutable JSON snapshots of the participants."""
    levels=[]
    for entry in specs or []:
        if isinstance(entry,dict):
            card=entry.get('card',{});cid=card.get('character_id');merge=card.get('merge_level',0)
        else:card,char,_=entry;cid=char.id;merge=card.merge_level if card else 0
        if cid==HINATA:levels.append(merge)
    chance=max(levels,default=0)*.07
    return chance>0 and (rng or (seeded(key) if key is not None else random.SystemRandom())).random()<chance


async def duplicate(conn,uid,*,rng=None):
    merge=await highest(conn,uid,KAGEYAMA)
    return merge is not None and merge>0 and (rng or random.SystemRandom()).random()<merge*.01


async def extra(conn,uid,key,days):
    """Reserve one additional allowance per eligible day in the caller's transaction."""
    merge=await highest(conn,uid,OIKAWA)
    if merge is None:return None
    for day in days:
        row=await (await conn.execute('SELECT claims FROM youth_daily WHERE owner_id=? AND day=? AND reward_key=?',(uid,day,key))).fetchone()
        if row and row[0]:continue
        await conn.execute('INSERT INTO youth_daily VALUES (?,?,?,1) ON CONFLICT(owner_id,day,reward_key) DO UPDATE SET claims=claims+1',(uid,day,key))
        return day,merge*.05
    return None
