"""Uncapped Fatigue is intentional, restored at the user's request."""
import unittest
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.contest import character


class UncappedFatigueTests(unittest.TestCase):
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

    def test_description_matches_restored_behavior(self):
        self.assertIn('stacks have no maximum',character('Kenma').abilities['A'].text)
        from hiq.character_notes import explanations
        self.assertIn('(uncapped)',' '.join(text for _,text in explanations(character('Kenma'))))
