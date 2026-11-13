from __future__ import annotations
import asyncio,hashlib,logging,shlex
from dataclasses import asdict
import discord
from discord.ext import commands
from ..items import ITEMS,SIGNATURE_ITEMS,ITEM_BY_KEY,THEMES,find_items,normalize
from ..query_text import split_query
from ..raid_store import RaidStore
from ..raids import RAID_TEAMS,RAID_POOLS,DIFFICULTIES,team_index,raid_team
from ..data import find_character
from ..cards import card_embed,display_title
from ..tournament import restore_specs,restore_result
from ..battle.engine import BattleEngine
from ..battle_render import render_battle_summary
from ..ui import SafeView,EmbedPager,PURPLE

log=logging.getLogger(__name__)

class EquipmentView(SafeView):
    PAGE_SIZE=10
    def __init__(self,bot,uid,card,items,page=0,key=None,type_page=0):
        super().__init__(timeout=600)
        self.bot=bot;self.uid=uid;self.card=card;self.items=sorted(items,key=lambda it:(ITEM_BY_KEY[it['detail']]['name'].casefold(),it['id']))
        self.page=page;self.key=key;self.type_page=type_page
        self.build()

    @classmethod
    async def create(cls,bot,uid,card):
        return cls(bot,uid,card,await RaidStore(bot.db).equipment_options(uid,card))

    async def interaction_check(self,i):
        if i.data.get('custom_id')=='card:learnmore':return True
        if i.user.id!=self.uid:
            await i.response.send_message('Only this card’s owner can change equipment.',ephemeral=True);return False
        return True

    def holder(self,item):
        from ..data import CHAR_BY_ID
        cid=item.get('equipped_card_id')
        if not cid:return 'Unequipped'
        char=CHAR_BY_ID.get(item.get('equipped_character_id'))
        name=f'{char.name} — {char.title}' if char else 'Character'
        return f'Equipped to {name} · {cid}'

    def instance_embed(self):
        from ..data import CHAR_BY_ID
        item=ITEM_BY_KEY[self.key]
        rows=self.copies[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        description=f"Choose a copy for **{display_title(CHAR_BY_ID[self.card.character_id])}** · `{self.card.instance_id}`.\nSelecting an equipped copy moves it from its current character.\n\n{item['effect']}\n\n"
        # Each holder is shown directly beneath the item's name/ID pair.
        description+='\n\n'.join(f"**{item['name']}** · `{row['id']}`\n-# {discord.utils.escape_markdown(self.holder(row))}" for row in rows)
        embed=discord.Embed(title=f"{item['name']} — Your copies",description=description,color=PURPLE)
        embed.set_footer(text=f'Page {self.page+1}/{self.pages} • {len(self.copies)} copies')
        return embed

    async def refresh(self,i):
        card=await self.bot.db.get_card(self.card.instance_id)
        if not card or card.owner_id!=self.uid:raise ValueError('You no longer own this card.')
        self.card=card
        self.items=await RaidStore(self.bot.db).equipment_options(self.uid,self.card)
        self.items.sort(key=lambda it:(ITEM_BY_KEY[it['detail']]['name'].casefold(),it['id']))
        if self.key and not any(it['detail']==self.key for it in self.items):self.key=None;self.page=self.type_page
        self.build()
        await i.response.edit_message(embed=self.instance_embed() if self.key else card_embed(self.bot,self.card),view=self)

    async def apply(self,i,item_id):
        card=await RaidStore(self.bot.db).equip(self.uid,self.card.instance_id,item_id)
        await i.response.edit_message(embed=card_embed(self.bot,card),view=await self.create(self.bot,self.uid,card))

    def build(self):
        self.clear_items()
        if self.key:
            self.copies=[it for it in self.items if it['detail']==self.key]
            self.pages=max(1,(len(self.copies)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
            self.page=max(0,min(self.page,self.pages-1))
            rows=self.copies[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
            select=discord.ui.Select(placeholder='Choose an item copy to equip',options=[discord.SelectOption(label=f"{ITEM_BY_KEY[self.key]['name']} · {it['id']}"[:100],description=self.holder(it)[:100],value=it['id']) for it in rows],row=0)
            async def choose(i):await self.apply(i,select.values[0])
        else:
            keys=list(dict.fromkeys(it['detail'] for it in self.items))
            self.pages=max(1,(len(keys)+23)//24);self.page=max(0,min(self.page,self.pages-1))
            options=[discord.SelectOption(label='Unequip / Empty slot',value='__empty__')]
            for key in keys[self.page*24:(self.page+1)*24]:
                copies=[it for it in self.items if it['detail']==key]
                free=sum(not it.get('equipped_card_id') for it in copies)
                options.append(discord.SelectOption(label=f"{ITEM_BY_KEY[key]['name']} — {len(copies)}x"[:100],description=f'{free} unequipped · {len(copies)-free} equipped',value=key))
            select=discord.ui.Select(placeholder='Browse item types, then choose a copy',options=options,row=0)
            async def choose(i):
                if select.values[0]=='__empty__':await self.apply(i,None);return
                self.type_page=self.page;self.page=0;self.key=select.values[0]
                await self.refresh(i)
        select.callback=choose;self.add_item(select)
        for delta,emoji in ((-1,'◀️'),(1,'▶️')):
            button=discord.ui.Button(emoji=emoji,disabled=self.page==0 if delta==-1 else self.page==self.pages-1,row=1)
            async def nav(i,d=delta):self.page+=d;await self.refresh(i)
            button.callback=nav;self.add_item(button)
        if self.key:
            back=discord.ui.Button(label='All item types',row=1)
            async def go_back(i):self.key=None;self.page=self.type_page;await self.refresh(i)
            back.callback=go_back;self.add_item(back)
        from ..character_notes import add_learn_more
        from ..data import CHAR_BY_ID
        add_learn_more(self,CHAR_BY_ID[self.card.character_id])
        from .youth_menus import add_art_buttons
        if not self.key:add_art_buttons(self,self.bot,self.uid,self.card)

class CraftSourceView(SafeView):
    def __init__(self,bot,uid,username,char):super().__init__(timeout=180);self.bot=bot;self.uid=uid;self.username=username;self.char=char;self.finished=False
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This crafting choice belongs to another player.',ephemeral=True);return False
        return True
    async def craft(self,i,source):
        if self.finished:raise ValueError('This crafting choice is already used.')
        self.finished=True
        try:card=await RaidStore(self.bot.db).craft(self.uid,self.username,self.char.id,source)
        except Exception:self.finished=False;raise
        await i.response.edit_message(content=f'Crafted **{display_title(self.char)}** as Neutral +0. Card ID: `{card.instance_id}` • {source} shards consumed.',view=None)
    @discord.ui.button(label='Character shards',style=discord.ButtonStyle.primary)
    async def character(self,i,button):await self.craft(i,'character')
    @discord.ui.button(label='School shards',style=discord.ButtonStyle.secondary)
    async def school(self,i,button):await self.craft(i,'school')

async def craft_command(bot,ctx,query=''):
    if not query.strip():
        from .crafting import CraftMenu
        view=await CraftMenu.create(bot,ctx.author.id,str(ctx.author))
        await ctx.send(embed=view.embed(),view=view);return
    store=RaidStore(bot.db);items=find_items(query,include_signatures=True)
    exact=[it for it in items if query.casefold() in (it['name'].casefold(),it['key'])]
    if exact:items=exact
    if len(items)==1:
        item=items[0]
        try:iid=await store.craft(ctx.author.id,str(ctx.author),item['key'])
        except ValueError as exc:await ctx.send(str(exc));return
        cost=(f'{item["shard_cost"]} signature shards + {item["character_shards"]} character shards + {item["coin_cost"]} coins' if item.get('signature') else f'{item["shard_cost"]} item shards')
        await ctx.send(f'Crafted **{item["name"]}**. '+(f'Inventory ID: `{iid}` • {cost}. Use ;equip.' if iid else f'Recycled automatically for Clan Dollars • {cost}.'));return
    matches=find_character(query)
    if len(matches)!=1:
        choices=[it['name'] for it in items]+[c.id for c in matches]
        await ctx.send('Be more specific: '+', '.join(choices[:12]) if choices else 'No item or character matched. Use ;items or ;gallery.');return
    char=matches[0];sources=await store.craft_sources(ctx.author.id,char)
    eligible=[kind for kind,(balance,cost) in sources.items() if balance>=cost]
    if len(eligible)==2:
        await ctx.send(f'Choose shards for **{display_title(char)}**: character {sources["character"][1]} or school {sources["school"][1]}.',view=CraftSourceView(bot,ctx.author.id,str(ctx.author),char));return
    if not eligible:
        parts=[f'{k}: {n}/{cost}' for k,(n,cost) in sources.items() if cost<10**9]
        await ctx.send('Not enough shards. '+ ' • '.join(parts));return
    try:card=await store.craft(ctx.author.id,str(ctx.author),char.id,eligible[0])
    except ValueError as exc:await ctx.send(str(exc));return
    await ctx.send(f'Crafted **{display_title(char)}** as Neutral +0. ID: `{card.instance_id}` • {eligible[0]} shards consumed.')

class RaidSetupView(SafeView):
    def __init__(self,cog,ctx,teams,fmt=3,theme='tactics',slot=None,previews=None,difficulty='hard'):
        super().__init__(timeout=300);self.cog=cog;self.ctx=ctx;self.uid=ctx.author.id;self.teams=teams;self.fmt=fmt;self.theme=theme;self.slot=slot;self.started=False;self.cancelled=False;self.difficulty=difficulty
        self.previews=previews or {(t,f):0 for t in THEMES for f in (3,6)}
        self.build()
        registry=getattr(cog.bot,'session_registry',None)
        if registry:registry.add_view({self.uid},self)
    def embed(self):
        e=discord.Embed(title='Raids',description='Choose a raid, format, stage and saved team. Easy wins roll 1 drop; Hard rolls 1–2; Extreme rolls 1–3. Extreme has one 6v6 team per theme.',color=PURPLE)
        e.add_field(name='Selected',value=f'{self.theme.title()} • {self.fmt}v{self.fmt} • {self.difficulty.title()} • team slot {self.slot or "choose"}',inline=False)
        from ..data import CHAR_BY_ID
        progress=self.previews[(self.theme,self.fmt)]
        opponent=raid_team(self.fmt,team_index(self.fmt,self.theme,progress,self.difficulty),self.difficulty)
        lines=[]
        for raw in opponent['units']:
            char=CHAR_BY_ID[raw['character_id']]
            gear=' • '+ITEM_BY_KEY[raw['equipment_key']]['name'] if raw.get('equipment_key') else ''
            lines.append(f'**{"F" if raw["lane"]=="front" else "B"} • {char.name} — {char.title} +{raw["merge"] if self.difficulty!="easy" else 0}** • +{raw["boon"].upper()}/−{raw["bane"].upper()}'+gear)
        e.add_field(name=f'Next opponent ({1 if self.difficulty=="extreme" else progress+1}/{1 if self.difficulty=="extreme" else 3}): {opponent["name"]}',value='\n'.join(lines),inline=False)
        rewards={'easy':'1 drop always. Character rarity: 40% 1★, 30% 2★, 25% 3★, 4% 4★, 1% 5★.', 'hard':'70% 1 drop / 30% 2 drops. Character rarity: 30% 1★, 25% 2★, 20% 3★, 15% 4★, 10% 5★.', 'extreme':'5% 1 drop / 40% 2 drops / 55% 3 drops. Character rarity: 27% 3★, 40% 4★, 33% 5★.'}[self.difficulty]
        rewards+='\nBase drop types: '+('40% item shards / 50% character shards' if self.difficulty=='easy' else '75.5% item shards / 14.5% character shards')+' / 5% coins / 5% clan coins.'
        e.add_field(name='Rewards',value=rewards+'\nEvery slot independently rolls an eligible drop; duplicates allowed. Currency drops: 100 coins or 100 clan coins. Each day unlocks 300 of each currency, twice each item’s recipe cost in shards, and 50 character shards. Unused allowances bank until Monday 00:00 in the game timezone; character shards retain the 350/week cap.\nSee ;questview and ;items for accrued allowances. Caps are checked after each slot. Remaining odds increase proportionally; empty slots occur only when all rewards are capped.',inline=False)
        e.add_field(name='Rotation',value='One fixed 6v6 team for this theme.' if self.difficulty=='extreme' else ' → '.join(RAID_TEAMS[str(self.fmt)][n]['name'] for n in RAID_POOLS[self.theme][self.fmt])+' → repeat. Easy and Hard share progress; 3v3 and 6v6 progress separately.',inline=False)
        e.add_field(name='Item pool',value=', '.join(it['name'] for it in ITEMS if it['theme']==self.theme),inline=False)
        e.set_footer(text='Use ;items for recipes/effects/eligibility, ;inv for drops, and ;craft name to craft.')
        return e
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('Open your own ;raid menu.',ephemeral=True);return False
        if self.started or self.cancelled:await i.response.send_message('This raid menu is closed.',ephemeral=True);return False
        return True
    def build(self):
        self.clear_items()
        # Discord cannot disable one select option, so a disabled 3v3 button marks
        # the unavailable format while the Extreme select offers only 6v6.
        for row,kind,values in [(0,'theme',[(t,t.title()) for t in sorted(THEMES)]),(1,'fmt',[(str(n),f'{n}v{n}') for n in ((6,) if self.difficulty=='extreme' else (3,6))]),(2,'difficulty',[(d,d.title()) for d in DIFFICULTIES])]:
            select=discord.ui.Select(placeholder={'fmt':'Choose format','theme':'Choose raid','difficulty':'Choose stage'}[kind],options=[discord.SelectOption(label=label,value=value,default=str(getattr(self,kind))==value) for value,label in values],row=row)
            async def choose(i,s=select,k=kind):
                setattr(self,k,int(s.values[0]) if k=='fmt' else s.values[0]);self.slot=None if k=='fmt' else self.slot
                if self.difficulty=='extreme' and self.fmt!=6:self.fmt=6;self.slot=None
                self.build();await i.response.edit_message(embed=self.embed(),view=self)
            select.callback=choose;self.add_item(select)
        teams=[t for t in self.teams if t.complete and t.team_format==self.fmt]
        select=discord.ui.Select(placeholder='Choose saved team',options=[discord.SelectOption(label=f'{t.name} — Slot {t.slot}'[:100],value=str(t.slot),default=self.slot==t.slot) for t in sorted(teams,key=lambda t:(t.name.casefold(),t.slot))] or [discord.SelectOption(label='No complete team in this format',value='none')],disabled=not bool(teams),row=3)
        async def team(i):self.slot=int(select.values[0]);self.build();await i.response.edit_message(embed=self.embed(),view=self)
        select.callback=team;self.add_item(select)
        button=discord.ui.Button(label='Start Raid',style=discord.ButtonStyle.success,disabled=self.slot is None,row=4)
        async def start(i):
            if self.started or self.cancelled:raise ValueError('This raid menu is closed.')
            self.started=True
            try:row=await self.cog.store.start(self.uid,str(self.ctx.author),self.ctx.channel.id,self.slot,self.fmt,self.theme,self.previews[(self.theme,self.fmt)],self.difficulty)
            except Exception:self.started=False;raise
            await i.response.edit_message(content=f'Raid `{row["id"]}` running against **{row["state"]["opponent_name"]}**…',embed=None,view=None)
            self.stop()
            self.cog.launch(row['id'])
        button.callback=start;self.add_item(button)
        if self.difficulty=='extreme':self.add_item(discord.ui.Button(label='3v3 unavailable for Extreme',disabled=True,row=4))

class RaidCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=RaidStore(bot.db);self.jobs={};self.restore_task=None
    async def cog_load(self):self.restore_task=asyncio.create_task(self.restore())
    def cog_unload(self):
        if self.restore_task:self.restore_task.cancel()
        for task in self.jobs.values():task.cancel()
    async def restore(self):
        await self.bot.wait_until_ready()
        for row in await self.store.pending():self.launch(row['id'])
    def launch(self,sid):
        if sid in self.jobs and not self.jobs[sid].done():return
        task=asyncio.create_task(self.run(sid));self.jobs[sid]=task
        task.add_done_callback(lambda done:self.jobs.pop(sid,None) if self.jobs.get(sid) is done else None)
    async def show_result(self,row,sender):
        from .battle import BattleLogLauncher
        from .community import item_label
        st=row['state'];a,b=restore_specs(st['a']),restore_specs(st['b']);result=restore_result(st['result']);cpu=st.get('opponent_name') or st['b'][0]['card']['original_owner_name']
        e=self.bot.get_cog('BattleCog')._result_embed(result,st['username'],cpu,a,b);e.title=f'Raid • {st["theme"].title()} • {st.get("difficulty","hard").title()} • '+('Victory' if result.winner_side=='A' else 'Draw' if result.winner_side=='draw' else 'Defeat')
        reward=st.get('reward');text='No drop.'
        if (result.winner_side=='A' or st.get('reward_attempted')) and not reward:
            check=st.get('reward_check',{})
            if st.get('reward_status')=='all_capped' and check.get('all_capped') is True and check.get('candidates') and all(r.get('remaining')==0 for r in check['candidates']):
                text='All rewards available in this raid are capped.'
            else:
                text='No reward was recorded for this victory. Cap exhaustion was not verified for this saved run.'
        if reward:
            if reward['kind'] in ('coins','clancoins'):text=f'**{reward["amount"]} {"clan coins" if reward["kind"]=="clancoins" else "coins"}**'
            else:text=f'**+{reward.get("amount",1)} {item_label(reward)}** • added to your shard balance. Use ;inv.'
        currency=st.get('currency',{})
        if st.get('reward_version')==3 and st.get('rewards'):
            text='\n'.join(f'**+{r["amount"]} {"clan coins" if r["kind"]=="clancoins" else "coins"}**' if r['kind'] in ('coins','clancoins') else f'**+{r.get("amount",1)} {item_label(r)}**' for r in st['rewards'])
        e.add_field(name='Raid rewards',value=text if st.get('reward_version',0)>=2 else f'**+{currency.get("coins",0)} coins • +{currency.get("clan_coins",0)} clan coins**\n'+text,inline=False)
        e.set_footer(text=f'Raid ID {row["id"]} • ;raidresume {row["id"]} shows this result without awarding again.')
        file=discord.File(await render_battle_summary(result,a,b,st['username'],cpu),filename='raid.png');e.set_image(url='attachment://raid.png')
        await sender.send(embed=e,file=file,view=BattleLogLauncher({row['owner_id']},'Raid Battle Log',result.log),allowed_mentions=discord.AllowedMentions.none())
    async def run(self,sid):
        try:
            row=await self.store.raid(sid)
            if not row or row['status'] not in ('running','broken'):return
            st=row['state'];a,b=restore_specs(st['a']),restore_specs(st['b'])
            seed=int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big')
            result=await asyncio.wait_for(asyncio.to_thread(BattleEngine(a,b,owner_a=row['owner_id'],name_a=st['username'],name_b=st.get('opponent_name') or st['b'][0]['card']['original_owner_name'],mode='raid',seed=seed).run),60)
            row=await self.store.finish(sid,asdict(result))
            channel=self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
            await self.show_result(row,channel)
        except asyncio.CancelledError:raise
        except Exception:log.exception('Raid %s paused; ;raidresume can recover it',sid);await self.store.mark_broken(sid)
    @commands.hybrid_command(name='raid',description='Start a fully specified raid immediately, or open the selection menu.')
    async def raid(self,ctx,theme:str='',format:str='',difficulty:str='',slot:str|None=None):
        if not ctx.guild:await ctx.send('Start raids in a server channel.');return
        # Defaults can prefill a menu, but never authorize an automatic start.
        supplied_all=bool(theme and format and difficulty and slot is not None)
        if theme.casefold() in ('3','3v3','6','6v6'):
            theme,format=format or 'tactics',theme
            if difficulty.isdecimal() and slot is None:slot=difficulty;difficulty='hard'
        notes=[];valid=True
        theme=theme.casefold();difficulty=difficulty.casefold()
        if theme and theme not in THEMES:notes.append('Choose a valid raid theme.');valid=False
        theme=theme if theme in THEMES else 'tactics'
        if difficulty and difficulty not in DIFFICULTIES:notes.append('Choose Easy, Hard or Extreme.');valid=False
        difficulty=difficulty if difficulty in DIFFICULTIES else 'hard'
        teams=await self.bot.db.list_saved_teams(ctx.author.id);active=await self.bot.db.active_team(ctx.author.id)
        default_format=active.team_format if active and active.team_format in (3,6) else 3
        fmt={'3':3,'3v3':3,'6':6,'6v6':6}.get(format.casefold()) if format else default_format
        if fmt not in (3,6):notes.append('Choose 3v3 or 6v6.');valid=False;fmt=default_format
        if difficulty=='extreme' and fmt!=6:notes.append('Extreme raids are 6v6 only.');valid=False;fmt=6;slot=None
        if slot is not None:
            try:slot=int(slot)
            except (ValueError,TypeError):notes.append('Choose a valid saved-team slot.');valid=False;slot=None
            if slot is not None and not any(t.slot==slot and t.team_format==fmt and t.complete for t in teams):
                notes.append('Choose a complete saved team of the matching format.');valid=False;slot=None
        elif active and active.complete and active.team_format==fmt:slot=active.slot
        if supplied_all and valid:
            try:row=await self.store.start(ctx.author.id,str(ctx.author),ctx.channel.id,slot,fmt,theme,difficulty=difficulty)
            except ValueError as exc:await ctx.send(str(exc));return
            self.launch(row['id'])
            await ctx.send(f'Raid `{row["id"]}` running • **{theme.title()} • {fmt}v{fmt} • {difficulty.title()} • Slot {slot}**\nOpponent: **{row["state"]["opponent_name"]}**')
            return
        keys=[(t,f) for t in THEMES for f in (3,6)]
        indices=await asyncio.gather(*(self.store.next_index(ctx.author.id,f,t) for t,f in keys))
        view=RaidSetupView(self,ctx,teams,fmt,theme,slot,dict(zip(keys,indices)),difficulty)
        await ctx.send(content=' '.join(notes) or None,embed=view.embed(),view=view)
    @commands.hybrid_command(name='raidresume',description='Recover an interrupted raid or reopen a completed result.')
    async def raidresume(self,ctx,raid_id:str|None=None):
        if not raid_id:
            pending=await self.store.pending(ctx.author.id);raid_id=pending[0]['id'] if pending else None
        row=await self.store.raid(raid_id) if raid_id else None
        if not row or row['owner_id']!=ctx.author.id:await ctx.send('No matching raid. Completed raid details expire after 30 minutes; active raids can still be recovered.');return
        if row['status']=='complete':
            if getattr(ctx,'interaction',None):await ctx.defer()
            await self.show_result(row,ctx)
        elif row['status'] in ('running','broken'):self.launch(row['id']);await ctx.send('Raid recovery started. The result will appear in its original channel.')
        else:await ctx.send('That raid is closed.')
    @commands.hybrid_command(name='items',aliases=['item'],description='Browse equipment; filters: name:/-n and text:/-t.')
    async def items(self,ctx,*,filters:str=''):
        try:tokens=split_query(filters)
        except ValueError:await ctx.send('Close any quotation marks in the filters.');return
        queries={};current='name'
        aliases={'-n':'name','--name':'name','name':'name','-t':'text','--text':'text','text':'text','effect':'text'}
        for token in tokens:
            prefix,sep,value=token.partition(':')
            if token in aliases:current=aliases[token];continue
            if sep and prefix in aliases:current=aliases[prefix];token=value
            queries[current]=(queries.get(current,'')+' '+token).strip()
        name_matches={it['key'] for it in find_items(queries.get('name',''),include_signatures=True)}
        matches=sorted([it for it in ITEMS+SIGNATURE_ITEMS if it['key'] in name_matches and (not queries.get('text') or queries['text'].casefold() in it['effect'].casefold())],key=lambda it:it['name'].casefold())
        from .item_browser import ItemBrowser
        view=ItemBrowser(self.bot,ctx.author.id,matches,await self.store.shard_progress(ctx.author.id))
        await ctx.send(embed=view.embed(),view=view)

    @commands.hybrid_command(name='allcraft',description='Craft all item and character shards; never consumes school shards.')
    async def allcraft(self,ctx):
        await ctx.defer()
        results=await self.store.craft_all_items(ctx.author.id,str(ctx.author))
        if not results:await ctx.send('You do not have enough item or character shards to craft anything. School shards were preserved.');return
        from ..ui import text_pages
        lines=[f'**{r["item"]["name"] if "item" in r else r["character"].id} ×{r["count"]}**\n'+(f'{sum(i is not None for i in r["ids"])} equipment added to ;inv; {sum(i is None for i in r["ids"])} recycled automatically.' if 'item' in r else 'Neutral +0 cards added to ;collection.') for r in results]
        pages=[discord.Embed(title='All Crafted • school shards preserved',description='\n\n'.join(lines[n:n+3]),color=PURPLE) for n in range(0,len(lines),3)]
        pager=EmbedPager(pages,ctx.author.id)
        if len(pages)==1:pager.clear_items()
        await ctx.send(embed=pager.embed(),view=pager)

    @commands.hybrid_command(name='allrecycle',description='Recycle all unequipped equipment, or every copy of a named item, for Clan Dollars.')
    async def allrecycle(self,ctx,*,item_name:str=''):
        if not item_name.strip():
            try:result=await self.store.recycle_equipment(ctx.author.id,all_items=True)
            except ValueError as exc:await ctx.send(str(exc));return
            await ctx.send(f'Recycled **{result["count"]} unequipped equipment items** • +{result["clan_coins"]} Clan Dollars.')
            return
        matches=find_items(item_name)
        if len(matches)!=1:await ctx.send('Use a more specific item name.');return
        try:result=await self.store.recycle_equipment(ctx.author.id,key=matches[0]['key'])
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(f'Recycled **{matches[0]["name"]} ×{result["count"]}** • +{result["clan_coins"]} Clan Dollars.')

    @commands.hybrid_command(name='equip',description='Equip an owned item to an owned card.')
    async def equip(self,ctx,card_id:str|None=None,item_id:str|None=None):
        if not card_id:
            from .equipping import EquipmentCardPicker
            cards=await self.bot.db.get_cards(ctx.author.id)
            if not cards:await ctx.send('You do not own any cards yet.');return
            view=EquipmentCardPicker(self.bot,ctx.author.id,cards)
            await ctx.send(embed=view.embed(),view=view);return
        if item_id is None:
            card=await self.bot.db.get_card(card_id)
            if not card or card.owner_id!=ctx.author.id:await ctx.send('Choose a card you own.');return
            await ctx.send(embed=card_embed(self.bot,card),view=await EquipmentView.create(self.bot,ctx.author.id,card));return
        try:card=await self.store.equip(ctx.author.id,card_id,item_id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(embed=card_embed(self.bot,card),view=await EquipmentView.create(self.bot,ctx.author.id,card))
    @commands.hybrid_command(name='unequip',description='Return a card’s equipped item to your available inventory.')
    async def unequip(self,ctx,card_id:str):
        try:card=await self.store.equip(ctx.author.id,card_id)
        except ValueError as exc:await ctx.send(str(exc));return
        await ctx.send(embed=card_embed(self.bot,card),view=await EquipmentView.create(self.bot,ctx.author.id,card))

async def setup(bot):await bot.add_cog(RaidCog(bot))
