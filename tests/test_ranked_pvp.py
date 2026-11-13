import asyncio
import json
import tempfile
import unittest
import uuid
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from hiq.contest import character
from hiq.database import Database
from hiq.pvp_store import PvPStore,calendar,elo_delta

NOW=datetime(2026,11,12,17,tzinfo=timezone.utc)
NEXT_DAY=datetime(2026,11,13,17,tzinfo=timezone.utc)
OLD=datetime(2026,11,8,18,tzinfo=timezone.utc)


class EloTests(unittest.TestCase):
    def test_expected_results_and_upsets(self):
        self.assertEqual(elo_delta(1000,1000,1),16)
        self.assertEqual(elo_delta(1000,1000,0),-16)
        self.assertEqual(elo_delta(1000,1000,.5),0)
        self.assertGreater(elo_delta(800,1200,1),16)
        self.assertLess(elo_delta(1200,800,1),16)

    def test_seasons_and_daily_limits_use_game_timezone(self):
        self.assertEqual(calendar(datetime(2027,1,1,4,59,tzinfo=timezone.utc)),('fortnight-2026-12-21','2026-12-31'))
        self.assertEqual(calendar(datetime(2027,1,1,5,0,tzinfo=timezone.utc)),('fortnight-2026-12-21','2027-01-01'))


class PvPStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'pvp.sqlite3')
        await self.db.init();self.store=PvPStore(self.db)

    async def asyncTearDown(self):self.temp.cleanup()

    async def team(self,uid,slot=1,fmt=3):
        await self.db.ensure_user(uid);await self.db.set_user_fields(uid,tutorial_started=True)
        names=['Suga','Lev','Kai','Daichi','Aran','Reon'][:fmt]
        async with self.store.tx() as c:
            cards=[await self.store._issue_card(c,uid,f'Player {uid}',character(n)) for n in names]
        await self.db.ensure_saved_team(uid,slot,f'Team {uid}/{slot}')
        ids=[c.instance_id for c in cards]
        if fmt==3:await self.db.replace_saved_team(uid,slot,*ids,team_format=3)
        else:await self.db.replace_saved_team(uid,slot,ids[0],ids[3],ids[4],front2_id=ids[1],front3_id=ids[2],back3_id=ids[5],team_format=6)
        await self.db.set_active_team(uid,slot)
        return cards

    async def users(self,*uids):
        for uid in uids:await self.team(uid)

    async def finish(self,row,winner='A',at=NOW):return await self.store.finish(row['id'],dict(winner_side=winner),at=at)

    async def history(self,a,b):
        async with self.store.tx() as c:await c.execute('INSERT INTO pvp_history(match_id,a_id,b_id) VALUES (?,?,?)',(uuid.uuid4().hex,a,b))

    async def test_automatic_match_picks_closest_eligible_elo(self):
        await self.users(1,2,3,4)
        await self.team(5,fmt=6)
        async with self.store.tx() as c:
            for uid,rating in [(1,1000),(2,1010),(3,1200),(4,1030),(5,1000)]:
                await c.execute('INSERT INTO pvp_ratings(month,owner_id,rating) VALUES (?,?,?)',('2026-11',uid,rating))
        row=await self.store.start(1,at=NOW)
        self.assertEqual(row['b_id'],2);self.assertEqual(row['format'],3)
        self.assertTrue(row['rated']);self.assertFalse(row['targeted'])

    async def test_avoidance_is_bidirectional_and_lasts_five_other_rated_matches(self):
        await self.users(1,2);await self.history(1,2)
        for oid in range(3,8):await self.history(1,oid)
        with self.assertRaisesRegex(ValueError,'No eligible'):await self.store.start(1,at=NOW)
        for oid in range(3,7):await self.history(2,oid)
        with self.assertRaisesRegex(ValueError,'No eligible'):await self.store.start(1,at=NOW)
        await self.history(2,7)
        self.assertEqual((await self.store.start(1,at=NOW))['b_id'],2)

    async def test_no_relaxation_when_only_recent_opponent_exists(self):
        await self.users(1,2)
        row=await self.store.start(1,at=NOW);await self.finish(row)
        with self.assertRaisesRegex(ValueError,'five other rated matches'):await self.store.start(1,at=NOW)

    async def test_targeted_rating_limit_applies_to_both_players_not_each_opponent(self):
        await self.users(1,2,3,4)
        first=await self.store.start(1,target=2,at=NOW)
        self.assertTrue(first['rated']);self.assertTrue(first['targeted']);await self.finish(first)
        repeat=await self.store.start(1,target=3,at=NOW)
        self.assertFalse(repeat['rated']);settled=await self.finish(repeat)
        self.assertEqual(settled['settlement']['delta'],0)
        defender=await self.store.start(2,target=3,at=NOW)
        self.assertFalse(defender['rated']);await self.finish(defender)
        # Being in an unrated practice match does not spend player 3's allowance.
        other=await self.store.start(3,target=4,at=NOW)
        self.assertTrue(other['rated']);await self.finish(other)
        tomorrow=await self.store.start(1,target=2,at=NEXT_DAY)
        self.assertTrue(tomorrow['rated'])

    async def test_practice_does_not_change_rating_record_or_repeat_history(self):
        await self.users(1,2)
        first=await self.store.start(1,target=2,at=NOW);await self.finish(first)
        repeat=await self.store.start(1,target=2,at=NOW);await self.finish(repeat,'B')
        _,board,own=await self.store.board(1,at=NOW)
        self.assertEqual((own['rating'],own['wins'],own['losses']),(1016,1,0))
        async with self.db.connect() as c:self.assertEqual((await self.store.one(c,'SELECT COUNT(*) n FROM pvp_history'))['n'],1)

    async def test_finish_is_once_only_and_keeps_existing_pvp_coin_rules(self):
        await self.users(1,2);row=await self.store.start(1,at=NOW)
        results=await asyncio.gather(self.finish(row),self.finish(row))
        self.assertEqual(results[0]['settlement'],results[1]['settlement'])
        self.assertEqual((await self.db.get_user(1)).coins,100)
        _,board,own=await self.store.board(1,at=NOW)
        self.assertEqual(own['rating'],1016);self.assertEqual(own['wins'],1)
        self.assertEqual(sum(r['rating'] for r in board),2000)

    async def test_draw_updates_both_records_without_coin_reward(self):
        await self.users(1,2);row=await self.store.start(1,at=NOW);await self.finish(row,'draw')
        _,board,own=await self.store.board(1,at=NOW)
        self.assertEqual(own['draws'],1);self.assertEqual(own['rating'],1000)
        self.assertEqual((await self.db.get_user(1)).coins,0)

    async def test_overlapping_matches_reserve_both_players(self):
        await self.users(1,2,3)
        row=await self.store.start(1,target=2,at=NOW)
        with self.assertRaisesRegex(ValueError,'already in a PvP'):await self.store.start(3,target=2,at=NOW)
        with self.assertRaisesRegex(ValueError,'already in a PvP'):await self.store.start(2,target=3,at=NOW)
        resumed=await self.store.start(1,target=3,at=NOW)
        self.assertEqual(resumed['id'],row['id']);self.assertEqual(resumed['state'],row['state'])
        await self.finish(row)
        self.assertTrue(await self.store.start(3,target=2,at=NOW))

    async def test_teamuse_fills_each_format_and_lock_freezes_only_automatic_replacement(self):
        original=await self.team(1);await self.team(1,2,6)
        prefs=await self.store.preferences(1)
        self.assertEqual(set(prefs['teams']),{3,6});self.assertEqual(prefs['profile']['default_format'],6)
        await self.store.set_lock(1,3,True)
        await self.team(1,3,3)
        prefs=await self.store.preferences(1)
        self.assertEqual(prefs['teams'][3]['entries'][0]['id'],original[0].instance_id)
        self.assertTrue(prefs['teams'][3]['locked']);self.assertEqual(prefs['profile']['default_format'],3)
        await self.store.set_team(1,3) # an explicit manual selection is still allowed
        prefs=await self.store.preferences(1)
        self.assertNotEqual(prefs['teams'][3]['entries'][0]['id'],original[0].instance_id)
        self.assertTrue(prefs['teams'][3]['locked']);self.assertIn(6,prefs['teams'])

    async def test_saved_team_edits_do_not_silently_rewrite_a_pvp_lineup(self):
        cards=await self.team(1);await self.db.clear_saved_team(1,1)
        prefs=await self.store.preferences(1)
        self.assertTrue(prefs['teams'][3]['valid']);self.assertEqual(prefs['teams'][3]['entries'][0]['id'],cards[0].instance_id)

    async def test_stale_controls_and_format_changes_are_rejected(self):
        await self.team(1);prefs=await self.store.preferences(1);r=prefs['teams'][3]['revision']
        await self.store.set_lock(1,3,True,r)
        with self.assertRaisesRegex(ValueError,'changed'):await self.store.set_lock(1,3,False,r)
        await self.team(1,2,6)
        with self.assertRaisesRegex(ValueError,'changed format'):await self.store.set_team(1,2,expected_format=3)

    async def test_card_removal_and_merge_repair_pvp_references(self):
        cards=await self.team(1)
        async with self.store.tx() as c:
            donor=await self.store._issue_card(c,1,'Player',character('Suga'))
            await self.db._replace_merged_card_tx(c,cards[0].instance_id,donor.instance_id)
        prefs=await self.store.preferences(1);self.assertEqual(prefs['teams'][3]['entries'][0]['id'],donor.instance_id)
        async with self.store.tx() as c:await self.db._clear_card_from_teams_tx(c,donor.instance_id)
        self.assertTrue((await self.store.preferences(1))['teams'][3]['valid'])

    async def test_owned_cards_are_revalidated_and_invalid_defenders_are_skipped(self):
        cards=await self.team(2);await self.team(1);await self.team(3)
        async with self.store.tx() as c:await c.execute('UPDATE cards SET owner_id=3 WHERE instance_id=?',(cards[0].instance_id,))
        row=await self.store.start(1,at=NOW);self.assertEqual(row['b_id'],3)

    async def test_profile_bootstraps_legacy_active_team_once(self):
        cards=await self.team(1)
        async with self.store.tx() as c:
            await c.execute('DELETE FROM pvp_teams');await c.execute('DELETE FROM pvp_profiles')
        prefs=await self.store.preferences(1)
        self.assertEqual(prefs['teams'][3]['entries'][0]['id'],cards[0].instance_id)
        self.assertTrue(prefs['teams'][3]['valid'])

    async def test_shield_blocks_initiating_direct_targeting_and_automatic_targeting(self):
        from hiq.gameplay_store import GameplayStore
        await self.users(1,2,3)
        async with self.store.tx() as c:await c.execute('UPDATE users SET coins=1000 WHERE discord_id=2')
        game=GameplayStore(self.db);ids,_=await game.buy_consumable(2,'pvpshield')
        await game.use_shield(2,ids[0])
        with self.assertRaisesRegex(ValueError,'cannot initiate'):await self.store.start(2,target=1,at=NOW)
        with self.assertRaisesRegex(ValueError,'active PvP Shield'):await self.store.start(1,target=2,at=NOW)
        self.assertEqual((await self.store.start(1,at=NOW))['b_id'],3)

    async def test_running_legacy_monthly_match_migrates_without_losing_elo(self):
        await self.users(1,2);row=await self.store.start(1,target=2,at=NOW)
        async with self.store.tx() as c:
            await c.execute("DELETE FROM economy_migrations WHERE version='v12-weekly-pvp'")
            await c.execute("DELETE FROM economy_migrations WHERE version='v12-biweekly-percentile-pvp'")
            await c.execute('DELETE FROM pvp_weeks')
            await c.execute("UPDATE pvp_ratings SET month='2026-11',rating=1200")
            await c.execute("UPDATE pvp_runs SET month='2026-11' WHERE id=?",(row['id'],))
        await self.store.settle(NOW);await self.finish(row)
        self.assertEqual((await self.store.board(1,NOW))[2]['rating'],1216)

    async def test_legacy_both_formats_are_recovered_and_invalid_teams_cannot_be_locked(self):
        await self.team(1,1,3);await self.team(1,2,6)
        async with self.store.tx() as c:
            await c.execute('DELETE FROM pvp_teams WHERE owner_id=1')
            await c.execute('DELETE FROM pvp_profiles WHERE owner_id=1')
        prefs=await self.store.preferences(1)
        self.assertTrue(all(prefs['teams'][size]['valid'] for size in (3,6)))
        async with self.store.tx() as c:await c.execute("UPDATE pvp_teams SET entries_json='[]' WHERE owner_id=1 AND format=6")
        with self.assertRaisesRegex(ValueError,'invalid'):await self.store.set_lock(1,6,True)
        self.assertTrue((await self.store.preferences(1))['teams'][6]['valid'])

    async def test_hinata_consolation_rewards_do_not_convert_a_loss_to_a_win(self):
        from hiq.youth_rewards import HINATA
        from hiq.data import CHAR_BY_ID
        await self.users(1,2)
        row=await self.store.start(1,target=2,at=NOW)
        row['state']['b'][0]['card'].update(character_id=HINATA,merge_level=10)
        async with self.store.tx() as c:await c.execute('UPDATE pvp_runs SET state_json=? WHERE id=?',(json.dumps(row['state']),row['id']))
        with patch('hiq.youth_rewards.loss_reward',return_value=True):await self.finish(row)
        self.assertEqual((await self.db.get_user(2)).coins,100)
        own=(await self.store.board(2,NOW))[2]
        self.assertEqual((own['rating'],own['wins'],own['losses']),(984,0,1))

    async def test_weekly_stacked_prizes_are_once_only_and_elo_resets_after_two_weeks(self):
        for uid in range(1,7):await self.db.ensure_user(uid)
        cycle=calendar(NOW)[0]
        await self.store.settle(NOW)
        async with self.store.tx() as c:
            for uid,rating in [(1,1200),(2,1100),(3,1050),(4,999),(5,900)]:await c.execute('INSERT INTO pvp_ratings(month,owner_id,rating,wins) VALUES (?,?,?,1)',(cycle,uid,rating))
            await c.execute('INSERT INTO pvp_ratings(month,owner_id,rating) VALUES (?,6,9999)',(cycle,))
        from datetime import timedelta
        for week in range(1,3):
            at=NOW+timedelta(weeks=week)
            await asyncio.gather(self.store.settle(at),self.store.settle(at));await self.db.init();await self.store.settle(at)
            month,board,own=await self.store.board(1,at)
            self.assertEqual(own['rating'],1200 if week<2 else 1000)
        for uid in range(1,6):
            self.assertEqual(sorted(i['stars'] for i in await self.store.items(uid)),sorted(list(range(1,7-uid))*2))
        self.assertEqual(await self.store.items(6),[])
        self.assertEqual((await self.db.get_user(1)).coins,0)
        self.assertEqual(board,[])
        async with self.db.connect() as c:
            self.assertEqual((await self.store.one(c,'SELECT COUNT(*) n FROM pvp_monthly_awards'))['n'],10)
            self.assertEqual((await self.store.one(c,'SELECT rating FROM pvp_ratings WHERE month=? AND owner_id=1',(cycle,)))['rating'],1200)

    async def test_late_pending_match_finishes_before_old_month_awards(self):
        await self.users(1,2);row=await self.store.start(1,at=OLD)
        await self.store.settle(NOW)
        self.assertEqual(await self.store.items(1),[])
        await self.finish(row,at=NOW)
        self.assertEqual(sorted(i['stars'] for i in await self.store.items(1)),[1,2,3,4,5])
        self.assertEqual((await self.store.board(1,NOW))[2]['rating'],1000)

    async def test_shared_rating_and_daily_limit_cross_formats(self):
        await self.users(1,2)
        for uid in (1,2):await self.team(uid,2,6)
        row=await self.store.start(1,target=2,fmt=3,at=NOW);await self.finish(row)
        row=await self.store.start(1,target=2,fmt=6,at=NOW);self.assertFalse(row['rated']);await self.finish(row)
        self.assertEqual((await self.store.board(1,NOW))[2]['rating'],1016)

    async def test_team_view_owner_lock_buttons_and_embed_limits(self):
        from hiq.cogs.pvp import PvPTeamView
        await self.team(1);await self.team(1,2,6)
        cog=SimpleNamespace(bot=SimpleNamespace(db=self.db),store=self.store)
        view=PvPTeamView(cog,1,await self.store.preferences(1),await self.db.list_saved_teams(1),await self.db.get_cards(1))
        self.assertLess(len(view.embed()),6000)
        self.assertEqual(len([c for c in view.children if getattr(c,'row',None)==2]),2)
        i=SimpleNamespace(user=SimpleNamespace(id=99),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(i))
        self.assertTrue(all(len(c.options)<=25 for c in view.children if hasattr(c,'options')))

    async def test_real_seeded_simulation_and_resume_do_not_double_settle(self):
        from hiq.cogs.pvp import PvPCog
        await self.users(1,2)
        row=await self.store.start(1,at=NOW,name='One',target_name='',channel=123)
        cog=object.__new__(PvPCog);cog.store=self.store
        settled,result,a,b=await cog.simulate(row['id'])
        self.assertEqual(settled['status'],'done');self.assertEqual((len(a),len(b)),(3,3))
        again,second,_,_=await cog.simulate(row['id'])
        self.assertEqual(again['settlement'],settled['settlement']);self.assertEqual(second.winner_side,result.winner_side)

    async def test_board_uses_arrows_and_target_daily_slots_do_not_apply_to_auto_matches(self):
        from hiq.cogs.pvp import PvPCog
        await self.users(1,2,3)
        row=await self.store.start(1,target=2,at=NOW);await self.finish(row)
        # The targeted daily allowance is used, but a different automatic opponent is rated.
        row=await self.store.start(1,at=NOW);self.assertTrue(row['rated']);self.assertEqual(row['b_id'],3)
        await self.finish(row)
        async with self.store.tx() as c:
            for uid in range(10,30):
                await c.execute('INSERT INTO pvp_ratings(month,owner_id,wins) VALUES (?,?,1)',(calendar(NOW)[0],uid))
        cog=object.__new__(PvPCog);cog.store=self.store
        ctx=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        board=await self.store.board(1,at=NOW)
        with patch.object(self.store,'board',AsyncMock(return_value=board)):
            await PvPCog.pvpboard.callback(cog,ctx)
        view=ctx.send.call_args.kwargs['view']
        self.assertGreater(len(view.pages),1);self.assertLess(len(view.embed()),6000)

    async def test_untargeted_command_needs_no_opponent_and_prefix_format_is_optional(self):
        from hiq.cogs.pvp import PvPCog,parse_format
        self.assertIsNone(PvPCog.pvp.clean_params['opponent'].default)
        self.assertEqual(parse_format('6v6'),6);self.assertIsNone(parse_format(''))
        from discord.ext.commands.view import StringView
        from discord.ext import commands
        ctx=SimpleNamespace(view=StringView('6v6'),message=SimpleNamespace(attachments=[]),interaction=None)
        with patch('discord.ext.commands.converter.MemberConverter.convert',AsyncMock(side_effect=commands.MemberNotFound('6v6'))):
            await PvPCog.pvp._parse_arguments(ctx)
        self.assertEqual(ctx.args[-2:],[None,'6v6'])
