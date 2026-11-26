import unittest
from dataclasses import replace
from unittest.mock import patch
from hiq.data import CHARACTERS,CHAR_BY_ID,find_character
from hiq.contest import character,ContestEngine
from hiq.sealed import team_specs
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.event_modes import AllForOneEngine

SEMI='Semi — Pinch Ace'


def specs(names,item=None):
    rows=team_specs('v13-test',1,[SEMI if n=='Semi' else character(n).id for n in names])
    if item:rows[0]=(replace(rows[0][0],equipment_key=item),rows[0][1],rows[0][2])
    return rows


class SemiTests(unittest.TestCase):
    def engine(self,allies=('Semi','Suga','Daichi'),foes=('Kai','Lev','Reon'),**kwargs):
        e=BattleEngine(specs(allies),specs(foes),seed=7,**kwargs)
        a,d=e.team_a.units[0],e.team_b.units[0]
        return e,a,d,CombatContext(e,a,d,a,d)

    def test_semi_stats_names_art_and_search(self):
        c=CHAR_BY_ID[SEMI]
        self.assertEqual((c.rarity,c.position,c.school,c.role_class),(4,'S','Shiratorizawa','DPS'))
        self.assertEqual(tuple(c.stats[s] for s in ('hp','qik','spd','jmp','def','rec')),(150,52,36,35,27,28))
        self.assertEqual([c.abilities[k].name for k in ('U','A','B','C')],['Lightning Serve',"Talent's Troubles","Setter's Magic",'Rightful Rhythm'])
        self.assertEqual(find_character('Eita Semi')[0].id,SEMI)
        self.assertTrue(c.card_art.endswith('/semi.png'));self.assertIn('5d9e9f56a444cc05cdd0da1d5e91e4ab',c.character_icon)

    def test_b_counts_self_and_setters_caps_three_and_repeats_each_turn(self):
        for allies,reduction in [(('Semi','Kai','Lev'),1),(('Semi','Suga','Kai'),2),(('Semi','Suga','Kenma','Oikawa','Atsumu','Daichi'),3)]:
            e,a,_,_=self.engine(allies=allies)
            for turn in (1,2,3):
                e.turn=turn;a.cd=4;e.semi_turn_start(a)
                self.assertEqual(a.cd,4-reduction);self.assertEqual(a.max_cd,4)

    def test_aoe_uses_visible_precombat_stats_each_foe_and_floors_independently(self):
        e,a,d,ctx=self.engine();a.cd=0;a.base['qik']=50;a.add_buff('qik',4);a.add_debuff('qik',-1)
        for u,rec in zip(e.team_b.units,(30,54,52)):u.base['rec']=rec
        e.team_b.units[0].add_buff('rec',2)
        before=e.team_b.hp;e.semi_precombat(ctx)
        self.assertEqual([u.damage_taken for u in e.team_b.units],[14,0,0])
        self.assertEqual(before-e.team_b.hp,14);self.assertEqual(a.damage_dealt,14)
        self.assertEqual(a.cd,4);self.assertTrue(a.ult_triggered_this_combat)
        self.assertTrue(any('all foes:' in s and 'Lightning Serve' in s for s in e.log))

    def test_aoe_ignores_combat_modifiers_and_regular_damage_reduction(self):
        e,a,d,ctx=self.engine(foes=('Kita',));a.cd=0;a.base['qik']=50;d.base['rec']=30
        d.cd=0;before=e.team_b.hp
        e.semi_precombat(ctx)
        self.assertEqual(before-e.team_b.hp,14)  # excludes Semi's combat Qik+5 and Kita's combat Rec bonuses/DR
        self.assertEqual(d.cd,0)

    def test_trigger_is_precombat_only_once_and_ultimate_suppression_is_respected(self):
        e,a,d,ctx=self.engine(foes=('Fukunaga',));a.cd=0
        before=e.team_b.hp;e.semi_precombat(ctx);self.assertEqual(e.team_b.hp,before)
        self.assertEqual(a.cd,0);self.assertNotIn('semi_ultimate',ctx.temp_flags)
        e,a,d,ctx=self.engine();a.cd=0;a.add_status('UltimateDisabled',False)
        e.semi_precombat(ctx);self.assertNotIn('semi_ultimate',ctx.temp_flags)
        e,a,d,ctx=self.engine();a.cd=0;e.semi_precombat(ctx)
        before=e.team_b.hp;a.cd=0;e.semi_precombat(ctx)
        self.assertEqual(e.team_b.hp,before);self.assertFalse(e.offensive_ult_ready(a,d,ctx))
        counter=CombatContext(e,d,a,d,a);e.semi_precombat(counter)
        self.assertNotIn('semi_ultimate',counter.temp_flags)

    def test_a_bonus_excludes_setters_survives_noncombat_action_and_expires_after_combat(self):
        e,a,d,ctx=self.engine();a.cd=0;e.semi_precombat(ctx)
        suga,daichi=e.team_a.units[1:]
        self.assertFalse(a.next_combat_bonuses);self.assertFalse(suga.next_combat_bonuses)
        self.assertEqual({k:m.value for k,m in daichi.next_combat_bonuses.items()},{'pow':4,'qik':4})
        self.assertFalse(daichi.visible_buffs)
        daichi.actions_taken+=1;daichi.expire_after_action();daichi.expire_end_turn(1)
        self.assertTrue(daichi.next_combat_bonuses)
        fight=CombatContext(e,daichi,d,daichi,d)
        enhanced=e.combat_stats(fight)[daichi.uid]['pow']
        saved=daichi.next_combat_bonuses;daichi.next_combat_bonuses={}
        normal=e.combat_stats(fight)[daichi.uid]['pow'];daichi.next_combat_bonuses=saved
        self.assertEqual(enhanced-normal,4)
        e.combat(fight);e.after_combat(fight)
        self.assertFalse(daichi.next_combat_bonuses)

    def test_a_next_combat_bonus_applies_when_defending_and_does_not_stack(self):
        e,a,d,ctx=self.engine();a.cd=0;e.semi_precombat(ctx)
        daichi=e.team_a.units[-1]
        a.cd=0;e.semi_precombat(CombatContext(e,a,d,a,d))
        self.assertEqual(daichi.next_combat_bonuses['pow'].value,4)
        fight=CombatContext(e,d,daichi,d,daichi);e.combat(fight);e.after_combat(fight)
        self.assertFalse(daichi.next_combat_bonuses)

    def test_c_refunds_one_only_after_trigger_and_only_once(self):
        e,a,d,ctx=self.engine();a.cd=0;e.semi_precombat(ctx)
        e.semi_after_combat(ctx);self.assertEqual(a.cd,3)
        e.semi_after_combat(ctx);self.assertEqual(a.cd,3)
        a.cd=2;e.semi_after_combat(CombatContext(e,a,d,a,d));self.assertEqual(a.cd,2)

    def test_ready_during_combat_waits_for_next_initiation(self):
        e,a,d,ctx=self.engine(foes=('Kai',));a.cd=1
        e.combat(ctx);e.after_combat(ctx)
        self.assertFalse(any('triggers Lightning Serve' in s for s in e.log))
        a.cd=0;next_ctx=CombatContext(e,a,d,a,d);e.combat(next_ctx)
        self.assertEqual(sum('triggers Lightning Serve' in s for s in e.log),1)

    def test_energy_drink_and_three_setters_ready_the_first_serve(self):
        e=BattleEngine(specs(('Semi','Suga','Kenma'),'energy_drink'),specs(('Kai','Lev','Reon')),seed=3)
        e.apply_match_start();e.start_turn();self.assertEqual(e.team_a.units[0].cd,0)

    def test_aoe_can_finish_battle_before_normal_hits_without_phantom_attacks(self):
        e,a,d,ctx=self.engine();a.cd=0;e.team_b.hp=1
        e.combat(ctx)
        self.assertEqual(e.team_b.hp,0);self.assertEqual(a.attacks_made,0)

    def test_af1_targets_only_active_foe_and_counts_support_setters(self):
        e=AllForOneEngine(specs(('Semi','Suga','Kenma','Daichi')),specs(('Reon','Kai','Lev')),seed=3)
        a,d=e.team_a.units[0],e.team_b.units[0];a.cd=4;e.semi_turn_start(a);self.assertEqual(a.cd,1)
        a.cd=0;ctx=CombatContext(e,a,d,a,d);e.semi_precombat(ctx)
        self.assertGreater(d.damage_taken,0)
        self.assertEqual([u.damage_taken for u in e.team_b.units[1:]],[0,0])
        self.assertTrue(all(not u.next_combat_bonuses for u in e.team_a.units[1:]))

    def test_contest_credits_aoe_once_without_hitting_inactive_supports(self):
        e=ContestEngine(specs(('Semi','Suga','Daichi','Reon','Kenma','Aone')),0,seed=2)
        a,d=e.team_a.units[0],e.feature;a.cd=0;ctx=CombatContext(e,a,d,a,d)
        e.semi_precombat(ctx)
        self.assertEqual(e.score,a.damage_dealt);self.assertGreater(e.score,0)
        self.assertTrue(all(u.damage_taken==0 for u in e.supports))


class BalanceTests(unittest.TestCase):
    def test_exact_balance_stats_and_all_libero_receive_buffs(self):
        expected={'Unnan':{'pow':49,'jmp':39},'Ezota':{'rec':34},'Nozawa':{'hp':172},'Goshiki':{'pow':51},'Kita':{'rec':36},'Suna':{'hp':156,'qik':51,'def':34,'jmp':35},'Hirugami':{'hp':168,'pow':47,'jmp':34}}
        for name,stats in expected.items():
            c=character(name)
            for stat,value in stats.items():self.assertEqual(c.stats[stat],value,(name,stat))
        recs={'Nishinoya':38,'Yaku':41,'Komi':36,'Akagi':34,'Yamagata':35,'Kosuke':34,'Kanbayashi':35,'Shibayama':27,'Watari':29,'Akifumi':28,'Marika Tachibana':37}
        self.assertEqual({c.name:c.stats['rec'] for c in CHARACTERS if c.position=='Li'},recs)

    def test_unnan_neutralizes_all_visible_penalties_only_during_combat(self):
        e=BattleEngine(specs(('Unnan',)),specs(('Kai',)),seed=1);a,d=e.team_a.units[0],e.team_b.units[0]
        for stat in ('pow','qik','def','rec','spd','jmp'):a.add_debuff(stat,-6)
        ctx=CombatContext(e,a,d,a,d);stats=e._base_combat_stats(a,d,ctx)
        self.assertEqual(stats, {s:a.base[s] for s in stats})
        self.assertEqual(a.visible_penalty('rec'),-6);self.assertEqual(a.noncombat_stat('rec'),a.base['rec']-6)

    def test_unnan_equality_def_check_delays_ready_ultimate(self):
        # Use an ordinary offensive Ultimate to test Unnan's first-hit delay.
        e=BattleEngine(specs(('Lev',)),specs(('Unnan',)),seed=1);a,d=e.team_a.units[0],e.team_b.units[0]
        a.cd=0;d.cd=4;ctx=CombatContext(e,a,d,a,d)
        with patch.object(e,'combat_stats',return_value={a.uid:dict(a.base,**{'def':35}),d.uid:dict(d.base,**{'def':35})}),patch.object(e,'charge_delta',return_value=0):e.resolve_hit(ctx,a,d)
        self.assertFalse(a.ult_triggered_this_combat);self.assertEqual(a.cd,1)

    def test_unnan_ultimate_reduces_fifty_five_percent_plus_flat_five(self):
        e=BattleEngine(specs(('Suna',)),specs(('Unnan',)),seed=1);a,d=e.team_a.units[0],e.team_b.units[0]
        a.cd=99;d.cd=0;ctx=CombatContext(e,a,d,a,d)
        fixed=dict(pow=100,qik=100,spd=0,jmp=0,rec=80);fixed['def']=80
        with patch.object(e,'combat_stats',return_value={a.uid:fixed,d.uid:fixed}):e.resolve_hit(ctx,a,d)
        self.assertEqual(d.damage_taken,4)  # floor(20 * .45) - 5

    def test_goshiki_killer_instinct_uses_twenty_five_percent_penetration(self):
        e=BattleEngine(specs(('Goshiki',)),specs(('Suga',)),seed=1);a,d=e.team_a.units[0],e.team_b.units[0]
        a.cd=d.cd=99;e.team_b.hp=e.team_b.max_hp//2;ctx=CombatContext(e,a,d,a,d)
        fixed=dict(pow=100,qik=100,spd=0,jmp=0,rec=80);fixed['def']=80
        with patch.object(e,'combat_stats',return_value={a.uid:fixed,d.uid:fixed}):e.resolve_hit(ctx,a,d)
        self.assertEqual(d.damage_taken,40)  # 100 - floor(80 * .75)
