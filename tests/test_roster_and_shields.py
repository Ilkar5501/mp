import unittest
from unittest.mock import Mock,patch
from test_event_modes import specs
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.battle.roster_update import WALL,KUNIMI

def duel(own,foe='Kai',*,item=None,foe_item=None):
    engine=BattleEngine(specs([own],item=item),specs([foe],item=foe_item),seed=1)
    a=engine.team_a.units[0];d=engine.team_b.units[0];ctx=CombatContext(engine,a,d,a,d)
    engine.effect_context=ctx;engine.in_post_combat=False
    return engine,a,d,ctx

class RosterAndShieldTests(unittest.TestCase):
    def test_great_wall_a_grants_ten_percent_and_caps_both_flat_reduction_branches(self):
        e,a,d,ctx=duel('Aran',WALL)
        mods={'pow':0,'qik':0}
        e.roster_mods(ctx,d,{'def':50,'rec':25},mods)
        self.assertEqual(mods,{'pow':5,'qik':5})
        for other in (0,5):self.assertEqual(e.roster_flat_reduction(ctx,d,{'def':100,'rec':25},other),8)

    def test_great_wall_c_heals_two_per_mb_in_three_six_and_nine_unit_teams(self):
        from hiq.data import CHARACTERS,CHAR_BY_ID
        middle=[c for c in CHARACTERS if c.position=='MB' and c.id!=WALL]
        for size,amount in ((3,6),(6,12),(9,18)):
            own=[(None,CHAR_BY_ID[WALL],'front')]+[(None,c,'front') for c in middle[:size-1]]
            e=BattleEngine(own,specs(['Kai']),seed=1);e.team_a.hp=1
            e.roster_end_turn();self.assertEqual(e.team_a.hp,1+amount)
    def test_great_wall_af1_support_uses_new_c_instead_of_old_hirugami_c(self):
        from hiq.event_modes import AllForOneEngine
        e=AllForOneEngine(specs(['Lev',WALL]),specs(['Kai']),seed=1)
        main,wall=e.team_a.units;e.turn=1;e.support_turn(wall)
        self.assertEqual(main.visible_bonus('qik'),2)
        self.assertEqual(main.visible_bonus('def'),2)
        self.assertEqual(main.visible_bonus('spd'),2)
    def test_kunimi_lazy_tip_adds_ten_after_the_fifty_percent_multiplier(self):
        for full,expected in ((False,150),(True,160)):
            e,a,d,ctx=duel(KUNIMI,'Ikejiri — Old Friend');a.cd=0;d.cd=99
            if full:ctx.temp_flags['kunimi_full_power']=3
            stats={a.uid:{'pow':100,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':1,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0}}
            e.rng=Mock(random=Mock(return_value=.999));e.team_b.hp=e.team_b.max_hp=1000
            with patch.object(e,'combat_stats',return_value=stats):e.resolve_hit(ctx,a,d)
            self.assertEqual(a.damage_dealt,expected)

    def test_start_and_end_turn_great_wall_support(self):
        e=BattleEngine(specs([WALL,'Aran','Lev']),specs(['Kai']),seed=1)
        wall,aran,lev=e.team_a.units;aran.lane='front';aran.base.update({'def':40,'rec':20})
        e.turn=1;e.roster_turn_start(wall)
        self.assertEqual(aran.visible_bonus('spd'),2);self.assertEqual(aran.visible_bonus('pow'),0)
        self.assertEqual(lev.visible_bonus('pow'),2);self.assertEqual(lev.visible_bonus('def'),2)
        before=e.team_a.hp=e.team_a.max_hp-30;e.roster_end_turn()
        self.assertEqual(e.team_a.hp,before+4)

    def test_kunimi_buffs_aoba_allies_instead_of_refunding_cooldown(self):
        e=BattleEngine(specs([KUNIMI,'Oikawa']),specs(['Kai']),seed=1)
        unit,ally=e.team_a.units;foe=e.team_b.units[0];ctx=CombatContext(e,unit,foe,unit,foe)
        ctx.temp_flags['kunimi_full_power']=3;unit.cd=3;e.roster_after_combat(ctx)
        self.assertEqual(ally.visible_bonus('pow'),4);self.assertEqual(ally.visible_bonus('spd'),4)
        self.assertEqual(unit.cd,3)
    def test_kunimi_reserves_are_combat_penalties_then_consume_for_full_power(self):
        e,a,d,ctx=duel(KUNIMI);e.turn=1;e.roster_turn_start(a)
        self.assertEqual(e.expansion_memory[a.uid]['reserve'],2)
        self.assertFalse(a.visible_debuffs)
        e.roster_combat_start(ctx);self.assertNotIn('kunimi_full_power',ctx.temp_flags)
        self.assertEqual(e.combat_stats(ctx)[a.uid]['spd'],a.noncombat_stat('spd')-2)
        e.turn=2;e.roster_turn_start(a);e.roster_combat_start(ctx)
        self.assertEqual(ctx.temp_flags['kunimi_full_power'],3)
        self.assertEqual(e.expansion_memory[a.uid]['reserve'],0)
        self.assertEqual(e.combat_stats(ctx)[a.uid]['pow'],a.noncombat_stat('pow')+9)
        self.assertFalse(e.can_counter(ctx,d,a))
        a.cd=3;e.roster_after_combat(ctx);self.assertEqual(a.cd,2)

    def test_kunimi_low_hp_activates_with_two_stacks_but_does_not_prevent_counter(self):
        e,a,d,ctx=duel(KUNIMI);e.turn=1;e.roster_turn_start(a)
        e.team_a.hp=e.team_a.max_hp//2;e.roster_combat_start(ctx)
        self.assertEqual(ctx.temp_flags['kunimi_full_power'],2)
        self.assertTrue(e.can_counter(ctx,d,a))
        self.assertEqual(e.combat_stats(ctx)[a.uid]['pow'],a.noncombat_stat('pow')+6)

    def test_great_wall_ultimate_reduction_reflection_and_stuff_block(self):
        e,a,d,ctx=duel('Aran',WALL)
        a.cd=99;d.cd=0;e.rng=Mock(random=Mock(return_value=.999))
        stats={a.uid:{'pow':100,'qik':0,'def':0,'rec':0,'spd':0,'jmp':0},d.uid:{'pow':48,'qik':0,'def':40,'rec':25,'spd':0,'jmp':0}}
        with patch.object(e,'combat_stats',return_value=stats),patch.object(e,'expansion_flat_reduction',return_value=0):e.resolve_hit(ctx,a,d)
        self.assertEqual(a.damage_dealt,28)  # (100-40)*.6 - 8
        self.assertEqual(ctx.temp_flags['wall_prevented'][d.uid],24)
        e.team_a.hp=e.team_a.max_hp=1000;a.attacks_this_combat=1
        d.add_debuff('def',-7)
        e.roster_after_combat(ctx)
        self.assertEqual(e.team_a.hp,975)  # 20 reflected + 5 Stuff Block
        self.assertIn('Heal Block',a.statuses);self.assertFalse(d.visible_debuffs)

    def test_great_wall_flat_reduction_halves_with_another_flat_source(self):
        e,a,d,ctx=duel('Aran',WALL)
        stats={'def':40,'rec':25}
        self.assertEqual(e.roster_flat_reduction(ctx,d,stats,0),8)
        self.assertEqual(e.roster_flat_reduction(ctx,d,stats,5),4)
        d.lane='back';self.assertEqual(e.roster_flat_reduction(ctx,d,stats,0),0)

    def test_great_wall_does_not_inherit_old_hirugami_universal_counter(self):
        e,a,d,ctx=duel('Semi',WALL)
        self.assertFalse(e.can_counter(ctx,d,a))

    def test_fish_cakes_blocks_postcombat_foe_effects_but_not_own_combat_or_allies(self):
        e,a,d,ctx=duel('Aran',item='fish_cakes')
        e.effect_source=d;a.add_debuff('spd',-4);self.assertEqual(a.visible_penalty('spd'),-4)
        a.clear_debuffs();e.in_post_combat=True
        a.add_debuff('spd',-7);a.add_status('Heal Block',False,source=(d.uid,'B'))
        self.assertFalse(a.visible_debuffs);self.assertNotIn('Heal Block',a.statuses)
        e.effect_source=a;a.add_debuff('spd',-2);self.assertEqual(a.visible_penalty('spd'),-2)
        before=e.team_a.hp
        self.assertFalse(e.postcombat_damage(ctx,e.team_a,15,source=d));self.assertEqual(e.team_a.hp,before)
        self.assertTrue(e.postcombat_damage(ctx,e.team_a,6,source=a));self.assertEqual(e.team_a.hp,before-6)

    def test_chocolate_milk_blocks_enemy_cd_penalties_for_same_row_only(self):
        e,a,d,ctx=duel('Aran',item='chocolate_milk');a.cd=0
        self.assertFalse(e.inflict_cooldown(d,a));self.assertEqual(a.cd,0)
        self.assertTrue(e.inflict_cooldown(a,a));self.assertEqual(a.cd,1)

    def test_fish_cakes_does_not_protect_another_units_unrelated_combat(self):
        e=BattleEngine(specs(['Aran','Suga'],item='fish_cakes'),specs(['Kai']),seed=1)
        a,ally=e.team_a.units;d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
        e.effect_context=ctx;e.in_post_combat=True
        from dataclasses import replace
        a.card=replace(a.card,equipment_key=None)
        before=e.team_a.hp
        self.assertTrue(e.postcombat_damage(ctx,e.team_a,10,source=d));self.assertEqual(e.team_a.hp,before-10)
