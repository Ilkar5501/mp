"""Quest progress, rollover rules and collection earnings bonuses."""
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
    from .youth_rewards import highest,OIKAWA
    async with db.connect() as c:
        merge=await highest(c,uid,OIKAWA)
        cur=await c.execute('SELECT reward_key,SUM(claims) AS n FROM youth_daily WHERE owner_id=? AND day BETWEEN ? AND ? GROUP BY reward_key',(uid,(await store.days(uid,today))[0],today))
        extras={r['reward_key']:r['n'] for r in await cur.fetchall()}
        cur=await c.execute('SELECT reward_key,claims FROM youth_daily WHERE owner_id=? AND day=?',(uid,today));today_extras={r['reward_key']:r['claims'] for r in await cur.fetchall()}
    extra=int(merge is not None);rate=(merge or 0)*.05
    def factor(key):return days if key in ROLLOVER_MODES else 1
    def row(key):return progress.get(key,{})
    def block(label,key,count_label,limit,cap,note,currency='🪙',column='coins'):
        f=factor(key);r=row(key);amount=r.get(column,0)
        count=(amount+99)//100 if key=='raid' else r.get('wins',0)
        bonus=extra if key!='prefecture' else 0
        if key=='raid':count+=extras.get('raid_'+('coins' if column=='coins' else 'clancoins'),0)
        normal={'battle':50,'raid':100,'pvp':100,'war':200,'tournament':450}.get(key,0)
        return f'> **{label}**\n{count_label}: {min(count,(limit+bonus)*f)} / {(limit+bonus)*f} | {currency}: {amount} / {cap*f+int(normal*rate)*bonus*f}\n-# {note}'
    coins=[block('Battles ⚔️','battle','Battle Count',9,450,'50 per win, 450 daily cap.'),
           block('Prefecture Battles','prefecture','Battle Count',3,300,'100 per win, 300 daily cap.')]
    f=factor('gauntlet_regional')
    coins.append('> **Gauntlets 🥊**\n'+ '\n'.join(f'{label}: Clear Count {min(row(key).get("wins",0),f*(1+extra))} / {f*(1+extra)} | 🪙: {row(key).get("coins",0)} / {(250+int(250*rate))*f}' for key,label in [('gauntlet_regional','Regional'),('gauntlet_national','National')])+'\n-# 200 per clear, plus 50 for a lineup you haven’t cleared with this week. Up to 250 per day for each gauntlet.')
    coins.extend([block('Raids 🏴‍☠️','raid','Drops',3,300,'Up to 100 per Coin drop, 300 daily cap.'),
                  block('Tournaments 🏆','tournament','Wins',1,450,'300 per win. Separately, 150 per entry with at least four humans, win or lose.'),
                  block('PvP 🗡️','pvp','PvP Count',4,400,'100 per distinct opponent defeated, 400 daily cap.'),
                  block('Clan Wars 🛡️','war','Wins',1,200,'200 per win, 200 daily cap.')])
    tournament_human=row('tournament_human');tournament_main=row('tournament')
    coins[4]=(f'> **Tournaments 🏆**\nWins: {min(tournament_main.get("wins",0),days*(1+extra))} / {days*(1+extra)} | 🪙: {tournament_main.get("coins",0)-tournament_human.get("coins",0)} / {(300+int(300*rate))*days}\n'
              f'Human entries: {min(tournament_human.get("wins",0),days*(1+extra))} / {days*(1+extra)} | 🪙: {tournament_human.get("coins",0)} / {(150+int(150*rate))*days}\n'
              '-# Separate allowances: 300 per win; 150 to every human entrant with at least four humans, regardless of outcome.')
    community=CommunityStore(db);clan=await community.progress(uid)
    async with db.connect() as c:
        cur=await c.execute('SELECT checkin_clan FROM clan_daily WHERE user_id=? AND day=?',(uid,today));check=await cur.fetchone()
        bonus=None
        if check and check['checkin_clan']:
            cur=await c.execute('SELECT 1 FROM clan_checkin_bonus WHERE clan_id=? AND day=?',(check['checkin_clan'],today));bonus=await cur.fetchone()
    checkin=150*int(clan['checked_in'])+50*int(bool(bonus))
    dollars=[f'> **Check-in ✅**\nCheck-in: {int(clan["checked_in"])+today_extras.get("checkin",0)} / {1+extra} | 💵: {checkin+int(150*rate)*today_extras.get("checkin",0)} / {200+int(150*rate)}\n-# 150 when you check in, plus 50 when all five clan members check in.',
             block('Raids 🏴‍☠️','raid','Drops',3,300,'Up to 100 per Clan Dollar drop, 300 daily cap.','💵','clan_coins'),
             block('Clan Wars 🛡️','war','Wins',1,200,'200 per win, 200 daily cap.','💵','clan_coins')]
    async with db.connect() as c:
        cur=await c.execute('SELECT 1 FROM contest_scores WHERE owner_id=? AND day=?',(uid,today));attempted=bool(await cur.fetchone())
    dollars.append(f'> **Clan Contest 💥**\nAttempts: {int(attempted)+today_extras.get("contest",0)} / {1+extra} | 💵: {(200 if attempted else 0)+int(200*rate)*today_extras.get("contest",0)} / {200+int(200*rate)}\n-# 200 for daily participation. Retries improve your best score; weekly rankings award extra prizes.')
    shards=await RaidStore(db).shard_progress(uid)
    training=[f'{school}: {await db.training_wins(uid,school,week_key())} / 9' for school in sorted(TRAINING_SCHOOLS)]
    shard_text='> **Training 🎯**\n'+'\n'.join(training)+'\n-# Each school offers nine shards per week.\n\n> **Raids 🏴‍☠️**\nItem Shards: check `;items` for each item’s progress.\n'+f'Character Shards: {shards.get("character_shard",0)} / {50*days}\n-# One per character-shard drop, 50 daily allowance; 350 weekly cap.'
    reset=int(next_week_reset().timestamp())
    rollover=f'Some dailies carry over, so missing a day doesn’t mean losing those rewards straight away.\n\nEach new day adds another allowance for **{", ".join(k.title().replace("_"," ") for k in sorted(ROLLOVER_MODES))}**. Their caps on these pages grow with that allowance.\n\nUnused portions stay available until the weekly reset <t:{reset}:F>. After that, the old week’s missed rewards are gone. You cannot use future days early.\n\nPvP, check-ins, daily packs, donations, and war rewards do not carry over. Training has its own nine-shard weekly limit per school. Already-earned Coins, Clan Dollars, and shards stay in your inventory.'
    if extra:rollover+=f'\n\nYoung Emperor grants one extra allowance at {rate:.0%} currency rewards. Training is unchanged. Kings of the Playground adds its currency bonus outside these caps.'
    from .event_store import EventStore
    marks=(await EventStore(db).wallet(uid))['af1_marks']
    shard_text+=f'\n\n> **AF1 Marks**\nWallet: {marks}. Earn up to 25 per day; use ;fuse.\nTiny Titans: each item accrues 25 shards/day; view each allowance with ;items.'
    earnings=(
        '**3★ Oikawa — Young Emperor**\n'
        'Adds one extra daily reward allowance for Battles, each Gauntlet, Tournaments, PvP, Clan Wars, check-ins, raid Coins and Clan Dollars, and Clan Contest. Training is unchanged. '
        'The extra allowance pays **merge × 5%** of its normal currency reward: +0 pays 0%, +1 pays 5%, and +10 pays 50%. Fractional Coins and Clan Dollars round down. '
        'Normal allowances keep their full rewards. Extra allowances follow each activity’s usual rollover rules.\n\n'
        'Tournament win rewards (300) and four-human participation rewards (150) have independent allowances. Every human entrant can earn participation rewards, win or lose; a qualifying champion can receive both.\n\n'
        '**3★ Kageyama — King of the Lost Court**\n'
        'Eligible non-currency drops from Raids, AF1 and Tiny Titans have a **merge × 1%** chance to be duplicated: 0% at +0, up to 10% at +10. '
        'This includes eligible item and character shards. The original drop must pass its normal cap; a duplicate is extra and does not consume that cap. '
        'Coins, Clan Dollars and AF1 Marks are not duplicated.\n\n'
        '**Oikawa & Kageyama — Kings of the Playground**\n'
        'Adds **merge + 1 Coins or Clan Dollars per reward payout**: +1 at +0, up to +11 at +10. '
        'This bonus is outside normal earning caps. It does not raise activity counts or shard allowances. Purchases, trades and escrow refunds do not generate it.\n\n'
        '**Ownership and stacking**\n'
        'These effects work from your collection; the cards do not need to be on an active team. Each effect uses only your highest-merged owned copy, and duplicate copies do not stack. '
        'Losing your last copy through fusion, trade, recycle or donation stops its future bonus. A remaining copy continues at its own merge level. '
        'Earned rewards stay, and used allowances do not reset.\n\n'
        'The earlier pages show Oikawa-adjusted limits. Their short reward notes describe the normal base rewards; Kageyama’s duplicates and the Duo’s extra currency sit outside those base caps.'
    )
    return [discord.Embed(title=title,description=text,color=PURPLE) for title,text in [('Coin Dailies 🪙','\n\n'.join(coins)),('Clan Dollar Dailies 💵','\n\n'.join(dollars)),('Shards 💎',shard_text),('How rollover works',rollover),('How cards affect earnings',earnings)]]
