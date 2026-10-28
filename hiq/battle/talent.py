"""Prodigious Talent's timed support and distinct active-bonus rules."""
from collections import defaultdict


def talent(unit):
    return unit.name == 'Kageyama' and unit.title == 'Prodigious Talent'


class ProdigiousTalentEffects:
    def grant_open_play(self, target, source, slot):
        existing = target.statuses.get('Open Play')
        previous_expiry = existing.expires_after_action if existing else None
        extend = (existing is not None and previous_expiry is not None
                  and previous_expiry > target.actions_taken
                  and not target.acted_this_turn and not target.action_in_progress)
        target.add_status('Open Play', True, through_next_action=True, source=(source.uid, slot))
        status = target.statuses['Open Play']
        if previous_expiry is not None and previous_expiry > target.actions_taken:
            status.expires_after_action = max(previous_expiry, status.expires_after_action)
            status.source_expiries = dict(existing.source_expiries) or {s: previous_expiry for s in existing.sources}
        if extend:
            status.expires_after_action += 1
            self._log(f'{self._unit_label(source)}: {target.name} Open Play extended by one action.')
        status.source_expiries[(source.uid, slot)] = status.expires_after_action
        status.sources.update(status.source_expiries)

    def begin_bonus_event(self):
        # Keep simultaneous stat grants together, but distinguish later applications.
        self.bonus_event += 1
        for unit in self.units:
            unit.bonus_event = self.bonus_event

    def talent_turn_start(self, unit):
        if not talent(unit):
            return
        memory=self.expansion_memory[unit.uid]
        targets=memory.setdefault('open_play_targets',{'turn':self.turn,'previous':set(),'current':set()})
        if targets['turn']!=self.turn:
            targets['previous']=targets['current'] if targets['turn']==self.turn-1 else set()
            targets['current']=set(); targets['turn']=self.turn
        allies = [a for a in self.team(unit).units if a is not unit
                  and a.character.position in ('MB', 'WS', 'OP')
                  and a.uid not in targets['previous']]
        if allies:
            target = self.rng.choice(allies)
            self.talent_previous_target[unit.uid] = target.uid
            targets['current'].add(target.uid)
            self.grant_open_play(target, unit, 'C')
            self._log(f'{self._unit_label(unit)} C: grants Open Play to {target.name} through their next action.')
        else:
            self.talent_previous_target[unit.uid] = None
            for stat in ('pow', 'def', 'rec', 'jmp'):
                unit.add_buff(stat, 3, source=(unit.uid, 'C'))
            unit.clear_debuffs()
            self._log(f'{self._unit_label(unit)} C: no eligible ally; Pow/Def/Rec/Jmp+3 and all debuffs removed.')

    def talent_ultimate(self, unit):
        self.begin_bonus_event()
        for ally in self.team(unit).units:
            ally.add_status('Perfect Window', True, through_next_action=True, source=(unit.uid, 'U'))
            self.grant_open_play(ally, unit, 'U')

    def talent_after_combat(self, ctx):
        if ctx.temp_flags.get('initiator_ultimate') != ctx.attacker.uid:
            return
        for source in self.team(ctx.attacker).units:
            key = (self.turn, source.uid)
            if talent(source) and source is not ctx.attacker and self.source_allowed(ctx, source) and self.talent_tempo_used[key] < 2:
                self.talent_tempo_used[key] += 1
                source.cd = max(0, source.cd - 1)
                self._log(f'{self._unit_label(source)} B: allied initiating Ultimate; remaining CD -1 ({self.talent_tempo_used[key]}/2 this turn).')

    def distinct_bonuses(self, ctx, unit, stats):
        """Count named effects individually; group unnamed effects by source/timing.

        Only surviving visible stat winners count. Permanent stats, school bonds,
        penalties and reactions that happen only after combat are excluded.
        """
        foe = ctx.defender if unit is ctx.attacker else ctx.attacker
        initiated = unit is ctx.attacker
        bonuses = {(unit.uid, 'A', 'combat')} if talent(unit) else set()
        sources = {a.uid: a for a in self.team(unit).units}
        active = self._base_combat_stats(unit, foe, ctx)
        fatigue = unit.statuses.get('Fatigue')
        n = int(fatigue.payload or 0) if fatigue else 0
        penalties = {'spd': n, 'jmp': n, 'pow': max(n-1, 0), 'qik': max(n-1, 0), 'def': max(n-2, 0), 'rec': max(n-2, 0)}
        for stat, mod in unit.visible_buffs.items():
            if mod.value <= 0 or active[stat] != unit.noncombat_stat(stat)-penalties.get(stat, 0)-self.expansion_bond_penalty(ctx,unit):
                continue
            group = mod.bonus_group or ('visible', stat)
            bonuses.add(group)
        for name, status in unit.statuses.items():
            if status.positive and name != 'AkaashiCharge':
                bonuses.add(('named', name))
        # Stat auras are recorded by the ordinary live-stat calculation.
        bonuses.update((uid, slot, 'combat') for uid, slot in ctx.temp_flags.get('ally_stat_impacts', {}).get(unit.uid, set()))
        charge = unit.statuses.get('AkaashiCharge')
        if charge and not self.gear(foe, 'own_tempo'):
            for uid in charge.payload or []:
                if uid in sources and self.source_allowed(ctx, sources[uid]):
                    bonuses.add((uid, 'C', 'combat'))
        for source in self.team(unit).units:
            if source is unit or not self.source_allowed(ctx, source):
                continue
            if source.name == 'Atsumu':
                bonuses.add(('named', 'Excellence'))
            if source.name == 'Reon' and not initiated and not self.gear(foe, 'own_tempo'):
                bonuses.add((source.uid, 'C', 'combat'))
            if source.name == 'Iwaizumi' and ctx.temp_flags.get('offensive_crit_uid') == unit.uid and not self.crits_disabled(ctx) and foe.name != 'Kanoka':
                bonuses.add((source.uid, 'C', 'combat'))
            if source.name == 'Kanbayashi' and source.lane == unit.lane and ctx.temp_flags.get('offensive_crit_uid') == foe.uid and 'Perfect Window' not in foe.statuses:
                bonuses.add((source.uid, 'C', 'combat'))
            if source.name == 'Kageyama' and source.title == 'Crown of Precision' and initiated and self.team_hp_pct(unit) <= 75 and self.skill_spd(unit, stats[unit.uid]) >= self.skill_spd(foe, stats[foe.uid])+3:
                if foe.name not in ('Shirabu', 'Futakuchi') and not self.gear(unit, 'rigorous_film_study') and not self.gear(foe, 'rigorous_film_study') and not (foe.name == 'Aone' and stats[foe.uid]['def'] >= stats[unit.uid]['def']+3):
                    bonuses.add((source.uid, 'B', 'combat'))
        item_mods = defaultdict(int)
        self.equipment_mods(ctx, unit, foe, initiated, item_mods)
        if any(value > 0 for value in item_mods.values()):
            bonuses.add((unit.uid, 'Item', 'combat'))
        # Barrier is a persistent combat benefit, grouped by its granting skill.
        if unit.barrier > 0:
            bonuses.update((uid, slot, 'barrier') for uid, slot in unit.barrier_sources)
        return bonuses
