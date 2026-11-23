"""An independent Tiny Titans PvP ladder with weekly Clan Dollar prizes."""
import json
from datetime import date,timedelta
from .pvp_store import PvPStore,calendar
from .pvp_cycles import cycle_for,week_start,prize_stars
from .event_modes import boosted_character
from .ttb_rotation import rotation,selected_ids

SCHEMA='''CREATE TABLE IF NOT EXISTS pvp_ttb_weeks (
 week TEXT PRIMARY KEY,cycle TEXT NOT NULL,phase INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0);'''

class PvPTTBStore(PvPStore):
    ladder='pvp_ttb'
    week_table='pvp_ttb_weeks'

    def window(self,at=None):
        cycle,day=calendar(at)
        return 'ttb:'+cycle,day

    async def match_specs(self,c,uid,fmt,day):
        picks=selected_ids(await rotation(c,day))
        return [(card,boosted_character(char) if char.id in picks else char,lane) for card,char,lane in await self.specs_tx(c,uid,fmt)]

    async def boosts(self,day=None):
        day=day or calendar()[1]
        async with self.tx() as c:return await rotation(c,day)

    async def settle_tx(self,c,current,day=None):
        day=day or calendar()[1];monday=week_start(day);base,phase=cycle_for(day);cycle='ttb:'+base
        await c.execute('INSERT OR IGNORE INTO pvp_ttb_weeks(week,cycle,phase) VALUES (?,?,?)',(monday.isoformat(),cycle,phase))
        oldest=await self.one(c,'SELECT MIN(week) AS week FROM pvp_ttb_weeks WHERE closed=0')
        cursor=date.fromisoformat(oldest['week'])
        while cursor<monday:
            key=cursor.isoformat();old_base,old_phase=cycle_for(key);old_cycle='ttb:'+old_base
            await c.execute('INSERT OR IGNORE INTO pvp_ttb_weeks(week,cycle,phase) VALUES (?,?,?)',(key,old_cycle,old_phase))
            row=await self.one(c,'SELECT closed FROM pvp_ttb_weeks WHERE week=?',(key,))
            if row['closed']:cursor+=timedelta(weeks=1);continue
            pending=await self.rows(c,"SELECT state_json FROM pvp_runs WHERE month=? AND status='running' AND rated=1",(old_cycle,))
            if any(week_start(json.loads(r['state_json'])['day'])<=cursor for r in pending):break
            board=await self.rows(c,'SELECT * FROM pvp_ratings WHERE month=? AND wins+losses+draws>0 ORDER BY rating DESC,wins DESC,losses ASC,owner_id',(old_cycle,))
            from .youth_rewards import currency
            for rank,player in enumerate(board,1):
                amount=sum(stars*100 for stars in prize_stars(rank,len(board)))
                paid=await currency(c,player['owner_id'],amount,clan=True,qualifies=True)
                await c.execute('INSERT INTO pvp_monthly_awards VALUES (?,?,?,?,?)',('ttb-week-'+key,player['owner_id'],rank,player['rating'],json.dumps(dict(clan_coins=paid))))
            await c.execute('UPDATE pvp_ttb_weeks SET closed=1 WHERE week=?',(key,))
            cursor+=timedelta(weeks=1)
        await c.execute("DELETE FROM pvp_pair_results WHERE season LIKE 'ttb:%' AND season<>? AND NOT EXISTS(SELECT 1 FROM pvp_runs WHERE month=pvp_pair_results.season AND status='running')",(cycle,))
        return cycle,phase
