import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from contextlib import closing

from hiq.database import Database
from hiq.retention import install, prune
from hiq.market_store import MarketStore
from hiq.cogs.market import MarketCog
from hiq.data import CHARACTERS
from hiq.ui import expire_result_view
from hiq.cogs.retention import RetentionCog
import discord


class RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'game.sqlite3'
        self.db = Database(self.path)
        await self.db.init()
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("INSERT INTO users(discord_id,coins,created_at) VALUES(1,1234,'2026-11-10')")
        self.conn.execute("INSERT INTO pvp_ratings VALUES('2026-11',1,1200,8,3,1)")
        self.conn.execute("INSERT INTO contest_scores VALUES(1,'2026-11-10','2026-11-09','clan',500)")
        self.conn.execute("INSERT INTO economy_daily VALUES(1,'2026-11-10','battle',3,150,0)")
        self.conn.commit()

    async def asyncTearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_thirty_minutes_from_completion_and_restart_does_not_extend(self):
        c = self.conn
        c.execute("INSERT INTO event_runs VALUES('active',1,'af1','running','{}',NULL,0,'2000-01-01')")
        c.execute("INSERT INTO event_runs VALUES('done',2,'tiny_titans','running','{}',NULL,0,'2000-01-01')")
        c.execute("UPDATE event_runs SET status='done',result_json='large replay' WHERE id='done'")
        c.commit()
        expires = c.execute("SELECT expires_at FROM replay_expiry WHERE record_id='done'").fetchone()[0]
        self.assertAlmostEqual(expires, time.time()+1800, delta=3)
        install(c, now=expires+9999)
        self.assertEqual(c.execute("SELECT expires_at FROM replay_expiry WHERE record_id='done'").fetchone()[0], expires)
        prune(c, now=expires-1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM event_runs').fetchone()[0], 2)
        prune(c, now=expires)
        self.assertEqual(c.execute('SELECT id FROM event_runs').fetchall(), [('active',)])

    def test_cleanup_preserves_permanent_data_wars_active_escrow_and_pending_delivery(self):
        c = self.conn
        for table in ('pvp_runs','contest_runs','event_runs','raid_runs'):
            # Fill schema-required fields without involving live battles.
            cols=c.execute(f'PRAGMA table_info({table})').fetchall()
            values={r[1]: (1 if r[2]=='INTEGER' else '{}') for r in cols if r[4] is None}
            values.update(id=table,status={'pvp_runs':'done','contest_runs':'complete','event_runs':'done','raid_runs':'complete'}[table])
            for key in ('a_id','b_id','owner_id'):
                if key in values:values[key]=1
            c.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',tuple(values.values()))
        for kind in ('war','prefecture','sealed'):
            c.execute('INSERT INTO community_sessions VALUES(?,?,?,?,?,?,?)',(kind,kind,'complete','{}',2,3,'2026-11-10'))
        for tid, message in [('delivered',5),('pending',None)]:
            c.execute("INSERT INTO tournaments(id,channel_id,guild_id,creator_id,status,created_at,results_message_id) VALUES(?,2,3,1,'complete','2026-11-10',?)",(tid,message))
            c.execute("INSERT INTO tournament_entries VALUES(?,1,'Player','large snapshot','2026-11-10')",(tid,))
            c.execute("INSERT INTO tournament_matches(tournament_id,match_key,owner_a,owner_b,winner_id,loser_id,result_json) VALUES(?,'W1',1,2,1,2,'large replay')",(tid,))
            c.execute("INSERT INTO tournament_rewards(tournament_id,owner_id,kind,amount,day_key) VALUES(?,1,'winner',100,'2026-11-10')",(tid,))
        for aid, status in [('closed','sold'),('escrow','active')]:
            c.execute('INSERT INTO market_auctions(id,seller_id,status,asset_json,prices_json,created_at,expires_at) VALUES(?,1,?,\'{}\',\'{}\',0,9999999999)',(aid,status))
            c.execute("INSERT INTO market_bids(auction_id,currency,bidder_id,amount) VALUES(?,'coins',2,100)",(aid,))
        c.commit()
        keep=('users','cards','pvp_ratings','contest_scores','economy_daily','tournament_rewards')
        before={t:c.execute(f'SELECT * FROM {t}').fetchall() for t in keep}
        prune(c,expire_completed=True)
        for t in keep:self.assertEqual(before[t],c.execute(f'SELECT * FROM {t}').fetchall(),t)
        self.assertEqual(c.execute('SELECT id FROM community_sessions').fetchall(),[('war',)])
        self.assertEqual(c.execute('SELECT id FROM tournaments').fetchall(),[('pending',)])
        self.assertEqual(c.execute('SELECT tournament_id FROM tournament_entries').fetchall(),[('pending',)])
        self.assertEqual(c.execute('SELECT tournament_id FROM tournament_matches').fetchall(),[('pending',)])
        self.assertEqual(c.execute('SELECT auction_id FROM market_bids').fetchall(),[('escrow',)])
        self.assertEqual(c.execute('SELECT id FROM market_auctions').fetchall(),[('escrow',)])
        self.assertEqual(prune(c,expire_completed=True)['event_runs'],0)

    def test_history_keeps_each_players_last_five_opponents(self):
        c=self.conn
        for i in range(40):c.execute('INSERT INTO pvp_history(match_id,a_id,b_id) VALUES(?,?,?)',(str(i),1,2+i%3))
        c.commit()
        def recent(uid):return c.execute('SELECT sequence FROM pvp_history WHERE a_id=? OR b_id=? ORDER BY sequence DESC LIMIT 5',(uid,uid)).fetchall()
        before={uid:recent(uid) for uid in (1,2,3,4)}
        prune(c)
        self.assertLess(c.execute('SELECT COUNT(*) FROM pvp_history').fetchone()[0],40)
        for uid in before:self.assertEqual(recent(uid),before[uid])

    def test_reactivated_rows_and_external_deletion_do_not_leave_expiry(self):
        c=self.conn
        c.execute("INSERT INTO event_runs VALUES('x',1,'af1','done','{}','{}',0,'2000')")
        c.execute("UPDATE event_runs SET status='running' WHERE id='x'")
        c.commit()
        self.assertEqual(c.execute('SELECT * FROM replay_expiry').fetchall(),[])

        prune(c,expire_completed=True)
        self.assertEqual(c.execute('SELECT id FROM event_runs').fetchall(),[('x',)])
        c.execute("UPDATE event_runs SET status='done' WHERE id='x'")
        c.execute("DELETE FROM event_runs WHERE id='x'")
        c.commit()
        self.assertEqual(c.execute('SELECT * FROM replay_expiry').fetchall(),[])

    def test_cancelled_raids_expire_but_broken_raids_remain_recoverable(self):
        c=self.conn
        for rid,status in [('cancelled','cancelled'),('broken','broken')]:
            c.execute('INSERT INTO raid_runs VALUES(?,1,2,?,\'{}\',\'2026-11-10\')',(rid,status))
        c.commit()
        prune(c,expire_completed=True)
        self.assertEqual(c.execute('SELECT id FROM raid_runs').fetchall(),[('broken',)])

    def test_cli_backup_compaction_and_legacy_removal(self):
        c=self.conn
        c.execute('CREATE TABLE battles(id INTEGER,payload TEXT)')
        c.execute('CREATE TABLE battle_relations(id INTEGER)')
        c.execute("INSERT INTO battles VALUES(1,?)",('x'*500000,))
        c.commit()
        script=Path(__file__).resolve().parents[1]/'scripts/cleanup_storage.py'
        original=self.path.read_bytes()
        subprocess.run([sys.executable,str(script),str(self.path)],check=True,capture_output=True)
        self.assertEqual(self.path.read_bytes(),original)
        subprocess.run([sys.executable,str(script),str(self.path),'--apply','--bot-stopped','--backup-dir',self.temp.name,'--expire-completed','--drop-legacy','--vacuum'],check=True,capture_output=True)
        backup=next(Path(self.temp.name).glob('*.backup'))
        with closing(sqlite3.connect(backup)) as b:self.assertEqual(b.execute('SELECT COUNT(*) FROM battles').fetchone()[0],1)
        self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='battles'").fetchone())
        self.assertEqual(c.execute('SELECT coins FROM users').fetchone()[0],1234)
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    async def test_market_receipt_attempt_is_not_repeated_after_restart(self):
        self.conn.close()
        store=MarketStore(self.db)
        async with store.tx() as c:
            card=await store._issue_card(c,1,'Player',CHARACTERS[0])
        row=await store.create(1,'card',card.instance_id,1,{'coins':{'min':100,'buyout':200}})
        await store.cancel(1,row['id'])
        user=SimpleNamespace(send=AsyncMock())
        bot=SimpleNamespace(db=self.db,get_user=lambda uid:user)
        cog=SimpleNamespace(bot=bot,store=store)
        await MarketCog.send_receipts(cog)
        await MarketCog.send_receipts(cog)
        self.assertEqual(user.send.await_count,1)
        self.conn=sqlite3.connect(self.path)
        prune(self.conn,expire_completed=True)
        self.assertEqual(self.conn.execute('SELECT * FROM market_receipt_delivery').fetchall(),[])
        self.assertEqual((await self.db.get_card(card.instance_id)).owner_id,1)

    async def test_absolute_view_expiry_releases_results_and_callbacks(self):
        view=discord.ui.View(timeout=None)
        view.lines=['heavy log']
        view.stats={'A':{'damage':99}}
        view.add_item(discord.ui.Button(label='Logs'))
        expire_result_view(view,deadline=time.monotonic()-1)
        await asyncio.sleep(.01)
        self.assertTrue(view.is_finished())
        self.assertEqual(view.lines,[])
        self.assertEqual(view.stats,{})
        self.assertEqual(view.children,[])

    async def test_activity_coalesces_without_database_polling_or_postponement(self):
        bot=SimpleNamespace(db=self.db,persistent_views=[],wait_until_ready=AsyncMock())
        cog=RetentionCog(bot)
        try:
            await cog.cog_load()
            self.assertIsNone(cog.timer)
            await self.db.get_user(1)
            self.assertIsNone(cog.timer, 'Existing-player reads must not schedule cleanup')
            await self.db.set_user_fields(1,coins=1235)
            first=cog.timer
            self.assertIsNotNone(first)
            for _ in range(100):cog.request()
            self.assertIs(cog.timer,first)
            self.assertAlmostEqual(first.when()-asyncio.get_running_loop().time(),1800,delta=2)
            first.cancel()
            with patch('hiq.cogs.retention.maintain',return_value=({},False)) as cleanup:
                cog.launch()
                await cog.worker
                cleanup.assert_called_once()
            self.assertIsNone(cog.timer, 'Empty queue must leave no recurring job')
        finally:cog.cog_unload()

    async def test_remaining_records_get_one_later_batch_and_restart_recovers_queue(self):
        self.conn.execute("INSERT INTO event_runs VALUES('done',1,'af1','done','{}','{}',0,'2026-11-10')")
        self.conn.commit()
        bot=SimpleNamespace(db=self.db,persistent_views=[],wait_until_ready=AsyncMock())
        cog=RetentionCog(bot)
        try:
            await cog.cog_load()
            self.assertIsNotNone(cog.timer)
            cog.timer.cancel()
            with patch('hiq.cogs.retention.maintain',return_value=({},True)):
                cog.launch()
                await cog.worker
            later=cog.timer
            self.assertIsNotNone(later)
            cog.request()
            self.assertIs(cog.timer,later)
        finally:cog.cog_unload()
        self.assertIsNone(self.db.on_write)
        self.assertTrue(later.cancelled())

    async def test_failed_uncommitted_write_does_not_schedule(self):
        # The notification is a synchronous in-memory signal.
        from unittest.mock import Mock
        callback=Mock()
        self.db.on_write=callback
        try:
            async with self.db.connect() as c:
                await c.execute('UPDATE users SET coins=0 WHERE discord_id=1')
                # No commit: connection closure rolls this change back.
            callback.assert_not_called()
        finally:self.db.on_write=None


if __name__=='__main__':unittest.main()
