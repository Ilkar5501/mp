import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hiq.database import Database
from hiq.items import ITEM_BY_KEY,can_equip
from hiq.tournament_cpu import CPU_TEAMS,CPU_EQUIPMENT,CPU_NATURES,cpu_specs


class TournamentPresetTests(unittest.TestCase):
    def test_new_team_has_exact_requested_builds(self):
        class Fixed:
            def choice(self,teams):return teams[-1]
        specs=cpu_specs('test',1,rng=Fixed())
        self.assertEqual([c.name for _,c,_ in specs],['Echigo','Hinata','Akifumi','Atsumu','Kageyama','Teradomari'])
        self.assertEqual([card.merge_level for card,_,_ in specs],[0,0,0,0,0,10])
        self.assertEqual([(card.boon_stat,card.bane_stat) for card,_,_ in specs],list(CPU_NATURES))
        self.assertEqual([card.equipment_key for card,_,_ in specs],list(CPU_EQUIPMENT[CPU_TEAMS[-1]]))
        self.assertEqual([lane for _,_,lane in specs],['front']*3+['back']*3)
        self.assertEqual(specs[1][1].title,'Wings of Dawn');self.assertEqual(specs[4][1].title,'Crown of Precision')
        for card,char,_ in specs:self.assertTrue(can_equip(ITEM_BY_KEY[card.equipment_key],char))
    def test_previous_last_hope_team_keeps_its_variant_and_equipment(self):
        class Fixed:
            def choice(self,teams):return teams[10]
        specs=cpu_specs('test',1,rng=Fixed())
        self.assertEqual(next(c.title for _,c,_ in specs if c.name=='Kiryu'),'Last Hope')
        self.assertTrue(all(card.merge_level==0 and card.boon_stat is None for card,_,_ in specs))
        self.assertEqual([card.equipment_key for card,_,_ in specs],list(CPU_EQUIPMENT[CPU_TEAMS[10]]))


class TeamRenameTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiword_rename_preserves_lineup_and_owner(self):
        from hiq.cogs.teams import TeamsCog
        with tempfile.TemporaryDirectory() as temp:
            db=Database(Path(temp)/'teams.sqlite3');await db.init()
            await db.ensure_saved_team(1,2,'Old');await db.ensure_saved_team(2,2,'Other')
            cog=TeamsCog(SimpleNamespace(db=db));ctx=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
            await TeamsCog.teamedit.callback(cog,ctx,2,name='New Team Name')
            self.assertEqual((await db.get_saved_team(1,2)).name,'New Team Name')
            self.assertEqual((await db.get_saved_team(2,2)).name,'Other')
            self.assertEqual((await db.get_saved_team(1,2)).card_ids,(None,None,None))
            await TeamsCog.teamedit.callback(cog,ctx,2,name=' ')
            self.assertEqual((await db.get_saved_team(1,2)).name,'New Team Name')
            self.assertIn('1–40',ctx.send.call_args.args[0])
