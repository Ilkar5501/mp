from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ..stats import permanent_stats
from ..config import GAME_CONFIG
from ..data import CHAR_BY_ID, CHARACTERS
from ..models import CardInstance, CharacterDef
from .types import BattleResult, BattleTeam, BattleUnit, CombatContext
from .equipment import EquipmentEffects,add_base_equipment,equipment_key
from .talent import ProdigiousTalentEffects
from .expansion import ExpansionEffects
from .anchor import AnchorEffects

STATS = ('pow','qik','def','rec','spd','jmp')


def floori(v: float) -> int:
    return int(math.floor(v + 1e-9))


def pct(hp: int, max_hp: int) -> float:
    return 100.0 * hp / max_hp if max_hp else 0.0


def has(u: BattleUnit, name: str, title: str | None = None) -> bool:
    return u.name == name and (title is None or u.title == title)


class BattleEngine(AnchorEffects, ExpansionEffects, ProdigiousTalentEffects, EquipmentEffects):
    """Deterministic-enough 3v3 / 6v6 simulator for the current HiQ Battler rules.

    The spreadsheet remains the display/source-of-truth. Character-specific mechanics that cannot
    be represented by generic stat rules are implemented here as explicit hooks.
    """

    def __init__(self, team_a_specs, team_b_specs, *, owner_a: int | None = None,
                 owner_b: int | None = None, name_a: str = 'Player', name_b: str = 'Opponent',
                 mode: str = 'battle', seed: int | None = None):
        self.rng = random.Random(seed)
        self.mode = mode
        self.turn = 0
        self.log: list[str] = []
        self.tie_seed: dict[tuple[str,...], int] = {}
        self.crit_reward_used: dict[tuple[int,str], int] = defaultdict(int)
        self.extra_action_used: set[tuple[int,str]] = set()
        self.team_a = self._build_team('A', team_a_specs, owner_a, name_a)
        self.team_b = self._build_team('B', team_b_specs, owner_b, name_b)
        self.teams = {'A': self.team_a, 'B': self.team_b}
        self.units = self.team_a.units + self.team_b.units
        self.previous_turn_damage: dict[str, int] = {}
        self.turn_start_damage: dict[str, int] = {}
        self.siphon_used: set[str] = set()
        self.kanoka_persistence_used: set[tuple[int,str]] = set()
        self.kiryu_pressure_used: dict[tuple[int,str], int] = defaultdict(int)
        self.talent_tempo_used = defaultdict(int)
        self.talent_previous_target = {}
        self.bonus_event = 0
        self.max_turns = int(GAME_CONFIG['battle']['max_turns'])
        self.expansion_init()

    @classmethod
    def from_cards(cls, team_a_cards: list[tuple[CardInstance, str]], team_b_cards: list[tuple[CardInstance, str]], **kwargs):
        a = [(c, CHAR_BY_ID[c.character_id], lane) for c,lane in team_a_cards]
        b = [(c, CHAR_BY_ID[c.character_id], lane) for c,lane in team_b_cards]
        return cls(a,b,**kwargs)

    @classmethod
    def cpu_team(cls, rng: random.Random | None = None, size: int = 3) -> list[tuple[None, CharacterDef, str]]:
        """Generate three neutral templates and put the strongest selected Tank in front.

        The spreadsheet's Class column is authoritative. Any class containing ``Tank`` qualifies;
        if multiple selected units qualify, the highest-BST tank takes frontline. If the random
        trio contains no tank, the best DEF+REC profile is used as a sensible fallback.
        """
        rng = rng or random.Random()
        if size not in (3,6): raise ValueError('CPU size must be 3 or 6.')
        if size==6:
            chosen=[];names=set()
            while len(chosen)<6:
                c=rng.choice(CHARACTERS)
                if c.name not in names:chosen.append(c);names.add(c.name)
            ranking=sorted(chosen,key=lambda c:('tank' in (c.role_class or '').casefold(),
                c.bst if 'tank' in (c.role_class or '').casefold() else c.stats['def']+c.stats['rec'],c.bst,c.id),reverse=True)
            fronts=ranking[:3];backs=[c for c in chosen if c not in fronts]
            return [(None,c,'front') for c in fronts]+[(None,c,'back') for c in backs]
        pool = list(CHARACTERS)
        chosen = []
        names = set()
        while len(chosen) < 3:
            c = rng.choice(pool)
            if c.name in names:
                continue
            chosen.append(c)
            names.add(c.name)
        tanks = [c for c in chosen if 'tank' in (c.role_class or '').casefold()]
        if tanks:
            front = max(tanks, key=lambda c: (c.bst, c.stats.get('def', 0) + c.stats.get('rec', 0), c.id))
        else:
            front = max(chosen, key=lambda c: (c.stats.get('def', 0) + c.stats.get('rec', 0), c.bst, c.id))
        backs = [c for c in chosen if c is not front]
        return [(None, front, 'front'), (None, backs[0], 'back'), (None, backs[1], 'back')]

    def _build_team(self, side, specs, owner_id, owner_name) -> BattleTeam:
        units: list[BattleUnit] = []
        for i,(card,char,lane) in enumerate(specs):
            stats = permanent_stats(char, card.merge_level, card.boon_stat, card.bane_stat) if card else {k:int(char.stats.get(k,0)) for k in ('hp','pow','qik','def','rec','spd','jmp')}
            stats=add_base_equipment(stats,equipment_key(card))
            units.append(BattleUnit(
                uid=f'{side}{i}:{char.id}', card=card, character=char, side=side, lane=lane,
                original_lane=lane, base=stats, max_cd=max(0,int(char.ult_cd)+int(equipment_key(card)=="weighted_wristbands")), cd=max(0,int(char.ult_cd)+int(equipment_key(card)=="weighted_wristbands")),
            ))
        # Same-school synergy is permanent and applies to all listed stats, including HP.
        for u in units:
            bonus = sum(1 for a in units if a is not u and a.character.school == u.character.school)
            if bonus:
                for stat in ('hp','pow','qik','def','rec','spd','jmp'):
                    u.base[stat] += bonus
        max_hp = sum(u.base['hp'] for u in units)
        return BattleTeam(side=side, owner_id=owner_id, owner_name=owner_name, units=units, hp=max_hp, max_hp=max_hp)

    def team(self, u: BattleUnit) -> BattleTeam:
        return self.teams[u.side]

    def enemy_team(self, u: BattleUnit) -> BattleTeam:
        return self.team_b if u.side == 'A' else self.team_a

    def team_hp_pct(self, u: BattleUnit) -> float:
        t = self.team(u); return pct(t.hp,t.max_hp)

    def enemy_hp_pct(self, u: BattleUnit) -> float:
        t = self.enemy_team(u); return pct(t.hp,t.max_hp)

    def start_hp_pct(self, ctx: CombatContext, u: BattleUnit) -> float:
        snap=ctx.temp_flags.get('start_hp_pct')
        if snap is not None: return float(snap[u.side])
        return self.team_hp_pct(u)

    def start_enemy_hp_pct(self, ctx: CombatContext, u: BattleUnit) -> float:
        other='B' if u.side=='A' else 'A'
        snap=ctx.temp_flags.get('start_hp_pct')
        if snap is not None: return float(snap[other])
        return self.enemy_hp_pct(u)

    def allied(self, u: BattleUnit, name: str) -> bool:
        return any(x.name == name for x in self.team(u).units)

    def external_skills_allowed(self, ctx: CombatContext) -> bool:
        return not (has(ctx.attacker,'Fukunaga') or has(ctx.defender,'Fukunaga'))

    def source_allowed(self, ctx: CombatContext, source: BattleUnit) -> bool:
        if self.external_skills_allowed(ctx):
            return True
        return source in (ctx.attacker, ctx.defender)

    def _unit_label(self, u: BattleUnit) -> str:
        return f'[{self.team(u).owner_name}] {u.name}'

    def _log(self, text: str):
        self.log.append(text)

    # ---------- match / turn lifecycle ----------
    def apply_match_start(self):
        # Daichi permanently accelerates his own Ultimate.
        for u in self.units:
            if has(u,'Daichi'):
                u.max_cd = max(0,u.max_cd-1); u.cd = min(u.cd,u.max_cd)
        # Shirabu designates the highest-POW ally and permanently reduces their max CD by 1.
        for u in self.units:
            if has(u,'Shirabu'):
                allies = [a for a in self.team(u).units if a is not u]
                if allies:
                    target = max(allies, key=lambda x:(x.noncombat_stat('pow'), x.uid))
                    u.designated_uid = target.uid
                    target.max_cd = max(0,target.max_cd-1); target.cd = min(target.cd,target.max_cd)
                    self._log(f'{self._unit_label(u)} designates {self._unit_label(target)}; {target.name} max Ultimate CD is reduced by 1.')

    def start_turn(self):
        self.previous_turn_damage = {u.uid: u.damage_dealt - self.turn_start_damage.get(u.uid, u.damage_dealt) for u in self.units}
        self.turn_start_damage = {u.uid: u.damage_dealt for u in self.units}
        self.siphon_used.clear()
        self.turn += 1
        self.crit_reward_used = defaultdict(int)
        for u in self.units:
            u.acted_this_turn = False; u.extra_actions_this_turn = 0
        # True start-of-turn abilities resolve in pre-effect Speed order.
        pre = self._action_order(use_current_turn_buffs=True, for_start_turn=True)
        for u in pre:
            self._turn_start_owner(u)
        return self._action_order(use_current_turn_buffs=True, for_start_turn=False)

    def _action_order(self, use_current_turn_buffs=True, for_start_turn=False):
        groups = defaultdict(list)
        for u in self.units:
            groups[u.noncombat_stat('spd')].append(u)
        order=[]
        for speed in sorted(groups, reverse=True):
            group=groups[speed]
            if len(group)==1:
                order.extend(group); continue
            key=tuple(sorted(x.uid for x in group))
            if key not in self.tie_seed:
                self.tie_seed[key]=self.rng.randrange(len(group))
            offset=(self.tie_seed[key]+(self.turn-1))%len(group)
            ordered=sorted(group,key=lambda x:x.uid)
            order.extend(ordered[offset:]+ordered[:offset])
        return order

    def _turn_start_owner(self, u: BattleUnit, *, forced=False):
        self.begin_bonus_event()
        self.talent_turn_start(u)
        if not forced: self.equipment_turn_start(u)
        self.expansion_turn_start(u)
        team=self.team(u); enemy=self.enemy_team(u)
        # Kanbayashi supports himself and one randomly selected ally among the
        # lowest current Def+Rec ties. Each buff lasts through its recipient's next action.
        if has(u,'Kanbayashi') and pct(team.hp,team.max_hp)<=90:
            for stat in ('def','rec'): u.add_buff(stat,4,source=(u.uid,'A'))
            allies=[a for a in team.units if a is not u]
            if allies:
                lowest=min(a.noncombat_stat('def')+a.noncombat_stat('rec') for a in allies)
                tied=[a for a in allies if a.noncombat_stat('def')+a.noncombat_stat('rec')==lowest]
                target=self.rng.choice(tied)
                for stat in ('def','rec'): target.add_buff(stat,4,source=(u.uid,'A'))
                target.cd=max(0,target.cd-1)
                self._log(f'{self._unit_label(u)} A: Def/Rec+4 to self and {target.name}; {target.name} remaining Ultimate CD -1.')
        if has(u,'Tenma Udai'):
            row_allies=[a for a in team.units if a is not u and a.lane==u.lane]
            for ally in row_allies or [u]:
                for stat in ('def','rec'): ally.add_buff(stat,4,source=(u.uid,'C'))
        # Ushijima
        if has(u,'Ushijima') and self.turn % 2 == 1:
            for a in team.units: a.add_buff('jmp',6,source=(u.uid,'C'))
        # Shirabu
        if has(u,'Shirabu'):
            allies=[a for a in team.units if a is not u]
            if allies:
                target=self.rng.choice(allies)
                for x in (u,target):
                    x.add_buff('pow',6,source=(u.uid,'C')); x.add_buff('qik',6,source=(u.uid,'C'))
            if self.allied(u,'Ushijima'):
                for ally in allies:
                    if has(ally,'Ushijima'): ally.add_buff('pow',6,source=(u.uid,'C'))
                    if ally.character.school=='Shiratorizawa': ally.clear_debuffs()
        # Hirugami
        if has(u,'Hirugami') and u.lane=='front':
            for a in team.units:
                if a is u or a.character.position=='MB': a.add_buff('def',4,source=(u.uid,'C'))
        # Aone
        if has(u,'Aone'):
            for a in team.units:
                if a is not u and a.character.school=='Date Tech':
                    for s in ('pow','qik','spd'): a.add_buff(s,4,source=(u.uid,'C'))
        # Lev
        if has(u,'Lev'):
            allies=[a for a in team.units if a is not u]
            if allies: max(allies,key=lambda x:x.noncombat_stat('rec')).add_buff('rec',6,source=(u.uid,'C'))
        # Suna
        if has(u,'Suna'):
            allies=[a for a in team.units if a is not u]
            if allies: max(allies,key=lambda x:x.noncombat_stat('spd')).add_buff('spd',6,source=(u.uid,'C'))
        # Washio / Komi
        if has(u,'Washio'):
            for a in team.units:
                if a is not u and a.lane=='front': a.add_buff('def',3,source=(u.uid,'C'))
        if has(u,'Komi'):
            for a in team.units:
                if a is not u and a.lane=='back': a.add_buff('rec',3,source=(u.uid,'C'))
        # Konoha
        if has(u,'Konoha'):
            for a in team.units:
                if a is not u: a.add_buff('jmp',3,source=(u.uid,'C'))
        # Bokuto
        if has(u,'Bokuto') and pct(team.hp,team.max_hp)<=75:
            u.add_debuff('def',-3); u.add_debuff('rec',-3)
            for a in team.units:
                if a is not u: a.add_buff('def',3,source=(u.uid,'C')); a.add_buff('rec',3,source=(u.uid,'C'))
        # Usuri converts visible bonuses before foes take their next actions.
        if has(u,'Usuri'):
            for f in enemy.units:
                for stat, mod in list(f.visible_buffs.items()):
                    if mod.value > 0:
                        f.add_debuff(stat, -mod.value)
                        f.visible_buffs.pop(stat, None)
        # Kenma
        if has(u,'Kenma'):
            target=max(enemy.units,key=lambda x:x.noncombat_stat('jmp'))
            target.add_debuff('jmp',-7)
            for a in team.units:
                if a.lane=='front': a.add_buff('pow',4,source=(u.uid,'C')); a.add_buff('qik',4,source=(u.uid,'C'))
            if any(int(x.statuses.get('Fatigue').payload or 0)>=3 for x in enemy.units if x.statuses.get('Fatigue')):
                for a in team.units: a.add_buff('spd',3,source=(u.uid,'C'))
        # Suga's visible opening support.
        if has(u,'Suga'):
            allies=[a for a in team.units if a is not u]
            if allies:
                target=self.rng.choice(allies)
                for stat in ('pow','qik','spd'): target.add_buff(stat,6,source=(u.uid,'C'))
        # Aran
        if has(u,'Aran'):
            allies=[a for a in team.units if a is not u]
            if allies: max(allies,key=lambda x:x.noncombat_stat('pow')).add_buff('pow',6,source=(u.uid,'C'))
        # Tanaka cooldown priming
        if has(u,'Tanaka') and u.cd==u.max_cd: u.cd=max(0,u.cd-1)
        # Yaku turn-1 priming
        if has(u,'Yaku') and self.turn==1: u.cd=max(0,u.cd-2)
        # Koganegawa
        if has(u,'Koganegawa'):
            for a in team.units:
                if a is not u and a.character.school=='Date Tech':
                    for s in ('def','rec','jmp'): a.add_buff(s,4,source=(u.uid,'C'))
        # Futakuchi: chip then convert visible bonuses to penalties.
        if has(u,'Futakuchi'):
            enemy.hp=max(0,enemy.hp-20)
            for f in enemy.units:
                for stat,mod in list(f.visible_buffs.items()):
                    if mod.value>0:
                        f.add_debuff(stat,-mod.value); f.visible_buffs.pop(stat,None)
        # Goshiki
        if has(u,'Goshiki'):
            ep=pct(enemy.hp,enemy.max_hp)
            if ep<=75: u.add_buff('jmp',4,source=(u.uid,'C'))
            if ep<=50: u.cd=max(0,u.cd-1)
        # Tendo
        if has(u,'Tendo'):
            for f in enemy.units:
                if f.lane==u.lane and f.base['hp']<=u.base['hp']:
                    for stat,mod in list(f.visible_buffs.items()):
                        if mod.value>0:
                            f.add_debuff(stat,-mod.value); f.visible_buffs.pop(stat,None)
        # Yamagata
        if has(u,'Yamagata'):
            if pct(team.hp,team.max_hp)<=75: u.cd=max(0,u.cd-1)
            if pct(team.hp,team.max_hp)<=50:
                for a in team.units: a.barrier += floori(a.base['hp']*0.10); a.barrier_sources.add((u.uid,'C'))
        # Gao
        if has(u,'Gao'):
            for a in team.units:
                if a.noncombat_stat('def')>a.noncombat_stat('rec'): a.cd=max(0,a.cd-1)

    def action_start(self, actor: BattleUnit):
        # Visible penalties affect the remaining queue after this action resolves.
        if has(actor,'Daisho'):
            foes=self.enemy_team(actor).units
            unacted=[f for f in foes if not f.acted_this_turn]
            targets=[self.rng.choice(unacted)] if unacted else foes
            for f in targets:
                f.add_debuff('spd',-5); f.add_debuff('jmp',-5)
        # Akaashi grants charge accel to debuffed allies at the start of their action.
        for a in self.team(actor).units:
            if has(a,'Akaashi') and a is not actor and actor.visible_debuffs:
                actor.add_status('AkaashiCharge',True,payload=[x.uid for x in self.team(actor).units if has(x,'Akaashi') and x is not actor])
                actor.statuses['AkaashiCharge'].expires_after_action=actor.actions_taken+1

    def end_turn(self):
        for u in self.units:
            u.expire_end_turn(self.turn)
            u.statuses.pop('Hunted',None)
            u.statuses.pop('UltimateDisabled',None)
        for u in self.units:
            if has(u,'Kiryu','Last Hope'):
                gain=2 if self.allied(u,'Usuri') else 1
                old=u.burden_stacks; u.burden_stacks=min(10,u.burden_stacks+gain)
                if u.burden_stacks>old:
                    self._log(f'{self._unit_label(u)} gains {u.burden_stacks-old} Burden ({u.burden_stacks}/10).')

    # ---------- targeting ----------
    def targeting_distribution(self, defending_team: BattleTeam, offense_key: str, candidates: list[BattleUnit] | None = None, *, actor: BattleUnit | None = None) -> dict[str, float]:
        """Return the defensive targeting distribution for POW or QIK attackers.

        HiQ uses a shared-X solver, not proportional normalization:
          Frontline = X + 10 + applicable modifiers
          Backlines = X + applicable modifiers
        with one equal X solved so the active candidate weights total exactly 100.
        The method is intentionally offense-specific so every team has a distinct
        targeting_vs_POW and targeting_vs_QIK state at any moment.
        """
        units=[u for u in (candidates if candidates is not None else defending_team.units)
               if self.attack_target_allowed(u)]
        if not units:
            return {}
        offense_key=offense_key.casefold()
        offsets=[]
        for unit in units:
            modifier=10.0 if unit.lane=='front' else 0.0
            if actor is not None and has(actor,'Tenma Udai') and unit.lane=='front':
                modifier += 10.0
            if offense_key=='pow' and has(unit,'Tsuki') and unit.lane=='front':
                modifier += 30.0
            if offense_key=='qik' and has(unit,'Komi') and unit.lane=='back':
                modifier += 30.0
            if offense_key=='qik' and has(unit,'Iwaizumi'):
                modifier += 30.0
            offsets.append(modifier)
        x=(100.0-sum(offsets))/len(units)
        weights=[max(0.0, x+off) for off in offsets]
        # If an extreme future modifier set ever drives X below zero, preserve the
        # relative legal weights and normalize only as a safety fallback. A six-unit roster can
        # need this branch when several targeting modifiers stack.
        total=sum(weights)
        if total <= 0:
            weights=[100.0/len(units)]*len(units)
        elif abs(total-100.0) > 1e-9:
            weights=[w*100.0/total for w in weights]
        return {unit.uid: weight for unit,weight in zip(units,weights)}

    def targeting_states(self, defending_team: BattleTeam) -> dict[str, dict[str, float]]:
        return {
            'pow': self.targeting_distribution(defending_team,'pow'),
            'qik': self.targeting_distribution(defending_team,'qik'),
        }

    def choose_target(self, actor: BattleUnit) -> tuple[BattleUnit,bool]:
        enemies=[e for e in self.enemy_team(actor).units if self.attack_target_allowed(e)]
        if not enemies: return None, False
        # Hunted has absolute priority.
        hunted=[e for e in enemies if 'Hunted' in e.statuses]
        if hunted:
            return hunted[0], True
        # Teradomari forces lowest Def+Rec.
        if has(actor,'Teradomari'):
            return min(enemies,key=lambda x:x.noncombat_stat('def')+x.noncombat_stat('rec')), True
        # Gao can only target back line.
        candidates=enemies
        if has(actor,'Gao'):
            backs=[e for e in enemies if e.lane=='back']
            if backs: candidates=backs
        distribution=self.targeting_distribution(self.enemy_team(actor), actor.offense_key, candidates, actor=actor)
        weights=[distribution[e.uid] for e in candidates]
        target=self.rng.choices(candidates,weights=weights,k=1)[0]
        return target, False

    def maybe_replace_attacker(self, actor: BattleUnit, target: BattleUnit) -> tuple[BattleUnit,bool]:
        # Kyotani replaces the allied unit with the lowest active offense if his Ultimate is ready.
        team=self.team(actor)
        ky=next((a for a in team.units if has(a,'Kyotani')),None)
        if ky and ky is not actor and ky.cd==0 and 'UltimateDisabled' not in ky.statuses:
            lowest=min([a for a in team.units if a is not ky], key=lambda x:x.noncombat_stat(x.offense_key))
            if actor is lowest:
                self.trigger_ultimate(ky,None,'intercept')
                return ky, True
        return actor, False

    def maybe_save(self, attacker: BattleUnit, intended: BattleUnit) -> tuple[BattleUnit,bool]:
        # Anti-save effects: Reluctant Titan Kiryu initiation or Free Ball on intended target.
        if has(attacker,'Kiryu','Reluctant Titan') or 'Free Ball' in intended.statuses:
            return intended, False
        team=self.team(intended)
        eligible=[]
        for u in team.units:
            if u is intended or u.cd!=0 or 'UltimateDisabled' in u.statuses or not self.attack_target_allowed(u): continue
            if has(u,'Hirugami') or has(u,'Konoha') or (has(u,'Nishinoya') and attacker.offense_key=='qik'):
                eligible.append(u)
        if not eligible: return self.passive_save(attacker,intended)
        savior=max(eligible,key=lambda x:x.noncombat_stat('spd'))
        self.trigger_ultimate(savior,None,'save')
        return savior, True

    # ---------- combat stat layer ----------
    def _base_combat_stats(self,u: BattleUnit, foe: BattleUnit | None = None, ctx: CombatContext | None = None) -> dict[str,int]:
        neutral_bonus=set(); neutral_penalty=set()
        if foe is not None:
            if has(foe,'Shirabu'): neutral_bonus.update(STATS)
            if has(foe,'Ushijima'): neutral_bonus.update(('pow','def'))
            if has(foe,'Kuroo'): neutral_bonus.update(('pow','qik','jmp'))
            if has(foe,'Kita'): neutral_bonus.update(('pow','qik'))
            if has(foe,'Suwa') and ctx and ctx.defender is foe: neutral_bonus.update(('pow','qik'))
            if has(foe,'Teradomari') and ctx and ctx.attacker is foe: neutral_bonus.update(STATS)
            if has(foe,'Futakuchi') and u.visible_buffs: neutral_bonus.update(('pow','def','spd'))
            if has(foe,'Kiryu','Last Hope') and ctx and ctx.attacker is foe and foe.burden_stacks>=5:
                neutral_bonus.update(STATS)
        if foe is not None and self.gear(foe,'seam_finder'):neutral_bonus.update(('def','rec'))
        if foe is not None and foe.name=='Akifumi' and ctx and (ctx.attacker is foe or self.start_enemy_hp_pct(ctx,foe)>=50):neutral_bonus.update(('spd','rec'))
        if has(u,'Kita') and ctx and ctx.defender is u: neutral_penalty.update(('def','rec'))
        if has(u,'Asahi') and self.team_hp_pct(u)<=75: neutral_penalty.update(('pow','jmp'))
        if has(u,'Tenma Udai'): neutral_penalty.update(STATS)
        if has(u,'Iizuna') and ctx and ctx.attacker is u:
            neutral_penalty.update(('qik','def','rec'))
        if has(u,'Kiryu','Last Hope') and ctx and ctx.attacker is u and u.burden_stacks>=5:
            neutral_penalty.update(STATS)
        out={}
        for s in STATS:
            out[s]=u.noncombat_stat(s, neutralize_bonus=(s in neutral_bonus), neutralize_penalty=(s in neutral_penalty))
        # Kenma's Fatigue is an in-combat penalty that persists independently of visible debuffs.
        f=u.statuses.get('Fatigue')
        if f:
            n=int(f.payload or 0)
            out['spd']-=n; out['jmp']-=n
            out['pow']-=max(n-1,0); out['qik']-=max(n-1,0)
            out['def']-=max(n-2,0); out['rec']-=max(n-2,0)
        self.expansion_base_stats(u,foe,ctx,out)
        return out

    def combat_stats(self, ctx: CombatContext, temp: dict[str,dict[str,int]] | None = None) -> dict[str,dict[str,int]]:
        temp=temp or {}
        ctx.temp_flags['ally_stat_impacts']={}
        # Fixed-point passes let live conditions such as Suga's Jmp check respond to an Ultimate modifier.
        stats={ctx.attacker.uid:self._base_combat_stats(ctx.attacker,ctx.defender,ctx),ctx.defender.uid:self._base_combat_stats(ctx.defender,ctx.attacker,ctx)}
        for _ in range(4):
            ctx.temp_flags['ally_stat_impacts']={}
            ctx.temp_flags['item_bonus_stats']=stats
            new={uid:dict(v) for uid,v in stats.items()}
            for u,foe,initiated in [(ctx.attacker,ctx.defender,True),(ctx.defender,ctx.attacker,False)]:
                base=self._base_combat_stats(u,foe,ctx)
                # Tendo swaps original Speed values at combat start.
                if has(u,'Tendo'):
                    base['spd'] += foe.base['spd'] - u.base['spd']
                elif has(foe,'Tendo'):
                    base['spd'] += foe.base['spd'] - u.base['spd']  # foe's Tendo takes unit's speed; this unit takes Tendo's original speed
                mods=self._combat_mods(ctx,u,foe,stats.get(u.uid,base),stats.get(foe.uid,self._base_combat_stats(foe,u,ctx)),initiated)
                for s in STATS: base[s]+=mods.get(s,0)
                for s,v in temp.get(u.uid,{}).items(): base[s]=base.get(s,0)+v
                new[u.uid]=base
            if new==stats: break
            stats=new
        return stats

    def _combat_mods(self,ctx,u,foe,us,fs,initiated):
        m=defaultdict(int)
        team=self.team(u)
        if has(u,'Iizuna') and initiated:
            for stat in ('qik','def','rec'):m[stat]+=4
        # Own skills
        if has(u,'Hinata','Wings of Dawn') and initiated: m['qik']+=4; m['spd']+=4
        if has(u,'Kageyama','Crown of Precision') and initiated: m['pow']+=6
        if has(u,'Hinata','Miracle Maker') and not initiated: m['def']+=3; m['rec']+=3
        if has(u,'Ushijima'):
            m['pow']+=3+sum(u.visible_bonus(s) for s in STATS)
        if has(u,'Nishinoya') and not initiated: m['def']+=4; m['spd']+=4; m['jmp']+=4
        if has(u,'Nishinoya') and u.lane=='back': m['rec']+=4
        if has(u,'Hirugami') and not initiated:
            acted=sum(1 for x in self.enemy_team(u).units if x.acted_this_turn)
            val=min(5,1+acted); m['pow']+=val; m['def']+=val
        if has(u,'Tenma Udai') and initiated: m['jmp']+=8
        if has(u,'Kosuke') and not initiated:
            for stat in ('qik','rec','jmp'): m[stat]+=4
        if has(u,'Kanoka'): m['pow']+=5; m['spd']+=5
        if has(u,'Kuroo') and u.lane=='front': m['def']+=2; m['rec']+=2
        if has(u,'Aone') and foe.lane=='front': m['def']+=4; m['rec']+=4; m['jmp']+=4
        if has(u,'Lev') and initiated: m['spd']+=5
        if has(u,'Suna') and initiated: m['jmp']+=10; m['spd']+=6
        if has(u,'Washio'):
            m['def']+=6
            if any(a.visible_debuffs for a in team.units): m['pow']+=3; m['def']+=3
        if has(u,'Komi') and self.start_hp_pct(ctx,u)>=50: m['qik']+=5; m['rec']+=5
        if has(u,'Konoha') and not initiated: m['def']+=4; m['rec']+=4
        if has(u,'Konoha') and fs.get('spd',0)+5>=us.get('spd',0): m['jmp']+=7
        if has(u,'Akaashi'):
            if initiated: m['pow']+=6
            for s in ('pow','qik','spd','def','rec','jmp'):
                m[s]+=2*abs(min(0,u.visible_penalty(s)))
        if has(u,'Atsumu'): m['spd']+=self.turn+1
        if has(u,'Osamu'):
            if initiated: m['qik']+=3; m['spd']+=3
            if self.skill_spd(u,us)>self.skill_spd(foe,fs): m['jmp']+=4
        if has(u,'Aran') and initiated: m['pow']+=6
        if has(u,'Kita') and not initiated: m['def']+=5; m['rec']+=5
        if has(u,'Kita') and not (foe.visible_bonus('pow') or foe.visible_bonus('qik')): m['def']+=3; m['rec']+=3
        if has(u,'Akagi') and self.start_hp_pct(ctx,u)<=75:
            for s in ('qik','def','rec','spd','jmp'): m[s]+=4
        if has(u,'Yamamoto'):
            if initiated: m['pow']+=4; m['jmp']+=4
            if us.get('rec',u.noncombat_stat('rec'))>fs.get('rec',foe.noncombat_stat('rec')): m['jmp']+=3
        if has(u,'Fukunaga') and self.start_hp_pct(ctx,u)>=25:
            for s in ('pow','spd','def','rec'): m[s]+=5
        if has(u,'Yaku'):
            for s in ('qik','def','rec','spd','jmp'): m[s]+=u.visible_bonus(s)
        if has(u,'Kai'):
            m['rec']+=3
            if not initiated: m['def']+=5; m['rec']+=5
            if not initiated and any(a is not u and (
                (has(a,'Tsuki') and a.lane=='front' and foe.offense_key=='pow' and not has(foe,'Gao')) or
                (has(a,'Komi') and a.lane=='back' and foe.offense_key=='qik') or
                (has(a,'Iwaizumi') and foe.offense_key=='qik')
            ) for a in team.units) and not (ctx.override_targeting or ctx.save_triggered or ctx.kyotani_intercept):
                m['def']+=5; m['rec']+=5
        if has(u,'Asahi'):
            m['pow']+=min(8,1+2*u.actions_taken)
            if self.start_hp_pct(ctx,u)<=75: m['jmp']+=4
            if self.start_hp_pct(ctx,u)<=50: m['pow']+=6
        if has(u,'Daichi') and self.start_hp_pct(ctx,u)<=50:
            for s in ('pow','def','rec','jmp'): m[s]+=5
        if has(u,'Suga'):
            m['spd']-=5
            if fs.get('jmp',foe.noncombat_stat('jmp'))>us.get('jmp',u.noncombat_stat('jmp')):
                m['pow']+=3; m['def']+=3
        if has(u,'Tanaka') and initiated: m['pow']+=4; m['def']+=8
        if has(u,'Oikawa'):
            m['jmp']+=7
            if initiated: m['pow']+=5; m['jmp']+=5
        if has(u,'Iwaizumi') and not initiated: m['rec']+=6
        if has(u,'Kyotani') and initiated and (u.visible_buffs or u.has_positive_status()): m['pow']+=4; m['def']+=4
        if has(u,'Koganegawa') and not initiated: m['def']+=5; m['jmp']+=5
        if has(u,'Futakuchi') and foe.visible_debuffs: m['pow']+=5; m['def']+=5
        if has(u,'Goshiki') and initiated: m['pow']+=4; m['jmp']+=4
        if has(foe,'Tendo'): m['pow']-=3; m['qik']-=3
        if has(u,'Yamagata') and self.start_hp_pct(ctx,u)<=99: m['def']+=3; m['rec']+=3; m['jmp']+=3
        if has(u,'Reon'):
            if initiated: m['pow']+=4; m['jmp']+=4
            else: m['def']+=4; m['rec']+=4
        if has(u,'Teradomari') and initiated: m['pow']+=3; m['jmp']+=3
        if has(u,'Gao'):
            if initiated: m['pow']+=6
            else: m['def']+=4; m['jmp']+=4
        if has(u,'Korai'):
            if u.lane=='front': m['pow']+=4; m['def']+=4; m['jmp']+=4
            else: m['pow']+=4; m['rec']+=4; m['spd']+=4
        if has(u,'Kiryu','Reluctant Titan') and initiated: m['pow']+=6
        if has(u,'Kiryu','Last Hope') and initiated:
            if any(a is not u and a.lane==u.lane and a.character.position=='S' for a in team.units):
                m['pow']+=3; m['def']+=3; m['rec']+=3
            burden_bonus=u.burden_stacks+1
            m['pow']+=burden_bonus; m['def']+=burden_bonus; m['rec']+=burden_bonus
        if has(u,'Unnan') and not initiated: m['pow']+=0  # foe debuff handled below
        if has(u,'Daisho') and initiated: m['pow']+=4; m['spd']+=4

        if has(u,'Kageyama','Prodigious Talent'):
            m['pow']+=3; m['jmp']+=3
        if 'Open Play' in u.statuses:
            for stat in ('pow','qik','spd','def','rec','jmp'): m[stat]+=3

        # Enemy effects on this unit
        if has(foe,'Bokuto') and foe.visible_debuffs:
            m['def']-=1; m['rec']-=1
        if has(foe,'Ushijima'):
            m['pow']-=3; m['def']-=3
        if has(foe,'Kuroo'):
            m['pow']-=2; m['qik']-=2; m['jmp']-=2
        if has(foe,'Unnan') and initiated: m['pow']-=6; m['qik']-=6
        if has(foe,'Koganegawa') and initiated: m['pow']-=4; m['qik']-=4; m['jmp']-=4

        # External ally auras are disabled in Fukunaga's isolated duel.
        if self.external_skills_allowed(ctx):
            for a in team.units:
                if a is u: continue
                before=dict(m)
                if has(a,'Hinata','Miracle Maker'):
                    if u.lane=='front': m['pow']+=3; m['qik']+=3; m['spd']+=3
                    else: m['def']+=3; m['rec']+=3
                if has(a,'Kuroo') and u.lane=='back': m['rec']+=4
                if has(a,'Nishinoya') and u.lane=='back': m['rec']+=4
                if has(a,'Washio') and u.visible_debuffs: m['def']+=3
                if has(a,'Komi') and u.visible_debuffs: m['rec']+=3
                if has(a,'Konoha') and u.visible_debuffs: m['jmp']+=3
                if has(a,'Akaashi'):
                    for s in ('pow','qik','spd','def','rec','jmp'):
                        pen=abs(min(0,u.visible_penalty(s)))
                        m[s]+=2*pen
                if has(a,'Atsumu'): m['spd']+=self.turn+1
                if has(a,'Kita'):
                    m['def']+=2; m['rec']+=2
                    if not u.visible_buffs: m['pow']+=2; m['qik']+=2
                if has(a,'Osamu') and self.skill_spd(u,us)>self.skill_spd(foe,fs): m['jmp']+=4
                if has(a,'Yamamoto') and a.noncombat_stat('rec')>foe.noncombat_stat('rec'): m['jmp']+=3
                if has(a,'Kai'):
                    m['rec']+=3
                    if a.lane=='back' and u.lane=='front': m['def']+=3
                if has(a,'Oikawa'): m['jmp']+=7
                if has(a,'Kyotani'): m['pow']+=3
                if has(a,'Reon') and not initiated: m['def']+=3; m['rec']+=3
                if has(a,'Korai'):
                    if a.lane=='front' and u.lane=='back': m['def']+=3; m['jmp']+=3
                    elif a.lane=='back' and u.lane=='front': m['rec']+=3; m['spd']+=3
                if any(m.get(stat,0)!=before.get(stat,0) for stat in STATS):
                    ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((a.uid,'B' if has(a,'Akaashi') or has(a,'Reon') else 'C'))
                if has(a,'Kai') and u is ctx.defender and ctx.save_triggered:
                    m['def']+=5; m['rec']+=5
                    ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((a.uid,'B'))
        # Usuri supports one ally, selected from previous-turn damage (Kiryu priority).
        for source in team.units:
            if has(source,'Usuri') and self.source_allowed(ctx,source):
                allies = [ally for ally in team.units if ally is not source]
                kiryu = next((ally for ally in allies if has(ally,'Kiryu')), None)
                target = kiryu or (max(allies, key=lambda ally:self.previous_turn_damage.get(ally.uid,0)) if allies else None)
                if u is target:
                    amount = min(5,self.turn+2)
                    m['pow'] += amount; m['jmp'] += amount
                    if source is not u:
                        ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((source.uid,'B'))
        # Combat-local Kyotani intercept steroid
        if ctx.kyotani_intercept and u is ctx.attacker and has(u,'Kyotani'): m['jmp']+=8
        self.expansion_mods(ctx,u,foe,us,fs,initiated,m)
        self.equipment_mods(ctx,u,foe,initiated,m)
        return m

    def ally_skill_impacts(self,ctx,u,stats=None):
        """Distinct source/slot pairs currently benefiting the unit; own skills/bonds excluded."""
        stats=stats or self.combat_stats(ctx)
        foe=ctx.defender if u is ctx.attacker else ctx.attacker
        impacts=set(ctx.temp_flags.get('ally_stat_impacts',{}).get(u.uid,set()))
        allowed={a.uid for a in self.team(u).units if a is not u and self.source_allowed(ctx,a)}
        # Visible bonuses count only if they survive this foe's neutralization.
        active=self._base_combat_stats(u,foe,ctx)
        fatigue=u.statuses.get('Fatigue')
        n=int(fatigue.payload or 0) if fatigue else 0
        fatigue_penalties={'spd':n,'jmp':n,'pow':max(n-1,0),'qik':max(n-1,0),'def':max(n-2,0),'rec':max(n-2,0)}
        for stat,mod in u.visible_buffs.items():
            if active[stat]==u.noncombat_stat(stat)-fatigue_penalties[stat]-self.expansion_bond_penalty(ctx,u): impacts.update(mod.sources)
        if u.barrier>0: impacts.update(u.barrier_sources)
        for status in u.statuses.values():
            if status.positive:
                impacts.update(source for source in status.sources if not status.source_expiries or status.source_expiries.get(source,0)>u.actions_taken)
        charge=u.statuses.get('AkaashiCharge')
        if charge: impacts.update((uid,'C') for uid in (charge.payload or []))
        for source in self.team(u).units:
            if source.uid not in allowed: continue
            if has(source,'Usuri') and ctx.attacker is u and ctx.temp_flags.get('siphon'):
                impacts.add((source.uid,'A'))
            if has(source,'Kageyama','Crown of Precision') and ctx.attacker is u and self.team_hp_pct(u)<=75 and self.skill_spd(u,stats[u.uid])>=self.skill_spd(foe,stats[foe.uid])+3 and self.followup(ctx,u,foe,True):
                impacts.add((source.uid,'B'))
            if has(source,'Atsumu') and self.skill_spd(u,stats[u.uid])>self.skill_spd(foe,stats[foe.uid]): impacts.add((source.uid,'B'))
            if has(source,'Iwaizumi') and ctx.temp_flags.get('offensive_crit_uid')==u.uid and not self.crits_disabled(ctx) and not has(foe,'Kanoka'): impacts.add((source.uid,'C'))
        return {(uid,slot) for uid,slot in impacts if uid in allowed}

    # ---------- permissions / followups ----------
    def can_counter(self, ctx, defender, attacker, stats=None):
        if 'Cannot Counter' in defender.statuses: return False
        if ctx.attacker is attacker and self.expansion_memory[attacker.uid].get('initiations',{}).get(self.turn,0)>=2 and any(a.name=='Akifumi' and self.source_allowed(ctx,a) for a in self.team(attacker).units):return False
        if 'Intimidation' in attacker.statuses and ctx.attacker is attacker: return False
        if has(attacker,'Kiryu','Last Hope') and ctx.attacker is attacker and defender.character.position in ('MB','Li'):
            st=stats or self.combat_stats(ctx)
            if st[attacker.uid]['pow']>=st[defender.uid][defender.offense_key]+3: return False
        if has(attacker,'Lev') and attacker.offense_key=='qik' and defender.offense_key=='qik':
            st=stats or self.combat_stats(ctx)
            if self.skill_spd(attacker,st[attacker.uid])>self.skill_spd(defender,st[defender.uid]): return False
        if has(defender,'Lev') and attacker.offense_key=='qik' and defender.offense_key=='qik':
            st=stats or self.combat_stats(ctx)
            if self.skill_spd(defender,st[defender.uid])>self.skill_spd(attacker,st[attacker.uid]): return False
        universal = any(has(defender,n) for n in ('Hirugami','Kuroo','Yaku','Daichi','Yamagata'))
        if has(defender,'Nishinoya'): universal=True
        if has(defender,'Tendo'):
            st=stats or self.combat_stats(ctx)
            if self.skill_spd(defender,st[defender.uid])>self.skill_spd(attacker,st[attacker.uid]): universal=True
        if has(defender,'Kosuke'): universal=True
        if has(defender,'Nozawa'):
            st=stats or self.combat_stats(ctx)
            if st[defender.uid]['rec']>st[attacker.uid]['rec']: universal=True
        if defender.lane=='front' and defender.character.school=='Date Tech':
            for ally in self.team(defender).units:
                if ally is not defender and has(ally,'Kosuke') and self.source_allowed(ctx,ally):
                    universal=True
        return universal or defender.offense_key==attacker.offense_key

    def attacks_twice(self,ctx,u):
        if has(u,'Suga'): return True
        if has(u,'Hinata','Wings of Dawn') and self.allied(u,'Kageyama'): return True
        return False

    def followup(self,ctx,u,foe,initiated):
        st=self.combat_stats(ctx); us,fs=st[u.uid],st[foe.uid]
        guaranteed=has(u,'Kageyama','Prodigious Talent') and initiated and len(self.distinct_bonuses(ctx,u,st))>=4
        prevented=False
        if has(u,'Hinata','Wings of Dawn') and not self.allied(u,'Kageyama') and self.skill_spd(u,us)>=self.skill_spd(foe,fs)+5: guaranteed=True
        if has(u,'Nishinoya') and not initiated and self.skill_spd(u,us)>=self.skill_spd(foe,fs)+5: guaranteed=True
        if has(u,'Osamu') and initiated and self.skill_spd(u,us)>self.skill_spd(foe,fs): guaranteed=True
        if has(u,'Bokuto') and initiated: guaranteed=True
        if has(u,'Akagi') and False: pass
        if has(u,'Asahi') and self.start_hp_pct(ctx,u)<=25: guaranteed=True
        if has(u,'Koganegawa') and not initiated and self.start_hp_pct(ctx,u)<=75: guaranteed=True
        if has(u,'Daisho') and ctx.temp_flags.get(f'{u.uid}:daisho_followup'): guaranteed=True
        if has(u,'Takeru') and fs['def']+fs['rec']>us['def']+us['rec']: guaranteed=True
        if has(u,'Kiryu','Last Hope') and initiated and u.burden_stacks>=3 and self.can_counter(ctx,foe,u,st): guaranteed=True
        if 'Charged' in u.statuses: guaranteed=True
        if initiated and self.gear(foe,'open_guard'):guaranteed=True
        if initiated and self.gear(u,'last_rally') and self.start_hp_pct(ctx,u)<=50 and self.can_counter(ctx,foe,u,st):guaranteed=True
        if self.gear(u,'flashy_new_shoes') and not initiated and self.start_hp_pct(ctx,u)>=75 and self.can_counter(ctx,u,foe,st):guaranteed=True
        # Kageyama aura
        for a in self.team(u).units:
            if has(a,'Kageyama','Crown of Precision') and a is not u and self.source_allowed(ctx,a) and self.team_hp_pct(u)<=75 and initiated and self.skill_spd(u,us)>=self.skill_spd(foe,fs)+3:
                guaranteed=True
        # Shirabu high-HP lock
        if (has(u,'Shirabu') or has(foe,'Shirabu')):
            shir= u if has(u,'Shirabu') else foe
            if self.start_hp_pct(ctx,shir)>=50: prevented=True
        # Aone prevents foe followup when Def threshold met
        if has(foe,'Aone') and st[foe.uid]['def']>=us['def']+3: prevented=True
        if has(ctx.defender,'Nozawa'): prevented=True
        if self.echigo_followup_support(ctx,u): prevented=False
        # Futakuchi neutralizes guaranteed followup on penalized foe (not hard prevention)
        if has(foe,'Futakuchi') and u.visible_debuffs: guaranteed=False
        if self.gear(u,'rigorous_film_study') or self.gear(foe,'rigorous_film_study'):guaranteed=False
        return guaranteed and not prevented

    def desperation(self,ctx,u):
        return has(u,'Osamu') and self.start_hp_pct(ctx,u)<=75

    def vantage(self,ctx,u,foe):
        return ((has(u,'Suga') and self.start_hp_pct(ctx,u)<=75) or (has(u,'Ezota') and self.start_enemy_hp_pct(ctx,u)<=75)) and self.can_counter(ctx,u,foe)

    # ---------- Ultimate helpers ----------
    def ultimates_suppressed(self,ctx):
        return has(ctx.attacker,'Fukunaga') or has(ctx.defender,'Fukunaga')

    def trigger_ultimate(self,u:BattleUnit,ctx:CombatContext|None,kind:str):
        if u.cd!=0 or 'UltimateDisabled' in u.statuses: return False
        if ctx and self.ultimates_suppressed(ctx): return False
        u.cd=u.max_cd; u.ult_triggered_this_combat=True
        self._log(f'{self._unit_label(u)} triggers {u.character.abilities["U"].name or "Ultimate"}!')
        # External reactions to an Ultimate trigger.
        self.equipment_ultimate(u)
        if ctx and u is ctx.attacker and kind in ('offense','defense'):
            ctx.temp_flags['initiator_ultimate']=u.uid
        if ctx:
            for a in self.team(u).units:
                if has(a,'Daichi') and (kind not in ('offense','defense') or self.source_allowed(ctx,a)):
                    for ally in self.team(u).units: ally.clear_debuffs()
        # Shirabu recurring cooldown on designated ally occurs after reset.
        for a in self.team(u).units:
            if has(a,'Shirabu') and a.designated_uid==u.uid and (not ctx or kind not in ('offense','defense') or self.source_allowed(ctx,a)):
                u.cd=max(0,u.cd-1)
        # Character-specific reaction statuses.
        if has(u,'Atsumu') and ctx:
            foe=ctx.defender if u is ctx.attacker else ctx.attacker
            foe.add_status('UltimateDisabled',False,end_turn=self.turn)
        if has(u,'Yamagata'):
            u.add_buff('def',4); u.add_buff('rec',4)
        if has(u,'Teradomari'):
            foes=self.enemy_team(u).units
            target=min(foes,key=lambda x:x.noncombat_stat('def')+x.noncombat_stat('rec'))
            target.add_status('Hunted',False,end_turn=self.turn)
        if has(u,'Kiryu','Reluctant Titan'):
            foes=self.enemy_team(u).units
            target=max(foes,key=lambda x:x.noncombat_stat('def')+x.noncombat_stat('rec'))
            target.add_status('Exposed',False,through_next_action=True)
        return True

    def offensive_ult_ready(self,u,foe,ctx):
        if u.cd!=0 or 'UltimateDisabled' in u.statuses or self.ultimates_suppressed(ctx): return False
        # Postcombat/save/defensive Ultimates do not trigger on own attack.
        if u.name in ('Echigo','Watari','Ezota','Himekawa','Akifumi'): return False
        if has(u,'Kageyama') or has(u,'Bokuto') or has(u,'Fukunaga') or has(u,'Tendo'): return False
        if has(u,'Nishinoya') or has(u,'Hirugami') or has(u,'Konoha') or has(u,'Kyotani'): return False
        if has(u,'Hinata','Miracle Maker') or has(u,'Kita') or has(u,'Yaku') or has(u,'Unnan') or has(u,'Yamagata') or has(u,'Kosuke'): return False
        if has(u,'Daisho'):
            st=self.combat_stats(ctx)
            return self.skill_spd(u,st[u.uid])>=self.skill_spd(foe,st[foe.uid])-4
        if has(u,'Takeru'):
            st=self.combat_stats(ctx)
            return self.skill_spd(u,st[u.uid])>=self.skill_spd(foe,st[foe.uid])-4
        return True

    def defensive_ult_ready(self,u,attacker,ctx):
        if u.cd!=0 or 'UltimateDisabled' in u.statuses or self.ultimates_suppressed(ctx): return False
        if has(u,'Hinata','Miracle Maker') or has(u,'Kita') or has(u,'Yaku') or has(u,'Yamagata'): return True
        if has(u,'Unnan'): return attacker.offense_key=='qik'
        if has(u,'Kosuke') or has(u,'Ezota'): return True
        if has(u,'Takeru'):
            st=self.combat_stats(ctx)
            return self.skill_spd(u,st[u.uid])>=self.skill_spd(attacker,st[attacker.uid])-4
        return False

    # ---------- damage helpers ----------
    def charge_delta(self,ctx,u,foe,is_attacking):
        delta=1
        st=self.combat_stats(ctx)
        # These accelerate the unit per combat hit while the condition is active.
        if has(u,'Hinata','Miracle Maker') and ctx.defender is u: delta+=1
        if has(u,'Kuroo') and ctx.defender is u: delta+=1
        if has(u,'Tsuki') and self.start_hp_pct(ctx,u)>=50: delta+=1
        if has(u,'Tenma Udai') and self.skill_spd(u,st[u.uid])>self.skill_spd(foe,st[foe.uid]): delta+=1
        # These explicitly accelerate only the unit's own attacks.
        if is_attacking:
            if has(u,'Lev') and self.skill_spd(u,st[u.uid])>self.skill_spd(foe,st[foe.uid]): delta+=1
            if has(u,'Atsumu') and self.skill_spd(u,st[u.uid])>self.skill_spd(foe,st[foe.uid]): delta+=1
            if has(u,'Kyotani') and st[u.uid]['pow']>st[foe.uid]['pow']: delta+=1
            if 'AkaashiCharge' in u.statuses: delta+=1
        # Guard-type effects from opponent / allies.
        acceleration=delta-1
        if has(foe,'Tsuki') and self.start_hp_pct(ctx,foe)>=50: delta-=1
        if has(foe,'Konoha') and ctx.defender is foe: delta-=1
        if has(foe,'Iwaizumi') and ctx.defender is foe: delta-=1
        if self.external_skills_allowed(ctx):
            for a in self.team(foe).units:
                if has(a,'Reon') and a is not foe and ctx.attacker is u: delta-=1
        guard=1+acceleration-delta
        acceleration,guard=self.expansion_charge(ctx,u,foe,is_attacking,acceleration,guard)
        return self.equipment_charge(ctx,u,foe,is_attacking,acceleration,guard,st)

    def crit_multiplier(self,ctx,u):
        if any(has(a,'Iwaizumi') and self.source_allowed(ctx,a) for a in self.team(u).units): return 1.5
        return 1.2

    def crits_disabled(self,ctx):
        return has(ctx.attacker,'Fukunaga') or has(ctx.defender,'Fukunaga')

    def resolve_hit(self,ctx,attacker,defender,*,followup=False,hit_index=0):
        if self.team(defender).hp<=0: return
        attacker.ult_triggered_this_combat = attacker.ult_triggered_this_combat
        defender.ult_triggered_this_combat = defender.ult_triggered_this_combat

        # Unnan delays the initiating foe's first ready offensive Ultimate.
        if has(defender,'Unnan') and ctx.defender is defender and attacker.uid not in ctx.first_attack_by:
            st=self.combat_stats(ctx)
            if st[defender.uid]['def']>=st[attacker.uid]['def']+5 and self.offensive_ult_ready(attacker,defender,ctx):
                attacker.cd=min(attacker.max_cd,attacker.cd+1)
        ctx.first_attack_by.add(attacker.uid)
        off_trigger=False; def_trigger=False
        temp={}
        if self.offensive_ult_ready(attacker,defender,ctx):
            # Suga's Pow/Jmp+12 is part of the Ultimate payload and therefore affects live checks/crit.
            if has(attacker,'Suga'): temp[attacker.uid]={'pow':12,'jmp':12}
            off_trigger=self.trigger_ultimate(attacker,ctx,'offense')
            if has(attacker,'Usuri') and off_trigger:
                defender.barrier = 0
                defender.add_status('Heal Block',False,through_next_action=True)
                if defender is ctx.original_actor:
                    defender.statuses['Heal Block'].expires_after_action = defender.actions_taken + 2
            if has(attacker,'Daisho') and off_trigger: ctx.temp_flags[f'{attacker.uid}:daisho_followup']=True
        if self.defensive_ult_ready(defender,attacker,ctx):
            def_trigger=self.trigger_ultimate(defender,ctx,'defense')

        stats=self.combat_stats(ctx,temp)
        a=stats[attacker.uid]; d=stats[defender.uid]
        attack_stat=a[attacker.offense_key]
        def_stat=d['def'] if attacker.offense_key=='pow' else d['rec']

        # Adaptive defense selection.
        adaptive = has(attacker,'Suna') or has(attacker,'Akagi') or (has(attacker,'Aone') and defender.offense_key=='pow') or self.equipment_adaptive(attacker)
        if has(defender,'Hirugami'):
            if attacker.offense_key=='qik': def_stat=d['def']
            adaptive=False
        if adaptive: def_stat=min(d['def'],d['rec'])

        # Crit check comes before percentage transformations.
        offensive_crit=defensive_crit=False
        if not self.crits_disabled(ctx):
            diff=a['jmp']-d['jmp']
            chance=min(100,abs(diff)*int(GAME_CONFIG['battle']['crit_per_jmp_difference_percent']))
            if diff>0 and self.rng.random()<chance/100:
                offensive_crit=True
                if not has(defender,'Kanoka'): attack_stat=floori(attack_stat*self.crit_multiplier(ctx,attacker))
                attacker.crits+=1
            elif diff<0 and self.rng.random()<chance/100:
                defensive_crit=True
                if not has(attacker,'Kanoka'): def_stat=floori(def_stat*1.2)

        if defensive_crit:
            ctx.temp_flags.setdefault('defensive_crit_uids',set()).add(defender.uid)
        self.anchor_critical(ctx,attacker,defender,defensive_crit)
        critical_unit=attacker if offensive_crit else defender if defensive_crit else None
        if critical_unit is not None:
            ctx.temp_flags.setdefault('critical_unit_uids',set()).add(critical_unit.uid)
        if offensive_crit:
            ctx.temp_flags.setdefault('offensive_crit_uids',set()).add(attacker.uid)

        ctx.temp_flags['offensive_crit_uid']=attacker.uid if offensive_crit else None

        # Defense penetration / replacement.
        penetration=0.0
        if off_trigger:
            if attacker.name in ('Nozawa','Maruyama'): penetration=max(penetration,0.50)
            if has(attacker,'Hinata','Wings of Dawn'): penetration=max(penetration,0.30)
            if has(attacker,'Kuroo'): penetration=max(penetration,0.50)
            if has(attacker,'Osamu'): penetration=max(penetration,0.70 if self.allied(attacker,'Atsumu') else 0.50)
            if has(attacker,'Aran'): penetration=max(penetration,0.30)
            if has(attacker,'Yamamoto'): penetration=max(penetration,0.20)
            if has(attacker,'Iwaizumi'): penetration=max(penetration,0.30)
            if has(attacker,'Kiryu','Reluctant Titan'): penetration=max(penetration,0.50)
            if has(attacker,'Tenma Udai'): penetration=max(penetration,0.30)
            if has(attacker,'Kanoka'): penetration=max(penetration,0.60)
        if has(attacker,'Goshiki') and self.start_enemy_hp_pct(ctx,attacker)<=75: penetration=max(penetration,0.20)
        if has(attacker,'Korai') and attacker.lane=='back' and self.skill_spd(attacker,a)>self.skill_spd(defender,d): penetration=max(penetration,0.20)
        if 'Open Play' in attacker.statuses: penetration=max(penetration,0.20)
        effective_def=floori(def_stat*(1-penetration))
        if off_trigger and has(attacker,'Matsukawa'): attack_stat=floori(attack_stat*1.5)
        attack_power_before_def=attack_stat
        raw=max(0,attack_power_before_def-effective_def)

        bonus=self.expansion_damage_bonus(ctx,attacker,stats)+self.anchor_damage_bonus(ctx,attacker,stats)
        if off_trigger and has(attacker,'Iizuna'):bonus+=max(0,floori(a['rec']*.8))
        if self.gear(attacker,'firm_contact') and hit_index==0:bonus+=3
        if off_trigger and self.gear(attacker,'ultimate_impact'):bonus+=attacker.max_cd*4
        if has(attacker,'Kageyama','Prodigious Talent') and len(self.distinct_bonuses(ctx,attacker,stats))>=6: bonus+=5
        if 'Open Play' in attacker.statuses and has(attacker,'Hinata'): bonus+=floori(self.skill_spd(attacker,a)*0.15)
        if has(attacker,'Tenma Udai') and self.skill_spd(attacker,a)>self.skill_spd(defender,d): bonus+=5
        if has(attacker,'Kosuke'):
            bonus+=sum(1 for ally in self.team(attacker).units if ally is not attacker and
                       (stats[ally.uid]['def']>stats[ally.uid]['rec'] if ally.uid in stats else
                        ally.noncombat_stat('def')>ally.noncombat_stat('rec')))
        # Always-on / live bonus damage.
        for ally in self.team(attacker).units:
            if has(ally,'Atsumu') and self.source_allowed(ctx,ally) and self.skill_spd(attacker,a)>self.skill_spd(defender,d):
                bonus+=floori((self.skill_spd(attacker,a)-self.skill_spd(defender,d))*0.70)
        if has(attacker,'Yamamoto'):
            fatigue=defender.statuses.get('Fatigue'); n=int(fatigue.payload or 0) if fatigue else 0
            bonus+=min(14,3*n)
        if has(attacker,'Kiryu','Reluctant Titan') and ctx.attacker is attacker:
            bonus+=floori(d['def']*0.30)
        if has(attacker,'Bokuto') and ctx.attacker is attacker:
            impacts=self.ally_skill_impacts(ctx,attacker,stats)
            bonus+=min(10,2*len(impacts))
            self._log(f'{self._unit_label(attacker)} A: {len(impacts)} active ally skills, +{min(10,2*len(impacts))} damage.')
        if 'Exposed' in defender.statuses: bonus+=5
        if attacker.stored_attack_bonus:
            bonus+=attacker.stored_attack_bonus
            attacker.stored_attack_bonus=0
        if ctx.next_attack_bonus.get(attacker.uid,0):
            bonus+=ctx.next_attack_bonus.pop(attacker.uid)

        # Ultimate bonus damage / stat-scaling.
        damage_mult=1.2 if self.gear(attacker,'block_breaker') and defender.character.position=='MB' else 1.0
        neutralize_percent_dr=(has(attacker,'Kiryu','Last Hope') and ctx.attacker is attacker and attacker.burden_stacks>=5)
        if off_trigger:
            if has(attacker,'Shibayama'): damage_mult*=1.5
            if has(attacker,'Atema'): bonus+=floori(a['def']*.5)
            if has(attacker,'Ushijima'):
                attack_power_before_def=floori(attack_stat*1.5); raw=max(0,attack_power_before_def-effective_def)
            if has(attacker,'Lev'): damage_mult*=1.5
            if has(attacker,'Suna'): bonus+=floori(a['spd']*0.40)+10
            if has(attacker,'Aone'): bonus+=floori(a['def']*0.50)
            if has(attacker,'Washio'): bonus+=floori(a['def']*0.80)
            if has(attacker,'Komi'): bonus+=floori(a['rec']*0.80)
            if has(attacker,'Usuri'): bonus+=floori(a['pow']*0.50)
            if has(attacker,'Atsumu'): bonus+=15 if self.allied(attacker,'Osamu') else 10
            if has(attacker,'Akagi'): damage_mult*=1.5
            if has(attacker,'Yamamoto'): bonus+=floori(a['pow']*0.10); neutralize_percent_dr=True
            if has(attacker,'Kai'): bonus+=floori(a['rec']*0.50)
            if has(attacker,'Asahi'): bonus+=floori(a['pow']*0.30)
            if has(attacker,'Suga'): damage_mult*=1.10
            if has(attacker,'Tanaka'): damage_mult*=1.5
            if has(attacker,'Oikawa'): damage_mult*=2.5
            if has(attacker,'Koganegawa'): bonus+=floori(a['def']*0.50)
            if has(attacker,'Futakuchi'): bonus+=15+(10 if defender.visible_debuffs else 0)
            if has(attacker,'Goshiki'):
                ep=self.enemy_hp_pct(attacker); bonus+=30 if ep<=50 else 20 if ep<=75 else 10
            if has(attacker,'Reon') or has(attacker,'Kenma'): bonus+=floori(a['rec']*0.50)
            if has(attacker,'Teradomari'):
                attack_power_before_def=floori(attack_stat*1.30); raw=max(0,attack_power_before_def-effective_def)
            if has(attacker,'Gao'): bonus+=floori(a['def']*0.80)
            if has(attacker,'Korai'): bonus+=floori((a['jmp'] if attacker.lane=='front' else a['spd'])*0.40)
            if has(attacker,'Kiryu','Reluctant Titan'): bonus+=10
            if has(attacker,'Kiryu','Last Hope'): bonus+=25+(10 if defensive_crit else 0)
            if has(attacker,'Daisho'): bonus+=15
            if has(attacker,'Takeru'): bonus+=floori(a['spd']*0.15)
        elif has(attacker,'Takeru') and not self.ultimates_suppressed(ctx) and (attacker.cd==0 or attacker.ult_triggered_this_combat) and self.skill_spd(attacker,a)>=self.skill_spd(defender,d)-4:
            bonus+=floori(a['spd']*0.15)

        potential_damage=floori((attack_power_before_def+bonus)*damage_mult)
        damage=floori((raw+bonus)*damage_mult)

        # Percentage damage reduction.
        drs=[]
        if has(defender,'Hinata','Miracle Maker') and self.skill_spd(defender,d)>self.skill_spd(attacker,a):
            drs.append(min(0.40,(self.skill_spd(defender,d)-self.skill_spd(attacker,a))*0.04))
        first_attack_action = attacker.attacks_this_combat == 0
        if has(defender,'Komi') and self.start_hp_pct(ctx,defender)>=50 and self.followup(ctx,attacker,defender,True) and first_attack_action:
            drs.append(0.50)
        if has(defender,'Korai') and defender.lane=='front' and d['jmp']>a['jmp'] and first_attack_action:
            drs.append(0.30)
            if hit_index==0: ctx.next_attack_bonus[defender.uid]=ctx.next_attack_bonus.get(defender.uid,0)+5
        ultimate_start=len(drs)
        if def_trigger:
            if has(defender,'Ezota'): drs.append(0.30)
            if has(defender,'Hinata','Miracle Maker'): drs.append(0.30)
            if has(defender,'Kita'): drs.append(0.30)
            if has(defender,'Yaku'): drs.append(0.40)
            if has(defender,'Unnan'): drs.append(0.50)
            if has(defender,'Takeru'): drs.append(0.40)
            if has(defender,'Kosuke'): drs.append(0.40)
        ultimate_end=len(drs)
        if offensive_crit and has(defender,'Washio') and d['def']>a['def']:
            drs.append(min(0.40,(d['def']-a['def'])*0.04))
        if offensive_crit and any(has(source,'Kanbayashi') and source.lane==defender.lane and self.source_allowed(ctx,source)
                                  for source in self.team(defender).units):
            drs.append(0.30)
        item_dr=self.equipment_hit_reduction(ctx,attacker,defender)
        if item_dr:drs.append(item_dr)
        receiving=self.anchor_receiving(ctx,attacker,defender,stats)
        if receiving and followup:drs.append(.30)
        if 'Perfect Window' in attacker.statuses: drs=drs[ultimate_start:ultimate_end]
        if neutralize_percent_dr: drs=[]
        for dr in drs: damage=floori(damage*(1-dr))

        # Flat reduction.
        flat=self.expansion_flat_reduction(ctx,defender,attacker,stats)
        if receiving:flat+=max(0,floori(d['rec']*.15))
        if def_trigger and has(defender,'Yaku'): flat+=5
        if def_trigger and has(defender,'Unnan'): flat+=5
        if has(defender,'Koganegawa') and ctx.defender is defender and d['jmp']>a['jmp']:
            flat+=floori(d['def']*0.10)
        damage=max(0,damage-flat)
        if def_trigger and has(defender,'Yamagata'): damage=1

        # Barrier is tied to the targeted/defending unit but protects team HP.
        if defender.barrier>0 and damage>0:
            absorbed=min(defender.barrier,damage); defender.barrier-=absorbed; damage-=absorbed
        t=self.team(defender); t.hp=max(0,t.hp-damage)
        attacker.attacks_made += 1
        defender.attacks_tanked += 1
        attacker.damage_dealt+=damage; defender.damage_taken+=damage
        defender.damage_mitigated += max(0, potential_damage - damage)
        crit_label=' CRIT' if offensive_crit else ' [DEF CRIT]' if defensive_crit else ''
        self._log(f'{self._unit_label(attacker)} → {self._unit_label(defender)}: {damage} damage{crit_label}. ({t.hp}/{t.max_hp} defending team HP)')

        # Post-damage Ultimate healing/effects.
        if off_trigger:
            if has(attacker,'Shirabu') or has(attacker,'Tsuki'): self.heal_team(attacker,floori(damage*0.50))
            if has(attacker,'Kuroo'): self.heal_team(attacker,floori(damage*0.50))
            if has(attacker,'Akaashi'): self.heal_team(attacker,floori(damage*0.30))
            if has(attacker,'Kai'): self.heal_team(attacker,floori(damage*0.30))
            if has(attacker,'Daichi'): self.heal_team(attacker,floori(damage*0.50))
            if has(attacker,'Kanoka'): self.heal_team(attacker,floori(damage*0.60))
            if has(attacker,'Kanbayashi'): self.heal_team(attacker,floori(a['rec']*0.75))
        if def_trigger and has(defender,'Kosuke'): self.heal_team(defender,10)
        if def_trigger and has(defender,'Hinata','Miracle Maker') and defender.lane=='back':
            defender.stored_attack_bonus+=floori(d['spd']*0.40)

        self.expansion_hit_result(ctx,attacker,defender,damage,offensive_crit,off_trigger,def_trigger)
        self.anchor_ultimate_result(ctx,attacker,defender,off_trigger)

        # Oikawa crit reward happens only if an offensive crit dealt damage.
        if offensive_crit and damage>0:
            for ally in self.team(attacker).units:
                if has(ally,'Oikawa') and self.source_allowed(ctx,ally):
                    key=(self.turn,attacker.uid); cap=2 if attacker is ally else 1
                    if self.crit_reward_used[key]<cap:
                        attacker.cd=max(0,attacker.cd-1); self.crit_reward_used[key]+=1

        # Cooldown charge after the hit. Triggering side does not also charge from that hit.
        if not off_trigger:
            attacker.cd=max(0,attacker.cd-self.charge_delta(ctx,attacker,defender,True))
        if not def_trigger:
            defender.cd=max(0,defender.cd-self.charge_delta(ctx,defender,attacker,False))

        # Reactions resolve after this hit. They can prime a later hit, never the same hit.
        if offensive_crit and has(attacker,'Tenma Udai') and ctx.attacker is attacker:
            for foe in self.enemy_team(attacker).units: foe.add_debuff('def',-6)
        critical_unit=attacker if offensive_crit else defender if defensive_crit else None
        if critical_unit is not None:
            self._kanoka_critical_reaction(ctx,critical_unit)

    def _kanoka_critical_reaction(self,ctx,critical_unit):
        for unit in self.enemy_team(critical_unit).units:
            if not has(unit,'Kanoka') or not self.source_allowed(ctx,unit): continue
            unit.cd=max(0,unit.cd-1)
            key=(self.turn,unit.uid)
            if unit.cd==0 and key not in self.kanoka_persistence_used:
                self.kanoka_persistence_used.add(key)
                unit.clear_debuffs()
                for stat in ('pow','def','rec','spd'):
                    unit.add_buff(stat,6,source=(unit.uid,'B'))
                    mod=unit.visible_buffs[stat]
                    if unit is ctx.original_actor and (unit.uid,'B') in mod.sources:
                        mod.expires_after_action=unit.actions_taken+2
                self._log(f'{self._unit_label(unit)} B: debuffs removed; Pow/Def/Rec/Spd+6 through next action.')

    def _new_unit_after_combat(self,ctx):
        self.effect_context=ctx;self.in_post_combat=True
        # Keep these reactions available even when the last hit depleted a team's HP.
        if ctx.temp_flags.get('new_unit_after_combat_resolved'): return
        ctx.temp_flags['new_unit_after_combat_resolved']=True
        self.equipment_after_combat(ctx)
        self.talent_after_combat(ctx)
        self.expansion_after_combat(ctx)
        self.anchor_after_combat(ctx)
        critical_uids=ctx.temp_flags.get('critical_unit_uids',set())
        for source in self.units:
            if not has(source,'Kiryu','Last Hope') or not self.source_allowed(ctx,source): continue
            foe=next((u for u in (ctx.attacker,ctx.defender) if u.side!=source.side and u.uid in critical_uids),None)
            key=(self.turn,source.uid)
            if foe is None or self.kiryu_pressure_used[key]>=2: continue
            self.kiryu_pressure_used[key]+=1
            source.burden_stacks=min(10,source.burden_stacks+1)
            for stat in ('def','rec','spd'): foe.add_debuff(stat,-4)
            self._log(f'{self._unit_label(source)} C: gains 1 Burden ({source.burden_stacks}/10); {foe.name} Def/Rec/Spd-4 through next action.')

        # Burden recoil uses the updated stack count, including a C-slot stack
        # earned from this combat, and applies after every Kiryu initiation.
        if has(ctx.attacker,'Kiryu','Last Hope'):
            amount=ctx.attacker.burden_stacks
            team=self.team(ctx.attacker); damaged=self.postcombat_damage(ctx,team,amount)
            if amount and damaged:
                self._log(f'{self._unit_label(ctx.attacker)} takes {amount} Burden damage after combat.')

        offensive_uids=ctx.temp_flags.get('offensive_crit_uids',set())
        for source in self.units:
            if not has(source,'Kanbayashi') or not self.source_allowed(ctx,source): continue
            if any(foe.uid in offensive_uids for foe in self.enemy_team(source).units):
                self.heal_team(source,6)

        crit_uids=ctx.temp_flags.get('defensive_crit_uids',set())
        for side in (self.team_a,self.team_b):
            if any(u.uid in crit_uids for u in side.units):
                for source in side.units:
                    if has(source,'Kosuke') and self.source_allowed(ctx,source):
                        self.heal_team(source,8)
                        foe_team=self.enemy_team(source)
                        if not(ctx.temp_flags.get('relay_teradomari') and foe_team.side==ctx.attacker.side):
                            self.postcombat_damage(ctx,foe_team,5)
                            self._log(f'{self._unit_label(source)} C: 5 after-combat damage to {foe_team.owner_name}.')
        for unit in (ctx.attacker,ctx.defender):
            if has(unit,'Kanoka'):
                for foe in self.enemy_team(unit).units: foe.cd=min(foe.max_cd,foe.cd+1)
                self._log(f'{self._unit_label(unit)} C: all foes remaining Ultimate CD +1 (up to maximum).')

    def heal_team(self,u,amount):
        if amount<=0:return
        t=self.team(u)
        # Healing effects cannot revive a team after lethal damage.
        if t.hp<=0:return
        context=getattr(self,'effect_context',None)
        if context and self.clean_receive_blocks_healing(context,u):return
        if context and self.gear(context.attacker,'recovery_denial') and u.side==context.defender.side:
            if getattr(self,'in_post_combat',False):return
            amount=floori(amount*.5)
        if any('Heal Block' in ally.statuses for ally in t.units):
            self._log(f'{self._unit_label(u)} cannot restore team HP while Heal Block is active.')
            return
        before=t.hp; t.hp=min(t.max_hp,t.hp+amount)
        if t.hp>before:self._log(f'{self._unit_label(u)} restores {t.hp-before} team HP.')

    # ---------- combat sequence ----------
    def attack_action(self,ctx,attacker,defender,followup=False):
        hits=2 if self.attacks_twice(ctx,attacker) else 1
        for i in range(hits):
            if self.team(defender).hp<=0: break
            self.resolve_hit(ctx,attacker,defender,followup=followup,hit_index=i)
        attacker.attacks_this_combat+=1

    def combat(self,ctx:CombatContext):
        a,d=ctx.attacker,ctx.defender
        self.effect_context=ctx;self.in_post_combat=False
        ctx.temp_flags['start_team_hp']={team.side:team.hp for team in (self.team_a,self.team_b)}
        initiations=self.expansion_memory[a.uid].setdefault('initiations',{})
        initiations[self.turn]=initiations.get(self.turn,0)+1
        ctx.temp_flags['start_hp_pct']={'A':pct(self.team_a.hp,self.team_a.max_hp),'B':pct(self.team_b.hp,self.team_b.max_hp)}
        for u in (a,d): u.ult_triggered_this_combat=False; u.attacks_this_combat=0
        self.expansion_combat_start(ctx)
        self.anchor_combat_start(ctx)
        # Siphon applies to each ally's first initiation this turn, once after combat.
        if a.uid not in self.siphon_used and any(has(source,'Usuri') and source is not a and self.source_allowed(ctx,source) for source in self.team(a).units):
            self.siphon_used.add(a.uid)
            ctx.temp_flags['siphon'] = True
        ctx.temp_flags['tendo_attacks_before']=d.attacks_tanked
        canctr=self.can_counter(ctx,d,a)
        used_vantage=False
        if canctr and self.vantage(ctx,d,a):
            self.attack_action(ctx,d,a); used_vantage=True
            if self.team(a).hp<=0:return

        self.attack_action(ctx,a,d)
        if self.team(d).hp<=0:return
        af=self.followup(ctx,a,d,True)
        if af and self.desperation(ctx,a):
            self.attack_action(ctx,a,d,followup=True); af=False
            if self.team(d).hp<=0:return
        if canctr and not used_vantage:
            self.attack_action(ctx,d,a)
            if self.team(a).hp<=0:return
        df=self.followup(ctx,d,a,False)
        if canctr and df:
            self.attack_action(ctx,d,a,followup=True)
            if self.team(a).hp<=0:return
        if af:
            self.attack_action(ctx,a,d,followup=True)

    def resolve_siphon(self,ctx):
        a,d=ctx.attacker,ctx.defender
        if ctx.temp_flags.pop('siphon',False):
            a.cd = max(0,a.cd-1)
            d.cd = min(d.max_cd,d.cd+1)
            self._log(f'{self._unit_label(a)} uses Siphon: own remaining CD −1; {d.name} remaining CD +1.')

    def after_combat(self,ctx):
        self.effect_context=ctx;self.in_post_combat=True
        self.begin_bonus_event()
        a,d=ctx.attacker,ctx.defender
        self.resolve_siphon(ctx)
        ctx.next_attack_bonus.clear()
        # Wings Hinata decoy debuff
        for x in (a,d):
            if has(x,'Hinata','Wings of Dawn'):
                xstat=self.skill_spd(x,self.combat_stats(ctx)[x.uid])
                for f in self.enemy_team(x).units:
                    if self.skill_spd(f)<xstat: f.add_debuff('spd',-3); f.add_debuff('jmp',-3)
            if has(x,'Kageyama','Crown of Precision'):
                for f in self.enemy_team(x).units: f.add_debuff('def',-4); f.add_debuff('rec',-4)
                target = d if x is a else a
                target.add_debuff('spd',-3)
                if target is ctx.original_actor and target.visible_debuffs.get('spd'):
                    target.visible_debuffs['spd'].expires_after_action = target.actions_taken + 2
        # Nishinoya heal
        if has(d,'Nishinoya'): self.heal_team(d,6)
        # Konoha heal whenever he fought
        if has(a,'Konoha') or has(d,'Konoha'):
            self.heal_team(a if has(a,'Konoha') else d,6)
        # Bokuto self+target debuffs if Bokuto attacked
        for boku,foe in ((a,d),(d,a)):
            if has(boku,'Bokuto') and boku.attacks_this_combat>0:
                for x in (boku,foe):
                    for s in ('pow','jmp','spd'): x.add_debuff(s,-6)
        # Kenma Fatigue after any foe combat, if support not disabled.
        for foe in (a,d):
            enemy_team=self.enemy_team(foe)
            for k in enemy_team.units:
                if has(k,'Kenma'):
                    st=foe.statuses.get('Fatigue'); n=(int(st.payload or 0)+1) if st else 1
                    foe.add_status('Fatigue',False,payload=n)
        # Aran cooldown refund
        for x in (a,d):
            if has(x,'Aran') and x.ult_triggered_this_combat: x.cd=max(0,x.cd-2)
            if has(x,'Tsuki'):
                target=d if x is a else a
                for foe in self.enemy_team(x).units:
                    if foe is target or foe.lane=='front': foe.cd=min(foe.max_cd,foe.cd+1)
        # Kita heal if defensive Ultimate triggered
        if has(d,'Kita') and d.ult_triggered_this_combat: self.heal_team(d,6)
        # Akagi cannot-counter spread
        for x in (a,d):
            if has(x,'Akagi') and x.attacks_this_combat>0:
                for f in self.enemy_team(x).units: f.add_status('Cannot Counter',False,through_next_action=True)
        # Yaku target debuff
        if has(a,'Yaku') or has(d,'Yaku'):
            y=a if has(a,'Yaku') else d; target=d if y is a else a
            target.add_debuff('pow',-4); target.add_debuff('qik',-4)
        # Daichi post-Ult flat heal
        for x in (a,d):
            if has(x,'Daichi') and x.ult_triggered_this_combat: self.heal_team(x,10)
        # Tanaka ally buff
        for x in (a,d):
            if has(x,'Tanaka'):
                for ally in self.team(x).units:
                    if ally is not x:
                        for s in ('pow','qik','spd','def','rec'): ally.add_buff(s,2,source=(x.uid,'B'))
        # Iwaizumi Charged
        if has(d,'Iwaizumi') and a.offense_key=='qik': d.add_status('Charged',True,through_next_action=True)
        # Koganegawa Ultimate support
        for x in (a,d):
            if has(x,'Koganegawa') and x.ult_triggered_this_combat:
                for f in self.team(x).units:
                    if f.lane=='front': f.add_buff('def',4,source=(x.uid,'U')); f.add_buff('jmp',4,source=(x.uid,'U'))
        # Koganegawa turns a blocked defensive follow-up into Intimidation for his next action.
        if has(d,'Koganegawa') and self.start_hp_pct(ctx,d)<=75 and not (self.can_counter(ctx,d,a) and self.followup(ctx,d,a,False)):
            d.add_status('Intimidation',True,through_next_action=True)
        # Tendo self-chip after being attacked
        if has(d,'Tendo') and d.attacks_tanked > ctx.temp_flags.get('tendo_attacks_before',d.attacks_tanked):
            target,amount=(d,5) if self.skill_spd(d)>self.skill_spd(a) else (a,10)
            if not(ctx.temp_flags.get('relay_teradomari') and target.side==a.side):
                damaged=self.postcombat_damage(ctx,self.team(target),amount)
                if damaged:self._log(f'{self._unit_label(d)} A: {amount} after-combat damage to {self.team(target).owner_name}.')
        if has(d,'Unnan') and not d.ult_triggered_this_combat: self.heal_team(d,6)
        # Reon heal
        for x in (a,d):
            if has(x,'Reon') and x.ult_triggered_this_combat:self.heal_team(x,5)
        # Teradomari speed + self team damage
        if has(a,'Teradomari'):
            a.add_buff('spd',3)
            self.postcombat_damage(ctx,self.team(a),6)
        # Unnan team-wide Jmp debuff
        if has(a,'Unnan') or has(d,'Unnan'):
            un=a if has(a,'Unnan') else d
            for f in self.enemy_team(un).units:f.add_debuff('jmp',-3)
        # Daisho Free Ball
        for x in (a,d):
            if has(x,'Daisho'):
                for f in self.enemy_team(x).units:f.add_status('Free Ball',False,through_next_action=True)
        # Takeru heal / swap when his Ultimate triggered
        if has(a,'Takeru') and a.ult_triggered_this_combat:
            self.heal_team(a,10); self._swap_enemy_formation(a)
        if has(d,'Takeru') and d.ult_triggered_this_combat:
            self.heal_team(d,10); self._swap_enemy_formation(d)
        # Restoration requires Korai's Ultimate in this combat.
        for k in (a,d):
            if has(k,'Korai') and k.ult_triggered_this_combat:
                for ally in self.team(k).units: ally.lane=ally.original_lane

        self.expansion_after_combat(ctx)

        # Postcombat Ultimates.
        extra=False
        for x,initiated in ((a,True),(d,False)):
            if x.cd!=0 or 'UltimateDisabled' in x.statuses: continue
            if has(x,'Kageyama','Prodigious Talent'):
                if self.trigger_ultimate(x,ctx,'post'): self.talent_ultimate(x)
            elif has(x,'Kageyama','Crown of Precision'):
                if self.trigger_ultimate(x,ctx,'post'):
                    for ally in self.team(x).units:
                        for stat in ('pow','qik'):
                            ally.add_buff(stat,6,source=(x.uid,'U'))
                            if ally is ctx.original_actor:
                                ally.visible_buffs[stat].expires_after_action = ally.actions_taken + 2
            elif has(x,'Tendo'):
                if self.trigger_ultimate(x,ctx,'post'):
                    for f in self.enemy_team(x).units:f.add_debuff('pow',-6);f.add_debuff('qik',-6)
            elif initiated and x.name=='Akifumi':
                key=(self.turn,x.uid)
                allies=[ally for ally in self.team(x).units if ally is not x and ally.acted_this_turn]
                if allies and key not in self.extra_action_used and self.trigger_ultimate(x,ctx,'post'):
                    self.extra_action_used.add(key)
                    hp=min(ally.character.stats['hp'] for ally in allies)
                    ctx.temp_flags['akifumi_action']=self.rng.choice([ally for ally in allies if ally.character.stats['hp']==hp])
            elif initiated and x.name in ('Bokuto','Fukunaga','Himekawa'):
                key=(self.turn,x.uid)
                if key not in self.extra_action_used and self.trigger_ultimate(x,ctx,'post'):
                    self.extra_action_used.add(key); extra=True
        # Tanaka postcombat primer
        if has(a,'Tanaka') and a.cd==a.max_cd:a.cd=max(0,a.cd-1)
        if has(d,'Tanaka') and d.cd==d.max_cd:d.cd=max(0,d.cd-1)
        self._new_unit_after_combat(ctx)
        return extra

    def _swap_enemy_formation(self,u):
        enemy=self.enemy_team(u); fronts=[x for x in enemy.units if x.lane=='front']; backs=enemy.backs()
        if fronts and backs:
            f=fronts[0] if len(fronts)==1 else self.rng.choice(fronts)
            b=self.rng.choice(backs); f.lane='back'; b.lane='front'

    def perform_action(self,actor:BattleUnit, *, extra=False, relay_source=None, relay_teradomari=False):
        if not self.team(actor).living() or not self.enemy_team(actor).living(): return False
        actor.action_in_progress=True
        if self.transfer_action(actor): return True
        self.action_start(actor)
        target,override=self.choose_target(actor)
        if target is None:
            self._log(f'{self._unit_label(actor)} has no legal attack target.')
            actor.actions_taken+=1; actor.acted_this_turn=True; actor.action_in_progress=False; actor.expire_after_action()
            return True
        attacker,ky=self.maybe_replace_attacker(actor,target)
        defender,saved=self.maybe_save(attacker,target)
        ctx=CombatContext(self,attacker,defender,actor,target,override_targeting=override,kyotani_intercept=ky,save_triggered=saved)
        ctx.temp_flags['relay_source']=relay_source
        ctx.temp_flags['relay_teradomari']=relay_teradomari and attacker is actor
        self._log(f'[{self.turn}] {self._unit_label(attacker)} attacks {self._unit_label(defender)}.')
        self.combat(ctx)
        self.resolve_siphon(ctx)
        extra_action=False
        if self.team_a.living() and self.team_b.living(): extra_action=self.after_combat(ctx)
        else: self._new_unit_after_combat(ctx)
        actor.actions_taken+=1; actor.acted_this_turn=True; actor.action_in_progress=False; actor.expire_after_action()
        self.effect_context=None;self.in_post_combat=False
        recipient=ctx.temp_flags.get('akifumi_action')
        if recipient and self.team(actor).living() and self.enemy_team(actor).living():self.perform_action(recipient,extra=True)
        if extra_action and attacker is actor and self.team(actor).living() and self.enemy_team(actor).living():
            self._log(f'{self._unit_label(actor)} gains another action!')
            self.perform_action(actor,extra=True)
        return True

    def run(self) -> BattleResult:
        self.apply_match_start()
        for _ in range(self.max_turns):
            order=self.start_turn()
            self._log(f'— Turn {self.turn} (initial order): ' + ' → '.join(self._unit_label(u) for u in order))
            pending={u.uid for u in order}
            while pending and self.team_a.living() and self.team_b.living():
                # Re-sort visible Speed after each complete action; completed units stay out.
                actor=next(u for u in self._action_order() if u.uid in pending)
                pending.remove(actor.uid)
                self.perform_action(actor)
            self.end_turn()
            if not self.team_a.living() or not self.team_b.living(): break
        if self.team_a.hp==self.team_b.hp:
            winner='draw'
        else:
            winner='A' if self.team_a.hp>self.team_b.hp else 'B'
        stats={u.uid:{'damage_dealt':u.damage_dealt,'damage_taken':u.damage_taken,'damage_mitigated':u.damage_mitigated,'crits':u.crits,'actions':u.actions_taken,'attacks_made':u.attacks_made,'attacks_tanked':u.attacks_tanked,'lane':u.original_lane,'character_id':u.character.id} for u in self.units}
        return BattleResult(self.equipment_winner(winner),self.mode,self.turn,self.team_a.hp,self.team_b.hp,self.log,stats)
