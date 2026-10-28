"""9v9 pod targeting with team-wide effects and local save priority."""
from __future__ import annotations
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .battle.engine import BattleEngine, has
from .config import DATA_DIR
from .tournament import play_match

FORMATIONS=json.loads((DATA_DIR/'prefectures.json').read_text(encoding='utf-8'))


def cpu_specs(index):
    from .data import CHAR_BY_ID
    from .models import CardInstance
    from datetime import datetime, timezone
    formation=FORMATIONS[index]
    def neutral_card(char,i):
        return CardInstance(f'pref-cpu-{index}-{i}',f'PREF-CPU:{index}:{i}',char.card_id,char.id,
                            0,0,formation['name'],i,0,datetime(2026,1,1,tzinfo=timezone.utc),True,None,None)
    return [(neutral_card(CHAR_BY_ID[cid],i),CHAR_BY_ID[cid],lane)
            for i,(cid,lane) in enumerate([r for pod in formation['pods'] for r in pod])]


class PodBattleEngine(BattleEngine):
    def __init__(self,a,b,**kwargs):
        if len(a)!=9 or len(b)!=9:raise ValueError('Prefecture teams must contain nine units in three pods.')
        if any(len({c.name for _,c,_ in specs})!=9 for specs in (a,b)):raise ValueError('A Prefecture team cannot repeat character names.')
        super().__init__(a,b,**kwargs)
        self.pod_by_uid={u.uid:i//3 for team in (self.team_a,self.team_b) for i,u in enumerate(team.units)}

    def targeting_distribution(self,defending_team,offense_key,candidates=None,*,actor=None):
        if candidates is not None or not hasattr(self,'pod_by_uid'):
            return super().targeting_distribution(defending_team,offense_key,candidates,actor=actor)
        out={}
        for pod in range(3):
            units=[u for u in defending_team.units if self.pod_by_uid[u.uid]==pod]
            for uid,weight in super().targeting_distribution(defending_team,offense_key,units,actor=actor).items():out[uid]=weight/3
        return out

    def choose_target(self,actor):
        team=self.enemy_team(actor)
        pod=self.rng.randrange(3)
        enemies=[u for u in team.units if self.pod_by_uid[u.uid]==pod]
        hunted=[u for u in enemies if 'Hunted' in u.statuses]
        if hunted:return hunted[0],True
        if has(actor,'Teradomari'):return min(enemies,key=lambda u:u.noncombat_stat('def')+u.noncombat_stat('rec')),True
        candidates=enemies
        if has(actor,'Gao'):
            backs=[u for u in enemies if u.lane=='back']
            if backs:candidates=backs
        dist=super().targeting_distribution(team,actor.offense_key,candidates,actor=actor)
        return self.rng.choices(candidates,weights=[dist[u.uid] for u in candidates],k=1)[0],False

    def maybe_save(self,attacker,intended):
        if has(attacker,'Kiryu') or 'Free Ball' in intended.statuses:return intended,False
        eligible=[u for u in self.team(intended).units if u is not intended and u.cd==0 and 'UltimateDisabled' not in u.statuses
                  and (has(u,'Hirugami') or has(u,'Konoha') or (has(u,'Nishinoya') and attacker.offense_key=='qik'))]
        if not eligible:return intended,False
        local=[u for u in eligible if self.pod_by_uid[u.uid]==self.pod_by_uid[intended.uid]]
        savior=max(local,key=lambda u:u.noncombat_stat('spd')) if local else self.rng.choice(eligible)
        self.trigger_ultimate(savior,None,'save')
        return savior,True


def play_series(sid,round_no,a,b,uid_a,uid_b,name_a,name_b):
    """Fresh seeded battles, first to two. Draws resolve by remaining HP percentage."""
    score=[0,0];games=[]
    for game in range(1,4):
        seed=int.from_bytes(hashlib.sha256(f'{sid}:{round_no}:{game}'.encode()).digest()[:8],'big')
        engine=BattleEngine(a,b,owner_a=uid_a,owner_b=uid_b,name_a=name_a,name_b=name_b,mode='clanwar',seed=seed)
        result=engine.run()
        if result.winner_side not in ('A','B'):
            af=result.team_a_hp/engine.team_a.max_hp;bf=result.team_b_hp/engine.team_b.max_hp
            winner='A' if af>bf else 'B' if bf>af else engine.rng.choice(['A','B'])
            result=replace(result,winner_side=winner)
            result.log.append('Series draw tiebreak: remaining team HP percentage; seeded coin toss for an exact tie.')
        winner=0 if result.winner_side=='A' else 1
        score[winner]+=1;games.append(result)
        if score[winner]==2:return winner,score,games
    raise RuntimeError('Best-of-three did not finish.')
