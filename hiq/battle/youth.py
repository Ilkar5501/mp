"""Variant-specific effects for the five v12 characters."""
STATS=('pow','qik','def','rec','spd','jmp')
YOUTH_TITLES={'Old Friend','Furious Upstart','Young Emperor','King of the Lost Court','Kings of the Playground'}


class YouthEffects:
    def youth_memory(self,u):
        if not hasattr(self,'youth'):self.youth={}
        return self.youth.setdefault(u.uid,dict(overwork=0,demand=0))

    def youth_turn(self,u):
        if u.title!='Old Friend' or not self.skill_enabled(u,'B'):return
        for a in self.team(u).units:
            status=a.statuses.get('Fun Memory')
            if status and status.payload==u.uid:a.statuses.pop('Fun Memory',None)
            for stat,mod in list(a.visible_buffs.items()):
                if (u.uid,'B') in mod.sources:
                    mod.sources.discard((u.uid,'B'))
                    if not mod.sources:a.visible_buffs.pop(stat,None)
        daichis=[a for a in self.team(u).units if a.name=='Daichi']
        if daichis:
            for a in daichis:
                a.add_status('Fun Memory',True,payload=u.uid,source=(u.uid,'B'))
                for stat in ('pow','def','rec','spd','jmp'):a.add_buff(stat,1,through_next_action=False,source=(u.uid,'B'))
        elif self.turn%2==0:self.heal_team(u,20)

    def youth_mods(self,ctx,u,foe,us,fs,initiated,m):
        if u.title=='Old Friend' and self.skill_enabled(u,'A'):
            m['pow']+=3;m['def']+=3
        if u.title=='Furious Upstart' and initiated and self.skill_enabled(u,'A'):
            m['pow']+=4;m['spd']+=4
        if u.title=='Young Emperor':
            if self.skill_enabled(u,'A'):
                n=self.youth_memory(u)['overwork'];m['pow']+=2*n;m['spd']+=2*n
            if self.skill_enabled(u,'B'):
                if any(a is not u and (a.base['spd']>u.base['spd'] or a.base['jmp']>u.base['jmp']) for a in self.units):m['pow']+=4;m['jmp']+=4
                if foe.name=='Ushijima':m['def']+=5;m['rec']+=5
        if foe.title=='Young Emperor' and self.skill_enabled(foe,'A') and self.youth_memory(foe)['overwork']>=4:
            m['def']-=4;m['spd']-=4
        if u.title=='King of the Lost Court':
            if self.skill_enabled(u,'A'):
                for stat in ('pow','def','rec','spd','jmp'):m[stat]+=self.youth_memory(u)['demand']
            if not initiated and self.skill_enabled(u,'B'):m['def']+=2;m['rec']+=2
        if 'Demand' in u.statuses:
            for stat in ('pow','def','rec','spd','jmp'):m[stat]+=2
        if foe.title=='Kings of the Playground' and not (ctx.attacker is foe) and self.skill_enabled(foe,'A'):
            m['pow']-=4;m['qik']-=4
        for ally in self.team(u).units:
            if ally is u or not self.source_allowed(ctx,ally):continue
            if ally.title=='Old Friend' and self.skill_enabled(ally,'C') and u.character.position in ('OP','WS'):
                for stat in ('pow','qik','def','rec'):m[stat]+=1
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((ally.uid,'C'))
            if ally.title=='Kings of the Playground' and self.skill_enabled(ally,'B') and 'Fountain of Youth' in u.statuses:
                m['spd']+=4;m['jmp']+=4
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((ally.uid,'B'))
            if ally.title=='Young Emperor' and self.skill_enabled(ally,'A') and any(a.name=='Iwaizumi' for a in self.team(u).units):
                ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((ally.uid,'A'))

    def youth_crit_bonus(self,u):
        ctx=getattr(self,'effect_context',None)
        return 15 if any(a.title=='Young Emperor' and self.skill_enabled(a,'A') and (not ctx or a is u or self.source_allowed(ctx,a)) and any(b.name=='Iwaizumi' for b in self.team(u).units) for a in self.team(u).units) else 0

    def youth_start_combat(self,ctx):
        ctx.temp_flags['youth_before']={u.uid:(u.damage_dealt,u.damage_taken,'Demand' in u.statuses,'Fountain of Youth' in u.statuses) for u in (ctx.attacker,ctx.defender)}

    def youth_after_combat(self,ctx):
        if ctx.temp_flags.get('youth_finished'):return
        ctx.temp_flags['youth_finished']=True
        for u in (ctx.attacker,ctx.defender):
            before=ctx.temp_flags.get('youth_before',{}).get(u.uid,(u.damage_dealt,u.damage_taken,False,False))
            if before[2]:
                self.damage_team(self.team(u),6,source=u)
                for ally in self.team(u).units:
                    if ally.title=='King of the Lost Court':self.youth_memory(ally)['demand']=min(10,self.youth_memory(ally)['demand']+1)
            if before[3]:
                if u.ult_triggered_this_combat:self.heal_team(u,u.max_cd*5)
                else:
                    for stat in STATS:u.add_buff(stat,3)
            if u.title=='Young Emperor':
                if self.skill_enabled(u,'A') and u.damage_taken-before[1]>u.damage_dealt-before[0]:self.youth_memory(u)['overwork']=min(4,self.youth_memory(u)['overwork']+1)
                if u.cd==0 and self.trigger_ultimate(u,ctx,'postcombat'):
                    for ally in self.team(u).units:
                        if ally is not u:
                            for stat in ('pow','qik','jmp'):ally.add_buff(stat,5,source=(u.uid,'U'))
            if u.title=='King of the Lost Court' and self.skill_enabled(u,'A'):
                allies=[a for a in self.team(u).units if a is not u]
                if allies:self.rng.choice(allies).add_status('Demand',True,through_next_action=True,source=(u.uid,'A'))
            if u.title=='Kings of the Playground':
                allies=[a for a in self.team(u).units if a is not u]
                if u.ult_triggered_this_combat:
                    for ally in allies:ally.add_status('Fountain of Youth',True,through_next_action=True,source=(u.uid,'U'))
                if u is ctx.attacker and allies and self.skill_enabled(u,'B'):
                    self.rng.choice(allies).add_status('Fountain of Youth',True,through_next_action=True,source=(u.uid,'B'))

    def youth_damage_bonus(self,ctx,u,stats):
        if u is not ctx.attacker:return 0
        if not any((a is u or self.source_allowed(ctx,a)) and a.title=='Kings of the Playground' and self.skill_enabled(a,'B') for a in self.team(u).units):return 0
        return min(10,2*len(self.ally_skill_impacts(ctx,u,stats)))
