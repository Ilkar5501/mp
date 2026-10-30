"""v10 roster mechanics. Existing-roster battles keep their prior lifecycle."""
from collections import defaultdict
from math import ceil, floor

LIVE_STATS = ('pow','qik','def','rec','spd','jmp')


class ExpansionEffects:
    def expansion_init(self):
        self.expansion_memory = defaultdict(dict)
        self.relay_used = set()
        self.passive_save_used = defaultdict(int)
        self.hime_refund_used = set()

    def formation_units(self, team):
        return [a for a in team.units if a.lane=='front']+[a for a in team.units if a.lane=='back']

    def adjacent_allies(self, unit):
        units = self.formation_units(self.team(unit))
        if len(units)==3: return [a for a in units if a is not unit]
        if len(units)!=6: return [a for a in units if a is not unit]
        index=units.index(unit); row,col=divmod(index,3)
        return [a for i,a in enumerate(units) if abs(i//3-row)+abs(i%3-col)==1]

    def opposite_ally(self, unit):
        units=self.formation_units(self.team(unit)); i=units.index(unit)
        if len(units)==3: return units[(2,0,1)[i]]
        if len(units)==6: return units[5-i]
        return None

    def transfer_action(self, unit):
        if unit.name not in ('Echigo','Watari') or unit.cd or 'UltimateDisabled' in unit.statuses:
            return False
        key=(self.turn,unit.uid)
        if key in self.relay_used: return False
        if unit.name=='Echigo': recipient=self.opposite_ally(unit)
        else:
            setters=[a for a in self.team(unit).units if a is not unit and a.character.position=='S']
            recipient=self.rng.choice(setters) if setters else None
        if recipient is None or recipient is unit: return False
        if not self.trigger_ultimate(unit,None,'relay'): return False
        self.relay_used.add(key)
        # Transfer Ultimates are real team Ultimate triggers, just like combat ones.
        if any(a.name=='Daichi' for a in self.team(unit).units):
            for ally in self.team(unit).units: ally.clear_debuffs()
        self._log(f'{self._unit_label(unit)} transfers an action to {self._unit_label(recipient)}.')
        if unit.name=='Watari': self._turn_start_owner(recipient,forced=True)
        if unit.name=='Echigo':recipient.add_status('Safe Landing',True,source=(unit.uid,'U'))
        self.perform_action(recipient,extra=True,relay_source=unit.uid,
                            relay_teradomari=unit.name=='Echigo' and recipient.name=='Teradomari')
        unit.actions_taken+=1; unit.acted_this_turn=True; unit.action_in_progress=False
        unit.expire_after_action()
        return True

    def expansion_turn_start(self, unit):
        team=self.team(unit); mem=self.expansion_memory[unit.uid]
        if unit.name=='Akifumi':unit.cd=max(0,unit.cd-1)
        if unit.name=='Shibayama':
            value=min(4,sum(a.noncombat_stat('rec')>a.noncombat_stat('def') for a in team.units))
            for ally in team.units:
                if ally is unit or ally.character.school=='Nekoma':
                    for stat in LIVE_STATS: ally.add_buff(stat,value,source=(unit.uid,'C'))
        if unit.name=='Suwa':
            for ally in team.units:
                if (ally is unit or ally.lane=='front') and 'Bunch Block' not in ally.statuses:
                    ally.add_status('Bunch Block',True,source=(unit.uid,'C'))
        if unit.name=='Echigo':
            if self.turn==1:
                if not mem.get('opening_cd'):
                    unit.cd=max(0,unit.cd-2); mem['opening_cd']=True
            else:
                unit.cd=max(0,unit.cd-sum(a.character.school=='Tsubakihara' for a in team.units))
        if unit.name=='Watari':
            allies=[a for a in team.units if a is not unit]
            if allies:
                ally=self.rng.choice(allies)
                for stat in ('pow','qik'): ally.add_buff(stat,7,source=(unit.uid,'C'))
                if ally.character.position=='S': unit.add_buff('pow',4,source=(unit.uid,'C'))
        if unit.name=='Maruyama':
            for ally in team.units:
                if ally is unit or (ally.character.position=='WS' and ally.noncombat_stat('rec')>ally.noncombat_stat('def')):
                    ally.cd=max(0,ally.cd-1)
        if unit.name=='Atema':
            middles=[a for a in self.adjacent_allies(unit) if a.character.position=='MB']
            if middles:
                for ally in middles: ally.add_buff(self.rng.choice(('pow','def')),6,source=(unit.uid,'C'))
            else:
                for stat in ('pow','def'): unit.add_buff(stat,6,source=(unit.uid,'C'))
        if unit.name=='Himekawa':
            candidates=[a for a in team.units if not(a.name=='Teradomari' and mem.get('previous_tera'))]
            mem['previous_tera']=False
            if candidates:
                negative=self.rng.choice(candidates); negative.add_debuff('spd',-6)
                others=[a for a in candidates if a is not negative]
                positive=self.rng.choice(others) if others else None
                if positive: positive.add_buff('spd',6,source=(unit.uid,'C'))
                mem['previous_tera']=negative.name=='Teradomari' or (positive is not None and positive.name=='Teradomari')
                if negative.name=='Teradomari': mem['head_assist_turn']=self.turn+1

    def passive_save(self, attacker, intended):
        if attacker.name=='Fukunaga' or intended.name=='Fukunaga': return intended,False
        eligible=[]
        for unit in self.team(intended).units:
            if unit is intended or self.passive_save_used[(self.turn,unit.uid)]>=2 or not self.attack_target_allowed(unit): continue
            chance=0
            if unit.name=='Matsukawa' and unit.lane=='front' and attacker.offense_key=='qik': chance=.5
            if (unit.name=='Maruyama' and unit.lane=='back' and attacker.offense_key=='qik') or (unit.name=='Atema' and unit.lane=='front' and attacker.offense_key=='pow'):
                chance=.25+.05*sum(a is not unit and a.character.school=='Tsubakihara' for a in self.team(unit).units)
            if chance: eligible.append((unit,min(1.,chance)))
        for unit,chance in sorted(eligible,key=lambda pair:(-pair[0].noncombat_stat('spd'),pair[0].uid)):
            if self.rng.random()<chance:
                self.passive_save_used[(self.turn,unit.uid)]+=1
                self._log(f'{self._unit_label(unit)} enters combat in {intended.name}’s place.')
                return unit,True
        return intended,False

    def expansion_combat_start(self, ctx):
        ctx.temp_flags['v10_damage_before']={u.uid:u.damage_taken for u in (ctx.attacker,ctx.defender)}
        unit=ctx.attacker
        if unit.name=='Himekawa':
            mem=self.expansion_memory[unit.uid]; count=4+int(unit.lane=='back')
            reroll=bool(mem.pop('retry_tails',False)); heads=0
            for _ in range(count):
                result=self.rng.random()<.5
                if not result and reroll: result=self.rng.random()<.5
                heads+=int(result)
            if mem.get('head_assist_turn')==self.turn and heads<count: heads+=1
            mem['retry_tails']=heads<2
            ctx.temp_flags['hime_coins']=(heads,count)
            if heads==5: unit.add_status('Untargetable',True,through_next_action=True,source=(unit.uid,'B'))
            self._log(f'{self._unit_label(unit)} flips {heads} heads and {count-heads} tails.')

    def expansion_bond_penalty(self, ctx, unit):
        if not ctx: return 0
        bond=any(a is not unit and a.character.school==unit.character.school for a in self.team(unit).units)
        return int(bond and any(a.name=='Nozawa' and self.source_allowed(ctx,a) for a in self.enemy_team(unit).units))

    def expansion_base_stats(self, unit, foe, ctx, stats):
        if foe is None: return
        penalty=self.expansion_bond_penalty(ctx,unit)
        if penalty:
            for stat in LIVE_STATS: stats[stat]-=penalty

    def expansion_mods(self, ctx, unit, foe, us, fs, initiated, mods):
        name=unit.name; team=self.team(unit); mem=self.expansion_memory[unit.uid]
        if name=='Akifumi':
            for stat in ('qik','rec'):mods[stat]+=4+2*abs(unit.visible_penalty(stat))
        if foe.name=='Akifumi' and (ctx.attacker is foe or self.start_enemy_hp_pct(ctx,foe)>=50):
            mods['spd']-=6;mods['rec']-=6
        if name=='Shibayama':
            if initiated: mods['qik']+=5
            if sum(a is not unit and a.character.school=='Nekoma' for a in team.units)>=3: mods['spd']+=3
            stack=unit.statuses.get('Learning'); value=int(stack.payload or 0) if stack else 0
            mods['def']+=value; mods['rec']+=value
        if name=='Suwa' and initiated: mods['qik']+=4
        if name=='Ezota' and initiated and any(a.offense_key=='qik' for a in self.enemy_team(unit).units):
            mods['pow']+=6; mods['rec']+=4
        if name=='Echigo':
            if initiated: mods['qik']+=6
            adjacent=self.adjacent_allies(unit)
            if any(a.character.position=='MB' for a in adjacent): mods['def']+=4
            if any(a.character.position=='Li' for a in adjacent): mods['rec']+=4
        if name=='Matsukawa':
            st=unit.statuses.get('Terror'); value=int(st.payload or 0) if st else 0
            for stat in ('qik','spd','jmp'): mods[stat]+=value
        if name=='Watari' and initiated:
            mods['pow']+=min(8,sum(a.actions_taken for a in team.units if a is not unit and a.character.position=='S'))
        if name=='Maruyama':
            if initiated: mods['pow']+=4
            else:
                rec=min(4,sum(a is not unit and a.noncombat_stat('rec')>a.noncombat_stat('def') for a in team.units))
                defense=min(4,sum(a is not unit and a.noncombat_stat('def')>a.noncombat_stat('rec') for a in team.units))
                mods['def']+=rec; mods['jmp']+=rec; mods['rec']+=defense; mods['spd']+=defense
        if name=='Himekawa' and initiated:
            heads,count=ctx.temp_flags.get('hime_coins',(0,0)); value=5*(2*heads-count)
            for stat in ('pow','def','rec','spd','jmp'): mods[stat]+=value
        grit=unit.statuses.get('Grit & Grind')
        if grit and unit.lane=='front':
            for stat in ('def','jmp'): mods[stat]+=2*int(grit.payload or 0)
        if ctx.temp_flags.get('relay_teradomari') and unit is ctx.attacker:
            for stat in ('pow','spd','jmp'): mods[stat]+=4
            ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(unit.uid,set()).add((ctx.temp_flags['relay_source'],'U'))
        for source in team.units:
            if source.name=='Akifumi' and source is not unit and initiated and self.source_allowed(ctx,source) and self.expansion_memory[unit.uid].get('initiations',{}).get(self.turn,0)>=2:
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(unit.uid,set()).add((source.uid,'C'))
            if source.name=='Nozawa' and self.source_allowed(ctx,source):
                if unit is source or unit.lane=='front': mods['rec']+=2
                enemy_bond=any(a is not foe and a.character.school==foe.character.school for a in self.team(foe).units)
                if source is not unit and (unit.lane=='front' or enemy_bond):
                    ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(unit.uid,set()).add((source.uid,'C'))
            if source.name=='Echigo' and source is not unit and self.source_allowed(ctx,source) and self.team_hp_pct(unit)<=50:
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(unit.uid,set()).add((source.uid,'C'))

    def echigo_followup_support(self, ctx, unit):
        return self.team_hp_pct(unit)<=50 and any(a.name=='Echigo' and self.source_allowed(ctx,a) for a in self.team(unit).units)

    def expansion_charge(self, ctx, unit, foe, is_attacking, accel, guard):
        if 'Bunch Block' in unit.statuses and ctx.defender is unit: accel=max(1,accel)
        if ctx.temp_flags.get('relay_teradomari') and unit is ctx.attacker and is_attacking: accel=max(1,accel)
        if foe.name=='Watari' and foe is ctx.defender: guard=max(1,guard)
        return accel,guard

    def expansion_flat_reduction(self, ctx, unit, foe, stats):
        value=0; us=stats[unit.uid]; fs=stats[foe.uid]
        if unit.name=='Suwa':
            qualifying=sum((us['def'] if a is unit else a.noncombat_stat('def'))>=33 for a in self.team(unit).units)
            if qualifying>=ceil(2*len(self.team(unit).units)/3): value+=max(0,floor(us['def']*.2))
        if unit.name=='Ezota' and unit.lane=='back' and foe.offense_key=='pow' and us['rec']>fs['rec']:
            value+=max(0,floor(us['rec']*.15))
        return value

    def expansion_damage_bonus(self, ctx, unit, stats):
        bonus=0
        if unit.name=='Matsukawa' and ((unit is ctx.defender and ctx.save_triggered) or (unit is ctx.attacker and ctx.temp_flags.get('relay_source'))):
            mem=self.expansion_memory[unit.uid]; st=unit.statuses.get('Terror')
            bonus+=int(mem.get('terror_damage',0))+(int(st.payload or 0) if st else 0)
            ctx.temp_flags['consume_terror']=unit.uid
        return bonus

    def expansion_hit_result(self, ctx, attacker, defender, damage, offensive_crit, off_trigger, def_trigger):
        if ctx.temp_flags.pop('consume_terror',None)==attacker.uid:
            attacker.statuses.pop('Terror',None); self.expansion_memory[attacker.uid]['terror_damage']=0
        if off_trigger and attacker.name=='Suwa': self.heal_team(attacker,floor(damage*.3))
        if def_trigger and defender.name=='Ezota':
            self.heal_team(defender,6)
            for foe in self.enemy_team(defender).units:
                foe.add_debuff('qik',-4); foe.add_debuff('spd',-4)
        if offensive_crit and defender.name=='Matsukawa':
            st=defender.statuses.get('Terror'); count=int(st.payload or 0) if st else 0
            defender.add_status('Terror',True,min(5,count+1),source=(defender.uid,'B'))
            mem=self.expansion_memory[defender.uid]; mem['terror_damage']=mem.get('terror_damage',0)+floor(damage*.1)
        if offensive_crit and defender.name=='Atema':
            st=defender.statuses.get('Grit & Grind'); count=int(st.payload or 0) if st else 0
            if count>=3:
                ctx.temp_flags['atema_overflow_heal']=ctx.temp_flags.get('atema_overflow_heal',0)+3
            else:
                for ally in self.team(defender).units:
                    if ally is defender or ally.lane=='front':
                        old=ally.statuses.get('Grit & Grind'); n=int(old.payload or 0) if old else 0
                        ally.add_status('Grit & Grind',True,min(3,n+1),source=(defender.uid,'B'))
                if count+1==3:
                    for ally in self.team(defender).units:
                        if ally is not defender: ally.statuses.pop('Grit & Grind',None)
                    self.heal_team(defender,9)

    def expansion_after_combat(self, ctx):
        if ctx.temp_flags.get('v10_after_resolved'): return
        ctx.temp_flags['v10_after_resolved']=True
        for unit in (ctx.attacker,ctx.defender):
            if unit.name=='Shibayama':
                before=ctx.temp_flags.get('v10_damage_before',{}).get(unit.uid,unit.damage_taken)
                if unit.damage_taken-before>=unit.base['hp']*.25:
                    st=unit.statuses.get('Learning'); n=int(st.payload or 0) if st else 0
                    unit.add_status('Learning',True,n+1,source=(unit.uid,'B'))
            if unit.name=='Ezota' and unit.ult_triggered_this_combat:
                foe=ctx.defender if unit is ctx.attacker else ctx.attacker
                for stat in ('pow','qik'): foe.add_debuff(stat,-6)
                for stat in ('pow','rec'): unit.add_buff(stat,6,source=(unit.uid,'U'))
            if unit.name=='Matsukawa':
                for foe in self.enemy_team(unit).units:
                    foe.add_debuff('pow',-2); foe.add_debuff('qik',-2)
            if unit.name=='Atema' and ctx.temp_flags.get('atema_overflow_heal'):
                self.heal_team(unit,ctx.temp_flags['atema_overflow_heal'])
        unit=ctx.attacker
        if unit.name=='Himekawa':
            heads,_=ctx.temp_flags.get('hime_coins',(0,0)); key=(self.turn,unit.uid)
            if heads>=3 and key not in self.hime_refund_used:
                self.hime_refund_used.add(key); unit.cd=max(0,unit.cd-1)
