import unittest
from dataclasses import replace
from hiq.battle.engine import BattleEngine
from hiq.event_modes import AllForOneEngine
from hiq.contest import character
from hiq.sealed import team_specs


def specs(names,item=None):
    rows=team_specs('target-logs',1,[character(n).id for n in names])
    if item:rows[0]=(replace(rows[0][0],equipment_key=item),rows[0][1],rows[0][2])
    return rows


class TargetLogTests(unittest.TestCase):
    def engine(self,a,b,**kwargs):
        e=BattleEngine(specs(a),specs(b),name_a='Alice',name_b='Bob',seed=8,**kwargs)
        e.turn=1;return e

    def test_daisho_random_target_matches_actual_penalties(self):
        e=self.engine(['Daisho','Suga','Lev'],['Kai','Aone','Kenma'])
        e.action_start(e.team_a.units[0])
        targets=[u for u in e.team_b.units if 'spd' in u.visible_debuffs]
        self.assertEqual(len(targets),1)
        line=next(s for s in e.log if 'Wrong-Footed' in s)
        self.assertIn('start of action',line);self.assertIn('[Alice] Daisho',line)
        self.assertIn(e._unit_label(targets[0]),line)
        for other in e.team_b.units:
            if other not in targets:self.assertNotIn(e._unit_label(other),line)

    def test_daisho_all_foes_is_explicit_and_lists_each_recipient(self):
        e=self.engine(['Daisho','Suga','Lev'],['Kai','Aone','Kenma'])
        for u in e.team_b.units:u.acted_this_turn=True
        e.action_start(e.team_a.units[0]);line=next(s for s in e.log if 'Wrong-Footed' in s)
        self.assertIn('all foes:',line)
        for u in e.team_b.units:self.assertIn(e._unit_label(u),line)

    def test_allied_team_buff_and_non_triggering_turn(self):
        e=self.engine(['Ushijima','Daichi','Reon'],['Kai','Aone','Kenma'])
        e._turn_start_owner(e.team_a.units[0])
        line=next(s for s in e.log if 'Jmp+6' in s)
        self.assertIn('all allies (including self)',line)
        for u in e.team_a.units:self.assertIn(e._unit_label(u),line)
        e.turn=2;e.log.clear();e._turn_start_owner(e.team_a.units[0])
        self.assertFalse(any('Jmp+6' in s for s in e.log))

    def test_suga_item_targets_match_both_actual_recipients_including_retrigger(self):
        e=BattleEngine(specs(['Suga','Daichi','Reon'],'suga_mapo_tofu_bowls'),specs(['Kai','Aone','Kenma']),seed=4)
        e.turn=1;e._turn_start_owner(e.team_a.units[0],forced=True)
        targets=[u for u in e.team_a.units if 'pow' in u.visible_buffs]
        self.assertEqual(len(targets),2)
        line=next(s for s in e.log if 'Passing the Torch' in s)
        recipients=line.split('→',1)[1]
        for u in targets:self.assertIn(e._unit_label(u),line)
        for u in e.team_a.units:
            if u not in targets:self.assertNotIn(e._unit_label(u),recipients)

    def test_himekawa_separates_penalty_and_bonus_recipients(self):
        e=self.engine(['Himekawa','Daichi','Reon'],['Kai','Aone','Kenma'])
        e._turn_start_owner(e.team_a.units[0])
        negative=next(u for u in e.team_a.units if 'spd' in u.visible_debuffs)
        positive=next(u for u in e.team_a.units if 'spd' in u.visible_buffs)
        penalty=next(s for s in e.log if 'Spd−6' in s);bonus=next(s for s in e.log if 'Spd+6' in s)
        self.assertIn(e._unit_label(negative),penalty);self.assertIn(e._unit_label(positive),bonus)
        self.assertIsNot(negative,positive)

    def test_kenma_and_equipment_name_actual_enemy_target(self):
        e=BattleEngine(specs(['Kenma','Daichi','Reon'],'death_stare'),specs(['Kai','Aone','Lev']),name_a='Alice',name_b='Bob',seed=3)
        e.turn=1;target=max(e.team_b.units,key=lambda u:u.noncombat_stat('jmp'))
        e._turn_start_owner(e.team_a.units[0])
        kenma_target=next(u for u in e.team_b.units if u.visible_debuffs.get('jmp') and u.visible_debuffs['jmp'].value==-7)
        for name,recipient in (('Death Stare',target),('Mouse Trap',kenma_target)):
            line=next(s for s in e.log if name in s)
            self.assertIn(e._unit_label(recipient),line);self.assertIn('start of turn',line)

    def test_af1_support_names_fighter_and_not_inactive_supports(self):
        e=AllForOneEngine(specs(['Reon','Suga','Daichi']),specs(['Aone']),name_a='Alice',name_b='Bob',seed=1)
        e.turn=1;e._turn_start_owner(e.team_a.units[1])
        line=next(s for s in e.log if 'Passing the Torch' in s)
        self.assertIn('[Alice] Suga',line);self.assertIn('[Alice] Reon',line);self.assertNotIn('Daichi',line)

    def test_same_character_on_both_sides_has_owner_labels(self):
        e=self.engine(['Daisho','Suga','Lev'],['Daisho','Suga','Lev'])
        for u in e.team_b.units:u.acted_this_turn=True
        e.action_start(e.team_a.units[0]);line=next(s for s in e.log if 'Wrong-Footed' in s)
        self.assertIn('[Alice] Daisho',line);self.assertIn('[Bob] Daisho',line)

    def test_same_name_variants_have_titles_and_slots(self):
        foes=team_specs('target-logs',2,[character('Hinata','Wings of Dawn').id,character('Hinata','Miracle Maker').id,character('Kenma').id])
        e=BattleEngine(specs(['Daisho','Suga','Lev']),foes,name_a='Alice',name_b='Bob',seed=2)
        e.turn=1
        for u in e.team_b.units:u.acted_this_turn=True
        e.action_start(e.team_a.units[0]);line=next(s for s in e.log if 'Wrong-Footed' in s)
        self.assertIn('Wings of Dawn (slot 1)',line);self.assertIn('Miracle Maker (slot 2)',line)
