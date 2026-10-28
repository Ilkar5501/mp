"""Transactional tournament state and independent daily reward allowances."""
from __future__ import annotations
import json
import random
import uuid
from datetime import datetime,timezone
from .data import CHAR_BY_ID
from .tournament_cpu import cpu_specs,is_cpu
from .tournament import champion,next_match,result_dict,snapshot_specs

SCHEMA='''
CREATE TABLE IF NOT EXISTS tournaments (
 id TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
 creator_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 seeds_json TEXT NOT NULL DEFAULT '[]', lobby_message_id INTEGER,
 champion_id INTEGER, created_at TEXT NOT NULL, finished_at TEXT,
 results_message_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS tournament_live_channel ON tournaments(channel_id)
 WHERE status IN ('open','running');
CREATE TABLE IF NOT EXISTS tournament_entries (
 tournament_id TEXT NOT NULL,owner_id INTEGER NOT NULL,display_name TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,joined_at TEXT NOT NULL,
 PRIMARY KEY(tournament_id,owner_id)
);
CREATE TABLE IF NOT EXISTS tournament_matches (
 tournament_id TEXT NOT NULL,match_key TEXT NOT NULL,owner_a INTEGER NOT NULL,
 owner_b INTEGER NOT NULL,winner_id INTEGER NOT NULL,loser_id INTEGER NOT NULL,
 result_json TEXT NOT NULL,notified_a INTEGER NOT NULL DEFAULT 0,
 notified_b INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(tournament_id,match_key)
);
CREATE TABLE IF NOT EXISTS tournament_rewards (
 tournament_id TEXT NOT NULL,owner_id INTEGER NOT NULL,kind TEXT NOT NULL,
 amount INTEGER NOT NULL,day_key TEXT NOT NULL,PRIMARY KEY(tournament_id,owner_id)
);
CREATE TABLE IF NOT EXISTS tournament_daily (
 owner_id INTEGER NOT NULL,day_key TEXT NOT NULL,win_coins INTEGER NOT NULL DEFAULT 0,
 participation_coins INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner_id,day_key)
);
'''


def now():return datetime.now(timezone.utc).isoformat()


class TournamentStore:
    def __init__(self,db):self.db=db

    async def get(self,tid):
        async with self.db.connect() as db:
            cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));row=await cur.fetchone()
        return dict(row) if row else None

    async def entries(self,tid):
        async with self.db.connect() as db:
            cur=await db.execute('SELECT * FROM tournament_entries WHERE tournament_id=? ORDER BY joined_at,owner_id',(tid,));rows=await cur.fetchall()
        return {r['owner_id']:dict(r) for r in rows}

    async def matches(self,tid):
        async with self.db.connect() as db:
            cur=await db.execute('SELECT * FROM tournament_matches WHERE tournament_id=? ORDER BY rowid',(tid,));rows=await cur.fetchall()
        return {r['match_key']:dict(r) for r in rows}

    async def pending(self):
        async with self.db.connect() as db:
            cur=await db.execute("SELECT * FROM tournaments WHERE status IN ('open','running') OR (status='complete' AND results_message_id IS NULL)");rows=await cur.fetchall()
        return [dict(r) for r in rows]

    async def create_or_get(self,channel_id,guild_id,creator_id):
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute("SELECT * FROM tournaments WHERE channel_id=? AND status IN ('open','running')",(channel_id,));row=await cur.fetchone()
            if not row:
                tid=uuid.uuid4().hex
                await db.execute('INSERT INTO tournaments(id,channel_id,guild_id,creator_id,created_at) VALUES (?,?,?,?,?)',(tid,channel_id,guild_id,creator_id,now()))
                cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));row=await cur.fetchone()
            await db.commit()
        return dict(row)

    async def message(self,tid,field,message_id):
        if field not in ('lobby_message_id','results_message_id'):raise ValueError('Invalid message field.')
        async with self.db._lock,self.db.connect() as db:
            await db.execute(f'UPDATE tournaments SET {field}=? WHERE id=?',(message_id,tid));await db.commit()

    async def join(self,tid,owner,display_name,slot):
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));t=await cur.fetchone()
            if not t or t['status']!='open':raise ValueError('This lobby is no longer open.')
            cur=await db.execute('SELECT owner_id FROM tournament_entries WHERE tournament_id=?',(tid,));owners=[r[0] for r in await cur.fetchall()]
            if owner in owners:raise ValueError('You have already joined this tournament.')
            if len(owners)>=8:raise ValueError('This tournament is full.')
            cur=await db.execute('SELECT * FROM users WHERE discord_id=?',(owner,));u=await cur.fetchone()
            if not u or not u['tutorial_started']:raise ValueError('Complete `/start` before joining a tournament.')
            # Prevent the same account competing in simultaneous tournaments.
            cur=await db.execute("SELECT 1 FROM tournament_entries e JOIN tournaments t ON t.id=e.tournament_id WHERE e.owner_id=? AND t.status IN ('open','running')",(owner,))
            if await cur.fetchone():raise ValueError('You are already entered in another live tournament.')
            cur=await db.execute('SELECT * FROM saved_teams WHERE owner_id=? AND slot=?',(owner,slot));row=await cur.fetchone()
            team=self.db._saved_team(row) if row else None
            if not team or team.team_format!=6 or not team.complete:raise ValueError('Choose a complete 6v6 team.')
            specs=[];names=[]
            for _,iid,lane in team.entries:
                cur=await db.execute('SELECT * FROM cards WHERE instance_id=? AND owner_id=?',(iid,owner));row=await cur.fetchone()
                if not row:raise ValueError('A selected team member is no longer in your collection.')
                card=self.db._card(row);c=CHAR_BY_ID[card.character_id];specs.append((card,c,lane));names.append(c.name)
            if len(names)!=len(set(names)):raise ValueError('A team can use only one version of each character.')
            await db.execute('INSERT INTO tournament_entries VALUES (?,?,?,?,?)',(tid,owner,display_name[:80],json.dumps(snapshot_specs(specs)),now()))
            owners.append(owner);started=len(owners)==8
            if started:
                random.SystemRandom().shuffle(owners)
                await db.execute("UPDATE tournaments SET status='running',seeds_json=? WHERE id=?",(json.dumps(owners),tid))
            await db.commit()
        return started

    async def auto_fill(self,tid,host):
        """Fill and start atomically so join/double-click races cannot exceed eight."""
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));t=await cur.fetchone()
            if not t or t['status']!='open':raise ValueError('This lobby is no longer open.')
            if t['creator_id']!=host:raise ValueError('Only the host can auto-fill this lobby.')
            cur=await db.execute('SELECT owner_id FROM tournament_entries WHERE tournament_id=?',(tid,));owners=[row[0] for row in await cur.fetchall()]
            if len(owners)>=8:raise ValueError('This tournament is already full.')
            added=0;number=1
            while len(owners)<8:
                while -number in owners:number+=1
                specs=cpu_specs(tid,number)
                await db.execute('INSERT INTO tournament_entries VALUES (?,?,?,?,?)',
                    (tid,-number,f'CPU {number}',json.dumps(snapshot_specs(specs)),now()))
                owners.append(-number);number+=1;added+=1
            random.SystemRandom().shuffle(owners)
            await db.execute("UPDATE tournaments SET status='running',seeds_json=? WHERE id=?",(json.dumps(owners),tid))
            await db.commit()
        return added

    async def leave(self,tid,owner):
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute("SELECT 1 FROM tournaments WHERE id=? AND status='open'",(tid,))
            if not await cur.fetchone():raise ValueError('You can leave only before the bracket starts.')
            await db.execute('DELETE FROM tournament_entries WHERE tournament_id=? AND owner_id=?',(tid,owner));await db.commit()

    async def cancel(self,tid,owner):
        async with self.db._lock,self.db.connect() as db:
            cur=await db.execute("UPDATE tournaments SET status='cancelled' WHERE id=? AND creator_id=? AND status='open'",(tid,owner))
            if not cur.rowcount:raise ValueError('Only the host can cancel an open lobby.')
            await db.commit()

    async def record_match(self,tid,key,a,b,result):
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));t=await cur.fetchone()
            if not t or t['status']!='running':raise ValueError('Tournament is not running.')
            cur=await db.execute('SELECT * FROM tournament_matches WHERE tournament_id=?',(tid,));matches={r['match_key']:dict(r) for r in await cur.fetchall()}
            if key in matches:
                await db.commit();return False
            if next_match(json.loads(t['seeds_json']),matches)!=(key,a,b):raise ValueError('Match does not match the next bracket pairing.')
            if result.winner_side not in ('A','B'):raise ValueError('Tournament matches must have a winner.')
            winner,loser=(a,b) if result.winner_side=='A' else (b,a)
            await db.execute('INSERT INTO tournament_matches(tournament_id,match_key,owner_a,owner_b,winner_id,loser_id,result_json) VALUES (?,?,?,?,?,?,?)',(tid,key,a,b,winner,loser,json.dumps(result_dict(result))))
            await db.commit();return True

    async def mark_notice(self,tid,key,side,delivered):
        column='notified_a' if side=='A' else 'notified_b'
        async with self.db._lock,self.db.connect() as db:
            await db.execute(f'UPDATE tournament_matches SET {column}=? WHERE tournament_id=? AND match_key=?',(1 if delivered else -1,tid,key));await db.commit()

    async def daily(self,owner,day):
        from .rewards import RewardStore
        row=(await RewardStore(self.db).daily(owner,day)).get('tournament',{})
        return {'win_coins':row.get('coins',0),'wins':row.get('wins',0),'participation_coins':0}

    async def rewards(self,tid):
        async with self.db.connect() as db:
            cur=await db.execute('SELECT * FROM tournament_rewards WHERE tournament_id=?',(tid,));rows=await cur.fetchall()
        return {r['owner_id']:dict(r) for r in rows}

    async def finish(self,tid,day):
        # Bracket completion, wallet credit and reward allowances commit together.
        async with self.db._lock,self.db.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur=await db.execute('SELECT * FROM tournaments WHERE id=?',(tid,));t=await cur.fetchone()
            if not t:raise ValueError('Tournament not found.')
            if t['status']=='complete':await db.commit();return t['champion_id']
            if t['status']!='running':raise ValueError('Tournament is not running.')
            cur=await db.execute('SELECT * FROM tournament_matches WHERE tournament_id=?',(tid,));matches={r['match_key']:dict(r) for r in await cur.fetchall()}
            if next_match(json.loads(t['seeds_json']),matches) is not None:raise ValueError('The bracket is not complete.')
            winner=champion(matches)
            cur=await db.execute('SELECT owner_id FROM tournament_entries WHERE tournament_id=?',(tid,));owners=[r[0] for r in await cur.fetchall()]
            for owner in owners:
                if is_cpu(owner):continue
                from .rewards import award_in
                payout=await award_in(db,owner,'tournament',day=day,human_tournament=len(owners)==8 and not any(is_cpu(uid) for uid in owners)) if owner==winner else {'coins':0}
                amount=payout['coins'];kind='win' if owner==winner else 'participation'
                await db.execute('INSERT INTO tournament_rewards(tournament_id,owner_id,kind,amount,day_key,champion_coins,participation_coins) VALUES (?,?,?,?,?,?,?)',(tid,owner,kind,amount,day,amount,0))
            await db.execute("UPDATE tournaments SET status='complete',champion_id=?,finished_at=? WHERE id=?",(winner,now(),tid));await db.commit()
        return winner
