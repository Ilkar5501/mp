from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from ..battle.engine import BattleEngine
from ..cards import display_title, nature_text
from ..config import GAME_CONFIG
from ..abbreviations import school_name
from ..data import CHAR_BY_ID, find_character
from ..economy import next_week_reset, week_key
from ..gauntlet import training_specs
from ..progression import TRAINING_SCHOOLS, craft_cost, recycle_payout, training_difficulty

PURPLE = discord.Color.from_rgb(72, 42, 115)
SCHOOL_CHOICES = [app_commands.Choice(name=s, value=s) for s in sorted(TRAINING_SCHOOLS,key=str.casefold)]


def _resolve_school(raw: str) -> str | None:
    q=school_name(raw).casefold().strip()
    return next((s for s in TRAINING_SCHOOLS if s.casefold()==q), None)


def _uid(side: str, index: int, char_id: str) -> str:
    return f'{side}{index}:{char_id}'


def _award_units(result, a_specs, b_specs):
    rows=[]
    for side,specs in (('A',a_specs),('B',b_specs)):
        for i,(_,c,lane) in enumerate(specs):
            rows.append((side,c,lane,result.stats.get(_uid(side,i,c.id),{})))
    op=max(rows,key=lambda r:(r[3].get('damage_dealt',0),r[3].get('crits',0),-r[3].get('attacks_made',0),r[1].name))
    dp=max(rows,key=lambda r:(r[3].get('damage_taken',0)+r[3].get('damage_mitigated',0),r[3].get('damage_mitigated',0),r[3].get('attacks_tanked',0),r[1].name))
    return op,dp


def _compact_result(title: str, result, a_specs, b_specs, name_a: str, name_b: str) -> discord.Embed:
    winner=name_a if result.winner_side=='A' else name_b if result.winner_side=='B' else 'Draw'
    e=discord.Embed(title=f'{title} — {winner}',color=PURPLE)
    def block(side,specs):
        out=[]
        for i,(card,c,lane) in enumerate(specs):
            st=result.stats.get(_uid(side,i,c.id),{})
            out.append(f'**{"F" if lane=="front" else "B"} • {c.name} +{card.merge_level if card else 0}** — {st.get("damage_dealt",0)} dealt / {st.get("damage_taken",0)} tanked / {st.get("damage_mitigated",0)} mitigated')
        return '\n'.join(out)
    e.add_field(name=f'{name_a} • {result.team_a_hp} HP',value=block('A',a_specs),inline=False)
    e.add_field(name=f'{name_b} • {result.team_b_hp} HP',value=block('B',b_specs),inline=False)
    op,dp=_award_units(result,a_specs,b_specs)
    e.add_field(name='⚔️ OPOM',value=f'**{op[1].name}** — {op[3].get("damage_dealt",0)} damage',inline=True)
    e.add_field(name='🛡️ DPOM',value=f'**{dp[1].name}** — {dp[3].get("damage_taken",0)+dp[3].get("damage_mitigated",0)} pressure',inline=True)
    e.set_footer(text=f'{result.turns} turns')
    return e


class RecycleConfirmView(discord.ui.View):
    def __init__(self, cog: 'ProgressionCog', owner_id: int, instance_id: str):
        super().__init__(timeout=120)
        self.cog=cog; self.owner_id=owner_id; self.instance_id=instance_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This recycle confirmation belongs to another player.',ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Confirm Recycle',style=discord.ButtonStyle.danger)
    async def confirm(self,interaction:discord.Interaction,button:discord.ui.Button):
        card=await self.cog.bot.db.get_card(self.instance_id)
        if not card or card.owner_id!=self.owner_id:
            await interaction.response.edit_message(content='That card is no longer in your collection.',embed=None,view=None); return
        if card.locked:
            await interaction.response.edit_message(content='That card is locked and cannot be recycled.',embed=None,view=None); return
        char=CHAR_BY_ID[card.character_id]
        payout=recycle_payout(char.rarity,card.merge_level)
        try:
            await self.cog.bot.db.recycle_card(self.owner_id,card.instance_id,payout)
        except ValueError as exc:
            await interaction.response.edit_message(content=str(exc),embed=None,view=None); return
        await interaction.response.edit_message(content=f'♻️ Recycled **{display_title(char)} +{card.merge_level}** for **{payout} coins**. Any saved-team slot using that exact instance is now empty.',embed=None,view=None)

    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.edit_message(content='Recycle cancelled.',embed=None,view=None)


@dataclass
class TradeOffer:
    cards: list[str] = field(default_factory=list)
    coins: int = 0
    shards: dict[str,int] = field(default_factory=dict)
    items: list[str] = field(default_factory=list)
    raid_shards: dict[str,dict[str,int]] = field(default_factory=dict)

    def as_dict(self):
        return {'cards':list(self.cards),'coins':int(self.coins),'shards':dict(self.shards),'items':list(self.items),'raid_shards':{kind:dict(groups) for kind,groups in self.raid_shards.items()}}


class TradeOfferModal(discord.ui.Modal,title='Edit your trade offer'):
    card_ids=discord.ui.TextInput(label='Card IDs',placeholder='comma-separated exact instance IDs; blank = none',required=False,max_length=1000,style=discord.TextStyle.paragraph)
    coins=discord.ui.TextInput(label='Coins',placeholder='0',required=False,max_length=12)
    shards=discord.ui.TextInput(label='School shards',placeholder='Karasuno:3, Nekoma:1',required=False,max_length=500)

    items=discord.ui.TextInput(label='Inventory item IDs',placeholder='Equipment, Merge Copies, Blessings and packs; IDs',required=False,max_length=1000,style=discord.TextStyle.paragraph)

    raid_shards=discord.ui.TextInput(label='Item / character shards',placeholder="Captain's Armband:5, Teradomari — Agile Hunter:3",required=False,max_length=1000,style=discord.TextStyle.paragraph)

    def __init__(self,parent:'TradeBuilderView',user_id:int):
        super().__init__(); self.parent_view=parent; self.user_id=user_id
        offer=parent.offers[user_id]
        self.card_ids.default=', '.join(offer.cards)
        self.items.default=', '.join(offer.items)
        self.coins.default=str(offer.coins)
        self.shards.default=', '.join(f'{k}:{v}' for k,v in offer.shards.items())
        from ..shard_balances import label
        self.raid_shards.default=', '.join(f'{label(kind,detail).removesuffix(" Shard")}:{qty}' for kind,groups in offer.raid_shards.items() for detail,qty in groups.items())

    async def on_submit(self,interaction:discord.Interaction):
        if self.parent_view.finished or self.parent_view.cancelled or self.parent_view.processing:
            await interaction.response.send_message('This trade is closed or processing.',ephemeral=True); return
        try:
            cards=[x.strip() for x in str(self.card_ids.value or '').split(',') if x.strip()]
            if len(cards)!=len(set(cards)): raise ValueError('A card ID was entered more than once.')
            items=[x.strip() for x in str(self.items.value or '').split(',') if x.strip()]
            if len(items)!=len(set(items)): raise ValueError('An item ID was entered more than once.')
            from ..community_store import CommunityStore
            owned={it['id'] for it in await CommunityStore(self.parent_view.bot.db).items(self.user_id)}
            if any(iid not in owned for iid in items): raise ValueError('You do not own one of those inventory item IDs.')
            coins=int(str(self.coins.value or '0').strip() or 0)
            if coins<0: raise ValueError('Coins cannot be negative.')
            shards={}
            for piece in [x.strip() for x in str(self.shards.value or '').split(',') if x.strip()]:
                if ':' not in piece: raise ValueError('Shard entries must use `School:count`.')
                school_raw,count_raw=piece.rsplit(':',1); school=_resolve_school(school_raw)
                if not school: raise ValueError(f'Unknown shard school: {school_raw}')
                qty=int(count_raw.strip());
                if qty<0: raise ValueError('Shard quantities cannot be negative.')
                if qty: shards[school]=qty
            from ..shard_balances import resolve,balance as shard_balance
            raid_shards={}
            for piece in str(self.raid_shards.value or '').split(','):
                if not piece.strip():continue
                if ':' not in piece:raise ValueError('Use item or character name:count for shards.')
                name,count=piece.rsplit(':',1);kind,detail=resolve(name.strip());qty=int(count.strip())
                if qty<0:raise ValueError('Shard quantities cannot be negative.')
                groups=raid_shards.setdefault(kind,{})
                if detail in groups:raise ValueError('Enter each shard group only once.')
                groups[detail]=qty
            async with self.parent_view.bot.db.connect() as conn:
                for kind,groups in raid_shards.items():
                    for detail,qty in groups.items():
                        if qty>await shard_balance(conn,self.user_id,kind,detail):raise ValueError('Not enough '+detail+' shards.')
            # Friendly early validation; commit validates again atomically.
            for iid in cards:
                card=await self.parent_view.bot.db.get_card(iid)
                if not card or card.owner_id!=self.user_id: raise ValueError(f'You do not own `{iid}`.')
                if card.locked: raise ValueError(f'`{iid}` is locked and cannot be traded.')
            user=await self.parent_view.bot.db.get_user(self.user_id)
            if user.coins<coins: raise ValueError('You do not currently have that many coins.')
            for school,qty in shards.items():
                if await self.parent_view.bot.db.shard_balance(self.user_id,school)<qty: raise ValueError(f'You do not have {qty} {school} shards.')
        except (ValueError,TypeError) as exc:
            await interaction.response.send_message(str(exc),ephemeral=True); return
        previous=self.parent_view.offers[self.user_id]
        self.parent_view.offers[self.user_id]=TradeOffer(cards,coins,shards,items,raid_shards)
        if len(await self.parent_view._offer_text(self.user_id))>2400:
            self.parent_view.offers[self.user_id]=previous
            await interaction.response.send_message('This offer is too long to display. Trade fewer cards, items or shard groups at once.',ephemeral=True);return
        self.parent_view.confirmed.clear()
        await interaction.response.edit_message(embed=await self.parent_view.embed(),view=self.parent_view)


class TradeBuilderView(discord.ui.View):
    def __init__(self,bot,initiator:discord.Member,recipient:discord.Member):
        super().__init__(timeout=900); self.bot=bot; self.initiator=initiator; self.recipient=recipient
        self.offers={initiator.id:TradeOffer(),recipient.id:TradeOffer()}; self.confirmed=set(); self.finished=False; self.cancelled=False; self.processing=False
        self.activity_key=bot.session_registry.add_view(self.offers,self) if hasattr(bot,"session_registry") else None

    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if self.finished or self.cancelled:
            await interaction.response.send_message('This trade is already closed.',ephemeral=True); return False
        if interaction.user.id not in self.offers:
            await interaction.response.send_message('Only the two trade participants can use these controls.',ephemeral=True); return False
        return True

    async def _offer_text(self,uid:int)->str:
        o=self.offers[uid]; parts=[]
        if o.cards:
            lines=[]
            for iid in o.cards:
                card=await self.bot.db.get_card(iid)
                if card:
                    c=CHAR_BY_ID[card.character_id]; lines.append(f'`{iid}` — {display_title(c)} +{card.merge_level}')
                else: lines.append(f'`{iid}` — unavailable')
            parts.append('**Cards**\n'+'\n'.join(lines))
        if o.items:
            from ..community_store import CommunityStore
            from .community import item_label
            items={it['id']:it for it in await CommunityStore(self.bot.db).items(uid)}
            parts.append('**Inventory items**\n'+'\n'.join(f'`{iid}` — '+(item_label(items[iid]) if iid in items else 'unavailable') for iid in o.items))
        if o.raid_shards:
            from ..shard_balances import label
            parts.append('**Item / character shards**\n'+'\n'.join(f'{label(kind,detail)} — **{qty}x**' for kind,groups in o.raid_shards.items() for detail,qty in groups.items()))
        if o.coins: parts.append(f'**Coins:** {o.coins:,}')
        if o.shards: parts.append('**Shards:** '+', '.join(f'{s} ×{q}' for s,q in o.shards.items()))
        return '\n'.join(parts) if parts else '*Nothing offered yet.*'

    async def embed(self):
        e=discord.Embed(title='Trade Builder',description='Each player edits their own offer. **Both players must Confirm** after the final edit. Any edit resets confirmations.',color=PURPLE)
        for member in (self.initiator,self.recipient):
            mark=' ✅ Confirmed' if member.id in self.confirmed else ''
            chunks=[];chunk=[];size=0
            for line in (await self._offer_text(member.id)).splitlines():
                if chunk and size+len(line)+1>950:chunks.append('\n'.join(chunk));chunk=[];size=0
                chunk.append(line);size+=len(line)+1
            if chunk:chunks.append('\n'.join(chunk))
            for index,text in enumerate(chunks):
                e.add_field(name=f'{member.display_name}{mark}'+(' • continued' if index else ''),value=text,inline=False)
        e.set_footer(text='Locked cards cannot be traded. Card IDs, merges, natures, and original-owner history travel with the card.')
        return e

    @discord.ui.button(label='Edit My Offer',style=discord.ButtonStyle.primary)
    async def edit(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(TradeOfferModal(self,interaction.user.id))

    @discord.ui.button(label='Confirm',style=discord.ButtonStyle.success)
    async def confirm(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.finished or self.cancelled or self.processing:
            await interaction.response.send_message('This trade is already closed or processing.',ephemeral=True); return
        self.confirmed.add(interaction.user.id)
        if len(self.confirmed)<2:
            await interaction.response.edit_message(embed=await self.embed(),view=self); return
        self.processing=True
        try:
            await self.bot.db.execute_trade(self.initiator.id,self.offers[self.initiator.id].as_dict(),self.recipient.id,self.offers[self.recipient.id].as_dict())
        except ValueError as exc:
            self.processing=False
            self.confirmed.clear(); await interaction.response.edit_message(embed=await self.embed(),view=self)
            await interaction.followup.send(f'Trade could not complete: {exc}',ephemeral=True); return
        self.finished=True; self.processing=False
        if self.activity_key:self.bot.session_registry.remove_view(self.activity_key)
        await interaction.response.edit_message(content=f'✅ Trade completed between **{self.initiator.display_name}** and **{self.recipient.display_name}**. Any traded card that occupied a saved-team slot has left that slot empty.',embed=None,view=None)

    @discord.ui.button(label='Cancel Trade',style=discord.ButtonStyle.danger)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.processing:
            await interaction.response.send_message('This trade is processing.',ephemeral=True); return
        self.finished=True
        if self.activity_key:self.bot.session_registry.remove_view(self.activity_key)
        await interaction.response.edit_message(content=f'❌ Trade cancelled by **{interaction.user.display_name}**.',embed=None,view=None)


class TradeRequestView(discord.ui.View):
    def __init__(self,bot,initiator:discord.Member,recipient:discord.Member):
        super().__init__(timeout=120); self.bot=bot; self.initiator=initiator; self.recipient=recipient; self.finished=False; self.cancelled=False
        self.activity_key=bot.session_registry.add_view({initiator.id,recipient.id},self) if hasattr(bot,"session_registry") else None

    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if self.finished or self.cancelled:
            await interaction.response.send_message('This request is already closed.',ephemeral=True); return False
        if interaction.user.id!=self.recipient.id:
            await interaction.response.send_message('Only the requested player can accept or decline this trade.',ephemeral=True); return False
        return True

    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success)
    async def accept(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.finished or self.cancelled:
            await interaction.response.send_message('This request is already closed.',ephemeral=True); return
        self.finished=True
        if self.activity_key:self.bot.session_registry.remove_view(self.activity_key)
        view=TradeBuilderView(self.bot,self.initiator,self.recipient)
        await interaction.response.edit_message(content=None,embed=await view.embed(),view=view)

    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger)
    async def decline(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.finished=True
        if self.activity_key:self.bot.session_registry.remove_view(self.activity_key)
        await interaction.response.edit_message(content=f'**{self.recipient.display_name}** declined the trade.',embed=None,view=None)


class ProgressionCog(commands.Cog):
    def __init__(self,bot): self.bot=bot

    async def _active_specs(self,owner_id:int):
        team=await self.bot.db.active_team(owner_id)
        if not team or not team.complete: return None,team
        specs=[]
        for iid,lane in [(iid,lane) for _,iid,lane in team.entries]:
            card=await self.bot.db.get_card(iid)
            if not card: return None,team
            specs.append((card,CHAR_BY_ID[card.character_id],lane))
        return specs,team

    @commands.hybrid_command(name='training',description='Train against a school for one school shard on a win.')
    @app_commands.choices(school=SCHOOL_CHOICES)
    async def training(self,ctx:commands.Context,*,school:str):
        school=_resolve_school(school)
        if not school:
            await ctx.send('Choose one of: '+', '.join(TRAINING_SCHOOLS)); return
        specs,team=await self._active_specs(ctx.author.id)
        if not specs:
            await ctx.send('Your active team must be complete before training. Use `;teamuse <slot>` first.'); return
        if team.team_format != 3:
            await ctx.send('Training is **3v3**. Select a complete 3v3 team with `;teamuse <slot>` first.'); return
        wk=week_key(); wins=await self.bot.db.training_wins(ctx.author.id,school,wk)
        if wins>=9:
            reset=int(next_week_reset().timestamp())
            await ctx.send(f'You already earned all **9 {school} shards** available from training this week. Training resets <t:{reset}:R>.'); return
        difficulty,merge=training_difficulty(wins)
        enemy=training_specs(school,merge,random.Random())
        engine=BattleEngine(specs,enemy,owner_a=ctx.author.id,owner_b=None,name_a=ctx.author.display_name,name_b=f'{school} Training — {difficulty}',mode='training')
        result=await asyncio.to_thread(engine.run)
        battle=self.bot.get_cog('BattleCog')
        e=battle._result_embed(result,ctx.author.display_name,school,specs,enemy)
        e.title=f'🏋️ {school} Training • {difficulty} (+{merge})'
        if result.winner_side=='A':
            awarded,new_wins,balance=await self.bot.db.award_training_shard(ctx.author.id,school,wk,9)
            if awarded:
                e.add_field(name='Training Reward',value=f'✅ **+1 {school} Shard** • inventory: **{balance}** • weekly progress: **{new_wins}/9**',inline=False)
            else:
                e.add_field(name='Training Reward',value=f'Weekly {school} Training reached **9/9** while this battle was resolving, so no tenth shard was awarded.',inline=False)
        else:
            e.add_field(name='Training Reward',value=f'❌ No shard awarded. Weekly progress remains **{wins}/9**; you may retry immediately.',inline=False)
        from ..battle_render import render_battle_summary
        from .battle import BattleLogLauncher
        file=discord.File(await render_battle_summary(result,specs,enemy,ctx.author.display_name,school),filename='training.png')
        e.set_image(url='attachment://training.png')
        await ctx.send(embed=e,file=file,view=BattleLogLauncher({ctx.author.id},'Training Battle Log',result.log))

    @commands.hybrid_command(name='craft',description='Craft equipment or Neutral +0 characters from matching shards.')
    async def craft(self,ctx:commands.Context,*,player_name:str=''):
        from .raids import craft_command
        await craft_command(self.bot,ctx,player_name)

    @commands.hybrid_command(name='recycle',description='Recycle an unlocked card for coins or unequipped equipment for clan coins.')
    async def recycle(self,ctx:commands.Context,instance_id:str):
        card=await self.bot.db.get_card(instance_id)
        if not card or card.owner_id!=ctx.author.id:
            from ..raid_store import RaidStore
            try:result=await RaidStore(self.bot.db).recycle_equipment(ctx.author.id,item_id=instance_id)
            except ValueError as exc:await ctx.send(str(exc));return
            await ctx.send(f'Recycled {result["count"]} item • +{result["clan_coins"]} clan coins.');return
        if card.locked:
            await ctx.send('That card is locked and cannot be recycled.'); return
        c=CHAR_BY_ID[card.character_id]; payout=recycle_payout(c.rarity,card.merge_level)
        e=discord.Embed(title='Confirm Recycle',description=f'**{display_title(c)} {c.rarity}★ +{card.merge_level}**\nID: `{card.instance_id}`\n{nature_text(card)}\n\nPayout: **{payout} coins** = {c.rarity}★ × 10 × ({card.merge_level}+1).\n\n**This permanently deletes the card.**',color=discord.Color.red())
        await ctx.send(embed=e,view=RecycleConfirmView(self,ctx.author.id,card.instance_id))

    @commands.hybrid_command(name='pity',description='Set, replace, view, or clear your global pity target.')
    async def pity(self,ctx:commands.Context,*,player_name:str=''):
        q=player_name.strip()
        if not q:
            state=await self.bot.db.get_pity(ctx.author.id)
            if not state or state['character_id'] not in CHAR_BY_ID:
                await ctx.send('**Pity: Inactive** — no target set. Use `;pity <name>` to choose one.'); return
            c=CHAR_BY_ID.get(state['character_id'])
            status='Active' if state['armed'] else 'Inactive'
            explanation=(f'Your next {c.rarity}★ pull from an eligible pack is guaranteed to be this character.' if state['armed'] else 'A same-rarity miss activates the guarantee.')
            await ctx.send(f'🎯 Pity target: **{display_title(c)} {c.rarity}★**\n**Pity: {status}** — {explanation}')
            return
        if q.casefold() in {'clear','none','off'}:
            await self.bot.db.clear_pity(ctx.author.id); await ctx.send('Pity target cleared. **Pity: Inactive**.'); return
        matches=find_character(q)
        if not matches:
            await ctx.send(f'No player matched **{q}**.'); return
        if len(matches)>1:
            await ctx.send('Be more specific: '+', '.join(f'`{c.id}`' for c in matches[:10])); return
        c=matches[0]; await self.bot.db.set_pity(ctx.author.id,c.id,False)
        await ctx.send(f'🎯 Pity target set to **{display_title(c)} {c.rarity}★**. **Pity: Inactive** — a same-rarity miss activates the guarantee.')

    @commands.hybrid_command(name='trade',description='Request a two-player trade of cards, coins, and school shards.')
    async def trade(self,ctx:commands.Context,user:discord.Member):
        if user.bot or user.id==ctx.author.id:
            await ctx.send('Choose another non-bot player to trade with.'); return
        e=discord.Embed(title='Trade Request',description=f'**{ctx.author.display_name}** wants to trade with **{user.display_name}**.\n{user.mention}, accept or decline below.',color=PURPLE)
        await ctx.send(embed=e,view=TradeRequestView(self.bot,ctx.author,user))


async def setup(bot):
    await bot.add_cog(ProgressionCog(bot))
