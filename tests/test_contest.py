import asyncio,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from hiq.contest import ContestEngine,PRESETS,LEGACY_PRESETS,preset_specs,support_slots,character
from hiq.contest_store import ContestStore
from hiq.data import CHARACTERS
from hiq.sealed import team_specs
from hiq.database import Database
from hiq.economy import week_key,local_date_key,week_key_for
from hiq.battle.types import CombatContext


def players():return team_specs('contest-test',1,[c.id for c in list({c.name:c for c in CHARACTERS}.values())[:6]])


class ContestEngineTests(unittest.TestCase):
    def test_all_fourteen_formations_have_six_eligible_supports(self):
        self.assertEqual(len(PRESETS),14)
        for i in range(14):
            specs=preset_specs(i)
            self.assertEqual(len(specs),7)
            self.assertEqual(specs[0][0].merge_level,10)
            self.assertTrue(all(support_slots(c) for _,c,_ in specs[1:]))
            self.assertNotIn(specs[0][1].name,[c.name for _,c,_ in specs[1:]])
            self.assertFalse(any(card.equipment_key or card.boon_stat or card.bane_stat for card,_,_ in specs))

    def engine_for(self,name,title=None,lane=None):
        index=next(i for i,p in enumerate(PRESETS) if p[1]==name and (title is None or p[2]==title) and (lane is None or p[3]==lane))
        return ContestEngine(players(),index,seed=42)

    def context(self,e,initiates=True):
        a,d=(e.feature,e.team_a.units[0]) if initiates else (e.team_a.units[0],e.feature)
        return CombatContext(e,a,d,a,d)

    def test_featured_roles_and_required_pairings(self):
        self.assertEqual({p[3] for p in PRESETS if p[1]=='Korai'},{'front','back'})
        self.assertNotIn('Iizuna',[p[1] for p in PRESETS]);self.assertNotIn('Suwa',[p[1] for p in PRESETS])
        kanoka=self.engine_for('Kanoka')
        self.assertTrue({'Kiryu','Kanbayashi','Atsumu','Korai','Hinata','Kageyama'}<={u.name for u in kanoka.supports})
        self.assertEqual(next(u.title for u in kanoka.supports if u.name=='Kiryu'),'Last Hope')
        self.assertIn('Iizuna',[u.name for u in self.engine_for('Iwaizumi').supports])
        self.assertEqual(sum(any(c.name=='Oikawa' for _,c,_ in preset_specs(i)[1:]) for i in range(14)),4)
        kiryu=self.engine_for('Kiryu','Last Hope')
        self.assertIn('Usuri',[u.name for u in kiryu.supports])
        kiryu.end_turn();self.assertEqual(kiryu.feature.burden_stacks,2)
        for name,school,bond in [('Bokuto','Fukurodani',4),('Aone','Date Tech',2),('Kuroo','Nekoma',0)]:
            e=self.engine_for(name)
            self.assertEqual(sum(u.character.school==school for u in e.supports),bond)
            self.assertEqual(e.feature.base['hp'],e.feature.character.stats['hp']+2+bond)

    def test_slot_limits_prevent_usuri_siphon_and_excellence(self):
        e=ContestEngine(players(),11,rules_version=3,seed=42);e.turn=1;ctx=self.context(e)
        usuri=next(u for u in e.supports if u.name=='Usuri')
        atsumu=next(u for u in e.supports if u.name=='Atsumu')
        self.assertFalse(e.skill_enabled(usuri,'A'));self.assertFalse(e.skill_enabled(usuri,'B'))
        self.assertTrue(e.skill_enabled(usuri,'C'));self.assertFalse(e.skill_enabled(atsumu,'B'))
        stats=e.combat_stats(ctx)
        self.assertNotIn(('named','Excellence'),e.distinct_bonuses(ctx,e.feature,stats))
        self.assertFalse(any(uid==usuri.uid for uid,slot in e.ally_skill_impacts(ctx,e.feature,stats)))
        with patch.object(e,'attack_action'):
            e.combat(ctx)
        self.assertNotIn('siphon',ctx.temp_flags)

    def test_supports_cannot_trigger_ultimates_or_unlisted_match_start(self):
        e=self.engine_for('Ushijima');before=e.feature.max_cd;e.apply_match_start()
        self.assertEqual(e.feature.max_cd,before)  # Shirabu A is not an eligible support slot.
        for source in e.supports:
            source.cd=0
            self.assertFalse(e.trigger_ultimate(source,None,'post'))
        e.start_turn()
        self.assertEqual(e.feature.visible_bonus('pow'),6)  # Shirabu C still works.

    def test_kanbayashi_a_is_not_run_and_kiryu_burden_is_ignored(self):
        e=self.engine_for('Kanoka');e.team_b.hp=e.team_b.max_hp//2;e.start_turn()
        self.assertEqual(e.feature.visible_bonus('def'),0)
        self.assertEqual(e.feature.visible_bonus('rec'),0)
        ctx=self.context(e,False);foe=ctx.attacker
        ctx.temp_flags['critical_unit_uids']={foe.uid}
        e._new_unit_after_combat(ctx)
        kiryu=next(u for u in e.supports if u.name=='Kiryu')
        self.assertEqual(kiryu.burden_stacks,0)
        self.assertEqual(foe.visible_penalty('def'),-4)
        e.end_turn();self.assertEqual(kiryu.burden_stacks,0)

    def test_kanbayashi_critical_reduction_and_healing(self):
        e=self.engine_for('Kanoka');ctx=self.context(e,False)
        a,d=ctx.attacker,ctx.defender
        a.base.update(pow=100,qik=100,jmp=100,spd=0);d.base.update({'def':10,'rec':10,'jmp':0,'spd':0})
        a.cd=d.cd=99
        source=next(u for u in e.supports if u.name=='Kanbayashi')
        e.team_b.hp=50
        with patch.object(e.rng,'random',return_value=0):
            e.resolve_hit(ctx,a,d)
        reduced=d.damage_taken
        self.assertGreater(reduced,0)
        self.assertIn(a.uid,ctx.temp_flags.get('offensive_crit_uids',set()))
        e.team_b.hp=50;e._new_unit_after_combat(ctx)
        self.assertEqual(e.team_b.hp,56)
        # Same crit with the C-slot source in another row removes its 30% reduction.
        source.lane='back';d.damage_taken=0;a.cd=d.cd=99
        ctx=self.context(e,False)
        with patch.object(e.rng,'random',return_value=0):e.resolve_hit(ctx,a,d)
        self.assertGreater(d.damage_taken,reduced)

    def test_kenma_support_does_not_inflict_fatigue(self):
        e=self.engine_for('Kuroo')
        e.supports[0].character=character('Kenma');e.start_turn();ctx=self.context(e,False)
        self.assertFalse(e.skill_enabled(next(u for u in e.supports if u.name=='Kenma'),'A'))
        e.after_combat(ctx)
        self.assertFalse(any('Fatigue' in u.statuses for u in e.team_a.units))
        self.assertTrue(any(u.visible_penalty('jmp')==-7 for u in e.team_a.units))

    def test_cooldown_support_and_kageyama_bonus_threshold(self):
        aone=self.engine_for('Aone');before=aone.feature.cd;aone.start_turn()
        self.assertEqual(aone.feature.cd,before-1)  # Gao C.
        korai=ContestEngine(players(),3,rules_version=3,seed=42);before=korai.feature.cd;korai.start_turn()
        self.assertEqual(korai.feature.cd,before-1)  # Lev lifts Rec above Def for Maruyama C.
        e=self.engine_for('Kageyama','Prodigious Talent');e.start_turn();ctx=self.context(e)
        stats=e.combat_stats(ctx)
        self.assertGreaterEqual(len(e.distinct_bonuses(ctx,e.feature,stats)),6)
        self.assertTrue(e.followup(ctx,e.feature,ctx.defender,True))

    def test_iizuna_receiving_and_positional_exceptions(self):
        e=ContestEngine(players(),13,rules_version=3,seed=42);e.start_turn();ctx=self.context(e,False)
        ctx.attacker.base['rec']=1
        e.anchor_combat_start(ctx)
        self.assertIn('Clean Receive',e.feature.statuses)
        stats=e.combat_stats(ctx)
        self.assertTrue(e.anchor_receiving(ctx,ctx.attacker,e.feature,stats))
        noya=next(u for u in e.supports if u.name=='Nishinoya')
        self.assertIn((noya.uid,'C'),e.ally_skill_impacts(ctx,e.feature,stats))
        # A Nozawa support's opposing bond penalty survives the target-only proxy.
        same_school=character('Suga')
        ctx.attacker.character=same_school
        e.team_a.units[1].character=character('Daichi')
        other=self.engine_for('Aone');ctx=self.context(other,False)
        ctx.attacker.character=character('Suga');other.team_a.units[1].character=character('Daichi')
        self.assertEqual(other.expansion_bond_penalty(ctx,ctx.attacker),1)

    def test_legacy_open_play_support_alternates_active_recipient(self):
        e=ContestEngine(players(),12,rules_version=3,seed=42);e.start_turn()
        self.assertIn('Open Play',e.feature.statuses)
        e.feature.actions_taken+=1;e.feature.expire_after_action();e.end_turn();e.start_turn()
        self.assertNotIn('Open Play',e.feature.statuses)
        e.end_turn();e.start_turn();self.assertIn('Open Play',e.feature.statuses)

    def test_saved_legacy_attempt_keeps_original_opponent(self):
        e=ContestEngine(players(),8,rules_version=1,seed=8)
        self.assertEqual(e.feature.name,LEGACY_PRESETS[8][1]);self.assertEqual(e.feature.name,'Suwa')
        self.assertEqual(e.run().mode,'clancontest')

    def test_revised_supports_have_specific_payoffs(self):
        self.assertEqual([i for i in range(14) if any(c.name=='Kenma' for _,c,_ in preset_specs(i)[1:])],[4])
        e=self.engine_for('Ushijima');e.start_turn()
        self.assertEqual(e.feature.visible_bonus('def'),4)
        self.assertEqual(e.feature.visible_bonus('rec'),4)
        e=self.engine_for('Hinata','Wings of Dawn');e.start_turn()
        self.assertEqual({u.title for u in e.supports if u.name=='Kageyama'},{'Crown of Precision','Prodigious Talent'})
        e.team_b.hp=e.team_b.max_hp//2;ctx=self.context(e)
        ctx.defender.base['spd']=0
        self.assertTrue(e.attacks_twice(ctx,e.feature))
        self.assertTrue(e.followup(ctx,e.feature,ctx.defender,True))
        e=self.engine_for('Kuroo');e.team_b.hp=e.team_b.max_hp//2;e.start_turn()
        self.assertEqual(e.feature.barrier,e.feature.base['hp']//10)
        self.assertTrue(all(not e.skill_enabled(u,'B') for u in e.supports if u.name=='Yamagata'))
        e=self.engine_for('Iwaizumi');e.start_turn();ctx=self.context(e)
        self.assertIn('Kai',[u.name for u in e.supports])
        self.assertIn('Atsumu',[u.name for u in e.supports])
        self.assertIn('Kanbayashi',[u.name for u in e.supports])
        old=ContestEngine(players(),8,rules_version=2)
        self.assertIn('Yamamoto',[u.name for u in old.supports])

    def test_cycling_preserves_overkill_and_thresholds(self):
        e=ContestEngine(players(),0,seed=1);bar=e.team_b.max_hp
        self.assertEqual(bar,e.feature.base['hp'])
        e.damage_team(e.team_b,bar*2+17)
        self.assertEqual((e.score,e.bars,e.team_b.hp),(bar*2+17,2,bar-17))
        e.damage_team(e.team_b,bar-17)
        self.assertEqual(e.team_b.hp,bar);self.assertEqual(e.bars,3)

    def test_supports_never_act_or_receive_attacks(self):
        for i in range(14):
            e=ContestEngine(players(),i,seed=i);result=e.run()
            self.assertLessEqual(result.turns,20)
            self.assertGreater(e.team_b.hp,0)
            self.assertEqual(len(e.enemy_team(e.team_a.units[0]).units),1)
            for u in e.supports:
                self.assertEqual(u.actions_taken,0);self.assertEqual(u.attacks_tanked,0)
                self.assertFalse(u.ult_triggered_this_combat)

    def test_twenty_turn_limit_when_player_cannot_die(self):
        e=ContestEngine(players(),0,seed=1)
        e.team_a.hp=e.team_a.max_hp=1000000
        self.assertEqual(e.run().turns,20)


class ContestStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();self.store=ContestStore(self.db)
        for uid in range(1,5):await self.db.ensure_user(uid)
        self.clan=await self.store.create_clan(1,'Tester','Contest Clan','🏐')

    async def asyncTearDown(self):self.temp.cleanup()

    async def run_row(self,score,day=None,uid=1):
        import json,uuid
        sid=uuid.uuid4().hex;day=day or local_date_key()
        async with self.store.tx() as c:
            await c.execute("INSERT INTO contest_runs VALUES (?,?,?,?,'running',?)",(sid,uid,day,self.clan['id'],json.dumps({})))
        return sid,await self.store.finish(sid,score,{})

    async def test_best_score_only_and_once_daily_participation(self):
        sid,first=await self.run_row(100)
        self.assertEqual(first['state']['participation'],200)
        again=await self.store.finish(sid,100,{})
        self.assertEqual(again['state']['participation'],200)
        _,second=await self.run_row(80)
        self.assertEqual(second['state']['participation'],0)
        await self.run_row(140)
        self.assertEqual(await self.store.wallet(1),200)
        clans,users=await self.store.leaderboard()
        self.assertEqual(users[0]['score'],100)
        self.assertEqual(clans[0]['score'],100)
        self.assertEqual(users[0]['damage'],140)

    async def test_each_day_has_equal_weight_and_clans_normalize_separately(self):
        from hiq.community_store import now
        other=await self.store.create_clan(2,'Two','Other Clan','🏐')
        week=week_key();first=datetime.fromisoformat(week).date();second=first+timedelta(days=1)
        async with self.store.tx() as c:
            for uid,day,cid,score in [(1,first,self.clan['id'],70000),(2,first,other['id'],35000),
                                       (3,first,other['id'],35000),(1,second,self.clan['id'],100),
                                       (2,second,other['id'],1000)]:
                await c.execute('INSERT INTO contest_scores VALUES (?,?,?,?,?)',(uid,day.isoformat(),week,cid,score))
        clans,users=await self.store.leaderboard()
        self.assertEqual([r['owner_id'] for r in users],[2,1,3])
        self.assertEqual([r['score'] for r in users],[150,110,50])
        self.assertEqual([r['clan_id'] for r in clans],[other['id'],self.clan['id']])
        self.assertEqual([r['score'] for r in clans],[200,110])
        for standings in (clans,users):
            for day in (first,second):
                self.assertEqual(max(r['days'].get(day.isoformat(),{}).get('percent',0) for r in standings),100)
        # A better retry changes the reference, not the day's maximum weight.
        async with self.store.tx() as c:
            await c.execute('UPDATE contest_scores SET score=140000 WHERE owner_id=1 AND day=?',(first.isoformat(),))
        _,users=await self.store.leaderboard()
        self.assertEqual([r['owner_id'] for r in users],[2,1,3])
        self.assertEqual(users[0]['score'],125)

    async def test_zero_damage_day_has_no_division_error(self):
        await self.run_row(0)
        clans,users=await self.store.leaderboard()
        self.assertEqual(users[0]['score'],100)
        self.assertEqual(clans[0]['score'],100)

    async def test_prizes_use_daily_percentages_instead_of_raw_weekly_damage(self):
        other=await self.store.create_clan(2,'Two','Other Clan','🏐')
        current=datetime.fromisoformat(week_key()).date();first=current-timedelta(days=7)
        week=week_key_for(datetime.combine(first,datetime.min.time()))
        async with self.store.tx() as c:
            for uid,offset,cid,score in [(1,0,self.clan['id'],70000),(2,0,other['id'],35000),
                                         (1,1,self.clan['id'],100),(2,1,other['id'],1000)]:
                await c.execute('INSERT INTO contest_scores VALUES (?,?,?,?,?)',(uid,(first+timedelta(days=offset)).isoformat(),week,cid,score))
        await self.store.settle()
        self.assertEqual(await self.store.wallet(2),1000)
        self.assertEqual(await self.store.wallet(1),750)
        self.assertEqual((await self.db.get_user(2)).coins,1000)
        self.assertEqual((await self.db.get_user(1)).coins,750)

    async def test_board_pages_show_daily_percentages_without_exceeding_embed_limits(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from hiq.cogs.contest import ContestCog
        days={(datetime.fromisoformat(week_key()).date()+timedelta(days=i)).isoformat():dict(percent=100,damage=70000) for i in range(7)}
        clans=[dict(clan_id=str(i),score=700,days=days) for i in range(40)]
        players=[dict(owner_id=i,score=700,days=days) for i in range(40)]
        store=SimpleNamespace(settle=AsyncMock(),leaderboard=AsyncMock(return_value=(clans,players)),clan=AsyncMock(return_value={'name':'*'*40}))
        ctx=SimpleNamespace(defer=AsyncMock(),send=AsyncMock(),author=SimpleNamespace(id=1))
        await ContestCog.contestboard.callback(SimpleNamespace(store=store),ctx)
        pager=ctx.send.call_args.kwargs['view']
        self.assertGreater(len(pager.pages),2)
        for page in pager.pages:
            self.assertLessEqual(len(page.description),4096)
            self.assertLessEqual(len(page),6000)
            self.assertIn('Mon 100.00%',page.description)
            self.assertIn('Sun 100.00%',page.description)

    async def test_rotation_is_persistent_and_each_period_contains_all_teams(self):
        indices=[(await self.store.rotation(f'2026-11-{day:02}'))[0] for day in range(1,15)]
        self.assertEqual(set(indices),set(range(14)))
        self.assertEqual(await self.store.rotation('2026-11-01'),await ContestStore(self.db).rotation('2026-11-01'))
        self.assertNotEqual((await self.store.rotation('2026-11-14'))[1],(await self.store.rotation('2026-11-15'))[1])

    async def test_new_attempt_saves_formation_rules_version(self):
        from hiq.contest import RULES_VERSION
        from hiq.tournament import snapshot_specs
        with patch.object(self.store,'_team_snapshot',return_value=snapshot_specs(players())):
            row=await self.store.start(1,'Tester',1,123)
        saved=await ContestStore(self.db).run(row['id'])
        self.assertEqual(saved['state']['contest_rules'],RULES_VERSION)

    async def test_weekly_prizes_settle_exactly_once_after_restart(self):
        old=(datetime.fromisoformat(local_date_key())-timedelta(days=7)).date().isoformat()
        await self.run_row(500,old)
        before=(await self.db.get_user(1)).coins
        self.assertEqual(len(await self.store.settle()),1)
        self.assertEqual(await self.store.wallet(1),1200)
        self.assertEqual((await self.db.get_user(1)).coins,before+1000)

        self.assertEqual(await ContestStore(self.db).settle(),[])
        await self.run_row(999,old)
        self.assertEqual(await self.store.wallet(1),1200)
        self.assertEqual((await self.db.get_user(1)).coins,before+1000)

    async def test_top_three_clans_and_players_receive_distinct_prizes(self):
        from hiq.community_store import now
        old=(datetime.fromisoformat(local_date_key())-timedelta(days=7)).date().isoformat()
        second=await self.store.create_clan(2,'Two','Second Clan','🏐')
        third=await self.store.create_clan(3,'Three','Third Clan','🏐')
        async with self.store.tx() as c:
            await c.execute('INSERT INTO clan_members(user_id,clan_id,username,joined_at) VALUES (?,?,?,?)',(4,self.clan['id'],'Four',now()))
            for uid,cid,score in [(1,self.clan['id'],300),(2,second['id'],200),(3,third['id'],100)]:
                await c.execute('INSERT INTO contest_scores VALUES (?,?,?,?,?)',(uid,old,week_key_for(datetime.fromisoformat(old)),cid,score))
        before={uid:(await self.db.get_user(uid)).coins for uid in (1,2,3)}
        await self.store.settle()
        for uid,amount in ((1,1000),(2,750),(3,500),(4,1000)):
            self.assertEqual(await self.store.wallet(uid),amount)
            if uid in before:self.assertEqual((await self.db.get_user(uid)).coins,before[uid]+amount)
        self.assertEqual(await self.store.settle(),[])
