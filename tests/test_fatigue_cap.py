import unittest
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext,Status,fatigue_stacks
from hiq.contest import ContestEngine,character
from hiq.sealed import team_specs


class FatigueCapTests(unittest.TestCase):
    def test_repeated_combats_stop_at_seven_and_keep_original_penalties(self):
        e=BattleEngine([(None,character('Suga'),'front')],[(None,character('Kenma'),'front')],seed=1)
        a=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
        for n in range(1,31):
            e.after_combat(ctx)
            self.assertEqual(fatigue_stacks(a),min(n,7))
        active=e._base_combat_stats(a)
        for stat,penalty in dict(spd=7,jmp=7,pow=6,qik=6,**{'def':5},rec=5).items():
            self.assertEqual(a.base[stat]-active[stat],penalty)
        e.end_turn();self.assertEqual(fatigue_stacks(a),7)
        a.clear_debuffs();self.assertEqual(fatigue_stacks(a),0)

    def test_direct_application_and_legacy_payload_are_clamped(self):
        e=BattleEngine([(None,character('Suga'),'front')],[(None,character('Kenma'),'front')])
        a=e.team_a.units[0]
        a.add_status('Fatigue',False,payload=178)
        self.assertEqual(a.statuses['Fatigue'].payload,7)
        a.statuses['Fatigue']=Status('Fatigue',False,payload=178)
        active=e._base_combat_stats(a)
        self.assertEqual(a.statuses['Fatigue'].payload,7)
        self.assertEqual(a.base['spd']-active['spd'],7)
        self.assertEqual(a.base['rec']-active['rec'],5)

    def test_oversized_fatigue_does_not_inflate_excellence_damage(self):
        damages=[]
        for count in (7,178):
            e=BattleEngine([(None,character('Kenma'),'front'),(None,character('Atsumu'),'back')],
                          [(None,character('Bokuto'),'front')],seed=1)
            a=e.team_a.units[0];d=e.team_b.units[0]
            d.statuses['Fatigue']=Status('Fatigue',False,payload=count)
            a.cd=d.cd=99
            e.resolve_hit(CombatContext(e,a,d,a,d),a,d)
            damages.append(a.damage_dealt)
        self.assertEqual(damages[0],damages[1])
        self.assertLess(damages[0],100)

    def test_all_twenty_contest_turns_preserve_cap(self):
        chars=[character(n,t) for n,t in [('Bokuto',None),('Yamagata',None),('Kageyama','Crown of Precision'),
               ('Akifumi',None),('Atsumu',None),('Kenma',None)]]
        e=ContestEngine(team_specs('cap',1,[c.id for c in chars]),4,seed=1)
        e.team_a.hp=e.team_a.max_hp=1000000
        result=e.run()
        self.assertEqual(result.turns,20)
        self.assertEqual(fatigue_stacks(e.feature),7)
        self.assertEqual(e.feature.statuses['Fatigue'].payload,7)

    def test_card_and_extended_description_match_original_rule(self):
        kenma=character('Kenma')
        self.assertIn('maximum of 7',kenma.abilities['A'].text)
        from hiq.character_notes import explanations
        notes=' '.join(text for _,text in explanations(kenma))
        self.assertIn('(max 7)',notes)
        self.assertNotIn('uncapped',notes)
