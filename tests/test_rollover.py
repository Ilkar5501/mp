"""Week banking, scope, migration, reset, concurrency and shared raid caps."""
import asyncio,json,sys,tempfile,unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hiq.allowances import calendar_days,allowance_days
from hiq.config import TIMEZONE
from hiq.database import Database
from hiq.community_store import CommunityStore,now
from hiq.rewards import RewardStore
from hiq.raid_store import RaidStore
from hiq.data import CHARACTERS
from hiq.items import ITEMS
from hiq.economy import next_week_reset_from,week_key_for

MON='2026-10-26';TUE='2026-10-27';SUN='2026-11-01';NEXT='2026-11-02'


class RolloverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();self.store=CommunityStore(self.db);self.rewards=RewardStore(self.db)
        for uid in (1,2,3):await self.db.ensure_user(uid)
        async with self.store.tx() as c:await c.execute("UPDATE users SET created_at='2026-10-19T12:00:00+00:00'")

    async def asyncTearDown(self):self.temp.cleanup()

    async def test_all_seven_days_of_gauntlets_available_on_sunday(self):
        for mode in ('gauntlet_regional','gauntlet_national'):
            amounts=[(await self.rewards.award(1,mode,day=SUN,lineup=[c.id for c in CHARACTERS[i:i+3]]))['coins'] for i in range(15)]
            self.assertEqual(amounts,[250]*7+[0]*8)
            self.assertEqual((await self.rewards.daily(1,SUN))[mode]['coins'],1750)

    async def test_partial_completions_preserve_both_gauntlet_tiers(self):
        self.assertEqual((await self.rewards.award(1,'gauntlet_regional',day=MON,lineup=[c.id for c in CHARACTERS[:3]]))['coins'],250)
        self.assertEqual((await self.rewards.award(1,'gauntlet_regional',day=TUE,lineup=[c.id for c in CHARACTERS[:3]]))['coins'],200)
        rest=[(await self.rewards.award(1,'gauntlet_regional',day=SUN,lineup=[c.id for c in CHARACTERS[i+1:i+4]]))['coins'] for i in range(13)]
        self.assertEqual(rest,[50]+[250]*5+[0]*7)

    async def test_tournament_base_and_human_bonus_bank_separately(self):
        first=[(await self.rewards.award(1,'tournament',day=MON))['coins'] for _ in range(3)]
        self.assertEqual(first,[300,0,0])
        base=[(await self.rewards.award(1,'tournament',day=SUN))['coins'] for _ in range(7)]
        self.assertEqual(base,[300]*6+[0])
        bonus=[(await self.rewards.award(1,'tournament',day=SUN,human_tournament=True))['coins'] for _ in range(8)]
        self.assertEqual(bonus,[150]*7+[0])
        self.assertEqual((await self.rewards.daily(1,SUN))['tournament']['coins'],3150)

    async def test_prefecture_21_paid_wins_on_sunday(self):
        amounts=[(await self.rewards.award(1,'prefecture',day=SUN))['coins'] for _ in range(22)]
        self.assertEqual(amounts,[100]*21+[0]);self.assertEqual(sum(amounts),2100)

    async def test_future_days_locked_and_unpaid_wins_do_not_spend_them(self):
        monday=[(await self.rewards.award(1,'prefecture',day=MON))['coins'] for _ in range(12)]
        self.assertEqual(monday,[100]*3+[0]*9)
        tuesday=[(await self.rewards.award(1,'prefecture',day=TUE))['coins'] for _ in range(7)]
        self.assertEqual(tuesday,[100]*3+[0]*4)
        self.assertEqual((await self.rewards.daily(1,TUE))['prefecture']['wins'],6)

    async def test_unused_bank_expires_monday_instead_of_becoming_eight_days(self):
        await self.rewards.award(1,'prefecture',day=MON)
        await self.rewards.award(1,'prefecture',day=SUN)
        self.assertEqual(await self.rewards.daily(1,NEXT),{})
        monday=[(await self.rewards.award(1,'prefecture',day=NEXT))['coins'] for _ in range(7)]
        self.assertEqual(monday,[100]*3+[0]*4)
        self.assertEqual((await self.rewards.award(1,'gauntlet_regional',day=NEXT,lineup=[c.id for c in CHARACTERS[:3]]))['coins'],250)

    async def test_registration_day_limits_new_player_bank(self):
        async with self.store.tx() as c:await c.execute("UPDATE users SET created_at='2026-10-30T14:00:00+00:00' WHERE discord_id=1")
        self.assertEqual(len(await self.rewards.days(1,SUN)),3)
        amounts=[(await self.rewards.award(1,'prefecture',day=SUN))['coins'] for _ in range(19)]
        self.assertEqual(sum(amounts),900);self.assertEqual(amounts[-1],0)

    async def test_concurrent_awards_cannot_spend_one_allowance_twice(self):
        for _ in range(20):await self.rewards.award(1,'prefecture',day=SUN)
        result=await asyncio.gather(*(self.rewards.award(1,'prefecture',day=SUN) for _ in range(3)))
        self.assertEqual(sorted(r['coins'] for r in result),[0,0,100])
        self.assertEqual((await self.db.get_user(1)).coins,2100)

    async def test_existing_usage_survives_restart_without_reset_or_double_credit(self):
        async with self.store.tx() as c:
            await c.execute('INSERT INTO economy_daily VALUES (?,?,?,4,200,0)',(1,MON,'prefecture'))
            await c.execute('UPDATE users SET coins=200 WHERE discord_id=1')
        await Database(self.db.path).init()
        amounts=[(await self.rewards.award(1,'prefecture',day=SUN))['coins'] for _ in range(39)]
        self.assertEqual(amounts,[100]*19+[0]*20);self.assertEqual((await self.db.get_user(1)).coins,2100)

    async def test_battle_rolls_over_but_pvp_and_war_stay_daily(self):
        battle=[(await self.rewards.award(1,'battle',day=SUN))['coins'] for _ in range(64)]
        self.assertEqual(battle,[50]*63+[0])
        self.assertEqual([(await self.rewards.award(1,'war',day=SUN))['coins'] for _ in range(2)],[200,0])
        for opponent in range(10,19):
            value=await self.rewards.award(1,'pvp',day=SUN,opponent=opponent)
            self.assertEqual(value['coins'],100 if opponent<14 else 0)
        duplicate=await self.rewards.award(1,'pvp',day=SUN,opponent=10)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual((await self.rewards.award(1,'battle',day=NEXT))['coins'],50)
        self.assertEqual((await self.rewards.daily(1,NEXT))['battle']['coins'],50)

    async def test_donations_checkins_and_daily_pack_keep_daily_behavior(self):
        await self.store.create_clan(1,'One','Daily Clan','🏐')
        async with self.store.tx() as c:cards=[await self.store._issue_card(c,1,'One',CHARACTERS[0]) for _ in range(7)]
        for card in cards[:6]:await self.store.donate(1,card.instance_id,day=SUN)
        with self.assertRaises(ValueError):await self.store.donate(1,cards[6].instance_id,day=SUN)
        await self.store.checkin(1,'One',day=SUN)
        with self.assertRaises(ValueError):await self.store.checkin(1,'One',day=SUN)
        await self.db.set_user_fields(1,last_daily_date=SUN,starter_daily_claimed=1)
        from hiq.cogs.economy import EconomyCog
        context=SimpleNamespace(author=SimpleNamespace(id=1,name='One'),send=AsyncMock())
        before=len(await self.db.get_cards(1))
        with patch('hiq.cogs.economy.local_date_key',return_value=SUN):await EconomyCog.daily.callback(EconomyCog(SimpleNamespace(db=self.db)),context)
        self.assertIn('already opened',context.send.call_args.args[0])
        self.assertEqual(len(await self.db.get_cards(1)),before)

    async def make_raid(self,sid,theme='tactics',difficulty='extreme'):
        state=dict(theme=theme,difficulty=difficulty,format=6)
        async with self.store.tx() as c:await c.execute('INSERT INTO raid_runs VALUES (?,?,?, ?,?,?)',(sid,1,100,'running',json.dumps(state),now()))

    async def cap_all_except(self,kind,remaining,detail=None):
        store=RaidStore(self.db)
        async with self.store.tx() as c:
            for reward in store.reward_caps('tactics',{'_days':7},'extreme'):
                if reward['kind'] not in ('item_shard','character_shard'):continue
                selected=reward['kind']==kind and (detail is None or reward['detail']==detail)
                amount=reward['cap']-(remaining if selected else 0)
                await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,?)',(1,SUN,reward['key'],amount))
            await c.execute('INSERT INTO economy_daily VALUES (?,?,?,0,?,?)',(1,SUN,'raid',2100-(remaining if kind=='coins' else 0),2100-(remaining if kind=='clancoins' else 0)))

    async def test_raid_sunday_currency_duplicate_slots_recheck_seven_day_bank(self):
        await self.cap_all_except('coins',150);await self.make_raid('raid-coins')
        store=RaidStore(self.db)
        with patch('hiq.raid_store.local_date_key',return_value=SUN),patch('hiq.raids.drop_count',return_value=3):
            row=await store.finish('raid-coins',dict(winner_side='A'))
        self.assertEqual([r['amount'] for r in row['state']['rewards']],[100,50])
        progress=await store.shard_progress(1,day=SUN)
        self.assertEqual(progress['coins'],2100);self.assertEqual(progress['_days'],7)
        with self.assertRaises(ValueError):await store.finish('raid-coins',dict(winner_side='A'))

    async def test_raid_item_shard_bank_partial_usage_and_no_overflow(self):
        item=next(it for it in ITEMS if it['theme']=='tactics')
        await self.cap_all_except('item_shard',2,item['key']);await self.make_raid('raid-item')
        store=RaidStore(self.db)
        with patch('hiq.raid_store.local_date_key',return_value=SUN),patch('hiq.raids.drop_count',return_value=3):
            row=await store.finish('raid-item',dict(winner_side='A'))
        self.assertEqual(len(row['state']['rewards']),2)
        self.assertTrue(all(r['detail']==item['key'] for r in row['state']['rewards']))
        self.assertEqual((await store.shard_progress(1,day=SUN))[item['key']],item['shard_cost']*14)

    async def test_raid_character_shard_weekly_cap_stays_350(self):
        await self.cap_all_except('character_shard',20)
        async with self.store.tx() as c:await c.execute('INSERT INTO raid_shard_weekly VALUES (?,?,349)',(1,week_key_for(datetime.fromisoformat(SUN))))
        await self.make_raid('raid-char');store=RaidStore(self.db)
        with patch('hiq.raid_store.local_date_key',return_value=SUN),patch('hiq.raids.drop_count',return_value=3):
            row=await store.finish('raid-char',dict(winner_side='A'))
        self.assertEqual(len(row['state']['rewards']),1)
        self.assertEqual((await store.shard_progress(1,day=SUN))['character_shard_weekly'],350)
        progress=await store.shard_progress(1,day=NEXT)
        self.assertEqual(progress['character_shard_weekly'],0);self.assertEqual(progress['_days'],1)

    async def test_raid_midweek_bank_and_legacy_usage(self):
        async with self.store.tx() as c:
            await c.execute('INSERT INTO economy_daily VALUES (?,?,?,3,300,200)',(1,MON,'raid'))
            await c.execute('INSERT INTO raid_shard_daily VALUES (?,?,?,50)',(1,MON,'character_shard'))
        store=RaidStore(self.db);progress=await store.shard_progress(1,day=TUE)
        caps={r['key']:r for r in store.reward_caps('tactics',progress)}
        self.assertEqual(caps['coins']['remaining'],300)
        self.assertEqual(caps['clancoins']['remaining'],400)
        self.assertEqual(caps['character_shard']['remaining'],50)
        monday=await store.shard_progress(1,day=NEXT)
        caps={r['key']:r for r in store.reward_caps('tactics',monday)}
        self.assertEqual(caps['coins']['remaining'],300)

    async def test_questview_shows_banked_and_daily_modes_without_pack_rollover(self):
        from hiq.cogs.battle import BattleCog
        from hiq.ui import EmbedPager
        context=SimpleNamespace(author=SimpleNamespace(id=1,display_name='One'),send=AsyncMock())
        with patch('hiq.quest_pages.local_date_key',return_value=SUN):
            await BattleCog.questview.callback(BattleCog(SimpleNamespace(db=self.db)),context)
        pages=context.send.call_args.kwargs['view'].pages
        self.assertEqual(len(pages),5)
        self.assertEqual(pages[4].title,'How cards affect earnings')
        for text in ('Young Emperor','King of the Lost Court','Kings of the Playground','merge × 5%','merge × 1%','merge + 1','highest-merged owned copy','do not reset'):
            self.assertIn(text,pages[4].description)
        self.assertLessEqual(len(pages[4].description),4096)
        self.assertIn('Battle Count: 0 / 63',pages[0].description)
        self.assertIn('0 / 3150',pages[0].description)
        self.assertIn('0 / 2100',pages[0].description)
        self.assertIn('do not carry over',pages[3].description)



class CalendarTests(unittest.TestCase):
    def test_week_calendar_handles_dst_and_iso_year_boundaries(self):
        self.assertEqual(calendar_days(SUN),['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-10-31','2026-11-01'])
        self.assertEqual(calendar_days(NEXT),[NEXT])
        reset=next_week_reset_from(datetime(2026,11,1,23,59,tzinfo=TIMEZONE))
        self.assertEqual(reset.isoformat(),'2026-11-02T00:00:00-05:00')
        self.assertEqual(calendar_days('2027-01-03')[0],'2026-12-28')
        self.assertEqual(calendar_days('2027-01-04'),['2027-01-04'])


if __name__=='__main__':unittest.main(verbosity=2)
