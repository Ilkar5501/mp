"""Mode-local Tiny Titans boosts and symmetric All For One carrier battles."""
import json
from dataclasses import replace
from datetime import datetime,timezone
from .battle.engine import BattleEngine,has
from .config import DATA_DIR
from .data import CHAR_BY_ID,CHARACTERS
from .models import CardInstance
from .contest import support_slots

EVENT_TEAMS=json.loads((DATA_DIR/'event_teams.json').read_text(encoding='utf-8'))

def af1_support_slots(char):
    return ('C',) if char.name=='Shibayama' else support_slots(char)

def boosted_character(char):
    bonus=max(0,2*(5-char.rarity))
    stats=dict(char.stats)
    for key in ('hp',char.offense_key,'def','rec','spd','jmp'):stats[key]+=bonus
    stats['bst']=sum(stats[key] for key in ('hp','pow','qik','def','rec','spd','jmp'))
    return replace(char,stats=stats)

def validate_af1(specs,*,complete=False):
    if not 1<=len(specs)<=7:raise ValueError('Choose a main and up to six supports.')
    if any(c.name=='Fukunaga' for _,c,_ in specs):raise ValueError('Fukunaga cannot enter AF1.')
    if len({c.id for _,c,_ in specs})!=len(specs):raise ValueError('Use different card variants.')
    from .lineups import validate_duos
    validate_duos([c for _,c,_ in specs])
    if any(lane not in ('front','back') for _,_,lane in specs):raise ValueError('Choose front or back.')

def cpu_specs(mode,index):
    row=EVENT_TEAMS[mode][index]
    result=[]
    for i,u in enumerate(row['units']):
        c=CHAR_BY_ID[u['character_id']]
        card=CardInstance(f'event:{mode}:{index}:{i}',f'EVENT:{mode}:{index}:{i}',c.card_id,c.id,0,0,row['name'],i,
                          u.get('merge',row.get('merge',0)),datetime(2026,1,1,tzinfo=timezone.utc),True,
                          u.get('boon'),u.get('bane'),equipment_key=u.get('equipment_key'))
        result.append((card,c,u['lane']))
    return result

class TinyTitansEngine(BattleEngine):
    def __init__(self,a,b,**kwargs):
        for team in (a,b):
            if len(team)!=6 or len({c.name for _,c,_ in team})!=6:raise ValueError('Tiny Titans uses normal 6v6 teams.')
            if sum(lane=='front' for _,_,lane in team)!=3:raise ValueError('Use three front and three back.')
        # Replace templates only. Collection cards and ordinary battles are unchanged.
        super().__init__([(card,boosted_character(c),lane) for card,c,lane in a],
                         [(card,boosted_character(c),lane) for card,c,lane in b],mode='tiny_titans',**kwargs)

class ActiveTeam:
    """Enemy effects see the opposing main, never its inactive supports."""
    def __init__(self,team,main):
        object.__setattr__(self,'team',team);object.__setattr__(self,'units',[main])
    def __getattr__(self,key):return getattr(self.team,key)
    def __setattr__(self,key,value):setattr(self.team,key,value)
    def fronts(self):return [u for u in self.units if u.lane=='front']
    def backs(self):return [u for u in self.units if u.lane=='back']

class AllForOneEngine(BattleEngine):
    def __init__(self,a,b,**kwargs):
        for specs in (a,b):validate_af1(specs)
        # Supports retain owned stats/merges/natures for team comparisons; only
        # the main's equipment functions. This avoids six passive item engines.
        def prepared(specs):
            return [(replace(card,equipment_key=None,equipped_item_id=None) if i and card else card,c,lane)
                    for i,(card,c,lane) in enumerate(specs)]
        super().__init__(prepared(a),prepared(b),mode='all_for_one',**kwargs)
        self.mains={side:t.units[0] for side,t in self.teams.items()}
        self.support_ids={u.uid for t in self.teams.values() for u in t.units[1:]}
        for side,t in self.teams.items():
            main=self.mains[side];main.base['hp']*=6;t.hp=t.max_hp=main.base['hp']
            for u in t.units[1:]:u.cd=u.max_cd=999999

    def enemy_team(self,u):
        team=super().enemy_team(u)
        return ActiveTeam(team,self.mains[team.side]) if hasattr(self,'mains') else team

    def skill_enabled(self,u,slot):
        return u.uid not in getattr(self,'support_ids',set()) or slot in af1_support_slots(u.character)

    def effect_recipients(self,source,*,include_self=False):
        main=self.mains[source.side]
        return [main] if include_self or main is not source else []

    def expansion_bond_penalty(self,ctx,unit):
        foes=self.teams['B' if unit.side=='A' else 'A'].units
        bond=any(a is not unit and a.character.school==unit.character.school for a in self.team(unit).units)
        return int(bond and any(a.name=='Nozawa' and self.skill_enabled(a,'C') and self.source_allowed(ctx,a) for a in foes))

    def apply_match_start(self):
        super().apply_match_start()
        # Shirabu A is a carrier skill. Its target must be an active ally.
        # No AF1 carrier has another active ally, so its designation is inert.
        for main in self.mains.values():main.designated_uid=None

    def _action_order(self,*args,**kwargs):
        order=super()._action_order(*args,**kwargs)
        return order if kwargs.get('for_start_turn') else [u for u in order if u.uid not in self.support_ids]

    def _turn_start_owner(self,u,**kwargs):
        if u.uid in self.support_ids:
            self.begin_bonus_event();self.support_turn(u)
        else:super()._turn_start_owner(u,**kwargs)

    def talent_turn_start(self,u):
        if has(u,'Kageyama','Prodigious Talent') and u.uid not in self.support_ids:
            self.grant_open_play(u,u,'C')
        else:super().talent_turn_start(u)

    def guiding_break_count(self,team):
        return sum(int(u.statuses['Guiding Break'].payload or 1) for u in team.units if 'Guiding Break' in u.statuses)

    def inflict_guiding_break(self,source,target):
        old=target.statuses.get('Guiding Break');n=int(old.payload or 1) if old else 0
        if n>=2:return False
        target.add_status('Guiding Break',False,payload=n+1,through_next_action=True,source=(source.uid,'B'))
        self._log(f'{self._unit_label(target)}: Guiding Break {n+1}/2.')
        return True

    def attack_target_allowed(self,u):
        return u.uid not in self.support_ids and 'Untargetable' not in u.statuses

    def choose_target(self,actor):
        target=self.mains[self.enemy_team(actor).side]
        return (target,False) if self.attack_target_allowed(target) else (None,False)

    def maybe_save(self,attacker,intended):return intended,False
    def maybe_replace_attacker(self,actor,target):return actor,False
    def transfer_action(self,unit):return False
    def _swap_enemy_formation(self,u):return None
    def perform_action(self,u,**kwargs):
        return False if u.uid in self.support_ids else super().perform_action(u,**kwargs)
    def cardinal_foes(self,u):return self.enemy_team(u).units
    def adjacent_allies(self,u):return [a for a in self.team(u).units if a is not u and a.lane==u.lane]
    def opposite_ally(self,u):return None
    def formation_distance(self,a,b):return 0 if a is b else 1 if a.lane==b.lane else 2

    def support_turn(self,source):
        from .battle.youth import YOUTH_TITLES
        if source.title in YOUTH_TITLES:self.youth_turn(source);return
        f=self.mains[source.side];enemy=self.enemy_team(source);name=source.name;buffs={}
        if name=='Tenma Udai' and source.lane==f.lane:buffs={'def':4,'rec':4}
        elif name=='Ushijima' and self.turn%2:buffs={'jmp':6}
        elif name=='Aran':buffs={'pow':6}
        elif name=='Suga':buffs={'pow':6,'qik':6,'spd':6}
        elif name=='Shibayama' and f.character.school=='Nekoma':
            value=min(4,sum(a.noncombat_stat('rec')>a.noncombat_stat('def') for a in self.team(source).units))
            buffs={k:value for k in ('pow','qik','def','rec','spd','jmp')}
        elif name=='Lev':buffs={'rec':6}
        elif name=='Suna':buffs={'spd':6}
        elif name=='Hirugami' and source.lane=='front' and f.character.position=='MB':buffs={'def':4}
        elif name=='Konoha':buffs={'jmp':3}
        elif name=='Koganegawa' and f.character.school=='Date Tech':buffs={'def':4,'rec':4,'jmp':4}
        elif name=='Aone' and f.character.school=='Date Tech':buffs={'pow':4,'qik':4,'spd':4}
        elif name=='Suwa' and f.lane=='front':f.add_status('Bunch Block',True,source=(source.uid,'C'))
        elif name=='Washio' and f.lane=='front':buffs={'def':3}
        elif name=='Komi' and f.lane=='back':buffs={'rec':3}
        elif name=='Watari':buffs={'pow':7,'qik':7}
        elif name=='Shirabu':
            buffs={'pow':6,'qik':6}
            if f.character.school=='Shiratorizawa':f.clear_debuffs()
        elif name in ('Usuri','Futakuchi'):
            if name=='Futakuchi':self.damage_team(enemy,20,source=source)
            for foe in enemy.units:
                for stat,mod in list(foe.visible_buffs.items()):
                    if mod.value>0:foe.add_debuff(stat,-mod.value);foe.visible_buffs.pop(stat,None)
        elif name=='Kenma':
            foe=enemy.units[0];foe.add_debuff('jmp',-7)
            if f.lane=='front':buffs={'pow':4,'qik':4}
            if foe.statuses.get('Fatigue') and int(foe.statuses['Fatigue'].payload or 0)>=3:buffs['spd']=3
        elif name=='Yamagata' and self.team_hp_pct(f)<=50:
            f.barrier+=f.base['hp']//10;f.barrier_sources.add((source.uid,'C'))
        elif name=='Gao' and f.noncombat_stat('def')>f.noncombat_stat('rec'):f.cd=max(0,f.cd-1)
        elif name=='Maruyama' and f.character.position=='WS' and f.noncombat_stat('rec')>f.noncombat_stat('def'):f.cd=max(0,f.cd-1)
        elif has(source,'Kageyama','Prodigious Talent') and f.character.position in ('MB','WS','OP'):
            self.grant_open_play(f,source,'C')
        for stat,value in buffs.items():f.add_buff(stat,value,source=(source.uid,'C'))

    def end_turn(self):
        super().end_turn()
        for t in self.teams.values():
            for source in t.units[1:]:source.cd=source.max_cd;source.burden_stacks=0
