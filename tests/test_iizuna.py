"""Iizuna timing, targeting and healing regressions; PvP reward boundaries."""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import test_v11
from hiq.data import CHARACTERS
from hiq.database import Database
from hiq.community_store import CommunityStore
from hiq.rewards import RewardStore
from hiq.prefecture import PodBattleEngine
from hiq.battle.engine import BattleEngine

IIZUNA=next(c for c in CHARACTERS if c.name=='Iizuna')


class IizunaTests(unittest.TestCase):
    def engine(self, side='A', index=0):
        e,c=test_v11.PureTests().gear_engine()
        u=e.teams[side].units[index]
        u.character=IIZUNA;u.base.update(pow=0,qik=60);u.max_cd=u.cd=4
        e.effect_context=c
        return e,c,u

    def test_catalogue(self):
        self.assertEqual((IIZUNA.position,IIZUNA.school,IIZUNA.rarity,IIZUNA.ult_cd),('S','Itachiyama',5,4))
        self.assertEqual(IIZUNA.bst,342)
        self.assertEqual(IIZUNA.abilities['U'].name,'Regal Rally')

    def test_initiating_stats_and_penalty_neutralization(self):
        for side in ('A','B'):
            e,c,u=self.engine(side)
            for stat in ('qik','def','rec','spd'):u.add_debuff(stat,-7)
            stats=e.combat_stats(c)[u.uid]
            for stat in ('qik','def','rec'):
                self.assertEqual(stats[stat],u.base[stat]+(4 if side=='A' else -7))
            self.assertEqual(stats['spd'],u.base['spd']-7)

    def test_ultimate_adds_eighty_percent_rec_and_marks_highest_jump_after_hit(self):
        damages=[]
        for ready in (False,True):
            e,c,u=self.engine();u.cd=0 if ready else 4
            target=e.team_b.units[-1];target.base['jmp']=100
            rec=e.combat_stats(c)[u.uid]['rec']
            e.resolve_hit(c,u,c.defender)
            damages.append(u.damage_dealt)
            self.assertEqual('Guiding Break' in target.statuses,ready)
        self.assertEqual(damages[1]-damages[0],int(rec*.8))

    def test_ultimate_qik_matchup_does_not_mark(self):
        e,c,u=self.engine();u.cd=0
        c.defender.character=replace(c.defender.character,stats={**c.defender.character.stats,'pow':0,'qik':60})
        e.resolve_hit(c,u,c.defender)
        self.assertEqual(e.guiding_break_count(e.team_b),0)

    def test_ultimate_does_not_fall_back_when_highest_jump_is_marked(self):
        e,c,u=self.engine();target=e.team_b.units[-1];target.base['jmp']=100
        e.inflict_guiding_break(u,target)
        e.anchor_ultimate_result(c,u,c.defender,True)
        self.assertEqual(e.guiding_break_count(e.team_b),1)

    def test_two_mark_cap_and_cleansing(self):
        e,c,u=self.engine();a,b,d=e.team_b.units[:3]
        self.assertTrue(e.inflict_guiding_break(u,a));self.assertTrue(e.inflict_guiding_break(u,b))
        self.assertFalse(e.inflict_guiding_break(u,d));self.assertFalse(e.inflict_guiding_break(u,a))
        a.clear_debuffs();self.assertTrue(e.inflict_guiding_break(u,d))
        b.actions_taken+=1;b.expire_after_action();self.assertNotIn('Guiding Break',b.statuses)

    def test_defensive_crit_marks_immediately_and_boosts_same_hit(self):
        damages=[]
        for support in (False,True):
            e,c,u=self.engine('A',5)
            if not support:u.character=replace(u.character,name='NoSupport')
            c.attacker.base['rec']=100;c.defender.base['jmp']=100
            e.rng=Mock(random=Mock(return_value=0))
            e.resolve_hit(c,c.attacker,c.defender)
            damages.append(c.attacker.damage_dealt)
            self.assertEqual('Guiding Break' in c.defender.statuses,support)
        self.assertEqual(damages[1]-damages[0],10)

    def test_mark_damage_is_attacking_units_rec_and_only_on_initiation(self):
        e,c,u=self.engine('A',5)
        c.attacker.base['rec']=100
        e.inflict_guiding_break(u,e.team_b.units[1]);e.inflict_guiding_break(u,e.team_b.units[2])
        self.assertEqual(e.anchor_damage_bonus(c,c.attacker,e.combat_stats(c)),20)
        self.assertEqual(e.anchor_damage_bonus(c,c.defender,e.combat_stats(c)),0)

    def test_fukunaga_suppresses_external_anchor_support(self):
        e,c,u=self.engine('A',5);c.defender.character=replace(c.defender.character,name='Fukunaga')
        e.anchor_critical(c,c.attacker,c.defender,True)
        self.assertEqual(e.guiding_break_count(e.team_b),0)

    def test_receive_flat_reduction_after_followup_percentage(self):
        amounts=[]
        for followup in (False,True):
            e,c,u=self.engine('B',5);c.defender.base['rec']=40
            stats=e.combat_stats(c);d=stats[c.defender.uid];a=stats[c.attacker.uid]
            raw=a['pow']-d['def'];flat=int(d['rec']*.15)
            e.anchor_combat_start(c)
            self.assertIn('Clean Receive',c.defender.statuses)
            e.resolve_hit(c,c.attacker,c.defender,followup=followup)
            self.assertEqual(c.attacker.damage_dealt,int(raw*(.7 if followup else 1))-flat)
            amounts.append(c.attacker.damage_dealt)
        self.assertLess(amounts[1],amounts[0])

    def test_equal_rec_does_not_grant_receive(self):
        e,c,u=self.engine('B',5)
        e.anchor_combat_start(c)
        self.assertNotIn('Clean Receive',c.defender.statuses)

    def test_clean_receive_is_bonus_and_denies_both_combat_healing_phases(self):
        e,c,u=self.engine('B',5);c.defender.base['rec']=40;e.anchor_combat_start(c)
        self.assertTrue(c.defender.statuses['Clean Receive'].positive)
        self.assertIn(('named','Clean Receive'),e.distinct_bonuses(c,c.defender,e.combat_stats(c)))
        e.team_a.hp-=100;e.team_b.hp-=100
        for post in (False,True):
            e.in_post_combat=post;before=e.team_a.hp;e.heal_team(c.attacker,20)
            self.assertEqual(e.team_a.hp,before)
        before=e.team_b.hp;e.heal_team(c.defender,20);self.assertEqual(e.team_b.hp,before+20)

    def test_clean_receive_survives_next_action_aftercombat_then_expires(self):
        e,c=test_v11.PureTests().gear_engine(right_key='manager_s_good_luck_charm')
        c.attacker.add_status('Clean Receive',True,through_next_action=True)
        e.team_b.hp-=100
        e.choose_target=lambda actor:(c.defender,False)
        e.perform_action(c.attacker)
        self.assertFalse(any('restores' in line for line in e.log))
        self.assertNotIn('Clean Receive',c.attacker.statuses)
        before=e.team_b.hp;e.heal_team(c.defender,10);self.assertEqual(e.team_b.hp,before+10)

    def test_cooldown_refund_when_attacking_or_defending_once(self):
        for side in ('A','B'):
            e,c,u=self.engine(side)
            for foe in e.enemy_team(u).units[:2]:e.inflict_guiding_break(u,foe)
            e._new_unit_after_combat(c);self.assertEqual(u.cd,3)
            e._new_unit_after_combat(c);self.assertEqual(u.cd,3)

    def test_marked_units_cannot_be_targets_or_saviors_in_all_formats(self):
        for size in (3,6,9):
            chars=list({c.name:c for c in CHARACTERS}.values())[:size]
            specs=[(None,c,'front' if i%3==0 else 'back') for i,c in enumerate(chars)]
            e=(PodBattleEngine if size==9 else BattleEngine)(specs,specs,seed=1)
            actor=e.team_a.units[0];target=e.team_b.units[0]
            actor.character=replace(actor.character,name='Plain')
            e.inflict_guiding_break(actor,target)
            distribution=e.targeting_distribution(e.team_b,actor.offense_key)
            self.assertNotIn(target.uid,distribution)
            self.assertAlmostEqual(sum(distribution.values()),100)
            for _ in range(30):self.assertIsNot(e.choose_target(actor)[0],target)
            for unit in e.team_b.units:unit.cd=99
            target.character=replace(target.character,name='Hirugami');target.cd=0
            self.assertIsNot(e.maybe_save(actor,e.team_b.units[1])[0],target)


class PvpRewardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'audit.sqlite3')
        await self.db.init();self.rewards=RewardStore(self.db)

    async def asyncTearDown(self):self.temp.cleanup()

    async def test_four_unique_wins_four_hundred_total_repeat_zero_and_daily_reset(self):
        amounts=[]
        for opponent in range(2,8):amounts.append((await self.rewards.award(1,'pvp',opponent=opponent,day='2026-10-29'))['coins'])
        self.assertEqual(amounts,[100]*4+[0,0])
        self.assertEqual((await self.rewards.award(1,'pvp',opponent=2,day='2026-10-29'))['coins'],0)
        self.assertEqual((await self.rewards.award(1,'pvp',opponent=2,day='2026-10-30'))['coins'],100)

    async def test_concurrent_wins_cannot_exceed_four_hundred(self):
        amounts=await asyncio.gather(*(self.rewards.award(1,'pvp',opponent=uid) for uid in range(2,10)))
        self.assertEqual(sum(row['coins'] for row in amounts),400)

    async def test_legacy_completed_daily_cap_does_not_pay_again(self):
        await self.db.ensure_user(1)
        async with CommunityStore(self.db).tx() as c:
            await c.execute("INSERT INTO economy_daily VALUES (1,'2026-10-29','pvp',8,400,0)")
        self.assertEqual((await self.rewards.award(1,'pvp',opponent=2,day='2026-10-29'))['coins'],0)
