"""Sakusa's independent preliminary/hit crit rolls and named-buff Dispel."""

FOCUS='Perfectionism'
RESOURCE_STATUSES={'Learning','Terror','Burden','AkaashiCharge'}

class SakusaEffects:
    def sakusa_stacks(self,u):
        status=u.statuses.get(FOCUS)
        return int(status.payload or 0) if status else 0

    def critical_bonus(self,u):
        return 2*self.sakusa_stacks(u) if u.name=='Sakusa' else 0

    def named_buff_active(self,ctx,u,name):
        return (u.uid,name) not in ctx.temp_flags.get('dispelled_named',set())

    def sakusa_after_combat(self,ctx):
        if ctx.temp_flags.get('perfectionism_gained'):return
        ctx.temp_flags['perfectionism_gained']=True
        for u in (ctx.attacker,ctx.defender):
            if u.name=='Sakusa':
                n=min(5,self.sakusa_stacks(u)+1)
                u.add_status(FOCUS,True,payload=n,source=(u.uid,'A'))
                self._log(f'{self._unit_label(u)}: Perfectionism {n}/5.')

    def sakusa_precheck(self,ctx):
        u,foe=ctx.attacker,ctx.defender
        if u.name!='Sakusa' or self.crits_disabled(ctx):return
        stats=self.combat_stats(ctx)
        diff=stats[u.uid]['jmp']-stats[foe.uid]['jmp']
        chance=min(100,max(0,diff)*5+self.critical_bonus(u))
        enabled=diff>0 and self.rng.random()<chance/100
        ctx.temp_flags['sakusa_twice:'+u.uid]=enabled

    def dispellable_buffs(self,ctx,u):
        buffs=[name for name,status in u.statuses.items() if status.positive and name not in RESOURCE_STATUSES]
        if self.named_buff_active(ctx,u,'Excellence') and any(a.name=='Atsumu' and self.skill_enabled(a,'B') and self.source_allowed(ctx,a) for a in self.team(u).units):
            buffs.append('Excellence')
        return list(dict.fromkeys(buffs))

    def sakusa_hit_result(self,ctx,attacker,defender,critical,*,followup=False):
        if attacker.name!='Sakusa':return
        key='sakusa_followup:'+attacker.uid
        # Each initial critical hit gets one roll until a follow-up is earned.
        # Follow-up hits cannot generate another follow-up.
        if attacker is ctx.attacker and critical and not followup and not ctx.temp_flags.get(key):
            ctx.temp_flags[key]=self.rng.random()<.45
        choices=self.dispellable_buffs(ctx,defender)
        if not choices or self.rng.random()>=.5:return
        name=self.rng.choice(choices)
        if name=='Excellence':ctx.temp_flags.setdefault('dispelled_named',set()).add((defender.uid,name))
        elif name==FOCUS and self.sakusa_stacks(defender)>1:defender.statuses[name].payload-=1
        else:defender.statuses.pop(name,None)
        self._log(f'{self._unit_label(attacker)} dispels {name} from {self._unit_label(defender)}.')
