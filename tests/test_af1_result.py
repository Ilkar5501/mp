import unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from PIL import Image
from hiq.af1_result import af1_embed
from hiq.battle_render import render_af1_summary
from hiq.event_modes import cpu_specs,AllForOneEngine


class AF1ResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_winner_trophy_main_only_stats_marks_and_no_awards(self):
        a=cpu_specs('all_for_one',0);b=cpu_specs('all_for_one',1)
        result=AllForOneEngine(a,b,seed=1).run()
        for winner in ('A','B','draw'):
            result.winner_side=winner
            embed=af1_embed(result,a,b,'Player','CPU',1)
            fields=embed.fields
            self.assertEqual(sum('🏆' in f.name for f in fields),0 if winner=='draw' else 1)
            if winner!='draw':self.assertTrue(fields[0 if winner=='A' else 1].name.startswith('🏆'))
            self.assertEqual([f.name for f in fields[2:]],['Turns','AF1 Marks earned'])
            text=str(embed.to_dict())
            self.assertNotIn('OPOM',text);self.assertNotIn('DPOM',text);self.assertNotIn('Coins earned',text)
            for f in fields[:2]:self.assertIn('Dealt',f.value);self.assertIn('Tanked',f.value);self.assertIn('Mitigated',f.value)

    async def test_image_contains_fourteen_icons_and_support_grid(self):
        a=cpu_specs('all_for_one',0);b=cpu_specs('all_for_one',1)
        result=AllForOneEngine(a,b,seed=1).run()
        with patch('hiq.battle_render._fetch',new=AsyncMock(return_value=Image.new('RGB',(80,80),(200,80,160)))) as fetch:
            output=await render_af1_summary(result,a,b,'Player','CPU',1)
        self.assertEqual(fetch.await_count,14)
        image=Image.open(output);self.assertEqual(image.size,(1440,760))
        for origin in (55,755):
            for index in range(6):
                self.assertEqual(image.getpixel((origin+(index%3)*207+20,382+(index//3)*146+20)),(200,80,160))

    async def test_missing_supports_and_failed_icons_still_render(self):
        a=cpu_specs('all_for_one',0)[:1];b=cpu_specs('all_for_one',1)
        result=AllForOneEngine(a,b,seed=1).run()
        with patch('hiq.battle_render._fetch',new=AsyncMock(return_value=None)):
            output=await render_af1_summary(result,a,b,'Player','CPU',0)
        self.assertEqual(Image.open(output).size,(1440,760))
