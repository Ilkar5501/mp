"""Raid rewards remain available when another theme's items are exhausted."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_v11
from hiq.data import CHARACTERS
from hiq.economy import local_date_key,week_key
from hiq.items import ITEMS,THEMES
from hiq.raid_store import RaidStore
from hiq.shard_balances import balance


class RaidThemeCapsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_v11.DatabaseTests.asyncSetUp
    asyncTearDown=test_v11.DatabaseTests.asyncTearDown

    async def seed_caps(self,theme):
        day=local_date_key()
        async with self.store.tx() as c:
            await c.execute('DELETE FROM raid_shard_daily WHERE owner_id=1')
            for item in ITEMS:
                if item['theme']==theme:
                    await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(1,day,item['key'],item['daily_cap']))
            await c.execute("INSERT INTO raid_shard_daily VALUES (?,?,'character_shard',50)",(1,day))
            await c.execute('INSERT OR REPLACE INTO raid_shard_weekly VALUES (?,?,350)',(1,week_key()))
            await c.execute("INSERT OR REPLACE INTO economy_daily VALUES (?,?,'raid',99,300,300)",(1,day))

    async def teams(self):
        names=('Suga','Lev','Kai','Kita','Daichi','Reon')
        async with self.store.tx() as c:
            cards=[await self.store._issue_card(c,1,'Tester',next(ch for ch in CHARACTERS if ch.name==name)) for name in names]
        for slot in (1,2):await self.db.ensure_saved_team(1,slot)
        await self.db.replace_saved_team(1,1,*(ch.instance_id for ch in cards[:3]))
        await self.db.replace_saved_team(1,2,cards[0].instance_id,cards[3].instance_id,cards[4].instance_id,
            front2_id=cards[1].instance_id,front3_id=cards[2].instance_id,back3_id=cards[5].instance_id,team_format=6)

    async def test_defense_capped_other_themes_pay_in_all_formats_and_stages(self):
        await self.teams();raid=RaidStore(self.db)
        for theme in THEMES:
            if theme=='defense':continue
            for difficulty,fmt in (('easy',3),('hard',3),('easy',6),('hard',6),('extreme',6)):
                with self.subTest(theme=theme,difficulty=difficulty,fmt=fmt):
                    await self.seed_caps('defense')
                    row=await raid.start(1,'Tester',100,1 if fmt==3 else 2,fmt,theme,difficulty=difficulty)
                    with patch('hiq.raids.drop_count',return_value=3):
                        row=await raid.finish(row['id'],{'winner_side':'A'})
                    st=row['state'];self.assertEqual(st['reward_status'],'awarded')
                    self.assertFalse(st['reward_check']['all_capped'])
                    report=await raid.cap_report(1)
                    self.assertEqual(report['latest']['id'],row['id'])
                    self.assertEqual(report['latest']['state']['reward_check'],st['reward_check'])
                    self.assertEqual(len(st['rewards']),3)
                    allowed={i['key'] for i in ITEMS if i['theme']==theme}
                    async with self.db.connect() as c:
                        for reward in st['rewards']:
                            self.assertEqual(reward['kind'],'item_shard')
                            self.assertIn(reward['key'],allowed)
                            self.assertGreater(await balance(c,1,'item_shard',reward['detail']),0)
                    with self.assertRaises(ValueError):await raid.finish(row['id'],{'winner_side':'A'})

    async def test_exhausting_any_theme_leaves_every_other_theme_eligible(self):
        raid=RaidStore(self.db)
        for exhausted in THEMES:
            await self.seed_caps(exhausted)
            progress=await raid.shard_progress(1)
            for selected in THEMES:
                with self.subTest(exhausted=exhausted,selected=selected):
                    pool=raid.reward_pool(selected,progress)
                    expected={i['key'] for i in ITEMS if i['theme']==selected} if selected!=exhausted else set()
                    self.assertEqual({r['key'] for r in pool},expected)
                    self.assertEqual(len(pool),12 if selected!=exhausted else 0)

    async def test_cap_report_is_read_only_and_explains_independent_themes(self):
        from hiq.cogs.raids import raid_caps_embed,RaidSetupView
        await self.seed_caps('defense');raid=RaidStore(self.db)
        before=await raid.shard_progress(1)
        report=await raid.cap_report(1)
        self.assertEqual(before,await raid.shard_progress(1))
        self.assertIsNone(report['latest'])
        embed=raid_caps_embed(report)
        self.assertIn('**Defense:** 0/',embed.fields[0].value)
        for theme in THEMES:
            if theme!='defense':self.assertNotIn(f'**{theme.title()}:** 0/',embed.fields[0].value)
        self.assertIn('**Coins:** 0 left',embed.fields[1].value)
        self.assertIn('does not block',embed.fields[1].value)
        view=RaidSetupView(SimpleNamespace(bot=SimpleNamespace()),SimpleNamespace(author=SimpleNamespace(id=1)),[])
        menu=view.embed()
        self.assertIn('one recipe',next(f.value for f in menu.fields if f.name=='Rewards'))
        for e in (embed,menu):
            self.assertLessEqual(len(e),6000)
            for field in e.fields:self.assertLessEqual(len(field.value),1024)
        view.stop()

    def test_item_keys_are_unique_and_do_not_collide_with_shared_rewards(self):
        keys=[i['key'] for i in ITEMS]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertFalse(set(keys)&{'coins','clancoins','character_shard'})
