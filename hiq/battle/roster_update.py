"""The Great Wall and Energy Saver combat effects."""
from math import floor

WALL='Hirugami — The Great Wall'
KUNIMI='Kunimi — Energy Saver'

class RosterUpdateEffects:
    def wall_member(self,source,target,stats=None):
        if target.side!=source.side or target.lane!='front':return False
        values=stats or {s:target.noncombat_stat(s) for s in ('def','rec')}
        return target is source or target.character.position=='MB' or values['def']>values['rec']

    def wall_sources(self,ctx,target,slot,stats=None):
        return [u for u in self.team(target).units if u.char_id==WALL and self.skill_enabled(u,slot)
                and self.source_allowed(ctx,u) and self.wall_member(u,target,stats)]

    def roster_turn_start(self,u):
        memory=self.expansion_memory[u.uid]
        if u.char_id==KUNIMI and self.skill_enabled(u,'A'):
            memory['reserve']=min(3,memory.get('reserve',0)+(2 if self.turn==1 else 1))
            self.log_target_effect(u,'A',[u],f'grants Reserve ({memory["reserve"]}/3)')
        if u.char_id==WALL:
            if memory.get('wall_cleanse_turn')!=self.turn:
                memory['wall_cleanses']=0;memory['wall_cleanse_turn']=self.turn
            if not self.skill_enabled(u,'C'):return
            targets=self.effect_recipients(u,include_self=True)
            power=[a for a in targets if a is u or a.character.position=='MB']
            speed=[a for a in targets if self.wall_member(u,a)]
            for ally in power:
                for stat in ('pow','qik','def'):ally.add_buff(stat,2,source=(u.uid,'C'))
            for ally in speed:
                for stat in ('spd','jmp'):ally.add_buff(stat,2,source=(u.uid,'C'))
            if power:self.log_target_effect(u,'C',power,'grants Pow/Qik/Def+2 through next action')
            if speed:self.log_target_effect(u,'C',speed,'grants Spd/Jmp+2 through next action')

    def roster_end_turn(self):
        for unit in self.units:
            if unit.char_id!=WALL or not self.skill_enabled(unit,'C'):continue
            count=sum(a.character.position=='MB' for a in self.team(unit).units)
            size=len(self.team(unit).units);cap=6 if size<=3 else 12 if size<=6 else 18
            self.heal_team(unit,min(cap,count*2))

    def roster_combat_start(self,ctx):
        u=ctx.attacker
        if u.char_id!=KUNIMI or not self.skill_enabled(u,'B'):return
        memory=self.expansion_memory[u.uid];reserve=memory.get('reserve',0)
        if reserve and (reserve>=3 or self.team_hp_pct(u)<=50):
            memory['reserve']=0;ctx.temp_flags['kunimi_full_power']=reserve
            self.log_target_effect(u,'B',[u],f'Full Power consumes {reserve} Reserve; Pow/Spd/Jmp+{reserve*3} during combat')

    def roster_mods(self,ctx,u,stats,mods):
        if u.char_id==KUNIMI:
            if u is ctx.attacker and ctx.temp_flags.get('kunimi_full_power'):
                for stat in ('pow','spd','jmp'):mods[stat]+=3*ctx.temp_flags['kunimi_full_power']
            elif self.skill_enabled(u,'A'):
                reserve=self.expansion_memory[u.uid].get('reserve',0)
                mods['spd']-=reserve;mods['jmp']-=reserve
        sources=self.wall_sources(ctx,u,'A',stats)
        if sources:
            amount=max(0,floor(stats['def']*.1))
            mods['pow']+=amount;mods['qik']+=amount
            for source in sources:
                if source is not u:ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((source.uid,'A'))

    def roster_flat_reduction(self,ctx,target,stats,other_flat):
        if not self.wall_sources(ctx,target,'A',stats):return 0
        return min(8,max(0,floor(stats['def']*(.1 if other_flat>0 else .2))))

    def roster_after_combat(self,ctx):
        for unit in (ctx.attacker,ctx.defender):
            reflected=min(20,ctx.temp_flags.get('wall_prevented',{}).get(unit.uid,0))
            if reflected:self.postcombat_damage(ctx,self.enemy_team(unit),reflected,source=unit)
        for source in self.units:
            if source.char_id!=WALL or not self.skill_enabled(source,'B') or not self.source_allowed(ctx,source):continue
            by_id={unit.uid:unit for unit in self.units}
            pairs=[(by_id[target],by_id[foe]) for target,foe in ctx.temp_flags.get('wall_attacked',{}).get(source.uid,set())]
            for target,foe in pairs:
                foe.add_status('Heal Block',False,through_next_action=True,source=(source.uid,'B'))
                self.postcombat_damage(ctx,self.enemy_team(source),5,source=source)
                memory=self.expansion_memory[source.uid];limit=1 if len(self.team(source).units)==3 else 2
                if memory.get('wall_cleanses',0)<limit:
                    target.clear_debuffs();memory['wall_cleanses']=memory.get('wall_cleanses',0)+1
                    self.log_target_effect(source,'B',[target],'removes all debuffs after combat')
        unit=ctx.attacker
        if unit.char_id==KUNIMI and self.skill_enabled(unit,'C') and ctx.temp_flags.get('kunimi_full_power'):
            allies=[a for a in self.effect_recipients(unit) if a.character.school=='Aoba Johsai']
            if allies:
                for ally in allies:
                    for stat in ('pow','spd'):ally.add_buff(stat,4,source=(unit.uid,'C'))
                self.log_target_effect(unit,'C',allies,'grants Pow/Spd+4 through next action')
            else:unit.cd=max(0,unit.cd-1)
