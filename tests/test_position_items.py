import unittest
from dataclasses import replace
from hiq.contest import character
from hiq.data import CHARACTERS
from hiq.items import ITEMS,ITEM_BY_KEY,can_equip


class PositionEquipmentTests(unittest.TestCase):
    def test_position_items_accept_every_matching_position_and_no_other(self):
        items=[i for i in ITEMS if 'eligible_positions' in i]
        self.assertEqual(len(items),8)
        for item in items:
            for card in CHARACTERS:
                with self.subTest(item=item['key'],card=card.id):
                    self.assertEqual(can_equip(item,card),card.position in item['eligible_positions'])

    def test_future_names_are_eligible_by_tag(self):
        for key,pos in [('rock_solid_game_plan','S'),('synchronized_attack','MB'),('athletic_tape','MB'),('padded_arm_sleeves','Li')]:
            future=replace(character('Sakusa'),id='Future Card',name='Future Player',position=pos)
            self.assertTrue(can_equip(ITEM_BY_KEY[key],future))

    def test_signature_and_captain_restrictions_remain_specific(self):
        suga=character('Suga')
        self.assertTrue(can_equip(ITEM_BY_KEY['suga_mapo_tofu_bowls'],suga))
        self.assertFalse(can_equip(ITEM_BY_KEY['suga_mapo_tofu_bowls'],replace(suga,id='Suga — Future')))
        self.assertTrue(can_equip(ITEM_BY_KEY['captain_s_armband'],character('Oikawa')))
        self.assertFalse(can_equip(ITEM_BY_KEY['captain_s_armband'],character('Sakusa')))

    def test_sakusa_requested_stats_and_media(self):
        sakusa=character('Sakusa')
        self.assertEqual(sakusa.stats['pow'],50);self.assertEqual(sakusa.stats['bst'],349)
        self.assertTrue(sakusa.character_icon.endswith('9640dd3eb618fd81d0d6f99b67473ab4.jpg'))
        self.assertTrue(sakusa.card_art.endswith('9f0c8755fc8c037fea2fa6ca1ef19c89.jpg'))
