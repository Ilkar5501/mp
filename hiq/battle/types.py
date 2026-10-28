from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import CardInstance, CharacterDef

STAT_KEYS = ('hp','pow','qik','def','rec','spd','jmp')

@dataclass(slots=True)
class TimedMod:
    value: int
    sources: set[tuple[str,str]] = field(default_factory=set)
    expires_after_action: int | None = None
    expires_end_turn: int | None = None
    bonus_group: tuple = ()

@dataclass(slots=True)
class Status:
    name: str
    positive: bool
    payload: Any = None
    expires_after_action: int | None = None
    expires_end_turn: int | None = None
    sources: set[tuple[str,str]] = field(default_factory=set)
    source_expiries: dict[tuple[str,str],int] = field(default_factory=dict)

@dataclass(slots=True)
class BattleUnit:
    uid: str
    card: CardInstance | None
    character: CharacterDef
    side: str
    lane: str
    original_lane: str
    base: dict[str,int]
    max_cd: int
    cd: int
    visible_buffs: dict[str,TimedMod] = field(default_factory=dict)
    visible_debuffs: dict[str,TimedMod] = field(default_factory=dict)
    statuses: dict[str,Status] = field(default_factory=dict)
    barrier: int = 0
    barrier_sources: set[tuple[str,str]] = field(default_factory=set)
    action_in_progress: bool = False
    actions_taken: int = 0
    acted_this_turn: bool = False
    ult_triggered_this_combat: bool = False
    attacks_this_combat: int = 0
    attacks_made: int = 0
    attacks_tanked: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    damage_mitigated: int = 0
    crits: int = 0
    extra_actions_this_turn: int = 0
    designated_uid: str | None = None
    stored_attack_bonus: int = 0
    burden_stacks: int = 0
    bonus_event: int = 0

    @property
    def name(self) -> str:
        return self.character.name

    @property
    def title(self) -> str:
        return self.character.title

    @property
    def char_id(self) -> str:
        return self.character.id

    @property
    def offense_key(self) -> str:
        return self.character.offense_key

    def visible_bonus(self, stat: str) -> int:
        m = self.visible_buffs.get(stat)
        return max(0, m.value) if m else 0

    def visible_penalty(self, stat: str) -> int:
        m = self.visible_debuffs.get(stat)
        return min(0, m.value) if m else 0

    def noncombat_stat(self, stat: str, neutralize_bonus: bool = False, neutralize_penalty: bool = False) -> int:
        value = int(self.base.get(stat,0))
        if not neutralize_bonus:
            value += self.visible_bonus(stat)
        if not neutralize_penalty:
            value += self.visible_penalty(stat)
        return value

    def add_buff(self, stat: str, value: int, *, through_next_action: bool = True, turn: int | None = None, source: tuple[str,str] | None = None) -> None:
        if value <= 0: return
        exp = self.actions_taken + 1 + int(self.action_in_progress) if through_next_action else None
        existing = self.visible_buffs.get(stat)
        if existing is None or value > existing.value:
            self.visible_buffs[stat] = TimedMod(value=value, expires_after_action=exp, expires_end_turn=turn, sources={source} if source else set(), bonus_group=(*source,'visible',self.bonus_event) if source else ('visible',stat,self.bonus_event))
        elif value == existing.value:
            if source: existing.sources.add(source)
            if exp is not None: existing.expires_after_action=max(existing.expires_after_action or 0,exp)

    def add_debuff(self, stat: str, value: int, *, through_next_action: bool = True, turn: int | None = None) -> None:
        if value >= 0: return
        exp = self.actions_taken + 1 + int(self.action_in_progress) if through_next_action else None
        existing = self.visible_debuffs.get(stat)
        if existing is None or value < existing.value:
            self.visible_debuffs[stat] = TimedMod(value=value, expires_after_action=exp, expires_end_turn=turn)
        elif value == existing.value and exp is not None:
            existing.expires_after_action=max(existing.expires_after_action or 0,exp)

    def clear_debuffs(self) -> None:
        self.visible_debuffs.clear()
        for key in [k for k,v in self.statuses.items() if not v.positive]:
            self.statuses.pop(key, None)

    def add_status(self, name: str, positive: bool, payload: Any = None, *, through_next_action: bool = False, end_turn: int | None = None, source: tuple[str,str] | None = None) -> None:
        self.statuses[name] = Status(
            name=name, positive=positive, payload=payload,
            expires_after_action=(self.actions_taken + 1 + int(self.action_in_progress) if through_next_action else None),
            expires_end_turn=end_turn,
            sources={source} if source else set(),
        )

    def has_positive_status(self) -> bool:
        return any(s.positive for s in self.statuses.values())

    def expire_after_action(self) -> None:
        for d in (self.visible_buffs, self.visible_debuffs):
            for stat in [k for k,v in d.items() if v.expires_after_action is not None and self.actions_taken >= v.expires_after_action]:
                d.pop(stat, None)
        for key in [k for k,v in self.statuses.items() if v.expires_after_action is not None and self.actions_taken >= v.expires_after_action]:
            self.statuses.pop(key, None)

    def expire_end_turn(self, turn: int) -> None:
        for d in (self.visible_buffs, self.visible_debuffs):
            for stat in [k for k,v in d.items() if v.expires_end_turn is not None and turn >= v.expires_end_turn]:
                d.pop(stat, None)
        for key in [k for k,v in self.statuses.items() if v.expires_end_turn is not None and turn >= v.expires_end_turn]:
            self.statuses.pop(key, None)

@dataclass(slots=True)
class BattleTeam:
    side: str
    owner_id: int | None
    owner_name: str
    units: list[BattleUnit]
    hp: int
    max_hp: int

    def front(self) -> BattleUnit | None:
        return next((u for u in self.units if u.lane == 'front'), None)

    def backs(self) -> list[BattleUnit]:
        return [u for u in self.units if u.lane == 'back']

    def living(self) -> bool:
        return self.hp > 0

@dataclass(slots=True)
class CombatContext:
    battle: Any
    attacker: BattleUnit
    defender: BattleUnit
    original_actor: BattleUnit
    original_target: BattleUnit
    override_targeting: bool = False
    kyotani_intercept: bool = False
    save_triggered: bool = False
    first_attack_by: set[str] = field(default_factory=set)
    first_attack_damage_reduced_for: set[str] = field(default_factory=set)
    next_attack_bonus: dict[str,int] = field(default_factory=dict)
    temp_flags: dict[str,Any] = field(default_factory=dict)

@dataclass(slots=True)
class BattleResult:
    winner_side: str
    mode: str
    turns: int
    team_a_hp: int
    team_b_hp: int
    log: list[str]
    stats: dict[str,dict[str,int]]
