"""Bonyari's capped Keys, bodyguard substitution and shared locket."""
SCHOOL='Bonyari High'
CHITOGE='Chitoge Kirisaki — Fake Sweetheart'
TSUGUMI='Seishiro Tsugumi — Black Tiger'
KOSAKI='Kosaki Onodera — Sweet Promise'
MARIKA='Marika Tachibana — Determined Fiancée'
KEY='Key'


class NisekyuEffects:
    def has_key(self,u):return KEY in u.statuses

    def grant_key(self,source,target,slot):
        if self.has_key(target) or sum(self.has_key(a) for a in self.team(target).units)>=3:return False
        target.add_status(KEY,True,source=(source.uid,slot))
        self.log_target_effect(source,slot,[target],'grants Key')
        return True

    def nisekyu_turn_start(self,u):
        if u.char_id!=KOSAKI or not self.skill_enabled(u,'B'):return
        targets=self.effect_recipients(u,include_self=True)
        bonyari=[a for a in self.team(u).units if a.character.school==SCHOOL]
        memory=self.expansion_memory[u.uid]
        if self.turn==1 and not memory.get('promise_opened'):
            memory['promise_opened']=True
            if len(bonyari)>=3:
                opening=[a for a in targets if a.character.school==SCHOOL and (len(bonyari)<4 or a is not u)]
                for ally in sorted(opening,key=lambda a:a.char_id!=CHITOGE):self.grant_key(u,ally,'B')
        allies=[a for a in targets if a is not u and not self.has_key(a)]
        if allies:
            target=max(allies,key=lambda a:(a.char_id==CHITOGE,a.noncombat_stat(a.offense_key)))
            self.grant_key(u,target,'B')

    def nisekyu_end_turn(self):
        for u in self.units:
            if u.char_id==MARIKA and self.skill_enabled(u,'B') and self.team_hp_pct(u)<=75:
                targets=[a for a in self.effect_recipients(u) if not self.has_key(a)]
                if targets and sum(self.has_key(a) for a in self.team(u).units)<3:self.grant_key(u,self.rng.choice(targets),'B')

    def nisekyu_action_start(self,actor):
        if actor.character.school!=SCHOOL:return
        for wearer in self.team(actor).units:
            if self.gear(wearer,'raku_s_locket') and self.rng.random()<2/3:
                wearer.clear_debuffs()
                self.log_target_effect(wearer,'Item',[wearer],'removes all debuffs',phase='start of action',name="Raku’s Locket")

    def nisekyu_save(self,attacker,intended):
        if attacker.name=='Fukunaga' or intended.name=='Fukunaga':return intended,False
        candidates=[u for u in self.team(intended).units if u is not intended and u.char_id==TSUGUMI
                    and u.lane=='front' and self.skill_enabled(u,'A') and self.attack_target_allowed(u)
                    and self.expansion_memory[u.uid].get('bodyguard_turn')!=self.turn]
        for u in sorted(candidates,key=lambda a:-a.noncombat_stat('spd')):
            if intended.char_id==CHITOGE or (self.has_key(intended) and self.rng.random()<.5):
                self.expansion_memory[u.uid]['bodyguard_turn']=self.turn
                self._log(f'{self._unit_label(u)} — Bodyguard Instinct → {self._unit_label(intended)}: enters combat in ally’s place.')
                return u,True
        return intended,False

    def nisekyu_saved(self,ctx,u):
        return ctx.save_triggered and ctx.defender is u and ctx.original_target is not u

    def nisekyu_combat_start(self,ctx):
        ctx.temp_flags['chitoge_promise']=self.nisekyu_promise(ctx,ctx.attacker)
        target=ctx.original_target
        if ctx.save_triggered and target.char_id==CHITOGE and self.skill_enabled(target,'C') and self.source_allowed(ctx,target):
            self.grant_key(target,target,'C')

    def nisekyu_promise(self,ctx,u):
        return u is ctx.attacker and bool(ctx.temp_flags.get('chitoge_promise',u.char_id==CHITOGE and self.skill_enabled(u,'B') and self.has_key(u)))

    def nisekyu_counter(self,ctx,u):
        return (u.char_id==MARIKA and self.skill_enabled(u,'A')) or (u.char_id==TSUGUMI and self.skill_enabled(u,'B') and self.nisekyu_saved(ctx,u))

    def nisekyu_mods(self,ctx,u,mods):
        if self.has_key(u):
            mods['pow']+=3;mods['qik']+=3
        if u.char_id==CHITOGE and self.skill_enabled(u,'A'):
            mods['pow']+=5 if u is ctx.attacker else 4
            mods['spd' if u is ctx.attacker else 'def']+=5 if u is ctx.attacker else 4
        if u.char_id==TSUGUMI and self.skill_enabled(u,'B') and self.nisekyu_saved(ctx,u):
            for stat in ('qik','def','spd'):mods[stat]+=5
        if u.char_id==KOSAKI and self.skill_enabled(u,'A') and u is ctx.attacker:
            mods['qik']+=4;mods['rec']+=4
        if u.char_id==MARIKA and self.skill_enabled(u,'A'):
            bonus=min(6,2*sum(a is not u and self.has_key(a) for a in self.team(u).units))
            mods['def']+=bonus;mods['rec']+=bonus
        chitoge=[a for a in self.team(u).units if a is not u and a.char_id==CHITOGE and self.skill_enabled(a,'C') and self.source_allowed(ctx,a)]
        if chitoge and self.has_key(u):mods['pow']+=3
        marika=[a for a in self.team(u).units if a.char_id==MARIKA and self.skill_enabled(a,'C') and self.source_allowed(ctx,a) and (a is u or self.has_key(u))]
        if marika:mods['rec']+=3
        for source,slot in [(a,'C') for a in chitoge if self.has_key(u)]+[(a,'C') for a in marika if a is not u]:
            ctx.temp_flags.setdefault('ally_stat_impacts',{}).setdefault(u.uid,set()).add((source.uid,slot))

    def nisekyu_precombat_damage(self,ctx,target,amount):
        if any(a.char_id==MARIKA and self.skill_enabled(a,'C') and self.source_allowed(ctx,a) for a in self.team(target).units):return amount//2
        return amount

    def nisekyu_after_combat(self,ctx):
        if ctx.temp_flags.get('nisekyu_after_done'):return
        ctx.temp_flags['nisekyu_after_done']=True
        actor=ctx.attacker
        if self.has_key(actor):
            actor.statuses.pop(KEY,None)
            self._log(f'{self._unit_label(actor)} consumes Key after initiating combat.')
        for u in (ctx.attacker,ctx.defender):
            if u.char_id==TSUGUMI and self.skill_enabled(u,'C') and self.nisekyu_saved(ctx,u):
                target=ctx.original_target
                for stat in ('pow','qik','spd'):target.add_buff(stat,4,source=(u.uid,'C'))
                self.log_target_effect(u,'C',[target],'grants Pow/Qik/Spd+4 through next action',phase='after combat')
            if u.char_id==KOSAKI and self.skill_enabled(u,'C'):
                self.heal_team(u,8)
                previous_source=self.effect_source;self.effect_source=u
                try:u.add_debuff('spd',-3)
                finally:self.effect_source=previous_source
                self.log_target_effect(u,'C',[u],'Spd−3 through next action',phase='after combat')
            if u.char_id==MARIKA and u.ult_triggered_this_combat:self.grant_key(u,u,'U')
