"""Atomic free and paid packs: payment, pity, serials and cards settle together."""
from .community_store import CommunityStore
from .data import CHAR_BY_ID
from .economy import local_date_key,pack_cost,pull_pack,pull_starter_daily
from .progression import resolve_pity_sequence


class PackStore(CommunityStore):
    async def open(self,uid,username,pack_type='base',*,daily=False,day=None,position=None,school=None):
        if daily and pack_type!='base':raise ValueError('Daily packs must be Base Packs.')
        if pack_type not in ('base','position','school'):raise ValueError('Unknown pack type.')
        await self.db.ensure_user(uid)
        async with self.tx() as c:
            user=await self.one(c,'SELECT * FROM users WHERE discord_id=?',(uid,))
            today=day if day is not None else local_date_key()
            if daily and user['last_daily_date']==today:return None
            starter=daily and not user['starter_daily_claimed']
            chars=pull_starter_daily() if starter else pull_pack(pack_type,position=position,school=school)
            cost=0 if daily else pack_cost(pack_type)
            if user['coins']<cost:
                label={'base':'Base','position':'Position','school':'School'}[pack_type]
                raise ValueError(f'You need **{cost} coins** for a {label} Pack.')
            pity=await self.one(c,'SELECT * FROM pity WHERE owner_id=?',(uid,))
            if pity:
                target=CHAR_BY_ID.get(pity['character_id'])
                if target:
                    eligible=pack_type=='base' or (pack_type=='position' and target.position.casefold()==(position or '').casefold()) or (pack_type=='school' and target.school.casefold()==(school or '').casefold())
                    result=resolve_pity_sequence(chars,target,armed=bool(pity['armed']),target_eligible=eligible)
                    chars=result.cards
                    if result.cleared:await c.execute('DELETE FROM pity WHERE owner_id=?',(uid,))
                    else:await c.execute('UPDATE pity SET armed=? WHERE owner_id=?',(int(result.armed),uid))
                else:await c.execute('DELETE FROM pity WHERE owner_id=?',(uid,))
            await c.execute('UPDATE users SET coins=coins-? WHERE discord_id=?',(cost,uid))
            cards=[await self._issue_card(c,uid,username,char) for char in chars]
            if daily:
                await c.execute('UPDATE users SET last_daily_date=?,starter_daily_claimed=1 WHERE discord_id=?',(today,uid))
            return dict(chars=chars,cards=cards,coins=user['coins']-cost,starter=starter)
