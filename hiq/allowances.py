"""Calendar-week allowance windows in the configured game timezone."""
from __future__ import annotations
from datetime import date,datetime,timedelta
from .config import TIMEZONE
from .economy import local_date_key


def calendar_days(day=None):
    """Only elapsed days are available; Monday's reset discards the old week."""
    end=date.fromisoformat(day or local_date_key())
    start=end-timedelta(days=end.weekday())
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]


async def allowance_days(conn,uid,day=None):
    days=calendar_days(day)
    row=await (await conn.execute('SELECT created_at FROM users WHERE discord_id=?',(uid,))).fetchone()
    if row:
        joined=datetime.fromisoformat(row['created_at'])
        if joined.tzinfo is None:joined=joined.replace(tzinfo=TIMEZONE)
        joined=joined.astimezone(TIMEZONE).date().isoformat()
        # A caller may inspect legacy historical dates predating registration.
        # Such a request has at most its one requested day, never a future bank.
        start=min(joined,days[-1])
        days=[d for d in days if d>=start]
    return days
