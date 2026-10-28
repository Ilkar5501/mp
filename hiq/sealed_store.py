"""Atomic, restart-safe Sealed sessions in the existing session store."""
from __future__ import annotations
import json,time
from .community_store import CommunityStore,new_id,now
from .data import CHAR_BY_ID
from .sealed import SEALED_LIFETIME,make_draft,pick,bracket,team_specs,validate_team
from .tournament import snapshot_specs

LIVE=('drafting','building','running','broken')


class SealedStore(CommunityStore):
    async def create(self,users,channel):
        st=make_draft(users,list(CHAR_BY_ID));st['expires_at']=time.time()+SEALED_LIFETIME
        uidset={u['id'] for u in users}
        async with self.tx() as c:
            rows=[self.unpack(r) for r in await self.rows(c,"SELECT * FROM community_sessions WHERE kind='sealed'")]
            for row in rows:
                if row['status'] not in LIVE:continue
                if row['channel_id']==channel or time.time()>=row['state']['expires_at']:
                    row['status']='expired';row['state']['stage']='expired';row['state']['expiration_reason']='A new Sealed Draft started in this channel.' if row['channel_id']==channel else '30 minutes elapsed.'
                    await self._save(c,row)
                elif uidset.intersection(row['state']['order']):raise ValueError('A participant already has a live Sealed Draft in another channel.')
            sid=new_id('sealed-')
            await c.execute('INSERT INTO community_sessions(id,kind,status,state_json,channel_id,updated_at) VALUES (?,?,?,?,?,?)',(sid,'sealed','drafting',json.dumps(st),channel,now()))
            return await self.session(sid,c)

    async def current(self,c,sid,uid=None,stage=None):
        row=await self.session(sid,c)
        if not row or row['kind']!='sealed' or row['status'] not in LIVE or time.time()>=row['state']['expires_at']:raise ValueError('This Sealed Draft is no longer active.')
        if uid is not None and uid not in row['state']['order']:raise ValueError('You are not a participant in this Sealed Draft.')
        if stage is not None and row['status']!=stage:raise ValueError('That phase of this draft has ended.')
        return row

    async def read_active(self,sid,uid,stage=None):
        async with self.db.connect() as c:return await self.current(c,sid,uid,stage)

    async def choose(self,sid,uid,pack_id,count,cid):
        async with self.tx() as c:
            row=await self.current(c,sid,uid,'drafting')
            recipient,wake=pick(row['state'],uid,pack_id,count,cid)
            row['status']=row['state']['stage'];await self._save(c,row)
            return row,recipient,wake

    async def place(self,sid,uid,position,cid,version):
        async with self.tx() as c:
            row=await self.current(c,sid,uid,'building');st=row['state'];key=str(uid)
            if st['team_versions'].get(key,0)!=version:raise ValueError('Your team changed in another menu. Reopen Edit Team.')
            if cid not in st['picks'][key] or position not in range(6):raise ValueError('Choose one of your drafted characters.')
            team=st['draft_teams'].setdefault(key,[None]*6)
            for i,other in enumerate(team):
                if i!=position and other and CHAR_BY_ID[other].name==CHAR_BY_ID[cid].name:team[i]=None
            team[position]=cid;st['teams'].pop(key,None)
            st['team_versions'][key]=version+1
            await self._save(c,row);return row

    async def submit(self,sid,uid,version):
        async with self.tx() as c:
            row=await self.current(c,sid,uid,'building');st=row['state'];key=str(uid)
            if st['team_versions'].get(key,0)!=version:raise ValueError('Your team changed. Reopen Edit Team before submitting.')
            cids=st['draft_teams'].get(key,[]);validate_team(st['picks'][key],cids)
            st['teams'][key]=snapshot_specs(team_specs(sid,uid,cids))
            if len(st['teams'])==len(st['order']):
                row['status']='running';st.update(stage='running',bracket=bracket(st['order']),bracket_round=1,match_index=0,advancing=[])
            await self._save(c,row);return row

    async def record(self,sid,round_no,index,winner,score,games):
        async with self.tx() as c:
            row=await self.current(c,sid,stage='running');st=row['state']
            if (st['bracket_round'],st['match_index'])!=(round_no,index):raise ValueError('This tournament match was already recorded.')
            pair=st['bracket'][index]
            if winner not in pair:raise ValueError('Invalid tournament winner.')
            st['matches'].append(dict(round=round_no,index=index,players=pair,winner=winner,score=score,games=games))
            st['advancing'].append(winner);st['match_index']+=1
            if st['match_index']==len(st['bracket']):
                winners=st['advancing']
                if len(winners)==1:row['status']='complete';st.update(stage='complete',champion=winners[0])
                else:st.update(bracket=[list(p) for p in zip(winners[::2],winners[1::2])],bracket_round=round_no+1,match_index=0,advancing=[])
            await self._save(c,row);return row

    async def expire(self):
        async with self.tx() as c:
            for raw in await self.rows(c,"SELECT * FROM community_sessions WHERE kind='sealed' AND status IN ('drafting','building','running','broken')"):
                row=self.unpack(raw)
                if row['status'] in LIVE and time.time()>=row['state']['expires_at']:
                    row['status']='expired';row['state']['stage']='expired';row['state']['expiration_reason']='30 minutes elapsed.';await self._save(c,row)

    async def mark_notice(self,sid,key):
        async with self.tx() as c:
            row=await self.session(sid,c);row['state'][key]=True;await self._save(c,row)
