"""Coalesce data-changing activity into delayed storage cleanup batches."""
import asyncio
import logging
from discord.ext import commands
from ..retention import maintain

log = logging.getLogger(__name__)
BATCH_DELAY_SECONDS = 30 * 60


class RetentionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timer = None
        self.worker = None
        self.dirty = False
        self.closed = False

    async def cog_load(self):
        self.bot.db.on_write = self.request
        # One startup read recovers pending cleanup after a restart.
        async with self.bot.db.connect() as c:
            cur = await c.execute('SELECT 1 FROM replay_expiry LIMIT 1')
            pending = await cur.fetchone()
        if pending:
            self.request()

    def cog_unload(self):
        self.closed = True
        if self.bot.db.on_write == self.request:
            self.bot.db.on_write = None
        if self.timer is not None:
            self.timer.cancel()
        if self.worker is not None:
            self.worker.cancel()

    def request(self):
        """No SQL here. Repeated writes share one timer, without postponing it."""
        if self.closed:
            return
        if self.worker is not None:
            self.dirty = True
        elif self.timer is None:
            self.timer = asyncio.get_running_loop().call_later(BATCH_DELAY_SECONDS, self.launch)

    def launch(self):
        self.timer = None
        if not self.closed:
            self.worker = asyncio.create_task(self.cleanup())

    async def cleanup(self):
        pending = False
        self.dirty = False
        try:
            await self.bot.wait_until_ready()
            async with self.bot.db._lock:
                _, pending = await asyncio.to_thread(maintain, self.bot.db.path)
            for view in list(self.bot.persistent_views):
                for item in list(view.children):
                    custom_id = getattr(item, 'custom_id', '') or ''
                    if custom_id.startswith('hiq:to:'):
                        table = 'tournaments'
                    elif custom_id.startswith(('v11:sealed:', 'v8:pref:')):
                        table = 'community_sessions'
                    else:
                        continue
                    rid = custom_id.split(':')[2]
                    async with self.bot.db.connect() as c:
                        cur = await c.execute(f'SELECT 1 FROM {table} WHERE id=?', (rid,))
                        exists = await cur.fetchone()
                    if not exists:
                        view.stop()
                        view.clear_items()
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pending = True
            log.exception('Storage cleanup failed; retrying in the next delayed batch.')
        finally:
            self.worker = None
            # Finish queued work, including rows too new for the previous batch.
            # An empty queue means no further scheduled jobs.
            if pending or self.dirty:
                self.request()


async def setup(bot):
    await bot.add_cog(RetentionCog(bot))
