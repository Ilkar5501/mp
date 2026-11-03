"""Requested formations, explicit support projections and receiving redirects."""
import unittest
from hiq.contest import ContestEngine, PRESETS, preset_specs, character, support_slots
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.sealed import team_specs
from hiq.data import CHARACTERS


def players():
    return team_specs('revision',1,[c.id for c in list({c.name:c for c in CHARACTERS}.values())[:6]])


class ContestRevisionTests(unittest.TestCase):
    def test_requested_support_pools(self):
        expected=[
            ['Oikawa','Iwaizumi','Tenma Udai','Konoha','Suga','Shirabu'],
            ['Oikawa','Ushijima','Usuri','Suwa','Konoha','Akaashi'],
            ['Ushijima','Iwaizumi','Korai','Konoha','Kyotani','Watari'],
            ['Atsumu','Suna','Akaashi','Kageyama','Kageyama','Daichi'],
            ['Akaashi','Konoha','Washio','Komi','Kenma','Kageyama'],
            ['Koganegawa','Kosuke','Suwa','Gao','Reon','Nozawa'],
            ['Kageyama','Atsumu','Korai','Suga','Kageyama','Daichi'],
            ['Atsumu','Suna','Futakuchi','Hinata','Watari','Suwa'],
            ['Yamagata','Kanbayashi','Daichi','Reon','Nozawa','Iizuna'],
            ['Usuri','Kanbayashi','Iizuna','Kageyama','Daichi','Yamagata'],
            ['Suga','Daichi','Oikawa','Hinata','Akaashi','Suwa'],
            ['Kiryu','Kanbayashi','Atsumu','Korai','Hinata','Kageyama'],
            ['Oikawa','Ushijima','Akaashi','Atsumu','Suga','Kageyama'],
            ['Iizuna','Atsumu','Kanbayashi','Akaashi','Daichi','Kai']]
        self.assertEqual([[c.name for _,c,_ in preset_specs(i)[1:]] for i in range(14)],expected)
        for i in (3,6):
            self.assertEqual({c.title for _,c,_ in preset_specs(i)[1:] if c.name=='Kageyama'},
                             {'Crown of Precision','Prodigious Talent'})
        self.assertEqual(next(c.title for _,c,_ in preset_specs(11)[1:] if c.name=='Kiryu'),'Last Hope')
        self.assertEqual(next(c.title for _,c,_ in preset_specs(11)[1:] if c.name=='Hinata'),'Miracle Maker')
        self.assertEqual(support_slots(character('Kai')),('B','C'))

    def test_support_open_play_survives_every_featured_action(self):
        for index in (3,4,6,11,12):
            e=ContestEngine(players(),index,seed=1)
            for turn in range(3):
                e.start_turn()
                status=e.feature.statuses['Open Play']
                self.assertIsNone(status.expires_after_action)
                for action in range(2):
                    e.feature.actions_taken+=1;e.feature.expire_after_action()
                    self.assertIn('Open Play',e.feature.statuses)
                e.end_turn()

    def test_source_specific_position_projections(self):
        for index,name,title,lane in [(2,'Korai',None,'back'),(7,'Hinata','Miracle Maker','front'),(11,'Hinata','Miracle Maker','back')]:
            e=ContestEngine(players(),index,seed=1)
            source=next(u for u in e.supports if u.name==name)
            self.assertEqual(e.feature.lane,'front')
            self.assertEqual(e.support_target_lane(source,e.feature),lane)
            a,d=e.feature,e.team_a.units[0]
            ctx=CombatContext(e,a,d,a,d)
            stats=e.combat_stats(ctx)[a.uid]
            original=e.support_target_lane
            # Compare against the opposite projection, with the rest of the pool fixed.
            e.support_target_lane=lambda s,t: ('front' if lane=='back' else 'back') if s is source else original(s,t)
            other=e.combat_stats(ctx)[a.uid]
            if name=='Korai':
                self.assertEqual(stats['def']-other['def'],3)
                self.assertEqual(stats['jmp']-other['jmp'],3)
            elif lane=='front':
                self.assertEqual(stats['pow']-other['pow'],3)
                self.assertEqual(stats['spd']-other['spd'],3)
                self.assertEqual(stats['rec']-other['rec'],-3)
            else:
                self.assertEqual(stats['def']-other['def'],3)
                self.assertEqual(stats['rec']-other['rec'],3)
                self.assertEqual(stats['pow']-other['pow'],-3)

    def test_contest_kai_b_applies_to_iwaizumi_draw(self):
        e=ContestEngine(players(),13,seed=1)
        source=next(u for u in e.supports if u.name=='Kai')
        self.assertEqual(source.lane,'back')
        a=e.team_a.units[0];a.character=character('Hinata','Wings of Dawn')
        d=e.feature;ctx=CombatContext(e,a,d,a,d)
        drawn=e.combat_stats(ctx)[d.uid]
        self.assertIn((source.uid,'B'),e.ally_skill_impacts(ctx,d))
        ctx.override_targeting=True;forced=e.combat_stats(ctx)[d.uid]
        self.assertEqual(drawn['def']-forced['def'],5)
        self.assertEqual(drawn['rec']-forced['rec'],5)

    def test_saved_revision_three_keeps_formations_and_alternation(self):
        e=ContestEngine(players(),12,rules_version=3,seed=1)
        self.assertIn('Iwaizumi',[u.name for u in e.supports])
        e.start_turn();e.feature.actions_taken+=1;e.feature.expire_after_action()
        self.assertNotIn('Open Play',e.feature.statuses)
        e.end_turn();e.start_turn();self.assertNotIn('Open Play',e.feature.statuses)


class KaiRedirectTests(unittest.TestCase):
    def test_draws_saves_and_forced_targets_in_regular_battles(self):
        for name,lane,attack,eligible in [('Iwaizumi','front','qik',True),('Iwaizumi','front','pow',False),
                ('Tsuki','front','pow',True),('Tsuki','back','pow',False),
                ('Komi','back','qik',True),('Komi','front','qik',False),('Bokuto','front','qik',False)]:
            attacker=character('Hinata','Wings of Dawn') if attack=='qik' else character('Aran')
            e=BattleEngine([(None,attacker,'front')],[(None,character(name),lane),(None,character('Kai'),'back')])
            a=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
            active=e.combat_stats(ctx)[d.uid]
            ctx.override_targeting=True;forced=e.combat_stats(ctx)[d.uid]
            self.assertEqual(active['def']-forced['def'],5 if eligible else 0,(name,lane,attack))
            self.assertEqual(active['rec']-forced['rec'],5 if eligible else 0)
            ctx.save_triggered=True;saved=e.combat_stats(ctx)[d.uid]
            self.assertEqual(saved['def']-forced['def'],5)
            self.assertEqual(saved['rec']-forced['rec'],5)
            ctx.override_targeting=False;both=e.combat_stats(ctx)[d.uid]
            self.assertEqual(both,saved)  # A draw and a save do not grant +10.

    def test_external_kai_is_disabled_in_fukunaga_duel(self):
        e=BattleEngine([(None,character('Fukunaga'),'front')],[(None,character('Iwaizumi'),'front'),(None,character('Kai'),'back')])
        a=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d,save_triggered=True)
        saved=e.combat_stats(ctx)[d.uid]
        ctx.save_triggered=False
        self.assertEqual(saved,e.combat_stats(ctx)[d.uid])
