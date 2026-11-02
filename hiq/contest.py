"""Fixed Clan Contest formations and a twenty-turn, cycling-HP battle engine.

Only explicitly implemented support slots are selected by these presets.
Supports remain teammates for bonds, but never take actions or enter combat.
"""
from dataclasses import replace
from datetime import datetime,timezone
from .battle.engine import BattleEngine, floori, has
from .data import CHARACTERS
from .models import CardInstance

SUPPORT_SLOTS={'Oikawa':('B','C'),'Ushijima':('C',),'Aran':('C',),
    'Tenma Udai':('C',),'Suga':('C',),'Lev':('C',),'Suna':('C',),
    'Hirugami':('C',),'Suwa':('C',),'Kita':('C',),'Konoha':('C',),
    'Koganegawa':('C',),'Aone':('C',),'Daichi':('C',)}

def character(name,title=None):
    matches=[c for c in CHARACTERS if c.name==name and (title is None or c.title==title)]
    if len(matches)!=1:raise ValueError('Choose an exact contest variant: '+name)
    return matches[0]

# Offensive, defensive and tempo formations alternate. The mixed-school presets
# deliberately trade extra bonds for stronger support combinations.
PRESETS=[
 ('Iron Left', 'Ushijima',None,'front',['Oikawa','Daichi','Tenma Udai','Suga','Suwa','Kita']),
 ('Conduct the Court','Oikawa',None,'front',['Ushijima','Daichi','Suga','Tenma Udai','Lev','Suwa']),
 ('No Easy Points','Kiryu','Last Hope','front',['Oikawa','Aran','Tenma Udai','Suwa','Kita','Daichi']),
 ('Above the Net','Korai',None,'front',['Oikawa','Ushijima','Tenma Udai','Suna','Suwa','Konoha']),
 ('Iron Fortress','Aone',None,'front',['Koganegawa','Lev','Tenma Udai','Kita','Oikawa','Suwa']),
 ('Perfect Sequence','Kageyama','Prodigious Talent','front',['Suga','Daichi','Oikawa','Konoha','Tenma Udai','Suwa']),
 ('Unshaken Ace','Kanoka',None,'front',['Oikawa','Aran','Tenma Udai','Kita','Suwa','Daichi']),
 ('Small Giant','Tenma Udai',None,'front',['Oikawa','Ushijima','Aran','Suna','Suwa','Konoha']),
 ('Patience Pays','Suwa',None,'front',['Konoha','Tenma Udai','Kita','Oikawa','Lev','Suna']),
 ('Carefree Wall','Nozawa',None,'front',['Tenma Udai','Kita','Lev','Oikawa','Suwa','Suna']),
 ('Towering Trouble','Matsukawa',None,'front',['Oikawa','Ushijima','Tenma Udai','Kita','Suwa','Lev']),
 ('Dawn Breaker','Hinata','Wings of Dawn','front',['Suga','Daichi','Oikawa','Ushijima','Tenma Udai','Suwa']),
 ('Reach the Sky','Gao',None,'front',['Oikawa','Ushijima','Aran','Suna','Suwa','Konoha']),
 ('Hold the Line','Iizuna',None,'front',['Oikawa','Tenma Udai','Kita','Lev','Suna','Suwa'])]


def preset_specs(index):
    label,name,title,lane,supports=PRESETS[index]
    chars=[character(name,title)]+[character(n) for n in supports]
    if any(c.name==chars[0].name for c in chars[1:]):raise ValueError('A featured unit cannot support itself.')
    return [(CardInstance(f'contest-{index}-{i}',f'CONTEST:{index}:{i}',c.card_id,c.id,0,0,label,i,
                          10 if i==0 else 0,datetime(2026,1,1,tzinfo=timezone.utc),True),c,
             lane if i==0 else 'front') for i,c in enumerate(chars)]


class ContestEngine(BattleEngine):
    def __init__(self,players,index,**kwargs):
        if len(players)!=6:raise ValueError('Clan Contest uses a complete 6v6 team.')
        super().__init__(players,preset_specs(index),mode='clancontest',**kwargs)
        self.feature=self.team_b.units[0]
        self.supports=self.team_b.units[1:]
        self.support_ids={u.uid for u in self.supports}
        self.team_b.max_hp=self.team_b.hp=self.feature.base['hp']
        self.score=0;self.bars=0;self.max_turns=20
        for source in self.supports:
            source.cd=source.max_cd=999999

    def enemy_team(self,unit):
        team=super().enemy_team(unit)
        if unit.side=='A' and hasattr(self,'feature'):
            return ActiveOpponent(team,self.feature)
        return team

    def _action_order(self,*args,**kwargs):
        return [u for u in super()._action_order(*args,**kwargs) if u.uid not in self.support_ids]

    def attack_target_allowed(self,unit):
        return unit.uid not in self.support_ids and super().attack_target_allowed(unit)

    def perform_action(self,actor,**kwargs):
        if actor.uid in self.support_ids:return False
        return super().perform_action(actor,**kwargs)

    def maybe_save(self,attacker,intended):
        if intended.side=='B':return intended,False
        return super().maybe_save(attacker,intended)

    def maybe_replace_attacker(self,actor,target):
        if actor.side=='B':return actor,False
        return super().maybe_replace_attacker(actor,target)

    def damage_team(self,team,amount):
        amount=max(0,int(amount))
        if team.side!='B':return super().damage_team(team,amount)
        self.score+=amount
        remaining=team.hp-amount
        if remaining<=0:
            bars=1+(-remaining)//team.max_hp
            self.bars+=bars;remaining+=bars*team.max_hp
            self._log(f'Featured unit refills {bars} HP bar(s). Total contest damage: {self.score}.')
        team.hp=remaining

    def start_turn(self):
        super().start_turn()
        for source in self.supports:self.support_turn(source)
        return self._action_order()

    def support_turn(self,source):
        """Run only the listed slots, always selecting the sole active ally."""
        f=self.feature;name=source.name
        buffs={}
        if name=='Tenma Udai':buffs={'def':4,'rec':4}
        elif name=='Ushijima' and self.turn%2:buffs={'jmp':6}
        elif name=='Aran':buffs={'pow':6}
        elif name=='Suga':buffs={'pow':6,'qik':6,'spd':6}
        elif name=='Lev':buffs={'rec':6}
        elif name=='Suna':buffs={'spd':6}
        elif name=='Hirugami' and f.character.position=='MB':buffs={'def':4}
        elif name=='Konoha':buffs={'jmp':3}
        elif name=='Koganegawa' and f.character.school=='Date Tech':buffs={'def':4,'rec':4,'jmp':4}
        elif name=='Aone' and f.character.school=='Date Tech':buffs={'pow':4,'qik':4,'spd':4}
        elif name=='Suwa':f.add_status('Bunch Block',True,source=(source.uid,'C'))
        for stat,value in buffs.items():f.add_buff(stat,value,source=(source.uid,'C'))

    def end_turn(self):
        super().end_turn()
        # Their own passives and Ultimates never become active combat effects.
        for source in self.supports:source.cd=source.max_cd


class ActiveOpponent:
    """Foes see only the featured unit; its own ally skills see its supports."""
    def __init__(self,team,feature):
        object.__setattr__(self,'team',team)
        object.__setattr__(self,'units',[feature])

    def __getattr__(self,key):return getattr(self.team,key)
    def __setattr__(self,key,value):setattr(self.team,key,value)
