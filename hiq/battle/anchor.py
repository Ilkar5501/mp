"""Iizuna's timed targeting penalties, healing denial and receiving support."""
from math import floor


class AnchorEffects:
    def attack_target_allowed(self, unit):
        return not any(name in unit.statuses for name in ('Untargetable', 'Guiding Break'))

    def guiding_break_count(self, team):
        return sum('Guiding Break' in unit.statuses for unit in team.units)

    def inflict_guiding_break(self, source, target):
        if 'Guiding Break' in target.statuses or self.guiding_break_count(self.team(target)) >= 2:
            return False
        target.add_status('Guiding Break', False, through_next_action=True, source=(source.uid, 'B'))
        self._log(f'{self._unit_label(source)} inflicts Guiding Break on {self._unit_label(target)} through their next action.')
        return True

    def anchor_combat_start(self, ctx):
        stats=self.combat_stats(ctx)
        if self.anchor_receive_sources(ctx) and stats[ctx.defender.uid]['rec'] > stats[ctx.attacker.uid]['rec']:
            source=self.anchor_receive_sources(ctx)[0]
            ctx.defender.add_status('Clean Receive', True, through_next_action=True, source=(source.uid, 'C'))
            self._log(f'{self._unit_label(ctx.defender)} gains Clean Receive through their next action.')

    def anchor_receive_sources(self, ctx):
        return [unit for unit in self.team(ctx.defender).units
                if unit.name == 'Iizuna' and self.source_allowed(ctx, unit)]

    def anchor_receiving(self, ctx, attacker, defender, stats):
        return (attacker is ctx.attacker and defender is ctx.defender
                and bool(self.anchor_receive_sources(ctx))
                and stats[defender.uid]['rec'] > stats[attacker.uid]['rec'])

    def anchor_critical(self, ctx, attacker, defender, defensive_crit):
        if not defensive_crit or attacker is not ctx.attacker:
            return
        for source in self.team(attacker).units:
            if source.name == 'Iizuna' and self.source_allowed(ctx, source):
                self.inflict_guiding_break(source, defender)
                break

    def anchor_damage_bonus(self, ctx, attacker, stats):
        if attacker is not ctx.attacker:
            return 0
        if any(source.name == 'Iizuna' and self.source_allowed(ctx, source)
               for source in self.team(attacker).units):
            count=self.guiding_break_count(self.enemy_team(attacker))
            return max(0, floor(stats[attacker.uid]['rec'] * count / 10))
        return 0

    def anchor_ultimate_result(self, ctx, attacker, defender, triggered):
        if not triggered or attacker.name != 'Iizuna' or attacker.offense_key == defender.offense_key == 'qik':
            return
        # Select the highest-Jmp foe first. Never fall back to another eligible foe.
        foes=self.enemy_team(attacker).units
        target=max(foes, key=lambda foe: foe.noncombat_stat('jmp'))
        self.inflict_guiding_break(attacker, target)

    def anchor_after_combat(self, ctx):
        for unit in (ctx.attacker, ctx.defender):
            if unit.name == 'Iizuna' and self.guiding_break_count(self.enemy_team(unit)) == 2:
                unit.cd=max(0, unit.cd-1)
                self._log(f'{self._unit_label(unit)} A: two foes have Guiding Break; remaining Ultimate CD -1.')

    def clean_receive_blocks_healing(self, ctx, unit):
        return any(combatant.side != unit.side and 'Clean Receive' in combatant.statuses
                   for combatant in (ctx.attacker, ctx.defender))
