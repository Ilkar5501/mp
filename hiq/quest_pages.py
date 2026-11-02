"""Four short quest pages, using earned rewards rather than wallet balances."""
import discord
from .rewards import RewardStore, ROLLOVER_MODES
from .raid_store import RaidStore
from .community_store import CommunityStore
from .economy import local_date_key, week_key, next_week_reset
from .progression import TRAINING_SCHOOLS
from .ui import PURPLE


async def quest_pages(db,uid):
    today=local_date_key();store=RewardStore(db)
    progress=await store.daily(uid,today);days=len(await store.days(uid,today))
    def factor(key):return days if key in ROLLOVER_MODES else 1
    def row(key):return progress.get(key,{})
    def block(label,key,count_label,limit,cap,note,currency='🪙',column='coins'):
        f=factor(key);r=row(key);amount=r.get(column,0)
        count=(amount+99)//100 if key=='raid' else r.get('wins',0)
        return f'> **{label}**\n{count_label}: {min(count,limit*f)} / {limit*f} | {currency}: {amount} / {cap*f}\n-# {note}'
    coins=[block('Battles ⚔️','battle','Battle Count',25,450,'50 per win, 450 daily cap.'),
           block('Prefecture Battles','prefecture','Battle Count',3,300,'100 per win, 300 daily cap.')]
    f=factor('gauntlet_regional')
    coins.append('> **Gauntlets 🥊**\n'+ '\n'.join(f'{label}: Clear Count {min(row(key).get("wins",0),f)} / {f} | 🪙: {row(key).get("coins",0)} / {250*f}' for key,label in [('gauntlet_regional','Regional'),('gauntlet_national','National')])+'\n-# 200 per clear, plus 50 for a lineup you haven’t cleared with this week. Up to 250 per day for each gauntlet.')
    coins.extend([block('Raids 🏴‍☠️','raid','Drops',3,300,'Up to 100 per Coin drop, 300 daily cap.'),
                  block('Tournaments 🏆','tournament','Wins',1,450,'300 for a win, plus 150 with at least four humans. Repeat CPU wins cannot fill the bonus portion.'),
                  block('PvP 🗡️','pvp','PvP Count',4,400,'100 per distinct opponent defeated, 400 daily cap.'),
                  block('Clan Wars 🛡️','war','Wins',1,200,'200 per win, 200 daily cap.')])
    community=CommunityStore(db);clan=await community.progress(uid)
    async with db.connect() as c:
        cur=await c.execute('SELECT checkin_clan FROM clan_daily WHERE user_id=? AND day=?',(uid,today));check=await cur.fetchone()
        bonus=None
        if check and check['checkin_clan']:
            cur=await c.execute('SELECT 1 FROM clan_checkin_bonus WHERE clan_id=? AND day=?',(check['checkin_clan'],today));bonus=await cur.fetchone()
    checkin=150*int(clan['checked_in'])+50*int(bool(bonus))
    dollars=[f'> **Check-in ✅**\nCheck-in: {int(clan["checked_in"])} / 1 | 💵: {checkin} / 200\n-# 150 when you check in, plus 50 when all five clan members check in.',
             block('Raids 🏴‍☠️','raid','Drops',3,300,'Up to 100 per Clan Dollar drop, 300 daily cap.','💵','clan_coins'),
             block('Clan Wars 🛡️','war','Wins',1,200,'200 per win, 200 daily cap.','💵','clan_coins')]
    async with db.connect() as c:
        cur=await c.execute('SELECT 1 FROM contest_scores WHERE owner_id=? AND day=?',(uid,today));attempted=bool(await cur.fetchone())
    dollars.append(f'> **Clan Contest 💥**\nAttempts: {int(attempted)} / 1 | 💵: {200 if attempted else 0} / 200\n-# 200 for daily participation. Retries improve your best score; weekly rankings award extra prizes.')
    shards=await RaidStore(db).shard_progress(uid)
    training=[f'{school}: {await db.training_wins(uid,school,week_key())} / 9' for school in sorted(TRAINING_SCHOOLS)]
    shard_text='> **Training 🎯**\n'+'\n'.join(training)+'\n-# Each school offers nine shards per week.\n\n> **Raids 🏴‍☠️**\nItem Shards: check `;items` for each item’s progress.\n'+f'Character Shards: {shards.get("character_shard",0)} / {50*days}\n-# One per character-shard drop, 50 daily allowance; 350 weekly cap.'
    reset=int(next_week_reset().timestamp())
    rollover=f'Some dailies carry over, so missing a day doesn’t mean losing those rewards straight away.\n\nEach new day adds another allowance for **{", ".join(k.title().replace("_"," ") for k in sorted(ROLLOVER_MODES))}**. Their caps on these pages grow with that allowance.\n\nUnused portions stay available until the weekly reset <t:{reset}:F>. After that, the old week’s missed rewards are gone. You cannot use future days early.\n\nPvP, check-ins, daily packs, donations, and war rewards do not carry over. Training has its own nine-shard weekly limit per school. Already-earned Coins, Clan Dollars, and shards stay in your inventory.'
    return [discord.Embed(title=title,description=text,color=PURPLE) for title,text in [('Coin Dailies 🪙','\n\n'.join(coins)),('Clan Dollar Dailies 💵','\n\n'.join(dollars)),('Shards 💎',shard_text),('How rollover works',rollover)]]
