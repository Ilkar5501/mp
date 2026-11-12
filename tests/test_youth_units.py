import random,unittest
from datetime import datetime,timezone
from unittest.mock import Mock,patch
from hiq.data import CHAR_BY_ID,CHARACTERS
from hiq.models import CardInstance
from hiq.battle.engine import BattleEngine,has
from hiq.battle.types import CombatContext
from hiq.economy import pull_pack
from hiq.youth_rewards import OIKAWA,KAGEYAMA,HINATA,DUO,loss_reward

IKEJIRI='Ikejiri — Old Friend'
NEW=(IKEJIRI,HINATA,OIKAWA,KAGEYAMA,DUO)


def specs(*ids,merge=0):
    out=[]
    for i,cid in enumerate(ids):
        char=CHAR_BY_ID[cid]
        card=CardInstance(f'unit-{i}-{cid}',cid,char.card_id,cid,1,1,'Player',i,merge,datetime.now(timezone.utc))
        out.append((card,char,'front' if i==0 else 'back'))
    return out


def engine(*ids,foe='Kai — Solid Receive'):
    # Resolve the legacy opponent by name to keep its canonical title.
    foe=next(c.id for c in CHARACTERS if c.name=='Kai') if foe=='Kai — Solid Receive' else foe
    e=BattleEngine(specs(*ids),specs(foe),seed=1)
    a=e.team_a.units[0];d=e.team_b.units[0]
    return e,a,d,CombatContext(e,a,d,a,d)


class YouthCombatTests(unittest.TestCase):
    def test_component_name_searches_include_duo_without_broadening_exact_variant_queries(self):
        from hiq.search import filter_characters,filter_cards,parse_filters
        for name in ('Oikawa','Kageyama'):
            filters=parse_filters(name,gallery=True)
            results=filter_characters(list(CHARACTERS),filters)
            self.assertIn(DUO,{c.id for c in results})
            cards=[card for card,char,lane in specs(OIKAWA,KAGEYAMA,DUO)]
            found=filter_cards(cards,CHAR_BY_ID,parse_filters(name))
            self.assertIn(DUO,{c.character_id for c in found})
        exact=filter_characters(list(CHARACTERS),parse_filters(OIKAWA,gallery=True))
        self.assertEqual([c.id for c in exact],[OIKAWA])

    def test_all_five_have_real_stats_art_and_complete_skills_and_can_fight(self):
        self.assertEqual(len(CHARACTERS),72)
        for cid in NEW:
            with self.subTest(card=cid):
                c=CHAR_BY_ID[cid]
                self.assertTrue(c.card_art and c.character_icon and c.school and c.role_class)
                self.assertEqual(set(c.abilities),set('UABC'))
                e,*_=engine(cid)
                self.assertIn(e.run().winner_side,('A','B','draw'))

    def test_legacy_name_hooks_do_not_give_new_variants_old_skills(self):
        for cid in NEW:
            e,a,d,ctx=engine(cid)
            self.assertFalse(has(a,a.name))
            self.assertTrue(has(a,a.name,a.title))

    def test_ikejiri_memory_expires_on_his_turn_and_no_daichi_heals_even_turn(self):
        daichi=next(c.id for c in CHARACTERS if c.name=='Daichi')
        e,a,d,ctx=engine(IKEJIRI,daichi)
        ally=e.team_a.units[1];e.turn=1;e.youth_turn(a)
        self.assertIn('Fun Memory',ally.statuses)
        e.team_a.hp=e.team_a.max_hp
        defend=CombatContext(e,d,ally,d,ally)
        self.assertTrue(e.followup(defend,ally,d,False))
        e.team_a.units.remove(a)
        self.assertIn('Fun Memory',ally.statuses)
        e.team_a.units.append(a);e.youth_turn(a)
        self.assertEqual(len([s for s in ally.statuses if s=='Fun Memory']),1)
        e,a,d,ctx=engine(IKEJIRI);e.team_a.hp-=50;e.turn=1;e.youth_turn(a)
        self.assertEqual(e.team_a.hp,e.team_a.max_hp-50)
        e.turn=2;e.youth_turn(a);self.assertEqual(e.team_a.hp,e.team_a.max_hp-30)

    def test_new_offensive_ultimates_and_hinata_healing(self):
        for cid,expected in [(IKEJIRI,86),(HINATA,92),(KAGEYAMA,200),(DUO,86)]:
            with self.subTest(card=cid):
                e,a,d,ctx=engine(cid);a.cd=0;e.team_b.hp=e.team_b.max_hp=10000;e.team_a.hp=50
                st={a.uid:dict(pow=100,qik=0,def_=0,rec=0,spd=40,jmp=0),d.uid:dict(pow=0,qik=0,def_=20,rec=20,spd=10,jmp=0)}
                for value in st.values():value['def']=value.pop('def_')
                e.rng=Mock(random=Mock(return_value=.999))
                with patch.object(e,'combat_stats',return_value=st):e.resolve_hit(ctx,a,d)
                self.assertEqual(a.damage_dealt,expected)
                if cid==HINATA:self.assertEqual(e.team_a.hp,80)
                if cid==DUO:self.assertEqual(e.team_a.hp,50+int(expected*.30))

    def test_oikawa_postcombat_ultimate_overwork_and_base_crit(self):
        iw=next(c.id for c in CHARACTERS if c.name=='Iwaizumi')
        e,a,d,ctx=engine(OIKAWA,iw);a.cd=0
        self.assertFalse(e.offensive_ult_ready(a,d,ctx))
        self.assertEqual(e.youth_crit_bonus(a),15)
        for _ in range(5):
            ctx=CombatContext(e,a,d,a,d);e.youth_start_combat(ctx);a.damage_taken+=10;e.youth_after_combat(ctx)
        self.assertEqual(e.youth_memory(a)['overwork'],4)
        ally=e.team_a.units[1]
        self.assertEqual(ally.visible_bonus('pow'),5);self.assertEqual(ally.visible_bonus('qik'),5)
        self.assertEqual(a.visible_bonus('pow'),0)

    def test_demand_each_event_builds_a_stack_and_status_expires_after_action(self):
        e,k,d,ctx=engine(KAGEYAMA,HINATA);ally=e.team_a.units[1]
        for _ in range(12):
            ally.add_status('Demand',True,through_next_action=True)
            ctx=CombatContext(e,ally,d,ally,d);e.youth_start_combat(ctx)
            before=e.team_a.hp;e.youth_after_combat(ctx)
            self.assertEqual(e.team_a.hp,before-6)
        self.assertEqual(e.youth_memory(k)['demand'],10)
        ally.actions_taken+=1;ally.expire_after_action();self.assertNotIn('Demand',ally.statuses)
        ctx=CombatContext(e,d,k,d,k)
        self.assertEqual(e.charge_delta(ctx,k,d,False),2)

    def test_duo_fountain_aftercombat_and_expiration(self):
        e,a,d,ctx=engine(DUO,HINATA);ally=e.team_a.units[1]
        e.youth_start_combat(ctx);a.ult_triggered_this_combat=True;e.youth_after_combat(ctx)
        self.assertIn('Fountain of Youth',ally.statuses);self.assertNotIn('Fountain of Youth',a.statuses)
        ctx=CombatContext(e,ally,d,ally,d);e.youth_start_combat(ctx);e.youth_after_combat(ctx)
        for stat in ('pow','qik','def','rec','spd','jmp'):self.assertEqual(ally.visible_bonus(stat),3)
        ally.actions_taken+=1;ally.expire_after_action();self.assertNotIn('Fountain of Youth',ally.statuses)
        ally.add_status('Fountain of Youth',True);ally.ult_triggered_this_combat=True;e.team_a.hp=50
        ctx=CombatContext(e,ally,d,ally,d);e.youth_start_combat(ctx);e.youth_after_combat(ctx)
        self.assertEqual(e.team_a.hp,50+ally.max_cd*5)

    def test_duo_component_exclusion_allows_legacy_af1_variants(self):
        with self.assertRaisesRegex(ValueError,'Duo'):engine(DUO,OIKAWA)
        engine(OIKAWA,'Oikawa — Grand Conductor')

    def test_hinata_loss_roll_uses_participating_highest_copy(self):
        self.assertTrue(loss_reward(specs(HINATA,merge=10),rng=Mock(random=Mock(return_value=.69))))
        self.assertFalse(loss_reward(specs(HINATA,merge=10),rng=Mock(random=Mock(return_value=.71))))
        self.assertFalse(loss_reward(specs(HINATA,merge=0),rng=Mock(random=Mock(return_value=0))))
        self.assertFalse(loss_reward(specs(OIKAWA),rng=Mock(random=Mock(return_value=0))))

    def test_duo_is_fusion_only_and_four_regular_units_are_in_pack_pools(self):
        pulled=set()
        rng=random.Random(2)
        for _ in range(2000):
            pulled.update(c.id for c in pull_pack('base',rng=rng))
        self.assertTrue(set(NEW[:-1])<=pulled);self.assertNotIn(DUO,pulled)
