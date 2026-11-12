import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from hiq.database import Database
from hiq.gameplay_store import GameplayStore
from hiq.data import CHARACTERS,CHAR_BY_ID
from hiq.youth_rewards import OIKAWA,KAGEYAMA,HINATA,DUO,currency,duplicate
from unittest.mock import patch,Mock,AsyncMock
from hiq.items import ITEMS,SIGNATURE_ITEMS
from hiq.event_store import EventStore
from hiq.shard_balances import balance


class GameplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'game.sqlite3')
        await self.db.init();await self.db.ensure_user(1);self.store=GameplayStore(self.db)
        async with self.store.tx() as c:
            await c.execute('UPDATE users SET coins=20000 WHERE discord_id=1')
            self.card=await self.store._issue_card(c,1,'Player',CHARACTERS[0])
    async def asyncTearDown(self):self.temp.cleanup()

    async def test_dismantle_is_exactly_once_and_preserves_wallet(self):
        async with self.store.tx() as c:await c.execute('UPDATE cards SET merge_level=3 WHERE instance_id=?',(self.card.instance_id,))
        char,amount=await self.store.dismantle_card(1,self.card.instance_id)
        self.assertEqual(amount,char.rarity*4)
        await self.store.dismantle_card(1,self.card.instance_id,confirm=True)
        async with self.db.connect() as c:self.assertEqual(await balance(c,1,'character_shard',char.id),amount)
        with self.assertRaises(ValueError):await self.store.dismantle_card(1,self.card.instance_id,confirm=True)
        self.assertEqual((await self.db.get_user(1)).coins,20000)

    async def test_dismantle_rejects_locked_or_teamed_cards(self):
        async with self.store.tx() as c:await c.execute('UPDATE cards SET locked=1 WHERE instance_id=?',(self.card.instance_id,))
        with self.assertRaisesRegex(ValueError,'Locked'):await self.store.dismantle_card(1,self.card.instance_id,confirm=True)
        async with self.store.tx() as c:
            await c.execute('UPDATE cards SET locked=0 WHERE instance_id=?',(self.card.instance_id,))
            await c.execute("INSERT INTO saved_teams(owner_id,slot,name,front_id,updated_at) VALUES(1,1,'Team',?,'2026-11-11')",(self.card.instance_id,))
        with self.assertRaisesRegex(ValueError,'saved teams'):await self.store.dismantle_card(1,self.card.instance_id,confirm=True)

    async def test_autoclean_is_persistent_creation_only_and_can_be_disabled(self):
        key=ITEMS[0]['key'];value=ITEMS[0].get('recycle_value',ITEMS[0]['shard_cost'])
        await self.store.set_autoclean_page(1,[key],[key])
        async with self.store.tx() as c:
            iid=await self.store._issue_item(c,1,'equipment',detail=key)
        self.assertIsNone(iid);self.assertEqual(await self.store.wallet(1),value)
        self.assertEqual(await GameplayStore(self.db).autoclean(1),{key})
        await self.store.set_autoclean_page(1,[key],[])
        async with self.store.tx() as c:self.assertIsNotNone(await self.store._issue_item(c,1,'equipment',detail=key))
        self.assertEqual(await self.store.wallet(1),value)

    async def test_shield_has_two_hour_protection_and_six_hour_reuse(self):
        ids,cost=await self.store.buy_consumable(1,'pvpshield',2)
        until,reuse=await self.store.use_shield(1,ids[0],at=100)
        self.assertEqual((until,reuse),(7300,21700));self.assertEqual(cost,1000)
        with self.assertRaises(ValueError):await self.store.use_shield(1,ids[1],at=7400)
        self.assertEqual(len(await self.store.items(1)),1)
        await self.store.use_shield(1,ids[1],at=21700)
        self.assertEqual(await self.store.items(1),[])

    async def test_signature_shards_cap_earned_not_owned_and_retry_safe(self):
        key=SIGNATURE_ITEMS[0]['key'];events=EventStore(self.db)
        for i in range(28):
            sid=f'event-{i}'
            async with self.store.tx() as c:
                state=json.dumps(dict(signature_key=key,reward_policy='signature_drop_v2',drop_roll=True))
                await c.execute("INSERT INTO event_runs VALUES(?,1,'tiny_titans','running',?,NULL,0,?)",(sid,state,'2026-11-11T00:00:00+00:00'))
            await events.finish(1,sid,dict(winner_side='A'))
            self.assertEqual(await events.finish(1,sid,dict(winner_side='A')),0)
        async with self.db.connect() as c:
            self.assertEqual(await balance(c,1,'item_shard',key),25)
            cur=await c.execute('SELECT SUM(amount) FROM raid_shard_daily WHERE owner_id=1 AND reward_key=?',(key,))
            self.assertEqual((await cur.fetchone())[0],25)

    async def ingredient(self,cid,merge=0):
        async with self.store.tx() as c:
            card=await self.store._issue_card(c,1,'Player',CHAR_BY_ID[cid])
            await c.execute('UPDATE cards SET merge_level=? WHERE instance_id=?',(merge,card.instance_id))
            return card

    async def test_fusion_costs_preserves_merge_and_cannot_be_replayed(self):
        for level,mult in [(0,1),(1,1),(5,5),(10,10)]:
            a=await self.ingredient(OIKAWA,level);b=await self.ingredient(KAGEYAMA,level)
            async with self.store.tx() as c:await c.execute('INSERT INTO event_wallets(owner_id,af1_marks) VALUES (1,1000) ON CONFLICT(owner_id) DO UPDATE SET af1_marks=1000')
            before=(await self.db.get_user(1)).coins
            self.assertEqual(await self.store.fuse(1,a.instance_id,b.instance_id),(level,100*mult,25*mult))
            duo=await self.store.fuse(1,a.instance_id,b.instance_id,confirm=True)
            self.assertEqual((duo.character_id,duo.merge_level),(DUO,level))
            self.assertEqual((await self.db.get_user(1)).coins,before-100*mult)
            self.assertEqual((await EventStore(self.db).wallet(1))['af1_marks'],1000-25*mult)
            with self.assertRaises(ValueError):await self.store.fuse(1,a.instance_id,b.instance_id,confirm=True)

    async def test_fusion_rejects_mismatched_merge_and_locked_cards_without_spending(self):
        a=await self.ingredient(OIKAWA,2);b=await self.ingredient(KAGEYAMA,3)
        with self.assertRaises(ValueError):await self.store.fuse(1,a.instance_id,b.instance_id,confirm=True)
        self.assertEqual((await self.db.get_user(1)).coins,20000)
        async with self.store.tx() as c:await c.execute('UPDATE cards SET merge_level=2,locked=1 WHERE instance_id=?',(b.instance_id,))
        with self.assertRaisesRegex(ValueError,'Unlock'):await self.store.fuse(1,a.instance_id,b.instance_id,confirm=True)
        self.assertIsNotNone(await self.db.get_card(a.instance_id))

    async def test_shiny_stone_unlocks_next_slot_removes_bane_and_is_consumed_once(self):
        card=await self.ingredient(DUO)
        ids,_=await self.store.buy_consumable(1,'shiny_stone')
        boon=(await self.db.get_card(card.instance_id)).boon_stat
        self.assertEqual(await self.store.use_stone(1,ids[0],card.instance_id),1)
        shiny=await self.db.get_card(card.instance_id)
        self.assertEqual(shiny.shiny_slots,(1,));self.assertIsNone(shiny.bane_stat);self.assertEqual(shiny.boon_stat,boon)
        await self.store.lock_art(1,card.instance_id,0)
        self.assertEqual((await self.db.get_card(card.instance_id)).art_slot,0)
        with self.assertRaises(ValueError):await self.store.lock_art(1,card.instance_id,2)
        ids,_=await self.store.buy_consumable(1,'shiny_stone')
        with self.assertRaisesRegex(ValueError,'no remaining'):await self.store.use_stone(1,ids[0],card.instance_id)
        self.assertIn(ids[0],[i['id'] for i in await self.store.items(1)])

    async def test_merging_combines_shiny_slots_and_charges_duo_merge_coins(self):
        a=await self.ingredient(DUO);b=await self.ingredient(DUO)
        async with self.store.tx() as c:await c.execute("UPDATE cards SET shiny_slots='[1]',bane_stat=NULL WHERE instance_id=?",(b.instance_id,))
        result=await self.store.merge_many(1,a.instance_id,[b.instance_id])
        self.assertEqual((result.merge_level,result.shiny_slots,result.bane_stat),(1,(1,),None))
        self.assertEqual((await self.db.get_user(1)).coins,19900)

    async def test_af1_marks_are_daily_capped_and_repeat_safe(self):
        for i in range(27):
            sid=f'af1-{i}'
            async with self.store.tx() as c:await c.execute("INSERT INTO event_runs VALUES(?,1,'all_for_one','running','{}',NULL,0,'2026-11-12')",(sid,))
            await EventStore(self.db).finish(1,sid,dict(winner_side='A'))
            self.assertEqual(await EventStore(self.db).finish(1,sid,dict(winner_side='A')),0)
        self.assertEqual((await EventStore(self.db).wallet(1))['af1_marks'],25)

    async def test_collection_passives_choose_highest_owned_copy_without_stacking(self):
        for level in (2,5,3):await self.ingredient(DUO,level)
        async with self.store.tx() as c:
            self.assertEqual(await currency(c,1,100),106)
            self.assertEqual(await currency(c,1,100,clan=True),106)
            self.assertEqual(await currency(c,1,0),0)
        self.assertEqual((await self.db.get_user(1)).coins,20106)
        for level in (1,10,4):await self.ingredient(KAGEYAMA,level)
        async with self.store.tx() as c:
            self.assertTrue(await duplicate(c,1,rng=Mock(random=Mock(return_value=.099))))
            self.assertFalse(await duplicate(c,1,rng=Mock(random=Mock(return_value=.10))))

    async def test_collection_passives_stop_when_ownership_is_lost_and_used_caps_stay(self):
        from hiq.youth_rewards import highest,extra
        from hiq.economy import local_date_key
        cards=[await self.ingredient(cid,10) for cid in (OIKAWA,KAGEYAMA,DUO)]
        await self.db.ensure_user(2)
        async with self.store.tx() as c:
            self.assertIsNotNone(await extra(c,1,'battle',[local_date_key()]))
            for card in cards:await c.execute('UPDATE cards SET owner_id=2 WHERE instance_id=?',(card.instance_id,))
            for cid in (OIKAWA,KAGEYAMA,DUO):self.assertIsNone(await highest(c,1,cid))
            self.assertIsNone(await extra(c,1,'battle',[local_date_key()]))
            self.assertFalse(await duplicate(c,1,rng=Mock(random=Mock(return_value=0))))
            self.assertEqual(await currency(c,1,100),100)
            self.assertEqual(await currency(c,2,100),111)
            # Returning the card cannot reopen an already used extra allowance.
            await c.execute('UPDATE cards SET owner_id=1 WHERE instance_id=?',(cards[0].instance_id,))
            self.assertIsNone(await extra(c,1,'battle',[local_date_key()]))

    async def test_oikawa_extra_battle_allowance_is_reduced_once_and_does_not_grant_record_win_on_loss(self):
        from hiq.rewards import RewardStore
        await self.ingredient(OIKAWA,10)
        rewards=RewardStore(self.db)
        # Account was created today: only today's allowance is available.
        paid=[(await rewards.award(1,'battle'))['coins'] for _ in range(11)]
        self.assertEqual(paid,[50]*9+[25,0])
        before=await self.db.get_user(1)
        await rewards.award(1,'battle',record_win=False)
        after=await self.db.get_user(1)
        self.assertEqual(before.weekly_wins,after.weekly_wins)

    async def test_oikawa_checkin_allows_one_reduced_extra_reward(self):
        await self.ingredient(OIKAWA,10)
        await self.store.create_clan(1,'Player','Youth Club','🏐')
        await self.store.checkin(1,'Player');await self.store.checkin(1,'Player')
        self.assertEqual(await self.store.wallet(1),225)
        with self.assertRaises(ValueError):await self.store.checkin(1,'Player')

    async def test_kageyama_duplicate_does_not_consume_original_raid_drop_cap(self):
        from hiq.raid_store import RaidStore
        await self.ingredient(KAGEYAMA,10)
        raid=RaidStore(self.db);key=ITEMS[0]['key']
        async with self.store.tx() as c:await c.execute("INSERT INTO raid_runs VALUES ('duplicate-raid',1,1,'running',?,'2026-11-12')",(json.dumps(dict(theme=ITEMS[0]['theme'],difficulty='easy')),))
        pool=[dict(kind='item_shard',key=key,detail=key,amount=1,remaining=1,weight=1)]
        with patch.object(raid,'reward_pool',return_value=pool),patch('hiq.youth_rewards.duplicate',new=AsyncMock(return_value=True)):
            row=await raid.finish('duplicate-raid',dict(winner_side='A'))
        self.assertEqual(row['state']['reward']['amount'],2)
        async with self.db.connect() as c:
            self.assertEqual(await balance(c,1,'item_shard',key),2)
            used=await self.store.one(c,'SELECT SUM(amount) AS n FROM raid_shard_daily WHERE owner_id=1 AND reward_key=?',(key,))
            self.assertEqual(used['n'],1)

    async def test_raid_oikawa_extra_currency_is_discounted_and_duo_bonus_is_outside_cap(self):
        from hiq.raid_store import RaidStore
        from hiq.economy import local_date_key
        await self.ingredient(OIKAWA,10);await self.ingredient(DUO,5)
        raid=RaidStore(self.db);day=local_date_key()
        async with self.store.tx() as c:
            await c.execute("INSERT INTO economy_daily VALUES(1,?,'raid',3,300,0)",(day,))
            await c.execute("INSERT INTO raid_runs VALUES('extra-raid',1,1,'running',?,'2026-11-12')",(json.dumps(dict(theme='tactics',difficulty='easy')),))
        pool=[dict(kind='coins',key='coins',detail='',amount=100,remaining=100,weight=1)]
        with patch.object(raid,'reward_pool',return_value=pool):row=await raid.finish('extra-raid',dict(winner_side='A'))
        self.assertEqual(row['state']['currency']['coins'],56)
        progress=await raid.shard_progress(1)
        self.assertEqual(progress['coins'],300);self.assertEqual(progress['_extra_coins'],0)
        self.assertNotIn('coins',[r['key'] for r in raid.reward_pool('tactics',progress)])

    async def test_new_menus_fit_discord_and_shiny_controls_use_distinct_rows(self):
        from types import SimpleNamespace
        from hiq.cogs.youth_menus import StoneView,FuseView,BulkClanShop
        from hiq.cogs.raids import EquipmentView
        from hiq.cogs.gameplay import AutocleanView
        from hiq.cards import card_embed,compact_card_line
        a=await self.ingredient(OIKAWA);b=await self.ingredient(KAGEYAMA);duo=await self.ingredient(DUO)
        async with self.store.tx() as c:await c.execute("UPDATE cards SET shiny_slots='[1]',art_slot=1,bane_stat=NULL WHERE instance_id=?",(duo.instance_id,))
        duo=await self.db.get_card(duo.instance_id);bot=SimpleNamespace(db=self.db,emojis=[])
        views=[StoneView(self.store,1,'stone',[duo]),FuseView(self.store,1,[a,b]),BulkClanShop(self.store,1),AutocleanView(self.store,1,set()),await EquipmentView.create(bot,1,duo)]
        for view in views:
            for child in view.children:
                if hasattr(child,'options'):self.assertLessEqual(len(child.options),25)
        self.assertEqual(len([c for c in views[-1].children if c.row==3]),3)
        e=card_embed(bot,duo);self.assertIn('Kings of the Playground',e.author.name)
        self.assertIn('1570428970460250212',e.author.icon_url)
        self.assertIn('✨',e.author.name);self.assertIn('✨',compact_card_line(duo))
        self.assertEqual(e.image.url,CHAR_BY_ID[DUO].shiny_arts[0])

    async def test_help_covers_new_commands_without_oversized_pages(self):
        import bot as app
        from types import SimpleNamespace
        context=SimpleNamespace(author=SimpleNamespace(id=1),send=AsyncMock())
        await app.help_cmd.callback(context)
        pages=context.send.call_args.kwargs['view'].pages
        text='\n'.join(page.description for page in pages)
        for command in (';dismantle',';fuse',';use',';autoclean',';allrecycle',';recycle water bottle 5',';clanwar'):
            self.assertIn(command,text)
        self.assertTrue(all(len(page.description)<=1500 for page in pages))
