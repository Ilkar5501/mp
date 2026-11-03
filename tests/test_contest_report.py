import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from hiq.contest import ContestEngine,character
from hiq.sealed import team_specs
from hiq.cogs.contest import ContestResultView,ContestCog,damage_report_embed


def engine(index=7):
    chars=[character(n,t) for n,t in [('Bokuto',None),('Atsumu',None),('Himekawa',None),('Kenma',None),('Akifumi',None),('Kageyama','Crown of Precision')]]
    return ContestEngine(team_specs('report',1,[c.id for c in chars]),index,seed=5)


class DamageAccountingTests(unittest.TestCase):
    def test_support_damage_is_misc_and_survives_result_serialization(self):
        from dataclasses import asdict
        e=engine();source=next(u for u in e.supports if u.name=='Futakuchi')
        before=e.team_a.hp;e.turn=1;e.support_turn(source)
        self.assertEqual(before-e.team_a.hp,20)
        self.assertEqual(e.misc['B']['damage_dealt'],20)
        self.assertEqual(source.damage_dealt,0)
        result=e.run();saved=asdict(result)
        self.assertGreaterEqual(saved['stats']['B:misc']['damage_dealt'],20)
        embed=damage_report_embed(saved['stats'],'Player','Bot')
        self.assertEqual(len(embed.fields),2)
        self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
        self.assertLessEqual(len(embed),6000)
        self.assertNotIn('Futakuchi',embed.fields[1].value)
        self.assertIn('Misc. sources',embed.fields[1].value)

    def test_self_damage_is_not_credited_to_opponent(self):
        e=engine();u=e.team_a.units[0]
        e.damage_team(e.team_a,15,source=u)
        self.assertEqual(e.misc['A']['self_damage_taken'],15)
        self.assertEqual(e.misc['B']['damage_dealt'],0)
        e.damage_team(e.team_b,20,source=u)
        self.assertEqual(e.misc['A']['damage_dealt'],20)
        self.assertEqual(e.misc['B']['damage_taken'],20)

    def test_aliases_are_registered_without_duplicate_cc(self):
        self.assertEqual(ContestCog.clancontest.aliases.count('cc'),1)
        self.assertIn('cb',ContestCog.contestboard.aliases)
        self.assertIn('ccboard',ContestCog.contestboard.aliases)


class PrivateReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_is_ephemeral_and_owner_only(self):
        e=engine();result=e.run()
        view=ContestResultView(42,result.log,result.stats,'Owner','Opponent')
        stranger=SimpleNamespace(user=SimpleNamespace(id=99),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(stranger))
        await view.damage_report.callback(stranger)
        self.assertNotIn('embed',stranger.response.send_message.call_args.kwargs)
        owner=SimpleNamespace(user=SimpleNamespace(id=42),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertTrue(await view.interaction_check(owner))
        await view.damage_report.callback(owner)
        self.assertTrue(owner.response.send_message.call_args.kwargs['ephemeral'])
        self.assertEqual(owner.response.send_message.call_args.kwargs['embed'].title,'Clan Contest — Damage Report')
        self.assertEqual({b.label for b in view.children},{'Damage Report','View Full Battle Log'})
