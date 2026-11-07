"""Fatigue caps at eight in PvP and remains uncapped in other modes."""
import unittest
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.contest import character


class UncappedFatigueTests(unittest.TestCase):
    def test_pvp_caps_both_sides_at_eight_and_cleansing_restarts_stacks(self):
        e=BattleEngine([(None,character('Kenma'),'front')],[(None,character('Kenma'),'back')],mode='pvp',seed=1)
        a,d=e.team_a.units[0],e.team_b.units[0]
        ctx=CombatContext(e,a,d,a,d)
        for n in range(1,31):
            e.after_combat(ctx)
            for unit in (a,d):self.assertEqual(unit.statuses['Fatigue'].payload,min(n,8))
        e.end_turn()
        for unit in (a,d):
            self.assertEqual(unit.statuses['Fatigue'].payload,8)
            stats=e._base_combat_stats(unit)
            self.assertEqual(stats['spd'],unit.base['spd']-8)
            self.assertEqual(stats['rec'],unit.base['rec']-6)
            unit.clear_debuffs()
            self.assertNotIn('Fatigue',unit.statuses)
        e.after_combat(ctx)
        for unit in (a,d):self.assertEqual(unit.statuses['Fatigue'].payload,1)

    def test_non_pvp_modes_still_stack_past_eight(self):
        for mode in ('battle','gauntlet_regional','gauntlet_national','raid','contest','tournament','war','sealed','prefecture'):
            with self.subTest(mode=mode):
                e=BattleEngine([(None,character('Suga'),'front')],[(None,character('Kenma'),'back')],mode=mode,seed=1)
                a,d=e.team_a.units[0],e.team_b.units[0]
                ctx=CombatContext(e,a,d,a,d)
                for _ in range(12):e.after_combat(ctx)
                self.assertEqual(a.statuses['Fatigue'].payload,12)

    def test_stacks_continue_past_seven_and_persist(self):
        e=BattleEngine([(None,character('Suga'),'front')],[(None,character('Kenma'),'front')],seed=1)
        a=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
        for n in range(1,31):
            e.after_combat(ctx)
            self.assertEqual(a.statuses['Fatigue'].payload,n)
        e.end_turn();self.assertEqual(a.statuses['Fatigue'].payload,30)
        a.clear_debuffs();self.assertNotIn('Fatigue',a.statuses)

    def test_uncapped_penalties_and_excellence_are_restored(self):
        e=BattleEngine([(None,character('Kenma'),'front'),(None,character('Atsumu'),'back')],
                      [(None,character('Bokuto'),'front')],seed=1)
        a=e.team_a.units[0];d=e.team_b.units[0]
        d.add_status('Fatigue',False,payload=178)
        self.assertEqual(d.statuses['Fatigue'].payload,178)
        stats=e._base_combat_stats(d)
        self.assertEqual(stats['spd'],d.base['spd']-178)
        self.assertEqual(stats['rec'],d.base['rec']-176)
        self.assertLess(stats['rec'],0)
        a.cd=d.cd=99
        e.resolve_hit(CombatContext(e,a,d,a,d),a,d)
        self.assertGreater(a.damage_dealt,300)

    def test_description_explains_pvp_exception_and_uncapped_other_modes(self):
        self.assertIn('8 in PvP',character('Kenma').abilities['A'].text)
        self.assertIn('stacks have no maximum',character('Kenma').abilities['A'].text)
        from hiq.character_notes import explanations
        notes=' '.join(text for _,text in explanations(character('Kenma')))
        self.assertIn('maximum 8 stacks in PvP',notes)
        self.assertIn('uncapped',notes)
