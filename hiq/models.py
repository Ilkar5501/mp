from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

STAT_KEYS = ('hp', 'pow', 'qik', 'def', 'rec', 'spd', 'jmp')


@dataclass(slots=True, frozen=True)
class AbilityDef:
    name: str
    text: str
    timing: str = ''


@dataclass(slots=True, frozen=True)
class CharacterDef:
    id: str
    name: str
    title: str
    position: str
    school: str
    rarity: int
    role_class: str
    card_art: str
    character_icon: str
    stats: dict[str, int]
    ult_cd: int
    abilities: dict[str, AbilityDef]
    extended: dict[str, str]
    clarification_count: int = 0

    @property
    def offense_key(self) -> str:
        return 'pow' if self.stats.get('pow', 0) > 0 else 'qik'

    @property
    def card_id(self) -> str:
        return f'{self.name}:{self.title}' if self.title else self.name

    @property
    def bst(self) -> int:
        return sum(int(self.stats.get(k, 0)) for k in STAT_KEYS)


@dataclass(slots=True)
class CardInstance:
    instance_id: str
    full_instance_id: str
    card_id: str
    character_id: str
    owner_id: int
    original_owner_id: int
    original_owner_name: str
    serial_no: int
    merge_level: int
    obtained_at: datetime
    locked: bool = False
    boon_stat: str | None = None
    bane_stat: str | None = None
    equipped_item_id: str | None = None
    equipment_key: str | None = None


@dataclass(slots=True)
class TeamRecord:
    owner_id: int
    kind: str
    front_id: str
    back1_id: str
    back2_id: str


@dataclass(slots=True)
class SavedTeam:
    owner_id: int
    slot: int
    name: str
    front_id: str | None
    back1_id: str | None
    back2_id: str | None

    team_format: int = 3
    front2_id: str | None = None
    front3_id: str | None = None
    back3_id: str | None = None

    @property
    def positions(self):
        if self.team_format == 6:
            return (('Frontline 1','front_id','front'),('Frontline 2','front2_id','front'),
                ('Frontline 3','front3_id','front'),('Backline 1','back1_id','back'),
                ('Backline 2','back2_id','back'),('Backline 3','back3_id','back'))
        return (('Frontline','front_id','front'),('Backline 1','back1_id','back'),('Backline 2','back2_id','back'))

    @property
    def card_ids(self):
        return tuple(getattr(self,key) for _,key,_ in self.positions)

    @property
    def entries(self):
        return [(label,getattr(self,key),lane) for label,key,lane in self.positions]

    @property
    def complete(self) -> bool:
        return all(self.card_ids)

    @property
    def used(self) -> int:
        return sum(bool(x) for x in self.card_ids)


@dataclass(slots=True)
class UserProgress:
    discord_id: int
    coins: int
    last_daily_date: str | None
    daily_win_date: str | None
    daily_wins: int
    week_key: str | None
    weekly_wins: int
    weekly_claimed: str
    tutorial_started: bool = False
    starter_daily_claimed: bool = False
    active_team_slot: int = 1
