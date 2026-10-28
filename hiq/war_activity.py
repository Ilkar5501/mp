"""Persisted, per-side 24-hour war activity windows."""
from __future__ import annotations
import time
from datetime import datetime

WAR_WINDOW=24*60*60


class WarActivity:
    def open_war_round(self,st,initiative=None):
        deadline=time.time()+WAR_WINDOW
        st.update(stage='blind' if initiative is None else 'winner_send',initiative=initiative,
                  sends=[None,None],teams=[None,None],auto_losses=[],
                  deadlines=[deadline,deadline] if initiative is None else [deadline if s==initiative else None for s in (0,1)])

    def war_clock(self,row):
        st=row['state']
        if 'deadlines' not in st:
            deadline=datetime.fromisoformat(row['updated_at']).timestamp()+WAR_WINDOW
            st['deadlines']=[deadline,deadline]
            if st['stage']=='winner_send':st['deadlines'][1-st['initiative']]=None
        st.setdefault('auto_losses',[])
        st.setdefault('size',5)
        st.setdefault('participants',[[],[]])

    def war_action_open(self,row,side):
        self.war_clock(row)
        deadline=row['state']['deadlines'][side]
        if deadline is None or time.time()>=deadline or side in row['state']['auto_losses']:
            raise ValueError('Your activity window is closed. Reopen ;clanwarview for the current state.')

    def eligible_war_members(self,st,side):
        chosen=st.get('participants',[[],[]])[side]
        return [m for m in st['rosters'][side] if m['user_id'] not in st['eliminated'][side]
                and (len(chosen)<st.get('size',5) or m['user_id'] in chosen)]

    async def expire_war(self,sid,at=None):
        at=time.time() if at is None else at
        async with self.tx() as c:
            row=await self.session(sid,c)
            if not row or row['kind']!='war' or row['status']!='active':return False
            st=row['state'];migrated='deadlines' not in st;self.war_clock(row)
            expired=[s for s,d in enumerate(st['deadlines']) if d is not None and at>=d]
            if st['stage']=='bans':
                missing=[s for s in expired if st['bans'][s] is None]
            else:missing=[s for s in expired if st['sends'][s] is None]
            if missing:
                st['inactivity_reason']='Auto-forfeit due to inactivity: missing '+('ban' if st['stage']=='bans' else 'player send')
                if len(missing)==2:row['status']='cancelled';st['stage']='cancelled';st['inactivity_reason']='Both clans inactive; cancelled without wins or losses.'
                else:st['forfeit']=missing[0];await self._finish_war(c,row,1-missing[0])
                await self._save(c,row);return True
            changed=migrated
            if st['stage']!='bans':
                for side in expired:
                    if st['sends'][side] is not None and st['teams'][side] is None and side not in st['auto_losses']:
                        st['auto_losses'].append(side);changed=True
                ready=[st['teams'][s] is not None or s in st['auto_losses'] for s in (0,1)]
                if all(ready) and st['auto_losses']:
                    losers=list(st['auto_losses']);winner=1-losers[0] if len(losers)==1 else None
                    for side in losers:
                        uid=st['sends'][side]
                        if uid not in st['eliminated'][side]:st['eliminated'][side].append(uid)
                    if winner is not None:
                        uid=st['sends'][winner];st['wins'][str(uid)]=st['wins'].get(str(uid),0)+1
                    st['series'].append(dict(round=st['round'],players=list(st['sends']),winner=winner,
                                             score=[2 if s==winner else 0 for s in (0,1)],auto_losses=losers,
                                             reason='Auto-loss due to inactivity'))
                    exhausted=[s for s in (0,1) if len(st['eliminated'][s])>=st['size']]
                    if len(exhausted)==2:
                        row['status']='cancelled';st['stage']='cancelled';st['inactivity_reason']='Both rosters eliminated by inactivity; no clan result.'
                    elif exhausted:await self._finish_war(c,row,1-exhausted[0])
                    else:
                        st['round']+=1;self.open_war_round(st,winner)
                    changed=True
            if changed:await self._save(c,row)
            return changed
