"""Semi's pre-combat serve and next-combat ally bonuses."""
from .types import TimedMod


class SemiEffects:
    def semi_turn_start(self,unit):
        if unit.name!='Semi' or not self.skill_enabled(unit,'B'):return
        count=min(3,sum(a.character.position=='S' for a in self.team(unit).units))
        unit.cd=max(0,unit.cd-count)
        self.log_target_effect(unit,'B',[unit],f'remaining Ultimate CD −{count} (minimum 0)')

    def semi_precombat(self,ctx):
        unit=ctx.attacker
        if unit.name!='Semi' or ctx.temp_flags.get('semi_precombat_checked'):return
        ctx.temp_flags['semi_precombat_checked']=True
        foes=list(self.enemy_team(unit).units)
        # Snapshot before Ultimate reactions can cleanse penalties or grant buffs.
        qik=unit.noncombat_stat('qik')
        amounts=[max(0,qik-foe.noncombat_stat('rec'))*7//10 for foe in foes]
        if not self.trigger_ultimate(unit,ctx,'precombat'):return
        ctx.temp_flags['semi_ultimate']=unit.uid
        ctx.temp_flags['initiator_ultimate']=unit.uid
        self.log_target_effect(unit,'U',foes,'pre-combat Qik−Rec damage ×0.7 (minimum 0)',phase='before combat')
        for foe,amount in zip(foes,amounts):
            raw_amount=amount;amount=self.nisekyu_precombat_damage(ctx,foe,amount)
            foe.damage_mitigated+=raw_amount-amount
            if not self.team(foe).living():break
            self.damage_team(self.team(foe),amount)
            unit.damage_dealt+=amount;foe.damage_taken+=amount
            self._log(f'{self._unit_label(unit)} — Lightning Serve → {self._unit_label(foe)}: {amount} pre-combat damage.')
        if self.skill_enabled(unit,'A'):
            targets=[a for a in self.effect_recipients(unit) if a.character.position!='S']
            for ally in targets:
                for stat in ('pow','qik'):
                    ally.next_combat_bonuses[stat]=TimedMod(4,sources={(unit.uid,'A')})
            self.log_target_effect(unit,'A',targets,'Pow/Qik+4 during next combat',phase='before combat')

    def semi_after_combat(self,ctx):
        unit=ctx.attacker
        if ctx.temp_flags.get('semi_refunded') or ctx.temp_flags.get('semi_ultimate')!=unit.uid:return
        ctx.temp_flags['semi_refunded']=True
        if self.skill_enabled(unit,'C'):
            unit.cd=max(0,unit.cd-1)
            self.log_target_effect(unit,'C',[unit],'remaining Ultimate CD −1 (minimum 0)',phase='after combat')
