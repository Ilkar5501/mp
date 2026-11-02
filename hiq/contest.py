"""Fixed Clan Contest formations and a twenty-turn, cycling-HP battle engine.

Only explicitly implemented support slots are selected by these presets.
Supports remain teammates for bonds, but never take actions or enter combat.
"""
from datetime import datetime,timezone
from .battle.engine import BattleEngine, has
from .data import CHARACTERS
from .models import CardInstance

RULES_VERSION=3
SUPPORT_SLOTS={'Oikawa':('B','C'),'Ushijima':('C',),'Aran':('C',),
    'Tenma Udai':('C',),'Suga':('C',),'Lev':('C',),'Suna':('C',),
    'Hirugami':('C',),'Suwa':('C',),'Kita':('C',),'Konoha':('C',),
    'Koganegawa':('C',),'Aone':('C',),'Daichi':('C',),
    'Atsumu':('C',),'Osamu':('C',),'Iwaizumi':('C',),'Kyotani':('C',),'Korai':('C',),
    'Kiryu':('C',),'Kanbayashi':('B','C'),'Iizuna':('B','C'),
    'Akaashi':('B','C'),'Reon':('B','C'),'Kenma':('B','C'),
    'Usuri':('C',),'Kageyama':('C',),'Hinata':('C',),
    'Kuroo':('C',),'Nishinoya':('C',),'Nozawa':('C',),'Kai':('C',),
    'Yamamoto':('C',),'Washio':('C',),'Komi':('C',),'Kosuke':('B','C'),
    'Futakuchi':('C',),'Gao':('C',),'Maruyama':('C',),'Watari':('C',),
    'Shirabu':('C',),'Yamagata':('C',),'Echigo':('C',)}


def support_slots(char):
    if char.name=='Kageyama':
        return ('B',) if char.title=='Crown of Precision' else ('C',)
    if char.name=='Kiryu' and char.title!='Last Hope':return ()
    if char.name=='Hinata' and char.title!='Miracle Maker':return ()
    return SUPPORT_SLOTS.get(char.name,())

def character(name,title=None):
    name=name.split('@',1)[0]
    if ' — ' in name:name,title=name.split(' — ',1)
    matches=[c for c in CHARACTERS if c.name==name and (title is None or c.title==title)]
    if len(matches)!=1:raise ValueError('Choose an exact contest variant: '+name)
    return matches[0]

# Keep opponents for attempts that were saved before this revision.
LEGACY_PRESETS=[
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


# Six support characters per formation; explicit variants and source lanes prevent
# an accidental change of skill or Korai aura. Only active allies receive grants.
V2_PRESETS=[
 ('Monarch Above the Net','Ushijima',None,'front',
  ['Oikawa','Iwaizumi','Kenma','Konoha','Suga','Shirabu']),
 ('Little Giant — Jump','Korai',None,'front',
  ['Oikawa','Ushijima','Iwaizumi','Kenma','Konoha','Nozawa']),
 ('Conductor — Jump','Oikawa',None,'front',
  ['Ushijima','Iwaizumi','Kenma','Konoha','Kyotani','Watari']),
 ('Little Giant — Speed','Korai',None,'back',
  ['Atsumu','Suna','Osamu','Tenma Udai','Lev','Maruyama']),
 ('Fukurodani Encore','Bokuto',None,'front',
  ['Akaashi','Konoha','Washio','Komi','Watari','Iizuna']),
 ('Date Tech Iron Wall','Aone',None,'front',
  ['Koganegawa','Kosuke','Futakuchi','Gao','Reon','Nozawa']),
 ('Wings at Full Speed','Hinata','Wings of Dawn','front',
  ['Kageyama — Prodigious Talent','Atsumu','Osamu','Suga','Tenma Udai','Daichi']),
 ('Twin Shadow — Speed','Osamu',None,'front',
  ['Atsumu','Suna','Kita','Hinata — Miracle Maker','Kenma','Suwa']),
 ('Nekoma Counterplay','Kuroo',None,'front',
  ['Kenma','Yamamoto','Lev','Kai@back','Iizuna','Watari']),
 ('Last Hope — Burden and Break','Kiryu','Last Hope','front',
  ['Usuri','Iizuna','Reon','Aran','Suwa','Korai@back']),
 ('Prodigious Stat Ball','Kageyama','Prodigious Talent','front',
  ['Suga','Tenma Udai','Konoha','Hinata — Miracle Maker','Lev','Suwa']),
 ('Unshaken at Full Speed','Kanoka',None,'front',
  ['Kiryu — Last Hope','Kanbayashi','Usuri','Atsumu','Suna','Korai@back']),
 ('Original Giant — Crit and Speed','Tenma Udai',None,'front',
  ['Oikawa','Ushijima','Iwaizumi','Atsumu','Suga','Kenma']),
 ('Anchor-Powered Ace','Iwaizumi',None,'front',
  ['Iizuna','Lev','Nishinoya','Nozawa','Kai@back','Maruyama'])]

PRESETS=[
 ('Monarch Above the Net','Ushijima',None,'front',
  ['Oikawa','Iwaizumi','Tenma Udai','Konoha','Suga','Shirabu']),
 ('Little Giant — Jump','Korai',None,'front',
  ['Oikawa','Ushijima','Iwaizumi','Suwa','Konoha','Nozawa']),
 ('Conductor — Jump','Oikawa',None,'front',
  ['Ushijima','Iwaizumi','Echigo','Konoha','Kyotani','Watari']),
 ('Little Giant — Speed','Korai',None,'back',
  ['Atsumu','Suna','Osamu','Tenma Udai','Lev','Maruyama']),
 ('Fukurodani Encore','Bokuto',None,'front',
  ['Akaashi','Konoha','Washio','Komi','Watari','Iizuna']),
 ('Date Tech Iron Wall','Aone',None,'front',
  ['Koganegawa','Kosuke','Futakuchi','Gao','Reon','Nozawa']),
 ('Wings at Full Speed','Hinata','Wings of Dawn','front',
  ['Kageyama — Crown of Precision','Atsumu','Korai@back','Suga','Tenma Udai','Daichi']),
 ('Twin Shadow — Speed','Osamu',None,'front',
  ['Atsumu','Suna','Kita','Hinata — Miracle Maker','Watari','Suwa']),
 ('Nekoma Counterplay','Kuroo',None,'front',
  ['Yamagata','Kanbayashi','Daichi','Aran','Nozawa','Iizuna']),
 ('Last Hope — Burden and Break','Kiryu','Last Hope','front',
  ['Usuri','Iizuna','Reon','Aran','Suwa','Korai@back']),
 ('Prodigious Stat Ball','Kageyama','Prodigious Talent','front',
  ['Suga','Tenma Udai','Konoha','Hinata — Miracle Maker','Lev','Suwa']),
 ('Unshaken at Full Speed','Kanoka',None,'front',
  ['Kiryu — Last Hope','Kanbayashi','Usuri','Atsumu','Suna','Korai@back']),
 ('Original Giant — Crit and Speed','Tenma Udai',None,'front',
  ['Oikawa','Ushijima','Iwaizumi','Atsumu','Suga','Kageyama — Prodigious Talent']),
 ('Anchor-Powered Ace','Iwaizumi',None,'front',
  ['Iizuna','Kyotani','Kanbayashi','Nishinoya','Watari','Maruyama'])]


def presets_for(rules_version=RULES_VERSION):
    return LEGACY_PRESETS if rules_version==1 else V2_PRESETS if rules_version==2 else PRESETS


def preset_specs(index,rules_version=RULES_VERSION):
    label,name,title,lane,supports=presets_for(rules_version)[index]
    chars=[character(name,title)]+[character(n) for n in supports]
    if any(c.name==chars[0].name for c in chars[1:]):raise ValueError('A featured unit cannot support itself.')
    if len({c.name for c in chars})!=7:raise ValueError('Contest characters must have distinct player names.')
    lanes=[lane]
    for token,char in zip(supports,chars[1:]):
        lanes.append(token.split('@',1)[1] if '@' in token else lane if char.name in ('Kanbayashi','Tenma Udai') else 'front')
    return [(CardInstance(f'contest-{index}-{i}',f'CONTEST:{index}:{i}',c.card_id,c.id,0,0,label,i,
                          10 if i==0 else 0,datetime(2026,1,1,tzinfo=timezone.utc),True),c,
             lanes[i] if rules_version!=1 else lane if i==0 else 'front') for i,c in enumerate(chars)]


class ContestEngine(BattleEngine):
    def __init__(self,players,index,*,rules_version=RULES_VERSION,**kwargs):
        if len(players)!=6:raise ValueError('Clan Contest uses a complete 6v6 team.')
        self.rules_version=rules_version
        super().__init__(players,preset_specs(index,rules_version),mode='clancontest',**kwargs)
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
        if self.rules_version!=1 and kwargs.get('for_start_turn'):
            return super()._action_order(*args,**kwargs)
        return [u for u in super()._action_order(*args,**kwargs) if u.uid not in self.support_ids]

    def skill_enabled(self,unit,slot):
        return self.rules_version==1 or unit.uid not in self.support_ids or slot in support_slots(unit.character)

    def _turn_start_owner(self,unit,**kwargs):
        if self.rules_version!=1 and unit.uid in self.support_ids:
            self.begin_bonus_event()
            self.support_turn(unit)
            return
        return super()._turn_start_owner(unit,**kwargs)

    def talent_turn_start(self,unit):
        if self.rules_version!=1 and unit is self.feature and has(unit,'Kageyama','Prodigious Talent'):
            # The six inactive supports are not recipients of random ally grants.
            for stat in ('pow','def','rec','jmp'):unit.add_buff(stat,3,source=(unit.uid,'C'))
            unit.clear_debuffs()
            return
        return super().talent_turn_start(unit)

    def _combat_mods(self,ctx,u,foe,us,fs,initiated):
        mods=super()._combat_mods(ctx,u,foe,us,fs,initiated)
        if self.rules_version==1 or u is not self.feature:return mods
        for source in self.supports:
            if not self.source_allowed(ctx,source):continue
            # The mode's explicit positional exceptions apply to these C slots.
            stat='rec' if source.name=='Nishinoya' and u.lane!='back' else None
            if source.name=='Nozawa' and u.lane!='front':
                mods['rec']+=2
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((source.uid,'C'))
            if stat:
                mods[stat]+=4
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((source.uid,'C'))
        return mods

    def expansion_bond_penalty(self,ctx,unit):
        if self.rules_version!=1 and ctx and unit.side=='A':
            bond=any(a is not unit and a.character.school==unit.character.school for a in self.team(unit).units)
            return int(bond and any(a.name=='Nozawa' and self.source_allowed(ctx,a) for a in self.supports))
        return super().expansion_bond_penalty(ctx,unit)

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
        order=super().start_turn()
        if self.rules_version==1:
            for source in self.supports:self.support_turn(source)
            return self._action_order()
        return order

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
        elif name=='Washio':buffs={'def':3}
        elif name=='Komi':buffs={'rec':3}
        elif name=='Watari':buffs={'pow':7,'qik':7}
        elif name=='Shirabu':
            buffs={'pow':6,'qik':6}
            if f.character.school=='Shiratorizawa':f.clear_debuffs()
        elif name in ('Usuri','Futakuchi'):
            enemy=self.team_a
            if name=='Futakuchi':self.damage_team(enemy,20)
            for foe in enemy.units:
                for stat,mod in list(foe.visible_buffs.items()):
                    if mod.value>0:
                        foe.add_debuff(stat,-mod.value);foe.visible_buffs.pop(stat,None)
        elif name=='Kenma':
            max(self.team_a.units,key=lambda foe:foe.noncombat_stat('jmp')).add_debuff('jmp',-7)
            if f.lane=='front':buffs={'pow':4,'qik':4}
            if any(foe.statuses.get('Fatigue') and int(foe.statuses['Fatigue'].payload or 0)>=3 for foe in self.team_a.units):buffs['spd']=3
        elif name=='Yamagata' and self.team_hp_pct(f)<=50:
            f.barrier+=f.base['hp']//10;f.barrier_sources.add((source.uid,'C'))
        elif name=='Gao' and f.noncombat_stat('def')>f.noncombat_stat('rec'):f.cd=max(0,f.cd-1)
        elif name=='Maruyama' and f.character.position=='WS' and f.noncombat_stat('rec')>f.noncombat_stat('def'):f.cd=max(0,f.cd-1)
        elif has(source,'Kageyama','Prodigious Talent'):
            memory=self.expansion_memory[source.uid]
            if f.character.position in ('MB','WS','OP') and memory.get('contest_open_play_turn')!=self.turn-1:
                self.grant_open_play(f,source,'C');memory['contest_open_play_turn']=self.turn
            else:
                for stat in ('pow','def','rec','jmp'):source.add_buff(stat,3,source=(source.uid,'C'))
                source.clear_debuffs()
        for stat,value in buffs.items():f.add_buff(stat,value,source=(source.uid,'C'))

    def end_turn(self):
        super().end_turn()
        # Their own passives and Ultimates never become active combat effects.
        for source in self.supports:
            source.cd=source.max_cd
            if self.rules_version!=1:source.burden_stacks=0


class ActiveOpponent:
    """Foes see only the featured unit; its own ally skills see its supports."""
    def __init__(self,team,feature):
        object.__setattr__(self,'team',team)
        object.__setattr__(self,'units',[feature])

    def __getattr__(self,key):return getattr(self.team,key)
    def __setattr__(self,key,value):setattr(self.team,key,value)
