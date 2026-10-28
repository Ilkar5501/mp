from __future__ import annotations
import math
import discord

from ..cards import display_title,nature_text
from ..community_store import CommunityStore
from ..data import CHAR_BY_ID,character_search_score
from ..ui import SafeView,PURPLE


class MergeQueryModal(discord.ui.Modal,title='Search merge cards'):
    query=discord.ui.TextInput(label='Name, title or ID',max_length=100)
    def __init__(self,parent):super().__init__();self.parent=parent
    async def on_submit(self,i):
        self.parent.query=str(self.query);self.parent.page=0;self.parent.rebuild()
        await i.response.edit_message(embed=self.parent.embed(),view=self.parent)


class MultiMergeView(SafeView):
    def __init__(self,bot,uid,cards,items,*,query='',base=None,materials=None):
        super().__init__(timeout=300);self.bot=bot;self.uid=uid
        self.cards={c.instance_id:c for c in cards if not c.locked}
        self.items={i['id']:i for i in items if i['kind']=='mergecopy'}
        self.query=query;self.base=base;self.materials=list(materials or []);self.pending=None;self.page=0;self.finished=False;self.rebuild()
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('This merge builder belongs to another player.',ephemeral=True);return False
        if self.finished:await i.response.send_message('This merge is already complete.',ephemeral=True);return False
        return True
    def choices(self):
        out=[];base=self.cards.get(self.base);q=self.query.casefold().strip()
        for iid,card in self.cards.items():
            if iid==self.base or iid in self.materials:continue
            char=CHAR_BY_ID[card.character_id]
            if base and card.character_id!=base.character_id:continue
            score=character_search_score(char,self.query) if q else 1
            if q and q not in iid and q not in card.full_instance_id.casefold() and score<.54:continue
            out.append((iid,f'{char.name} — {char.title} +{card.merge_level}',f'{char.rarity}★ • {nature_text(card,compact=True).replace("**","")} • {iid}',score))
        out.sort(key=lambda r:(-r[3],r[1],r[0]))
        if base:
            stars=CHAR_BY_ID[base.character_id].rarity
            out += [(iid,f'Merge Copy — {stars}★',f'Universal +1 merge material • {iid}',1) for iid,it in self.items.items() if it['stars']==stars and iid not in self.materials]
        return sorted(out,key=lambda row:(row[1].casefold(),row[0]))
    def level(self):
        if not self.base:return None
        return self.cards[self.base].merge_level+sum(self.cards[iid].merge_level+1 if iid in self.cards else 1 for iid in self.materials)
    def embed(self):
        e=discord.Embed(title='Merge Cards & Materials',color=PURPLE)
        e.description='Choose the surviving **Card 1**, then select any applicable card copies or Merge Copy items. Each selection adds materials immediately. Confirm consumes all selected materials; Card 1 keeps its nature, ID and history.'
        base=self.cards.get(self.base)
        if base:
            e.add_field(name='Card 1 — Survivor',value=f'{display_title(CHAR_BY_ID[base.character_id])} +{base.merge_level}\n{nature_text(base)} • `{base.instance_id}`',inline=False)
            lines=[f'`{iid}` • '+(f'card +{self.cards[iid].merge_level} → {self.cards[iid].merge_level+1} copies' if iid in self.cards else 'Merge Copy → 1 copy') for iid in self.materials]
            shown=lines[:10]
            if len(lines)>10:shown.append(f'… and {len(lines)-10} more materials. Remove materials to stay within +10.')
            e.add_field(name='Consumed materials',value='\n'.join(shown) or 'Choose from the dropdown.',inline=False)
            e.add_field(name='Preview',value=f'**+{self.level()}** • '+('Within +10 cap' if self.level()<=10 else 'Above +10: remove materials'),inline=False)
        elif self.pending:e.add_field(name='Candidate',value=f'`{self.pending}` • click **Set Card 1**.',inline=False)
        count=len(self.choices());e.set_footer(text=f'Page {self.page+1}/{max(1,math.ceil(count/20))} • {count} options'+(f' • Name search: {self.query}' if self.query else ''))
        return e
    def rebuild(self):
        self.clear_items()
        if self.finished:return
        choices=self.choices();pages=max(1,math.ceil(len(choices)/20));self.page=min(self.page,pages-1);chunk=choices[self.page*20:(self.page+1)*20]
        select=discord.ui.Select(placeholder='Select materials to add' if self.base else 'Choose the surviving card',options=[discord.SelectOption(label=label[:100],value=iid,description=desc[:100]) for iid,label,desc,_ in chunk] or [discord.SelectOption(label='No matching materials',value='__none__')],max_values=min(10,len(chunk)) if self.base and chunk else 1,disabled=not bool(chunk),row=0)
        async def select_cb(i):
            if self.base:
                for iid in select.values:
                    if iid not in self.materials:self.materials.append(iid)
            else:self.pending=select.values[0]
            self.rebuild();await i.response.edit_message(embed=self.embed(),view=self)
        select.callback=select_cb;self.add_item(select)
        controls=[('Set Card 1',discord.ButtonStyle.primary,not(self.pending and not self.base)),('Search',discord.ButtonStyle.secondary,False),('Remove Last',discord.ButtonStyle.secondary,not bool(self.materials)),('Clear',discord.ButtonStyle.danger,False)]
        for label,style,disabled in controls:
            button=discord.ui.Button(label=label,style=style,disabled=disabled,row=1)
            async def cb(i,l=label):
                if l=='Set Card 1':self.base=self.pending;self.pending=None;self.page=0;self.query=''
                elif l=='Search':await i.response.send_modal(MergeQueryModal(self));return
                elif l=='Remove Last':
                    if self.materials:self.materials.pop()
                else:self.base=None;self.materials=[];self.pending=None;self.page=0
                self.rebuild();await i.response.edit_message(embed=self.embed(),view=self)
            button.callback=cb;self.add_item(button)
        for delta,emoji in ((-1,'◀️'),(1,'▶️')):
            button=discord.ui.Button(emoji=emoji,style=discord.ButtonStyle.secondary,row=2,disabled=(self.page==0 if delta==-1 else self.page==pages-1))
            async def nav(i,d=delta):self.page+=d;self.rebuild();await i.response.edit_message(embed=self.embed(),view=self)
            button.callback=nav;self.add_item(button)
        merge=discord.ui.Button(label='Merge',style=discord.ButtonStyle.success,row=2,disabled=not(self.base and self.materials and self.level()<=10))
        async def commit(i):
            if self.finished:raise ValueError('This merge is already complete.')
            merged=await CommunityStore(self.bot.db).merge_many(self.uid,self.base,self.materials)
            self.finished=True;self.rebuild()
            e=discord.Embed(title='✨ Merge Complete',description=f'**{display_title(CHAR_BY_ID[merged.character_id])}** is now **+{merged.merge_level}**.\nSurviving ID: `{merged.instance_id}`\nNature retained: {nature_text(merged)}\nConsumed: '+', '.join(f'`{iid}`' for iid in self.materials)+'\nAny saved slots using consumed cards are now empty.',color=discord.Color.green())
            await i.response.edit_message(embed=e,view=None)
        merge.callback=commit;self.add_item(merge)
