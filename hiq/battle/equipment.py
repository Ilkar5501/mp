"""Equipment effects; unequipped battles preserve existing rules."""
from __future__ import annotations
from math import floor

BASE_MODS={
 'open_guard':{'def':7,'rec':7},
 'protein_packed_meal':{'pow':1,'qik':1},'carb_loaded_meal':{'def':2},
 'electrolyte_rich_meal':{'rec':2},'light_lean_meal':{'spd':1},'high_energy_meal':{'jmp':1},
 'pre_game_meditation':{'def':6,'rec':6,'pow':-3,'qik':-3},
 'burden_of_expectation':{'pow':5,'qik':5,'spd':5,'def':-5,'rec':-5},
 'worthless_pride':dict(pow=3,qik=3,**{'def':3},rec=3,spd=3,jmp=3)}

def equipment_key(card):return getattr(card,'equipment_key',None)

def add_base_equipment(stats,key):
    out=dict(stats)
    for stat,amount in BASE_MODS.get(key,{}).items():
        if stat not in ('pow','qik') or out.get(stat,0)!=0:out[stat]+=amount
    return out

class EquipmentEffects:
    def cardinal_foes(self,u):
        own=self.formation_units(self.team(u));foes=self.formation_units(self.enemy_team(u));index=own.index(u)
        if len(own)!=6:return foes[index:index+1]
        row,col=divmod(index,3)
        return [f for j,f in enumerate(foes) if abs(j//3-row)+abs(j%3-col)==1]

    def formation_distance(self,a,b):
        units=self.formation_units(self.team(a))
        if a is b:return 0
        if len(units)==3:return 1
        ia,ib=units.index(a),units.index(b)
        return abs(ia//3-ib//3)+abs(ia%3-ib%3)

    def postcombat_damage(self,ctx,team,amount):
        protected=any(u.side==team.side and 'Safe Landing' in u.statuses for u in (ctx.attacker,ctx.defender))
        if not protected:team.hp=max(0,team.hp-amount)
        else:self._log(f'Safe Landing prevents {amount} after-combat team damage.')
        return not protected

    def gear(self,u,key):return equipment_key(u.card)==key
    def skill_spd(self,u,stats=None):
        value=stats['spd'] if stats is not None else u.noncombat_stat('spd')
        return value+(7 if self.gear(u,'smelling_salts') else 0)

    def equipment_turn_start(self,u):
        key=equipment_key(u.card)
        if not key:return
        foes=self.enemy_team(u).units
        if key=='alternating_drills':
            stat='rec' if self.turn%2 else 'def'
            for ally in self.team(u).units:
                if ally.lane==u.lane:ally.add_buff(stat,6,through_next_action=False,turn=self.turn,source=(u.uid,'Item'))
        if key=='pressure_board':
            for foe in self.cardinal_foes(u):
                if foe.base['hp']<=u.base['hp']:
                    for stat,mod in list(foe.visible_buffs.items()):foe.add_debuff(stat,-mod.value)
                    foe.visible_buffs.clear()
        if key=='energy_drink' and self.turn==1:u.cd=max(0,u.cd-1)
        if key=='water_bottle' and self.turn%2==0:self.heal_team(u,20)
        if key=='onset_of_desperation' and self.team_hp_pct(u)<=50:u.add_buff('spd',7,through_next_action=False,turn=self.turn,source=(u.uid,'Item'))
        if key in ('freezing_stare','death_stare'):
            target=max(foes,key=lambda f:f.noncombat_stat('spd' if key=='freezing_stare' else 'jmp'))
            target.add_debuff('spd' if key=='freezing_stare' else 'jmp',-5)
        plans={'rock_solid_game_plan':('pow','qik'),'all_out_game_plan':('def',),'tricky_game_plan':('rec',),'tiring_game_plan':('spd',),'wicked_game_plan':('jmp',)}
        if key in plans:
            own=self.team(u).units
            if len(own)==6:
                own=[x for x in own if x.lane=='front']+[x for x in own if x.lane=='back']
                foes=[x for x in foes if x.lane=='front']+[x for x in foes if x.lane=='back']
                index=own.index(u);r,col=divmod(index,3)
                targets=[foes[j] for j in range(min(6,len(foes))) if abs(j//3-r)+abs(j%3-col)==1]
            else:
                if len(own)==3:
                    own=[x for x in own if x.lane=='front']+[x for x in own if x.lane=='back']
                    foes=[x for x in foes if x.lane=='front']+[x for x in foes if x.lane=='back']
                targets=foes[own.index(u):own.index(u)+1]
            for f in targets:
                if f.noncombat_stat('rec')<u.noncombat_stat('rec'):
                    for stat in plans[key]:f.add_debuff(stat,-3)

    def equipment_mods(self,ctx,u,foe,initiated,mods):
        key=equipment_key(u.card) or ''
        if key in ('loaded_approach','loaded_reception') and initiated==(key=='loaded_approach'):
            for stat in (('pow','qik') if key=='loaded_approach' else ('def','rec')):mods[stat]+=u.max_cd+2
        if key=='specialist_badge' and not any(a is not u and a.character.position==u.character.position for a in self.team(u).units):
            mods['pow']+=6;mods['qik']+=6
        if key.startswith('fresh_') and ctx.temp_flags.get('start_team_hp',{}).get(u.side,self.team(u).hp)>=ctx.temp_flags.get('start_team_hp',{}).get(foe.side,self.team(foe).hp)+12:
            for stat in {'fresh_legs':('pow','qik'),'fresh_guard':('def',),'fresh_focus':('rec',)}.get(key,()):mods[stat]+=5 if key=='fresh_legs' else 6
        if key in ('rally_momentum','rally_footwork','rally_elevation') and initiated:
            acted=sum(a is not u and a.acted_this_turn for a in self.team(u).units)
            stats={'rally_momentum':('pow','qik'),'rally_footwork':('spd',),'rally_elevation':('jmp',)}[key]
            for stat in stats:mods[stat]+=min(5 if key=='rally_elevation' else 6,(1 if key=='rally_elevation' else 2)+2*acted)
        # Apply the opposing wearer's penalties to this combatant.
        if self.gear(foe,'seam_finder'):mods['def']-=3;mods['rec']-=3
        if self.gear(foe,'pace_disruptor') and self.skill_spd(u)>=self.skill_spd(foe):mods['pow']-=4;mods['qik']-=4
        if self.gear(foe,'reading_glasses') and self.skill_spd(foe)>=self.skill_spd(u)-7:
            penalty=min(7,max(0,floor((foe.noncombat_stat('rec')-u.noncombat_stat('rec'))*.5)))
            for stat in ('pow','qik','def'):mods[stat]-=penalty
        if self.gear(foe,'bonus_scrutiny') and not ctx.temp_flags.get('counting_item_bonuses'):
            ctx.temp_flags['counting_item_bonuses']=True
            try:
                st=ctx.temp_flags.get('item_bonus_stats') or {u.uid:dict(u.base),foe.uid:dict(foe.base)}
                penalty=min(5,len(self.distinct_bonuses(ctx,u,st)))
            finally:ctx.temp_flags['counting_item_bonuses']=False
            for stat in ('pow','qik','spd','jmp'):mods[stat]-=penalty
        if key.startswith('recruit_s_') and not any(a is not u and a.character.school==u.character.school for a in self.team(u).units):
            bonuses={'recruit_s_compression_sleeve':{'pow':4,'qik':4},'recruit_s_finger_tape':{'def':3,'rec':3},'recruit_s_shoes':{'spd':4},'recruit_s_knee_pads':{'jmp':4}}
            for stat,n in bonuses.get(key,{}).items():mods[stat]+=n
        if not initiated and ((key=='athletic_tape' and u.lane==foe.lane=='front') or (key=='padded_arm_sleeves' and u.lane==foe.lane=='back')):
            mods['def']+=4;mods['rec']+=4

    def equipment_charge(self,ctx,u,foe,is_attacking,accel,guard,stats):
        if self.gear(u,'weighted_wristbands') and u is ctx.defender:accel=max(1,accel)
        if is_attacking and self.gear(u,'heavy_hands') and stats[u.uid][u.offense_key]>=stats[foe.uid][foe.offense_key]+5:accel=max(1,accel)
        if self.gear(foe,'vigilance') and self.start_hp_pct(ctx,foe)>=75:guard=max(1,guard)
        if self.gear(foe,'own_tempo'):accel=0
        if self.gear(u,'own_tempo'):guard=0
        return max(0,1+accel-guard)

    def equipment_adaptive(self,u):
        return self.gear(u,'synchronized_attack') and any(a is not u and a.character.school==u.character.school for a in self.team(u).units)

    def equipment_ultimate(self,u):
        if self.gear(u,'captain_s_armband'):
            for ally in self.team(u).units:ally.clear_debuffs()

    def equipment_hit_reduction(self,ctx,attacker,defender):
        if not (self.gear(attacker,'ten_thousand_reps') or self.gear(defender,'ten_thousand_reps')):return 0
        pair=(attacker.uid,defender.uid)
        count=ctx.temp_flags.get('gear_hit_count',0)+1 if ctx.temp_flags.get('gear_previous_hit')==pair else 1
        ctx.temp_flags['gear_previous_hit']=pair;ctx.temp_flags['gear_hit_count']=count
        return .30 if self.gear(defender,'ten_thousand_reps') and count>=2 and attacker.character.position!=defender.character.position else 0

    def equipment_after_combat(self,ctx):
        for u in (ctx.attacker,ctx.defender):
            if self.gear(u,'second_wind') and u.cd==u.max_cd:u.cd=max(0,u.cd-1)
            if self.gear(u,'recovery_snack') and u is ctx.attacker:self.heal_team(u,12)
            if self.gear(u,'disruptive_whistle'):
                target=ctx.defender if u is ctx.attacker else ctx.attacker
                for foe in self.enemy_team(u).units:
                    if foe.lane==target.lane:
                        for stat in ('pow','qik'):foe.add_debuff(stat,-4)
            if self.gear(u,'manager_s_good_luck_charm'):self.heal_team(u,10)
            if self.gear(u,'worthless_pride') and not(ctx.temp_flags.get('relay_teradomari') and u is ctx.attacker):
                team=self.team(u)
                if self.postcombat_damage(ctx,team,15):self._log(f'{self._unit_label(u)}: Worthless Pride deals 15 team damage.')

    def equipment_winner(self,winner):
        if winner!='draw':return winner
        a=any(self.gear(u,'matchbreaker') for u in self.team_a.units)
        b=any(self.gear(u,'matchbreaker') for u in self.team_b.units)
        if a!=b:
            self._log('Matchbreaker turns the draw into a win.')
            return 'A' if a else 'B'
        return winner
