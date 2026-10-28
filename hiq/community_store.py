"""Transactional clan, inventory, redeem and cooperative-session persistence."""
from __future__ import annotations

import json
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from .abbreviations import school_name
from .config import GAME_CONFIG
from .data import CHAR_BY_ID, CHARACTERS
from .economy import local_date_key, week_key, pull_pack
from .progression import resolve_pity_sequence
from .tournament import snapshot_specs

ADMIN_ID = 626255484411969536
PREFECTURE_WIN_REWARD = 50
PREFECTURE_DAILY_CAP = 300
CLAN_ICONS = ('🏐','🦅','🦉','🐈','🦊','🐦','🐉','🐺','🦁','🐯','🐍','🦈','🔥','⚡','🌊','❄️','🌸','🌙','⭐','👑')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS clans (
 id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, icon TEXT NOT NULL,
 leader_id INTEGER NOT NULL, co_id INTEGER, wins INTEGER NOT NULL DEFAULT 0,
 losses INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clan_members (
 user_id INTEGER PRIMARY KEY, clan_id TEXT NOT NULL, username TEXT NOT NULL,
 joined_at TEXT NOT NULL, last_checkin TEXT
);
CREATE INDEX IF NOT EXISTS clan_members_clan ON clan_members(clan_id);
CREATE TABLE IF NOT EXISTS clan_applications (
 id TEXT PRIMARY KEY, clan_id TEXT NOT NULL, user_id INTEGER NOT NULL,
 username TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_by INTEGER,
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS clan_pending_application
 ON clan_applications(clan_id,user_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS clan_wallets (user_id INTEGER PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0 CHECK(coins>=0));
CREATE TABLE IF NOT EXISTS inventory_items (
 id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, kind TEXT NOT NULL,
 stars INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inventory_owner ON inventory_items(owner_id);
CREATE TABLE IF NOT EXISTS clan_daily (
 user_id INTEGER NOT NULL, day TEXT NOT NULL, donations INTEGER NOT NULL DEFAULT 0,
 checkin_clan TEXT, PRIMARY KEY(user_id,day)
);
CREATE TABLE IF NOT EXISTS clan_checkin_bonus (clan_id TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(clan_id,day));
CREATE TABLE IF NOT EXISTS clan_weekly (user_id INTEGER NOT NULL, week TEXT NOT NULL, war_coins INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,week));
CREATE TABLE IF NOT EXISTS redeem_codes (
 code TEXT PRIMARY KEY COLLATE NOCASE, prize_json TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('player','overall')), created_by INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS redeem_claims (code TEXT NOT NULL COLLATE NOCASE,user_id INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(code,user_id));
CREATE TABLE IF NOT EXISTS community_sessions (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
 state_json TEXT NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS community_rewards (
 session_id TEXT NOT NULL,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,
 PRIMARY KEY(session_id,user_id)
);
CREATE TABLE IF NOT EXISTS prefecture_daily (user_id INTEGER NOT NULL,day TEXT NOT NULL,coins INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,day));
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:10]


def parse_prize(raw):
    parts = raw.strip().split()
    if not parts:
        raise ValueError('Specify a prize.')
    key = parts[0].casefold()
    if key not in ('coins','clancoins') and len(parts)>1 and parts[-1].isdigit():
        count=int(parts[-1])
        if not 1<=count<=1000:raise ValueError('Item count must be between 1 and 1,000.')
        prize=parse_prize(' '.join(parts[:-1]));prize['count']=count;return prize
    m = re.fullmatch(r'(mergecopy|starpack|blessing)([1-5])',key)
    if m and len(parts)==1:
        return {'kind':m[1], 'stars':int(m[2])}
    if key in ('coins','clancoins') and len(parts)==2:
        try: amount=int(parts[1])
        except ValueError: raise ValueError('Currency prizes require a positive whole number.')
        if not 1 <= amount <= 1_000_000_000:
            raise ValueError('Currency prizes must be between 1 and 1,000,000,000.')
        return {'kind':key,'amount':amount}
    if key=='packbase' and len(parts)==1:
        return {'kind':'pack_base'}
    if key=='packschool' and len(parts)>=2:
        school=school_name(' '.join(parts[1:]))
        school=next((s for s in GAME_CONFIG['packs']['school']['eligible_schools'] if s.casefold()==school.casefold()),None)
        if school:return {'kind':'pack_school','detail':school}
        raise ValueError('That school has no School Pack. Use ;shop for eligible schools.')
    if key=='packpos' and len(parts)==2:
        pos=parts[1].upper()
        if pos in {c.position.upper() for c in CHARACTERS}:return {'kind':'pack_position','detail':pos}
        raise ValueError('Position must be MB, WS, OP, S or Li.')
    raise ValueError('Prize: mergecopy1–5, starpack1–5, packbase, packschool <school>, packpos <position>, coins <N>, clancoins <N>.')


def parse_expiry(timestamp):
    match=re.fullmatch(r'<t:(\d+):[fF]>',timestamp.strip())
    if not match:raise ValueError('Expiry must be a Discord timestamp ending in :f> or :F>, including date and time.')
    value=int(match[1])
    try:datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OverflowError,OSError):raise ValueError('That expiry timestamp is invalid.')
    if value<=datetime.fromisoformat(now()).timestamp():raise ValueError('Choose an expiry in the future.')
    return value


class CommunityStore:
    def __init__(self, db): self.db=db

    @asynccontextmanager
    async def tx(self):
        async with self.db._lock, self.db.connect() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    @staticmethod
    async def one(conn, sql, args=()):
        cur=await conn.execute(sql,args);row=await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def rows(conn, sql, args=()):
        cur=await conn.execute(sql,args)
        return [dict(r) for r in await cur.fetchall()]

    async def clan(self, user_id=None, clan_id=None, conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.clan(user_id,clan_id,c)
        if clan_id:return await self.one(conn,'SELECT * FROM clans WHERE id=?',(clan_id,))
        return await self.one(conn,'SELECT c.* FROM clans c JOIN clan_members m ON m.clan_id=c.id WHERE m.user_id=?',(user_id,))

    async def members(self, clan_id, conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.members(clan_id,c)
        return await self.rows(conn,'SELECT * FROM clan_members WHERE clan_id=? ORDER BY joined_at,user_id',(clan_id,))

    async def all_clans(self):
        async with self.db.connect() as c:
            return await self.rows(c,'SELECT c.*,COUNT(m.user_id) AS occupancy FROM clans c LEFT JOIN clan_members m ON m.clan_id=c.id GROUP BY c.id ORDER BY c.name')

    async def _clan_busy(self, conn, clan_id):
        sessions=await self.rows(conn,"SELECT state_json FROM community_sessions WHERE kind='war' AND status IN ('active','running','broken')")
        return any(clan_id in json.loads(r['state_json'])['clans'] for r in sessions)

    async def _officer(self,conn,user_id,*,leader_only=False):
        clan=await self.clan(user_id=user_id,conn=conn)
        if not clan or user_id not in ([clan['leader_id']] if leader_only else [clan['leader_id'],clan['co_id']]):
            raise ValueError('Only your clan’s '+('leader' if leader_only else 'leader or co-leader')+' can do that.')
        return clan

    async def create_clan(self, user_id, username, name, icon):
        name=name.strip()
        if not 2 <= len(name) <= 40 or any(ord(c)<32 for c in name) or '@' in name:
            raise ValueError('Clan names must be 2–40 characters, without mentions or control characters.')
        if icon not in CLAN_ICONS:raise ValueError('Choose a clan icon from the menu.')
        async with self.tx() as c:
            if await self.clan(user_id=user_id,conn=c):raise ValueError('You already belong to a clan.')
            if await self.one(c,'SELECT id FROM clans WHERE name=? COLLATE NOCASE',(name,)):raise ValueError('That clan name is already taken.')
            cid=new_id('cl-')
            await c.execute('INSERT INTO clans(id,name,icon,leader_id,created_at) VALUES (?,?,?,?,?)',(cid,name,icon,user_id,now()))
            await c.execute('INSERT INTO clan_members(user_id,clan_id,username,joined_at) VALUES (?,?,?,?)',(user_id,cid,username,now()))
            await c.execute("UPDATE clan_applications SET status='withdrawn' WHERE user_id=? AND status='pending'",(user_id,))
            return await self.clan(clan_id=cid,conn=c)

    async def edit_clan(self,actor,name,icon):
        name=name.strip()
        if not 2<=len(name)<=40 or any(ord(ch)<32 for ch in name) or '@' in name:
            raise ValueError('Clan names must be 2–40 characters, without mentions or control characters.')
        if icon not in CLAN_ICONS:raise ValueError('Choose a clan icon from the menu.')
        async with self.tx() as c:
            clan=await self._officer(c,actor,leader_only=True)
            taken=await self.one(c,'SELECT id FROM clans WHERE name=? COLLATE NOCASE AND id<>?',(name,clan['id']))
            if taken:raise ValueError('That clan name is already taken.')
            await c.execute('UPDATE clans SET name=?,icon=? WHERE id=?',(name,icon,clan['id']))
            return await self.clan(clan_id=clan['id'],conn=c)

    async def apply(self,user_id,username,clan_id):
        async with self.tx() as c:
            if await self.clan(user_id=user_id,conn=c):raise ValueError('Leave your current clan before applying.')
            clan=await self.clan(clan_id=clan_id,conn=c)
            if not clan:raise ValueError('No clan with that ID exists.')
            if len(await self.members(clan_id,c))>=5:raise ValueError('That clan is full.')
            if await self.one(c,"SELECT id FROM clan_applications WHERE clan_id=? AND user_id=? AND status='pending'",(clan_id,user_id)):
                raise ValueError('Your application is already pending.')
            aid=new_id('app-')
            await c.execute('INSERT INTO clan_applications(id,clan_id,user_id,username,created_at) VALUES (?,?,?,?,?)',(aid,clan_id,user_id,username,now()))
            return aid,clan

    async def decide(self,actor,application_id,accept):
        async with self.tx() as c:
            clan=await self._officer(c,actor)
            app=await self.one(c,'SELECT * FROM clan_applications WHERE id=?',(application_id,))
            if not app or app['clan_id']!=clan['id'] or app['status']!='pending':raise ValueError('That application is no longer pending for your clan.')
            if accept:
                if await self._clan_busy(c,clan['id']):raise ValueError('Membership cannot change during a war.')
                if len(await self.members(clan['id'],c))>=5:raise ValueError('Your clan is full.')
                if await self.clan(user_id=app['user_id'],conn=c):raise ValueError('The applicant has already joined another clan.')
                await c.execute('INSERT INTO clan_members(user_id,clan_id,username,joined_at) VALUES (?,?,?,?)',(app['user_id'],clan['id'],app['username'],now()))
                await c.execute("UPDATE clan_applications SET status='withdrawn' WHERE user_id=? AND status='pending' AND id<>?",(app['user_id'],application_id))
            await c.execute('UPDATE clan_applications SET status=?,decided_by=? WHERE id=?',('accepted' if accept else 'declined',actor,application_id))
            return app,clan

    async def appoint(self,actor,user_id):
        async with self.tx() as c:
            clan=await self._officer(c,actor,leader_only=True)
            if await self._clan_busy(c,clan['id']):raise ValueError('Leadership cannot change during a war.')
            member=await self.clan(user_id=user_id,conn=c)
            if not member or member['id']!=clan['id'] or user_id==actor:raise ValueError('Choose another member of your clan.')
            co=None if clan['co_id']==user_id else user_id
            await c.execute('UPDATE clans SET co_id=? WHERE id=?',(co,clan['id']))
            return co

    async def remove_member(self,actor,user_id=None):
        async with self.tx() as c:
            clan=await self.clan(user_id=actor,conn=c)
            if not clan:raise ValueError('You are not in a clan.')
            target=actor if user_id is None else user_id
            if target!=actor:
                await self._officer(c,actor)
                if target in (clan['leader_id'],clan['co_id']) and actor!=clan['leader_id']:raise ValueError('A co-leader cannot remove a leader or co-leader.')
            if await self._clan_busy(c,clan['id']):raise ValueError('Membership cannot change during a war.')
            if target==clan['leader_id']:
                if len(await self.members(clan['id'],c))>1:raise ValueError('Transfer leadership with ;clanlead @member before leaving.')
                await c.execute('DELETE FROM clans WHERE id=?',(clan['id'],))
                await c.execute("UPDATE clan_applications SET status='withdrawn' WHERE clan_id=? AND status='pending'",(clan['id'],))
            cur=await c.execute('DELETE FROM clan_members WHERE user_id=? AND clan_id=?',(target,clan['id']))
            if not cur.rowcount:raise ValueError('That person is not in your clan.')
            await c.execute('UPDATE clans SET co_id=NULL WHERE id=? AND co_id=?',(clan['id'],target))

    async def transfer_leader(self,actor,target):
        async with self.tx() as c:
            clan=await self._officer(c,actor,leader_only=True)
            if await self._clan_busy(c,clan['id']):raise ValueError('Leadership cannot change during a war.')
            member=await self.clan(user_id=target,conn=c)
            if target==actor or not member or member['id']!=clan['id']:raise ValueError('Choose another member of your clan.')
            await c.execute('UPDATE clans SET leader_id=?,co_id=CASE WHEN co_id=? THEN NULL ELSE co_id END WHERE id=?',(target,target,clan['id']))

    async def wallet(self,user_id):
        async with self.db.connect() as c:
            row=await self.one(c,'SELECT coins FROM clan_wallets WHERE user_id=?',(user_id,))
            return row['coins'] if row else 0

    async def _credit(self,c,uid,amount):
        await c.execute('INSERT INTO clan_wallets(user_id,coins) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET coins=coins+excluded.coins',(uid,amount))

    async def donate(self,user_id,card_id,day=None):
        day=day or local_date_key()
        async with self.tx() as c:
            clan=await self.clan(user_id=user_id,conn=c)
            if not clan:raise ValueError('You must be in a clan to donate.')
            daily=await self.one(c,'SELECT donations FROM clan_daily WHERE user_id=? AND day=?',(user_id,day))
            if daily and daily['donations']>=3:raise ValueError('You have used all three donations today.')
            row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(user_id,card_id,card_id))
            if not row:raise ValueError('That exact card is not in your collection.')
            if row['locked']:raise ValueError('Unlock the card before donating it.')
            card=self.db._card(row)
            payout=CHAR_BY_ID[card.character_id].rarity*10*(card.merge_level+1)
            members=await self.members(clan['id'],c)
            await self.db._clear_card_from_teams_tx(c,card.instance_id)
            await c.execute('DELETE FROM cards WHERE instance_id=?',(card.instance_id,))
            await c.execute('INSERT INTO clan_daily(user_id,day,donations) VALUES (?,?,1) ON CONFLICT(user_id,day) DO UPDATE SET donations=donations+1',(user_id,day))
            for member in members:await self._credit(c,member['user_id'],payout)
            return payout,len(members)

    async def checkin(self,user_id,username,day=None):
        day=day or local_date_key()
        async with self.tx() as c:
            clan=await self.clan(user_id=user_id,conn=c)
            if not clan:raise ValueError('You must be in a clan to check in.')
            old=await self.one(c,'SELECT checkin_clan FROM clan_daily WHERE user_id=? AND day=?',(user_id,day))
            if old and old['checkin_clan']:raise ValueError('You have already checked in today.')
            await c.execute('INSERT INTO clan_daily(user_id,day,checkin_clan) VALUES (?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET checkin_clan=excluded.checkin_clan',(user_id,day,clan['id']))
            await c.execute('UPDATE clan_members SET last_checkin=?,username=? WHERE user_id=?',(day,username,user_id))
            await self._credit(c,user_id,100)
            members=await self.members(clan['id'],c)
            count=await self.one(c,'SELECT COUNT(*) AS n FROM clan_daily d JOIN clan_members m ON d.user_id=m.user_id WHERE m.clan_id=? AND d.checkin_clan=? AND day=?',(clan['id'],clan['id'],day))
            bonus=len(members)==5 and count['n']==5
            if bonus:
                cur=await c.execute('INSERT OR IGNORE INTO clan_checkin_bonus(clan_id,day) VALUES (?,?)',(clan['id'],day))
                bonus=bool(cur.rowcount)
                if bonus:
                    for member in members:await self._credit(c,member['user_id'],100)
            return bonus,count['n']

    async def items(self,user_id):
        from .items import inventory_label
        async with self.db.connect() as c:
            rows=await self.rows(c,'SELECT i.*,c.instance_id AS equipped_on,c.character_id AS equipped_character FROM inventory_items i LEFT JOIN cards c ON c.equipped_item_id=i.id WHERE i.owner_id=?',(user_id,))
        names={'mergecopy':'Merge Copy','blessing':'Blessing','starpack':'Star Pack','pack_base':'Base Pack','pack_school':'School Pack','pack_position':'Position Pack'}
        return sorted(rows,key=lambda it:((inventory_label(it) or names.get(it['kind'],it['kind'])).casefold(),it['stars'],it['id']))

    async def shard_balances(self,user_id):
        async with self.db.connect() as c:
            return await self.rows(c,'SELECT kind,detail,quantity FROM shard_balances WHERE owner_id=? AND quantity>0 ORDER BY kind,detail',(user_id,))

    async def _issue_item(self,c,uid,kind,stars=0,detail=''):
        from .shard_balances import KINDS,credit
        if kind in KINDS:
            await credit(c,uid,kind,detail)
            return None
        iid=new_id('it-')
        await c.execute('INSERT INTO inventory_items(id,owner_id,kind,stars,detail,created_at) VALUES (?,?,?,?,?,?)',(iid,uid,kind,stars,detail,now()))
        return iid

    async def buy(self,uid,kind,stars):
        if kind not in ('mergecopy','blessing','starpack') or stars not in range(1,6):raise ValueError('Choose mergecopy, blessing or starpack with a star rating from 1 to 5.')
        cost=stars*{'mergecopy':150,'blessing':200,'starpack':100}[kind]
        async with self.tx() as c:
            if not await self.clan(user_id=uid,conn=c):raise ValueError('Join a clan to use its shop.')
            cur=await c.execute('UPDATE clan_wallets SET coins=coins-? WHERE user_id=? AND coins>=?',(cost,uid,cost))
            if not cur.rowcount:raise ValueError(f'You need {cost} clan coins.')
            return await self._issue_item(c,uid,kind,stars),cost

    async def merge_many(self,uid,survivor_id,consumed_ids):
        if not consumed_ids or len(consumed_ids)!=len(set(consumed_ids)):raise ValueError('Select at least one distinct material.')
        async with self.tx() as c:
            row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,survivor_id,survivor_id))
            if not row:raise ValueError('Card 1 must be a card you own; items cannot be survivors.')
            base=self.db._card(row)
            if base.locked:raise ValueError('Unlock Card 1 before merging.')
            level=base.merge_level;cards=[];items=[];seen={base.instance_id}
            for iid in consumed_ids:
                row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,iid,iid))
                if row:
                    card=self.db._card(row)
                    if card.instance_id in seen:raise ValueError('Every material must be distinct from Card 1 and other materials.')
                    seen.add(card.instance_id)
                    if card.locked or card.character_id!=base.character_id:raise ValueError('All card materials must be unlocked copies of the exact same character variant.')
                    level+=card.merge_level+1;cards.append(card.instance_id)
                else:
                    item=await self.one(c,'SELECT * FROM inventory_items WHERE id=? AND owner_id=?',(iid,uid))
                    if not item or item['kind']!='mergecopy' or item['stars']!=CHAR_BY_ID[base.character_id].rarity:
                        raise ValueError('Item materials must be owned Merge Copies matching Card 1’s star rating.')
                    level+=1;items.append(iid)
            if level>10:raise ValueError(f'That merge would create +{level}, above the +10 cap. Remove some materials.')
            for iid in cards:
                await self.db._clear_card_from_teams_tx(c,iid)
                await c.execute('DELETE FROM cards WHERE instance_id=?',(iid,))
            for iid in items:await c.execute('DELETE FROM inventory_items WHERE id=?',(iid,))
            await c.execute('UPDATE cards SET merge_level=? WHERE instance_id=?',(level,base.instance_id))
            base.merge_level=level
            return base

    async def bless(self,uid,item_id,card_id,boon,bane):
        async with self.tx() as c:
            item=await self.one(c,"SELECT * FROM inventory_items WHERE id=? AND owner_id=? AND kind='blessing'",(item_id,uid))
            row=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND (instance_id=? OR full_instance_id=?)',(uid,card_id,card_id))
            if not item or not row:raise ValueError('Choose an owned Blessing and an owned card.')
            char=CHAR_BY_ID[row['character_id']]
            if item['stars']!=char.rarity:raise ValueError('The Blessing must match the card’s star rating.')
            allowed={'hp',char.offense_key,'def','rec','spd','jmp'}
            boon,bane=boon.casefold(),bane.casefold()
            if boon not in allowed or bane not in allowed:raise ValueError('Choose usable stats: '+', '.join(sorted(allowed)))
            if boon==bane:boon=bane=None
            await c.execute('UPDATE cards SET boon_stat=?,bane_stat=?,nature_rolled=1 WHERE instance_id=?',(boon,bane,row['instance_id']))
            await c.execute('DELETE FROM inventory_items WHERE id=?',(item_id,))
            row['boon_stat'],row['bane_stat']=boon,bane
            return self.db._card(row)

    async def _issue_card(self,c,uid,username,char):
        serial=await self.db._next_serial(c,char.card_id)
        iid=uuid.uuid4().hex[:12];full_id=f'{char.card_id}:{serial:06d}'
        boon,bane=self.db._nature_for(iid,char.id)
        await c.execute('''INSERT INTO cards(instance_id,full_instance_id,card_id,character_id,owner_id,original_owner_id,original_owner_name,serial_no,merge_level,obtained_at,locked,boon_stat,bane_stat,nature_rolled)
            VALUES (?,?,?,?,?,?,?,?,0,?,0,?,?,1)''',(iid,full_id,char.card_id,char.id,uid,uid,username,serial,now(),boon,bane))
        return self.db._card(await self.one(c,'SELECT * FROM cards WHERE instance_id=?',(iid,)))

    async def open_item(self,uid,username,item_id):
        await self.db.ensure_user(uid)
        async with self.tx() as c:
            item=await self.one(c,'SELECT * FROM inventory_items WHERE id=? AND owner_id=?',(item_id,uid))
            if not item or item['kind'] not in ('starpack','pack_base','pack_school','pack_position'):raise ValueError('That owned item is not an unopened pack.')
            if item['kind']=='starpack':
                pool=[ch for ch in CHARACTERS if ch.rarity==item['stars']]
                chars=[random.choice(pool)]
                pack_type='starpack'
            else:
                pack_type=item['kind'][5:]
                detail={'school':item['detail']} if pack_type=='school' else {'position':item['detail']} if pack_type=='position' else {}
                chars=pull_pack(pack_type,**detail)
            pity=await self.one(c,'SELECT * FROM pity WHERE owner_id=?',(uid,))
            if pity and pity['character_id'] in CHAR_BY_ID:
                target=CHAR_BY_ID[pity['character_id']]
                eligible=pack_type=='base' or (pack_type=='starpack' and target.rarity==item['stars']) or (pack_type=='school' and target.school==item['detail']) or (pack_type=='position' and target.position.upper()==item['detail'].upper())
                result=resolve_pity_sequence(chars,target,armed=bool(pity['armed']),target_eligible=eligible)
                chars=result.cards
                if result.cleared:await c.execute('DELETE FROM pity WHERE owner_id=?',(uid,))
                else:await c.execute('UPDATE pity SET armed=? WHERE owner_id=?',(int(result.armed),uid))
            cards=[await self._issue_card(c,uid,username,char) for char in chars]
            await c.execute('DELETE FROM inventory_items WHERE id=?',(item_id,))
            return cards

    async def create_redeem(self,actor,code,prize,scope,expiry):
        if actor!=ADMIN_ID:raise ValueError('Only the designated administrator can create redeem codes.')
        code=code.strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]{2,40}',code):raise ValueError('Codes must be 2–40 letters, digits, underscores or hyphens.')
        if scope not in ('player','overall'):raise ValueError('Limit must be player (once per player) or overall (one total claim).')
        parsed=parse_prize(prize);expires_at=parse_expiry(expiry)
        async with self.tx() as c:
            if await self.one(c,'SELECT code FROM redeem_codes WHERE code=?',(code,)):raise ValueError('That code already exists; it cannot be overwritten.')
            await c.execute('INSERT INTO redeem_codes(code,prize_json,scope,created_by,created_at,expires_at) VALUES (?,?,?,?,?,?)',(code,json.dumps(parsed),scope,actor,now(),expires_at))
        return code,parsed

    async def redeem(self,uid,code):
        await self.db.ensure_user(uid)
        async with self.tx() as c:
            row=await self.one(c,'SELECT * FROM redeem_codes WHERE code=? COLLATE NOCASE',(code.strip(),))
            if not row:raise ValueError('That redeem code does not exist.')
            if row['expires_at'] is None or datetime.fromisoformat(now()).timestamp()>=row['expires_at']:raise ValueError('That redeem code has expired.')
            claims=await self.rows(c,'SELECT user_id FROM redeem_claims WHERE code=?',(row['code'],))
            if any(r['user_id']==uid for r in claims) or (row['scope']=='overall' and claims):raise ValueError('That code has already been redeemed for its allowed limit.')
            prize=json.loads(row['prize_json']);kind=prize['kind'];iid=None
            if kind=='coins':await c.execute('UPDATE users SET coins=coins+? WHERE discord_id=?',(prize['amount'],uid))
            elif kind=='clancoins':await self._credit(c,uid,prize['amount'])
            else:
                ids=[await self._issue_item(c,uid,kind,prize.get('stars',0),prize.get('detail','')) for _ in range(prize.get('count',1))]
                iid=ids[0] if len(ids)==1 else ids
            await c.execute('INSERT INTO redeem_claims(code,user_id,created_at) VALUES (?,?,?)',(row['code'],uid,now()))
            return prize,iid

    async def progress(self,uid):
        from .rewards import daily
        async with self.db.connect() as c:
            rewards=await daily(c,uid)
            check=await self.one(c,'SELECT * FROM clan_daily WHERE user_id=? AND day=?',(uid,local_date_key()))
        return {'war':rewards.get('war',{}).get('clan_coins',0),'war_gold':rewards.get('war',{}).get('coins',0),
                'prefecture':rewards.get('prefecture',{}).get('coins',0),'donations':check['donations'] if check else 0,
                'checked_in':bool(check and check['checkin_clan'])}

    def unpack(self,row):
        if not row:return None
        row=dict(row);row['state']=json.loads(row.pop('state_json'));return row

    async def session(self,sid,conn=None):
        if conn is None:
            async with self.db.connect() as c:return await self.session(sid,c)
        return self.unpack(await self.one(conn,'SELECT * FROM community_sessions WHERE id=?',(sid,)))

    async def sessions(self,kind=None):
        async with self.db.connect() as c:
            rows=await self.rows(c,'SELECT * FROM community_sessions'+(' WHERE kind=?' if kind else ''),(kind,) if kind else ())
            return [self.unpack(r) for r in rows]

    async def _save(self,c,row):
        await c.execute('UPDATE community_sessions SET status=?,state_json=?,updated_at=? WHERE id=?',(row['status'],json.dumps(row['state']),now(),row['id']))

    async def set_message(self,sid,mid):
        async with self.tx() as c:await c.execute('UPDATE community_sessions SET message_id=? WHERE id=?',(mid,sid))

    async def _create_session(self,c,kind,state,channel,status):
        sid=new_id('war-' if kind=='war' else 'pref-')
        await c.execute('INSERT INTO community_sessions(id,kind,status,state_json,channel_id,updated_at) VALUES (?,?,?,?,?,?)',(sid,kind,status,json.dumps(state),channel,now()))
        return await self.session(sid,c)

    async def challenge(self,actor,opponent_id,fmt,channel):
        if fmt not in (3,6):raise ValueError('Specify 3v3 or 6v6.')
        async with self.tx() as c:
            clan=await self._officer(c,actor)
            other=await self.clan(clan_id=opponent_id,conn=c)
            if not other or clan['id']==opponent_id:raise ValueError('Choose another existing clan.')
            for cl in (clan,other):
                if len(await self.members(cl['id'],c))!=5:raise ValueError('Both clans need exactly five members for a war.')
                if await self._clan_busy(c,cl['id']):raise ValueError('One of these clans is already in a war.')
            state={'clans':[clan['id'],other['id']],'format':fmt,'initiator':actor,'stage':'challenge','round':0,'rosters':[],
                   'eliminated':[[],[]],'wins':{},'series':[],'sends':[None,None],'teams':[None,None],'initiative':None}
            return await self._create_session(c,'war',state,channel,'challenge')

    async def respond_war(self,sid,actor,accept):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='war' or row['status']!='challenge':raise ValueError('That challenge is no longer pending.')
            cl=await self._officer(c,actor)
            if cl['id']!=row['state']['clans'][1]:raise ValueError('Only the challenged clan’s leader or co-leader can respond.')
            if not accept:row['status']='declined'
            else:
                rosters=[]
                for cid in row['state']['clans']:
                    if await self._clan_busy(c,cid):raise ValueError('A clan is already in another war.')
                    members=await self.members(cid,c)
                    if len(members)!=5:raise ValueError('Both clans must still have five members.')
                    rosters.append(members)
                row['state'].update(rosters=rosters,stage='bans',bans=[None,None],round=1)
                row['status']='active'
                # Close competing pending challenges to avoid accepting two wars.
                for other in await self.rows(c,"SELECT * FROM community_sessions WHERE kind='war' AND status='challenge' AND id<>?",(sid,)):
                    if set(json.loads(other['state_json'])['clans']) & set(row['state']['clans']):
                        await c.execute("UPDATE community_sessions SET status='cancelled',updated_at=? WHERE id=?",(now(),other['id']))
            await self._save(c,row);return row

    async def _war_officer_side(self,c,row,actor):
        cl=await self._officer(c,actor)
        if cl['id'] not in row['state']['clans']:raise ValueError('This war belongs to other clans.')
        return row['state']['clans'].index(cl['id'])

    async def ban_war_unit(self,sid,actor,character_id):
        """Commit one immutable, blind exact-variant ban per clan leader."""
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='war' or row['status']!='active' or row['state']['stage']!='bans':
                raise ValueError('That war is no longer waiting for bans.')
            clan=await self._officer(c,actor,leader_only=True)
            if clan['id'] not in row['state']['clans']:raise ValueError('This war belongs to other clans.')
            if character_id not in CHAR_BY_ID:raise ValueError('Choose a unit from the roster.')
            side=row['state']['clans'].index(clan['id']);bans=row['state']['bans']
            if bans[side] is not None:raise ValueError('Your clan’s ban is already locked.')
            bans[side]=character_id
            # Identical blind choices are legal; rejecting them would leak the opposing ban.
            if all(bans):row['state']['stage']='blind'
            await self._save(c,row);return row

    async def nominate(self,sid,actor,player,round_no):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['status']!='active':raise ValueError('That war is not waiting for sends.')
            state=row['state'];side=await self._war_officer_side(c,row,actor)
            if state['round']!=round_no:raise ValueError('That selection belongs to an earlier round.')
            if player not in [r['user_id'] for r in state['rosters'][side]] or player in state['eliminated'][side]:raise ValueError('Choose a clan member who has not been knocked out.')
            if state['sends'][side] is not None:raise ValueError('Your clan has already sent a player this round.')
            if state['stage']=='winner_send' and side!=state['initiative']:raise ValueError('The previous winner must send first.')
            if state['stage']=='response_send' and side==state['initiative']:raise ValueError('Wait for the other clan’s response.')
            if state['stage'] not in ('blind','winner_send','response_send'):raise ValueError('Sends are closed for this round.')
            state['sends'][side]=player
            if all(x is not None for x in state['sends']):state['stage']='teams'
            elif state['stage']=='winner_send':state['stage']='response_send'
            await self._save(c,row);return row

    async def _team_snapshot(self,c,uid,slot,fmt):
        row=await self.one(c,'SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(uid,slot))
        if not row:raise ValueError('That saved team no longer exists.')
        team=self.db._saved_team(row)
        if not team.complete or team.team_format!=fmt:raise ValueError(f'Choose a complete {fmt}v{fmt} team.')
        specs=[];names=set()
        for _,iid,lane in team.entries:
            cardrow=await self.one(c,'SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,iid))
            if not cardrow:raise ValueError('A selected team card is no longer owned by you.')
            card=self.db._card(cardrow);char=CHAR_BY_ID[card.character_id]
            if char.name in names:raise ValueError('Your team has a duplicate character name.')
            names.add(char.name);specs.append((card,char,lane))
        return snapshot_specs(specs)

    async def select_war_team(self,sid,uid,slot,round_no):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['status']!='active' or row['state']['stage']!='teams' or row['state']['round']!=round_no:raise ValueError('That team selection is no longer active.')
            st=row['state']
            if uid not in st['sends']:raise ValueError('Only a sent player can select their team.')
            side=st['sends'].index(uid)
            if st['teams'][side] is not None:raise ValueError('Your team is already locked for this series.')
            snapshot=await self._team_snapshot(c,uid,slot,st['format'])
            banned=set(st.get('bans',[]))-{None}
            if any(unit['character']['id'] in banned for unit in snapshot):
                raise ValueError('That team contains a banned unit. Edit it or choose another saved team.')
            st['teams'][side]=snapshot
            await self._save(c,row);return row

    async def resolve_claim(self,sid):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['status'] not in ('active','broken','running'):raise ValueError('That session is closed.')
            if row['kind']=='war' and not all(row['state']['teams']):raise ValueError('Both players must select teams first.')
            if row['kind']=='prefecture' and len(row['state']['players'])!=3:raise ValueError('Three players must join first.')
            row['status']='running';row['state']['stage']='resolving'
            await self._save(c,row);return row

    async def mark_broken(self,sid):
        async with self.tx() as c:await c.execute("UPDATE community_sessions SET status='broken',updated_at=? WHERE id=? AND status='running'",(now(),sid))

    async def _finish_war(self,c,row,winner):
        row['status']='complete';st=row['state'];st['winner']=winner;st['stage']='complete'
        from .rewards import award_in
        st['gold_rewards']={}
        for side,cid in enumerate(st['clans']):
            await c.execute('UPDATE clans SET '+('wins' if side==winner else 'losses')+'='+('wins' if side==winner else 'losses')+'+1 WHERE id=?',(cid,))
            for member in st['rosters'][side]:
                uid=member['user_id']
                payout=await award_in(c,uid,'war') if side==winner else {'coins':0,'clan_coins':0}
                st['gold_rewards'][str(uid)]=payout['coins']
                await c.execute('INSERT INTO community_rewards(session_id,user_id,amount) VALUES (?,?,?)',(row['id'],uid,payout['clan_coins']))
        st['teams']=[None,None]

    async def finish_series(self,sid,round_no,winner,score):
        if winner not in (0,1) or len(score)!=2 or sorted(score) not in ([0,2],[1,2]) or score[winner]!=2:raise ValueError('Invalid best-of-three result.')
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['status']!='running' or row['kind']!='war' or row['state']['round']!=round_no:raise ValueError('That series is already resolved or cancelled.')
            st=row['state'];loser=1-winner;a,b=st['sends']
            st['eliminated'][loser].append(st['sends'][loser])
            uid=st['sends'][winner];st['wins'][str(uid)]=st['wins'].get(str(uid),0)+1
            st['series'].append({'round':round_no,'players':[a,b],'winner':winner,'score':score})
            if len(st['eliminated'][loser])==5:await self._finish_war(c,row,winner)
            else:
                row['status']='active';st.update(stage='winner_send',round=round_no+1,initiative=winner,sends=[None,None],teams=[None,None])
            await self._save(c,row);return row

    async def forfeit(self,sid,actor):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='war' or row['status'] not in ('active','running','broken'):raise ValueError('That war is no longer active.')
            side=await self._war_officer_side(c,row,actor)
            row['state']['forfeit']=side
            await self._finish_war(c,row,1-side);await self._save(c,row);return row

    async def prefecture(self,host,username,channel,cpu_index):
        async with self.tx() as c:
            existing=await self.rows(c,"SELECT * FROM community_sessions WHERE kind='prefecture' AND status IN ('open','running','broken')")
            for row in existing:
                s=json.loads(row['state_json'])
                if s['host']==host or host in [p['user_id'] for p in s['players']]:raise ValueError('You already have an unresolved Prefecture lobby. Reopen it with ;pref <ID> or use ;refresh.')
            return await self._create_session(c,'prefecture',{'host':host,'host_name':username,'players':[],'cpu_index':cpu_index,'stage':'lobby'},channel,'open')

    async def join_prefecture(self,sid,uid,username,slot):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='prefecture' or row['status']!='open':raise ValueError('That lobby is no longer open.')
            players=row['state']['players']
            if any(p['user_id']==uid for p in players):raise ValueError('You already joined this lobby.')
            if len(players)>=3:raise ValueError('That lobby is full.')
            for other in await self.rows(c,"SELECT * FROM community_sessions WHERE kind='prefecture' AND status IN ('open','running','broken') AND id<>?",(sid,)):
                if uid in [p['user_id'] for p in json.loads(other['state_json'])['players']]:raise ValueError('Leave your other Prefecture lobby first.')
            snapshot=await self._team_snapshot(c,uid,slot,3)
            used={r['character']['name'] for p in players for r in p['snapshot']}
            if used & {r['character']['name'] for r in snapshot}:raise ValueError('Your pod repeats a character already submitted by another player; choose a different 3v3 team.')
            players.append({'user_id':uid,'username':username,'snapshot':snapshot})
            await self._save(c,row);return row

    async def start_prefecture(self,sid,actor):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['status']!='open' or row['kind']!='prefecture':raise ValueError('That Prefecture lobby is not open.')
            if row['state']['host']!=actor:raise ValueError('Only the host can start this lobby.')
            if len(row['state']['players'])!=3:raise ValueError('Three players must join first.')
            row['status']='running';row['state']['stage']='resolving';await self._save(c,row);return row

    async def finish_prefecture(self,sid,result):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='prefecture' or row['status']!='running':raise ValueError('This Prefecture battle has already resolved or closed.')
            reward_day=local_date_key()
            for player in row['state']['players']:
                uid=player['user_id'];amount=0
                if result['winner_side']=='A':
                    from .rewards import award_in
                    amount=(await award_in(c,uid,'prefecture',day=reward_day))['coins']
                await c.execute('INSERT INTO community_rewards(session_id,user_id,amount) VALUES (?,?,?)',(sid,uid,amount))
            row['status']='complete';row['state']['result']=result;await self._save(c,row);return row

    async def rewards(self,sid):
        async with self.db.connect() as c:return await self.rows(c,'SELECT * FROM community_rewards WHERE session_id=?',(sid,))

    async def refresh_session(self,sid,uid,*,broken=False):
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row:raise ValueError('Session not found.')
            st=row['state']
            if row['kind']=='war':
                clan=await self.clan(user_id=uid,conn=c)
                if not clan or clan['id'] not in st['clans']:raise ValueError('That war belongs to other clans.')
                if row['status'] not in ('challenge','broken') and not (broken and row['status'] in ('active','running')):
                    raise ValueError('A functioning war cannot be closed with refresh. Use Forfeit to concede.')
                row['status']='cancelled';st['stage']='cancelled';st['teams']=[None,None]
            else:
                if uid!=st['host'] and uid not in [p['user_id'] for p in st['players']]:raise ValueError('You are not part of that lobby.')
                if row['status']=='open':
                    if uid==st['host']:row['status']='cancelled'
                    else:st['players']=[p for p in st['players'] if p['user_id']!=uid]
                elif row['status']=='broken' or (broken and row['status']=='running'):row['status']='cancelled'
                else:raise ValueError('That battle is already complete or is running normally.')
            await self._save(c,row);return row
