"""Short PvE menus, saved AF1 editing and challenge shard drops."""
import asyncio,math
from dataclasses import asdict
import discord
from discord.ext import commands
from ..data import CHAR_BY_ID
from ..items import ITEM_BY_KEY,normalize
from ..event_modes import af1_support_slots as support_slots
from ..event_store import EventStore
from ..event_modes import EVENT_TEAMS,TinyTitansEngine,AllForOneEngine
from ..tournament import restore_specs
from ..ui import SafeView,PURPLE
from .battle import BattleLogLauncher

class OwnedView(SafeView):
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('Open your own menu.',ephemeral=True);return False
        return True

class TeamSearch(discord.ui.Modal,title='Find a card'):
    query=discord.ui.TextInput(label='Name or title',required=False,max_length=80)
    def __init__(self,parent):super().__init__();self.parent=parent
    async def on_submit(self,i):
        self.parent.query=str(self.query).strip();self.parent.page=0
        self.parent.build();await i.response.edit_message(embed=self.parent.embed(),view=self.parent)

class AF1TeamView(OwnedView):
    def __init__(self,cog,uid,team,cards):
        super().__init__(timeout=900);self.cog=cog;self.uid=uid;self.team=team;self.cards=cards
        self.index=0;self.page=0;self.query='';self.build()
    def embed(self):
        byid={c.instance_id:c for c in self.cards};lines=[]
        for j,e in enumerate(self.team['entries']):
            label='Main' if j==0 else f'Support {j}'
            c=byid.get(e['id']) if e else None
            char=CHAR_BY_ID[c.character_id] if c else None
            name=(char.name+(f' · {char.title}' if sum(x.name==char.name for x in CHAR_BY_ID.values())>1 else '')) if char else 'Empty'
            suffix=f' • {e["lane"]} • +{c.merge_level}' if c else ''
            lines.append(f'{"› " if self.index==j else ""}**{label}:** {name}{suffix}')
        em=discord.Embed(title=f'{self.team["name"]} · slot {self.team["slot"]}',description='\n'.join(lines),color=PURPLE)
        em.set_footer(text='Saved automatically • Main HP ×6 • Support items inactive')
        return em
    async def save(self,i,iid,lane):
        self.team=await self.cog.store.set_entry(self.uid,self.team['slot'],self.index,iid,lane,self.team['revision'])
        self.cards=await self.cog.bot.db.get_cards(self.uid);self.build()
        await i.response.edit_message(embed=self.embed(),view=self)
    def build(self):
        self.clear_items()
        choose=discord.ui.Select(placeholder='Choose a position',options=[discord.SelectOption(label='Main' if j==0 else f'Support {j}',value=str(j),default=j==self.index) for j in range(7)],row=0)
        async def position(i):
            self.index=int(choose.values[0]);self.page=0;self.build();await i.response.edit_message(embed=self.embed(),view=self)
        choose.callback=position;self.add_item(choose)
        used={e['id'] for j,e in enumerate(self.team['entries']) if e and j!=self.index}
        q=normalize(self.query)
        matches=sorted([c for c in self.cards if c.instance_id not in used and CHAR_BY_ID[c.character_id].name!='Fukunaga' and q in normalize(c.character_id)],key=lambda c:(c.character_id,-c.merge_level,c.instance_id))
        pages=max(1,math.ceil(len(matches)/25));self.page=min(self.page,pages-1)
        opts=[]
        for card in matches[self.page*25:(self.page+1)*25]:
            c=CHAR_BY_ID[card.character_id];slots='/'.join(support_slots(c)) or 'No support skills'
            opts.append(discord.SelectOption(label=f'{c.name} — {c.title}'[:100],value=card.instance_id,description=f'+{card.merge_level} • {slots} • {card.instance_id}'[:100]))
        picker=discord.ui.Select(placeholder=f'Choose a card · page {self.page+1}/{pages}',options=opts or [discord.SelectOption(label='No cards found',value='none')],disabled=not bool(opts),row=1)
        async def select_card(i):
            e=self.team['entries'][self.index];await self.save(i,picker.values[0],e['lane'] if e else 'front' if self.index==0 else 'back')
        picker.callback=select_card;self.add_item(picker)
        for delta,emoji in [(-1,'◀️'),(1,'▶️')]:
            b=discord.ui.Button(emoji=emoji,row=2,disabled=self.page==0 if delta==-1 else self.page==pages-1)
            async def nav(i,d=delta):self.page+=d;self.build();await i.response.edit_message(embed=self.embed(),view=self)
            b.callback=nav;self.add_item(b)
        search=discord.ui.Button(label='Search',row=2)
        async def find(i):await i.response.send_modal(TeamSearch(self))
        search.callback=find;self.add_item(search)
        e=self.team['entries'][self.index]
        for lane in ('front','back'):
            b=discord.ui.Button(label=lane.title(),row=3,disabled=e is None,style=discord.ButtonStyle.primary if e and e['lane']==lane else discord.ButtonStyle.secondary)
            async def row(i,l=lane):await self.save(i,self.team['entries'][self.index]['id'],l)
            b.callback=row;self.add_item(b)
        remove=discord.ui.Button(label='Remove',row=3,disabled=e is None)
        async def delete(i):await self.save(i,None,'front')
        remove.callback=delete;self.add_item(remove)
        use=discord.ui.Button(label='Use Team',row=3,style=discord.ButtonStyle.success,disabled=self.team['entries'][0] is None)
        async def activate(i):
            await self.cog.store.use(self.uid,self.team['slot']);await i.response.send_message(f'Using **{self.team["name"]}** in AF1.',ephemeral=True)
        use.callback=activate;self.add_item(use)

class EventMenu(OwnedView):
    def __init__(self,cog,ctx,mode,index=0):
        super().__init__(timeout=900);self.cog=cog;self.ctx=ctx;self.uid=ctx.author.id;self.mode=mode;self.index=index;self.build()
    def embed(self):
        rows=EVENT_TEAMS[self.mode];r=rows[self.index];af1=self.mode=='all_for_one'
        description=('Defeat each CPU team in order to advance. Losses keep the same opponent; the list repeats after the final challenge. Your main fights with HP ×6. Earn 1 AF1 Mark per successful run, up to 25/day. Spend Marks with ;fuse.' if af1 else '6v6 • Lower stars gain extra stats. Each challenge has its own 25-shard daily allowance, banking until the weekly reset.')
        em=discord.Embed(title='All For One' if af1 else 'Tiny Titans',description=description,color=PURPLE)
        names=[u['character_id'].replace(' — ',' · ')+' ('+u['lane']+')' for u in r['units']]
        em.add_field(name=f'{self.index+1}. {r["name"]} · {r["tier"]}',value=('**Main:** '+names[0]+'\n**Supports:**\n'+'\n'.join(names[1:])) if af1 else '**Front:**\n'+'\n'.join(names[:3])+'\n**Back:**\n'+'\n'.join(names[3:]),inline=False)
        em.set_footer(text='Main HP ×6 • ;af1team to edit' if af1 else 'Clear: 50% unique shard • Craft with ;craft')
        return em
    def build(self):
        self.clear_items()
        if self.mode=='tiny_titans':
            select=discord.ui.Select(placeholder='Choose a CPU team',options=[discord.SelectOption(label=f'{j+1}. {r["name"]}',description=r['tier'],value=str(j),default=j==self.index) for j,r in enumerate(EVENT_TEAMS[self.mode])],row=0)
            async def choose(i):self.index=int(select.values[0]);self.build();await i.response.edit_message(embed=self.embed(),view=self)
            select.callback=choose;self.add_item(select)
        play=discord.ui.Button(label='Battle',style=discord.ButtonStyle.success,row=1)
        async def battle(i):
            await i.response.defer();await self.cog.run_event(self.ctx,self.mode,self.index,interaction=i,expected_index=self.index if self.mode=='all_for_one' else None)
            if self.mode=='all_for_one':
                self.index=await self.cog.store.af1_opponent(self.uid);self.build()
                await i.edit_original_response(embed=self.embed(),view=self)
        play.callback=battle;self.add_item(play)
        rules=discord.ui.Button(label='Rules',row=1)
        async def explain(i):
            text=('Main HP ×6.\nUp to six supports; listed passive skills only.\nSupport items are inactive.\nFukunaga is banned.\nPT can target himself.\nGuiding Break stacks twice and does not block targets.' if self.mode=='all_for_one' else 'Normal 6v6 rules.\nEach unit gains 2 × (5 − stars) to HP and its active combat stats.\nThe unused offense stays zero.\nFukunaga is legal.')
            await i.response.send_message(text,ephemeral=True)
        rules.callback=explain;self.add_item(rules)

class EventsCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.store=EventStore(bot.db);self.locks={}
    async def run_event(self,ctx,mode,index,interaction=None,*,expected_index=None):
        uid=ctx.author.id
        send=interaction.followup.send if interaction else ctx.send
        lock=self.locks.setdefault(uid,asyncio.Lock())
        if lock.locked():await send('Your event battle is running.');return
        async with lock:
            try:
                row=await self.store.start(uid,mode,index,expected_index=expected_index);state=row['state']
                cls=TinyTitansEngine if mode=='tiny_titans' else AllForOneEngine
                engine=cls(restore_specs(state['a']),restore_specs(state['b']),seed=state['seed'],name_a=ctx.author.display_name,name_b=state['label'],owner_a=uid)
                file=None
                if mode=='tiny_titans':
                    a=[(u.card,u.character,u.lane) for u in engine.team_a.units]
                    b=[(u.card,u.character,u.lane) for u in engine.team_b.units]
                    result,em,view,file=await self.bot.get_cog('BattleCog')._finish_battle(
                        ctx,engine,a,b,None,ctx.author.display_name,state['label'],reward_regular_win=False)
                else:result=await asyncio.to_thread(engine.run)
                earned=await self.store.finish(uid,row['id'],asdict(result))
                capped=await self.store.reward_status(uid,row['id'])=='capped'
            except ValueError as error:await send(str(error));return
            outcome={'A':'Win','B':'Loss','draw':'Draw'}[result.winner_side]
            if mode=='tiny_titans':
                name=ITEM_BY_KEY.get(state.get('signature_key'),{}).get('name','Unique Item')
                em.title=f'Tiny Titans · {outcome} · {state["label"]}'
                em.add_field(name='Challenge Drop',value=f'{earned} **{name} Shard(s)**' if earned else 'This item’s accrued shard allowance is capped. No additional shards awarded.' if capped else 'No drop.',inline=False)
                em.set_footer(text='6v6 · Tiny Titans · 50% unique equip shard chance')
                await send(embed=em,view=view,file=file)
            else:
                from ..af1_result import af1_embed
                from ..battle_render import render_af1_summary
                a=[(u.card,u.character,u.lane) for u in engine.team_a.units]
                b=[(u.card,u.character,u.lane) for u in engine.team_b.units]
                em=af1_embed(result,a,b,ctx.author.display_name,state['label'],earned)
                if capped:em.add_field(name='Reward allowance reached',value='You have earned all 25 AF1 Marks available today. No additional Marks awarded.',inline=False)
                file=discord.File(await render_af1_summary(result,a,b,ctx.author.display_name,state['label'],earned),filename='af1_result.png')
                view=BattleLogLauncher({uid},state['label'],result.log)
                await send(embed=em,view=view,file=file)
    @commands.hybrid_command(name='tinytitans',aliases=['tt'],description='Play Tiny Titans against boosted 6v6 CPU teams.')
    async def tiny_titans(self,ctx,opponent:int=0):
        if opponent:
            await ctx.defer();await self.run_event(ctx,'tiny_titans',opponent-1)
        else:
            view=EventMenu(self,ctx,'tiny_titans');await ctx.send(embed=view.embed(),view=view)
    @commands.hybrid_command(name='allforone',aliases=['af1'],description='Progress through All For One CPU challenges in order.')
    async def all_for_one(self,ctx):
        index=await self.store.af1_opponent(ctx.author.id)
        view=EventMenu(self,ctx,'all_for_one',index);await ctx.send(embed=view.embed(),view=view)
    @commands.hybrid_command(name='af1team',description='Create or edit one of five saved AF1 teams.')
    async def af1team(self,ctx,slot:int=1,*,name:str=''):
        team=await self.store.team(ctx.author.id,slot,name);cards=await self.bot.db.get_cards(ctx.author.id)
        view=AF1TeamView(self,ctx.author.id,team,cards);await ctx.send(embed=view.embed(),view=view)
    @commands.hybrid_command(name='af1use',description='Choose your active saved AF1 team.')
    async def af1use(self,ctx,slot:int):
        team=await self.store.use(ctx.author.id,slot);await ctx.send(f'Using **{team["name"]}** in AF1.')
    @commands.hybrid_command(name='eventreset',description='Abandon a pending event battle without rewards.')
    async def eventreset(self,ctx):
        lock=self.locks.setdefault(ctx.author.id,asyncio.Lock())
        if lock.locked():await ctx.send('Your battle is running. Wait for the result.');return
        async with lock:
            count=await self.store.abandon(ctx.author.id)
            await ctx.send('Pending event cleared. No rewards granted.' if count else 'No pending event battle.')

async def setup(bot):await bot.add_cog(EventsCog(bot))
