import unittest
from unittest.mock import Mock, patch
from test_event_modes import specs
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext


def duel(names, foe='Kai', item=None):
    e=BattleEngine(specs(names,item),specs([foe]),seed=1)
    a,d=e.team_a.units[0],e.team_b.units[0]
    return e,a,d,CombatContext(e,a,d,a,d)


class SignatureCombatTests(unittest.TestCase):
    def test_aran_converts_visible_and_aura_speed_without_a_cap(self):
        results=[]
        for item in (None,'aran_out_of_nowhere'):
            e,a,d,ctx=duel(['Aran','Korai'],item=item)
            a.add_buff('spd',100)
            results.append(e.combat_stats(ctx)[a.uid])
            if item:
                e.turn=1;before=a.cd;e.equipment_turn_start(a)
                self.assertEqual(a.cd,max(0,before-2))
                e.equipment_turn_start(a);self.assertEqual(a.cd,max(0,before-2))
        self.assertGreaterEqual(results[1]['jmp']-results[0]['jmp'],70)
        self.assertEqual(results[1]['spd'],results[0]['spd'])
        self.assertEqual(results[1]['pow']-results[0]['pow'],5)
        self.assertEqual(results[1]['def']-results[0]['def'],5)

    def test_lev_counter_denial_followup_control_and_damage(self):
        e,a,d,ctx=duel(['Lev'],'Daichi','lev_rookie_gains')
        a.base['spd']=100
        self.assertFalse(e.can_counter(ctx,d,a))
        a.add_status('Charged',True)
        d.character=__import__('hiq.contest',fromlist=['character']).character('Aone')
        d.base['def']=1000
        self.assertTrue(e.followup(ctx,a,d,True))
        d.add_status('Charged',True)
        self.assertFalse(e.followup(ctx,d,a,False))
        # Strip outside effects to isolate per-hit Qik damage.
        damages=[]
        for item in (None,'lev_rookie_gains'):
            e,a,d,ctx=duel(['Lev'],item=item)
            e.rng=Mock(random=Mock(return_value=.999));a.cd=5
            with patch.object(e,'combat_stats',return_value={a.uid:{'pow':0,'qik':100,'def':0,'rec':0,'spd':100,'jmp':0},d.uid:{'pow':0,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0}}):
                e.resolve_hit(ctx,a,d)
            damages.append(a.damage_dealt)
        self.assertEqual(damages[1]-damages[0],15)

    def test_tanaka_base_stats_self_buff_and_row_cleanse(self):
        e,a,d,ctx=duel(['Tanaka','Kai','Suga'],item='tanaka_mountain_chalk')
        plain,pa,_,_=duel(['Tanaka','Kai','Suga'])
        for stat in ('hp','pow','def','rec','spd','jmp'):self.assertEqual(a.base[stat]-pa.base[stat],2)
        self.assertEqual(a.base['qik'],pa.base['qik'])
        row_ally,other=e.team_a.units[1:];row_ally.lane=a.lane;other.lane='back' if a.lane=='front' else 'front'
        for u in e.team_a.units:u.add_debuff('spd',-6);u.add_status('Fatigue',False,payload=8)
        a.ult_triggered_this_combat=True;e.after_combat(ctx)
        for u in (a,row_ally):
            self.assertFalse(u.visible_debuffs);self.assertNotIn('Fatigue',u.statuses)
        self.assertIn('Fatigue',other.statuses)
        for u in e.team_a.units:
            for stat in ('pow','qik','def','rec','spd','jmp'):self.assertEqual(u.visible_bonus(stat),4)


class SakusaTests(unittest.TestCase):
    def test_perfectionism_post_combat_only_and_max_five(self):
        e,a,d,ctx=duel(['Sakusa'])
        e.combat(ctx);self.assertEqual(e.sakusa_stacks(a),0)
        e.after_combat(ctx);self.assertEqual(e.sakusa_stacks(a),1)
        e.sakusa_after_combat(ctx);self.assertEqual(e.sakusa_stacks(a),1)
        for _ in range(9):e.sakusa_after_combat(CombatContext(e,a,d,a,d))
        self.assertEqual(e.sakusa_stacks(a),5);self.assertEqual(e.critical_bonus(a),10)
        self.assertEqual(e.crit_multiplier(ctx,a),1.1)

    def test_quad_has_independent_preliminary_and_actual_crit_rolls(self):
        e,a,d,ctx=duel(['Sakusa'])
        a.base['jmp']=100;d.base['jmp']=0;d.add_status('Cannot Counter',False)
        e.team_b.hp=e.team_b.max_hp=10000
        # preliminary, hit 1, successful follow-up roll, hits 2/3/4
        e.rng=Mock(random=Mock(side_effect=[0,0,0,0,0,0]))
        e.combat(ctx)
        self.assertEqual(a.attacks_made,4);self.assertEqual(a.crits,4)
        self.assertEqual(e.rng.random.call_count,6)
        self.assertEqual(e.sakusa_stacks(a),0)

    def test_failed_preliminary_roll_is_not_an_actual_crit(self):
        e,a,d,ctx=duel(['Sakusa'])
        a.base['jmp']=d.base['jmp']+1;d.add_status('Cannot Counter',False)
        e.rng=Mock(random=Mock(side_effect=[.99,0,.99]))
        e.combat(ctx);self.assertEqual(a.attacks_made,1);self.assertEqual(a.crits,1)
        self.assertEqual(e.rng.random.call_count,3)

    def test_each_initial_crit_gets_a_50_percent_roll_until_one_succeeds(self):
        for rolls,hits,crits in [
            ([0,0,.9,0,.49,.99,.99],4,2), # fail first roll; second crit retries
            ([0,.99,0,.49,.99,.99],4,1),   # only second initial hit crits
            ([0,0,.49,0,.99,.99],4,2),    # first succeeds; second crit needs no roll
            ([0,0,.50,0,.99],2,2),        # .50 is outside the success interval
        ]:
            with self.subTest(rolls=rolls):
                e,a,d,ctx=duel(['Sakusa']);a.base['jmp']=d.base['jmp']+3
                d.add_status('Cannot Counter',False);e.team_b.hp=e.team_b.max_hp=10000
                e.rng=Mock(random=Mock(side_effect=rolls));e.combat(ctx)
                self.assertEqual(a.attacks_made,hits);self.assertEqual(a.crits,crits)
                self.assertEqual(e.rng.random.call_count,len(rolls))

    def test_single_hit_can_earn_single_followup_after_preliminary_failure(self):
        e,a,d,ctx=duel(['Sakusa']);a.base['jmp']=d.base['jmp']+3
        d.add_status('Cannot Counter',False);e.team_b.hp=e.team_b.max_hp=10000
        e.rng=Mock(random=Mock(side_effect=[.99,0,.49,.99]));e.combat(ctx)
        self.assertFalse(e.attacks_twice(ctx,a));self.assertEqual(a.attacks_made,2)
        self.assertEqual(a.crits,1);self.assertEqual(e.rng.random.call_count,4)

    def test_dispel_removes_named_buff_not_visible_or_intrinsic_resource(self):
        e,a,d,ctx=duel(['Sakusa'])
        d.add_buff('spd',6);d.add_status('Learning',True,payload=3);d.add_status('Open Play',True)
        e.rng=Mock(random=Mock(return_value=0),choice=Mock(side_effect=lambda rows:rows[0]))
        e.sakusa_hit_result(ctx,a,d,False)
        self.assertNotIn('Open Play',d.statuses);self.assertIn('Learning',d.statuses)
        self.assertEqual(d.visible_bonus('spd'),6)

    def test_defending_sakusa_gets_no_b_precheck_double_hit_or_followup_roll(self):
        e=BattleEngine(specs(['Daichi']),specs(['Sakusa']),seed=1)
        a,d=e.team_a.units[0],e.team_b.units[0];a.base['jmp']=0;d.base['jmp']=100
        e.team_a.hp=e.team_a.max_hp=10000;e.team_b.hp=e.team_b.max_hp=10000
        ctx=CombatContext(e,a,d,a,d);e.rng=Mock(random=Mock(return_value=0))
        e.sakusa_precheck(ctx);self.assertEqual(e.rng.random.call_count,0)
        e.combat(ctx)
        self.assertEqual(d.attacks_made,1);self.assertFalse(e.attacks_twice(ctx,d))
        self.assertFalse(ctx.temp_flags.get('sakusa_followup:'+d.uid))
        self.assertEqual(e.crit_multiplier(ctx,d),1.2)
        e.after_combat(ctx);self.assertEqual(e.sakusa_stacks(d),1)

    def test_defensive_dispel_and_other_critical_support_remain_active(self):
        e=BattleEngine(specs(['Daichi']),specs(['Sakusa','Iwaizumi']),seed=1)
        a,d=e.team_a.units[0],e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
        a.add_status('Open Play',True)
        e.rng=Mock(random=Mock(return_value=0),choice=Mock(side_effect=lambda rows:rows[0]))
        e.sakusa_hit_result(ctx,d,a,True)
        self.assertNotIn('Open Play',a.statuses)
        self.assertFalse(ctx.temp_flags.get('sakusa_followup:'+d.uid))
        self.assertEqual(e.rng.random.call_count,1)
        self.assertEqual(e.crit_multiplier(ctx,d),1.5)

    def test_ultimate_penetration_and_half_damage_heal(self):
        e,a,d,ctx=duel(['Sakusa'])
        self.assertEqual(a.max_cd,5);a.cd=0;e.team_a.hp=10
        e.rng=Mock(random=Mock(return_value=.999))
        with patch.object(e,'combat_stats',return_value={a.uid:{'pow':100,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':0,'qik':0,'def':60,'rec':60,'spd':0,'jmp':0}}):
            e.resolve_hit(ctx,a,d)
        self.assertEqual(a.damage_dealt,70);self.assertEqual(e.team_a.hp,45)
