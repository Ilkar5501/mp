import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from hiq.battle.engine import BattleEngine
from hiq.battle.types import CombatContext
from hiq.data import CHARACTERS


def duel(name,foe='Kai'):
    own=next(c for c in CHARACTERS if c.name==name)
    other=next(c for c in CHARACTERS if c.name==foe)
    e=BattleEngine([(None,own,'front')],[(None,other,'front')],seed=1)
    a=e.team_a.units[0];d=e.team_b.units[0]
    return e,a,d,CombatContext(e,a,d,a,d)


class DaishoTakeruTests(unittest.TestCase):
    def damage(self,foe='Hinata — Miracle Maker',*,inspiration=False,ultimate=False,item=None,perfect=False,flat=0,foe_spd=10):
        from test_event_modes import specs
        e=BattleEngine(specs(['Takeru']),specs([foe],item=item),seed=1)
        a=e.team_a.units[0];d=e.team_b.units[0];ctx=CombatContext(e,a,d,a,d)
        a.cd=99;d.cd=0 if ultimate else 99
        if inspiration:a.add_status('Inspiration',True)
        if perfect:a.add_status('Perfect Window',True)
        ctx.temp_flags.update(gear_previous_hit=(a.uid,d.uid),gear_hit_count=1)
        stats={a.uid:dict(pow=0,qik=100,spd=0,jmp=0,rec=0),d.uid:dict(pow=0,qik=100,spd=foe_spd,jmp=0,rec=0)}
        for row in stats.values():row['def']=0
        e.rng=Mock(random=Mock(return_value=.999));e.team_b.hp=e.team_b.max_hp=10000
        with patch.object(e,'combat_stats',return_value=stats),patch.object(e,'expansion_flat_reduction',return_value=flat):e.resolve_hit(ctx,a,d)
        return a.damage_dealt

    def test_inspiration_halves_skill_reduction_but_preserves_ultimate_reduction(self):
        self.assertEqual(self.damage(),60)
        self.assertEqual(self.damage(inspiration=True),80)
        self.assertEqual(self.damage(ultimate=True),42)
        self.assertEqual(self.damage(inspiration=True,ultimate=True),56)

    def test_inspiration_halves_item_reduction_and_preserves_flat_and_fixed_damage(self):
        self.assertEqual(self.damage('Kai',item='ten_thousand_reps'),70)
        self.assertEqual(self.damage('Kai',item='ten_thousand_reps',inspiration=True),85)
        self.assertEqual(self.damage(ultimate=True,foe_spd=0,flat=7),63)
        self.assertEqual(self.damage(ultimate=True,foe_spd=0,flat=7,inspiration=True),63)
        self.assertEqual(self.damage('Yamagata',ultimate=True,inspiration=True),1)

    def test_inspiration_and_perfect_window_do_not_halve_ultimate_reduction(self):
        self.assertEqual(self.damage(ultimate=True,perfect=True),70)
        self.assertEqual(self.damage(ultimate=True,perfect=True,inspiration=True),70)

    def test_takeru_keeps_original_minus_four_threshold_in_both_directions(self):
        e,a,d,ctx=duel('Takeru');a.cd=0
        for diff,expected in [(4,True),(5,False)]:
            stats={a.uid:{'spd':40},d.uid:{'spd':40+diff}}
            with patch.object(e,'combat_stats',return_value=stats):
                self.assertEqual(e.offensive_ult_ready(a,d,ctx),expected)
                self.assertEqual(e.defensive_ult_ready(a,d,ctx),expected)

    def test_bento_inspiration_does_not_count_as_an_ally_character_skill(self):
        from test_event_modes import specs
        e=BattleEngine(specs(['Takeru','Bokuto'],item='takeru_zunda_mochi_bento'),specs(['Kai']),seed=1)
        source,ally=e.team_a.units;d=e.team_b.units[0]
        e.equipment_ultimate(source);ctx=CombatContext(e,ally,d,ally,d)
        self.assertNotIn((source.uid,'Item'),e.ally_skill_impacts(ctx,ally))

    def test_bento_reduces_max_cooldown_once_and_grants_stats_and_timed_status(self):
        from test_event_modes import specs
        a_specs=specs(['Takeru','Daichi'],item='takeru_zunda_mochi_bento')
        e=BattleEngine(a_specs,specs(['Kai']),seed=1)
        a,ally=e.team_a.units;d=e.team_b.units[0]
        e.equipment_match_start();self.assertEqual((a.max_cd,a.cd),(2,2))
        e.equipment_match_start();self.assertEqual(a.max_cd,2)
        ctx=CombatContext(e,a,d,a,d);mods={k:0 for k in ('pow','qik','def','rec','spd','jmp')}
        e.equipment_mods(ctx,a,d,True,mods)
        self.assertEqual((mods['qik'],mods['spd']),(5,5))
        e.equipment_ultimate(a)
        for unit in (a,ally):
            self.assertIn('Inspiration',unit.statuses)
            unit.actions_taken+=1;unit.expire_after_action()
            self.assertNotIn('Inspiration',unit.statuses)

    def test_daisho_ultimate_has_no_speed_gate_and_followup_requires_visible_penalty(self):
        for penalty in ('none','visible','status'):
            e,a,d,ctx=duel('Daisho');a.cd=0;a.base['spd']=0;d.base['spd']=200
            self.assertTrue(e.offensive_ult_ready(a,d,ctx))
            if penalty=='visible':d.add_debuff('jmp',-5)
            if penalty=='status':d.add_status('Free Ball',False)
            e.team_b.hp=e.team_b.max_hp=10000;e.rng=Mock(random=Mock(return_value=.999))
            e.resolve_hit(ctx,a,d)
            self.assertTrue(a.ult_triggered_this_combat)
            self.assertEqual(bool(ctx.temp_flags.get(f'{a.uid}:daisho_followup')),penalty=='visible')
            if penalty=='visible':
                d.visible_debuffs.clear()
                self.assertTrue(ctx.temp_flags[f'{a.uid}:daisho_followup'])

    def test_daisho_ultimate_retains_fifteen_damage_bonus(self):
        damage=[]
        for ready in (False,True):
            e,a,d,ctx=duel('Daisho');a.cd=0 if ready else 3
            stats={a.uid:dict(pow=100,qik=0,spd=10,jmp=0,rec=0),d.uid:dict(pow=0,qik=0,spd=100,jmp=0,rec=20)}
            for row in stats.values():row['def']=20
            e.rng=Mock(random=Mock(return_value=.999));e.team_b.hp=e.team_b.max_hp=10000
            with patch.object(e,'combat_stats',return_value=stats):e.resolve_hit(ctx,a,d)
            damage.append(a.damage_dealt)
        self.assertEqual(damage[1]-damage[0],15)

    def test_rally_surge_restores_twenty_after_ultimate(self):
        e,a,d,ctx=duel('Takeru');e.team_a.hp=50
        a.ult_triggered_this_combat=True;e.after_combat(ctx)
        self.assertEqual(e.team_a.hp,70)


class BentoRecipeTests(unittest.IsolatedAsyncioTestCase):
    async def test_recipe_equipment_restriction_and_recycle_value(self):
        from hiq.database import Database
        from hiq.community_store import CommunityStore
        from hiq.raid_store import RaidStore
        from hiq.shard_balances import credit,balance
        from hiq.data import CHAR_BY_ID
        from hiq.items import ITEM_BY_KEY,can_equip
        from hiq.event_modes import EVENT_TEAMS,cpu_specs,TinyTitansEngine
        key='takeru_zunda_mochi_bento';item=ITEM_BY_KEY[key]
        takeru=CHAR_BY_ID['Takeru — Flashing Salmon'];daisho=CHAR_BY_ID['Daisho — Serpent of Chaos']
        self.assertTrue(can_equip(item,takeru));self.assertFalse(can_equip(item,daisho))
        challenge=next(i for i,c in enumerate(EVENT_TEAMS['tiny_titans']) if c['signature_key']==key)
        team=cpu_specs('tiny_titans',challenge)
        self.assertEqual(len(team),6);self.assertIn(takeru.id,{c.id for _,c,_ in team})
        self.assertIn(TinyTitansEngine(team,team,seed=1).run().winner_side,('A','B','draw'))
        with tempfile.TemporaryDirectory() as temp:
            db=Database(Path(temp)/'game.sqlite3');await db.init();await db.ensure_user(1)
            store=CommunityStore(db);raid=RaidStore(db)
            async with store.tx() as conn:
                await conn.execute('UPDATE users SET coins=500 WHERE discord_id=1')
                await credit(conn,1,'item_shard',key,25);await credit(conn,1,'character_shard',takeru.id,25)
                a=await store._issue_card(conn,1,'Player',takeru);b=await store._issue_card(conn,1,'Player',daisho)
            iid=await raid.craft(1,'Player',key)
            self.assertEqual((await db.get_user(1)).coins,0)
            async with db.connect() as conn:
                self.assertEqual(await balance(conn,1,'item_shard',key),0)
                self.assertEqual(await balance(conn,1,'character_shard',takeru.id),0)
            with self.assertRaises(ValueError):await raid.equip(1,b.instance_id,iid)
            equipped=await raid.equip(1,a.instance_id,iid)
            e=BattleEngine([(equipped,takeru,'front')],[(None,daisho,'front')],seed=1)
            e.apply_match_start();self.assertEqual(e.team_a.units[0].max_cd,2)
            await raid.equip(1,a.instance_id,None)
            self.assertEqual((await raid.recycle_equipment(1,item_id=iid))['clan_coins'],25)
