from __future__ import annotations

import math
from dataclasses import replace

import discord
from discord.ext import commands

from ..cards import display_title
from ..data import CHAR_BY_ID, character_search_score
from ..models import SavedTeam
from ..team_render import render_team_image

PURPLE = discord.Color.from_rgb(72, 42, 115)


class SearchTeamModal(discord.ui.Modal, title='Search your collection'):
    card_name = discord.ui.TextInput(label='Card Name', placeholder='e.g. Oikawa, Grand Conductor, Hinata...', required=False, max_length=100)

    def __init__(self, parent: 'TeamCreateView'):
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.search_query = str(self.card_name.value or '').strip()
        self.parent_view.page = 0
        self.parent_view.rebuild()
        await interaction.response.send_message(
            f'Search updated to **{self.parent_view.search_query or "all cards"}**.',
            ephemeral=True,
        )
        await self.parent_view.refresh_message()


class PlacementView(discord.ui.View):
    def __init__(self, parent: 'TeamCreateView', card_id: str):
        super().__init__(timeout=60)
        self.parent_view = parent
        self.card_id = card_id

    async def interaction_check(self, interaction):
        return await self.parent_view.interaction_check(interaction)

    async def _place(self, interaction: discord.Interaction, lane: str):
        try:
            team = await self.parent_view.bot.db.set_team_member(
                self.parent_view.owner_id,
                self.parent_view.slot,
                self.card_id,
                lane,
                expected_format=self.parent_view.team_format,
            )
        except ValueError as e:
            await interaction.response.edit_message(content=str(e), view=None)
            return
        card = await self.parent_view.bot.db.get_card(self.card_id)
        c = CHAR_BY_ID[card.character_id]
        lane_label = 'frontline' if lane == 'front' else 'backline'
        self.parent_view.selected_names.add(c.name)
        self.parent_view.rebuild()
        await interaction.response.edit_message(
            content=f'Position set! **{display_title(c)}** has been added to **{team.name}** as {lane_label}.',
            view=None,
        )
        await self.parent_view.refresh_message()

    @discord.ui.button(label='Frontline', style=discord.ButtonStyle.primary)
    async def front(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._place(interaction, 'front')

    @discord.ui.button(label='Backline', style=discord.ButtonStyle.primary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._place(interaction, 'back')

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content='Selection cancelled.', view=None)


class TeamCardSelect(discord.ui.Select):
    def __init__(self, parent: 'TeamCreateView'):
        self.parent_view = parent
        opts = []
        for card in parent.page_cards():
            c = CHAR_BY_ID[card.character_id]
            opts.append(discord.SelectOption(
                label=display_title(c)[:100],
                value=card.instance_id,
                description=f'{c.rarity}★ | +{card.merge_level} | {c.position} | ID {card.instance_id}'[:100],
            ))
        super().__init__(
            placeholder='Choose a card from this page…',
            min_values=1,
            max_values=1,
            options=opts or [discord.SelectOption(label='No cards match this page', value='__none__')],
            disabled=not bool(opts),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == '__none__':
            await interaction.response.send_message('No card is available on this page.', ephemeral=True)
            return
        card = await self.parent_view.bot.db.get_card(self.values[0])
        if not card:
            await interaction.response.send_message('That card could not be found.', ephemeral=True)
            return
        c = CHAR_BY_ID[card.character_id]
        rules = (
            f'Choose where to deploy **{display_title(c)}**.\n\n'
            f'**Deployment rules:** {"3 Frontline + 3 Backline" if self.parent_view.team_format==6 else "1 Frontline + 2 Backline"}; no duplicate character names, including alternate versions.'
        )
        await interaction.response.send_message(rules, view=PlacementView(self.parent_view, card.instance_id), ephemeral=True)


class TeamCreateView(discord.ui.View):
    def __init__(self, bot, owner_id: int, slot: int, team_name: str, cards, selected_names=None, team_format=3):
        super().__init__(timeout=600)
        self.bot = bot
        self.owner_id = owner_id
        self.slot = slot
        self.team_name = team_name
        self.cards = sorted(cards,key=lambda c:(CHAR_BY_ID[c.character_id].name.casefold(),CHAR_BY_ID[c.character_id].title.casefold(),c.instance_id))
        self.team_format = team_format
        self.selected_names = set(selected_names or ())
        self.page = 0
        self.per_page = 25
        self.search_query = ''
        self.message: discord.Message | None = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This team builder belongs to another player.', ephemeral=True)
            return False
        return True

    def filtered_cards(self):
        available = [c for c in self.cards if CHAR_BY_ID[c.character_id].name not in self.selected_names]
        if not self.search_query:
            return available
        q = self.search_query
        ranked = [(character_search_score(CHAR_BY_ID[c.character_id], q), c) for c in available]
        ranked = [(s, c) for s, c in ranked if s >= 0.54]
        ranked.sort(key=lambda x:(CHAR_BY_ID[x[1].character_id].name.casefold(),CHAR_BY_ID[x[1].character_id].title.casefold(),x[1].instance_id))
        return [c for _, c in ranked]

    @property
    def pages(self):
        return max(1, math.ceil(len(self.filtered_cards()) / self.per_page))

    def page_cards(self):
        cards = self.filtered_cards()
        start = self.page * self.per_page
        return cards[start:start + self.per_page]

    async def build_embed(self):
        team = await self.bot.db.get_saved_team(self.owner_id, self.slot)
        e = discord.Embed(title=f'Team Builder — Slot {self.slot}: {team.name}', color=PURPLE)
        filtered = self.filtered_cards()
        e.description = (
            f'**{len(filtered)}** cards available • Page **{self.page + 1}/{self.pages}**\n'
            f'**Slots used:** {team.used}/{team.team_format}\n'
            + (f'**Search:** `{self.search_query}`\n' if self.search_query else '')
            + '\nChoose a card from the menu below. Use **Search by Name/Title** to narrow the menu.'
        )
        if team.used:
            current = []
            for label, iid, _ in team.entries:
                if iid:
                    card = await self.bot.db.get_card(iid)
                    if card:
                        c = CHAR_BY_ID[card.character_id]
                        current.append(f'**{label}:** {display_title(c)} +{card.merge_level} (`{iid}`)')
                else:
                    current.append(f'**{label}:** —')
            e.add_field(name='Current Team', value='\n'.join(current), inline=False)
        if team.complete:
            e.add_field(name='Ready', value='This team is complete and is now your **active battle team**.', inline=False)
        e.set_footer(text='Discord select menus cannot show remote character-icon images; icons are rendered in ;teamview instead.')
        return e

    async def refresh_message(self):
        team = await self.bot.db.get_saved_team(self.owner_id,self.slot)
        self.selected_names = set()
        self.team_format = team.team_format
        for iid in team.card_ids:
            card = await self.bot.db.get_card(iid) if iid else None
            if card: self.selected_names.add(CHAR_BY_ID[card.character_id].name)
        self.rebuild()
        if self.message:
            try:
                await self.message.edit(embed=await self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    def rebuild(self):
        self.clear_items()
        self.page = min(self.page, self.pages - 1)
        self.add_item(TeamCardSelect(self))

        prev = discord.ui.Button(emoji='◀️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page <= 0)
        nxt = discord.ui.Button(emoji='▶️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page >= self.pages - 1)
        search = discord.ui.Button(label='Search by Name/Title', style=discord.ButtonStyle.primary, row=1)
        reset = discord.ui.Button(label='Reset Team', style=discord.ButtonStyle.danger, row=1)
        clear_search = discord.ui.Button(label='Clear Search', style=discord.ButtonStyle.secondary, row=2, disabled=not bool(self.search_query))

        async def prev_cb(interaction):
            self.page = max(0, self.page - 1)
            self.rebuild()
            await interaction.response.edit_message(embed=await self.build_embed(), view=self)

        async def next_cb(interaction):
            self.page = min(self.pages - 1, self.page + 1)
            self.rebuild()
            await interaction.response.edit_message(embed=await self.build_embed(), view=self)

        async def search_cb(interaction):
            await interaction.response.send_modal(SearchTeamModal(self))

        async def reset_cb(interaction):
            await self.bot.db.clear_saved_team(self.owner_id, self.slot)
            self.selected_names.clear()
            self.rebuild()
            await interaction.response.edit_message(embed=await self.build_embed(), view=self)

        async def clear_cb(interaction):
            self.search_query = ''
            self.page = 0
            self.rebuild()
            await interaction.response.edit_message(embed=await self.build_embed(), view=self)

        toggle = discord.ui.Button(label='Switch to 6v6' if self.team_format==3 else 'Switch to 3v3 (resets team)', row=2, style=discord.ButtonStyle.secondary)
        async def toggle_cb(interaction):
            await interaction.response.defer()
            try:
                await self.bot.db.change_team_format(self.owner_id,self.slot,6 if self.team_format==3 else 3)
            except ValueError as error:
                await interaction.followup.send(str(error),ephemeral=True); return
            await self.refresh_message()
        toggle.callback=toggle_cb
        self.add_item(toggle)
        prev.callback = prev_cb
        nxt.callback = next_cb
        search.callback = search_cb
        reset.callback = reset_cb
        clear_search.callback = clear_cb
        for item in (prev, nxt, search, reset, clear_search):
            self.add_item(item)


class EditPositionSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(placeholder='Choose the position to replace or swap…',row=0,options=[
            discord.SelectOption(label=label,value=str(i),default=i==parent.position)
            for i,(label,_,_) in enumerate(parent.draft_positions)])

    async def callback(self, interaction):
        self.parent_view.position = int(self.values[0])
        self.parent_view.rebuild()
        await interaction.response.edit_message(embed=await self.parent_view.build_embed(),view=self.parent_view)


class EditCardSelect(TeamCardSelect):
    def __init__(self,parent):
        super().__init__(parent)
        self.row = 1
        self.placeholder = 'Choose the replacement card (an existing member swaps)…'

    async def callback(self,interaction):
        if self.values[0] == '__none__': return
        parent = self.parent_view
        try:
            parent.stage_card(self.values[0])
        except ValueError as error:
            await interaction.response.send_message(str(error),ephemeral=True)
            return
        parent.rebuild()
        await interaction.response.edit_message(embed=await parent.build_embed(),view=parent)


class TeamEditView(TeamCreateView):
    def __init__(self,bot,owner_id,team,cards):
        self.original = team.card_ids
        self.original_format = team.team_format
        self.draft = list(self.original)
        self.record = team
        self.position = 0
        self.finished = False
        self.saving = False
        self.by_id = {c.instance_id:c for c in cards}
        super().__init__(bot,owner_id,team.slot,team.name,cards,team_format=team.team_format)

    @property
    def draft_positions(self):
        return replace(self.record,team_format=self.team_format).positions

    def toggle_format(self):
        existing={key:iid for (_,key,_),iid in zip(self.draft_positions,self.draft)}
        self.team_format=6 if self.team_format==3 else 3
        self.draft=[existing.get(key) for _,key,_ in self.draft_positions] if self.team_format==6 else [None]*3
        self.position=0
        self.page=0

    async def save_draft(self):
        picks={key:iid for (_,key,_),iid in zip(self.draft_positions,self.draft)}
        return await self.bot.db.replace_saved_team(self.owner_id,self.slot,
            picks.get('front_id'),picks.get('back1_id'),picks.get('back2_id'),
            front2_id=picks.get('front2_id'),front3_id=picks.get('front3_id'),back3_id=picks.get('back3_id'),
            team_format=self.team_format,expected=self.original,expected_format=self.original_format)

    def filtered_cards(self):
        # Existing members remain selectable for a swap; uniqueness is checked
        # against the resulting draft, rather than removing them before editing.
        if not self.search_query: return list(self.cards)
        scored = [(character_search_score(CHAR_BY_ID[c.character_id],self.search_query),c) for c in self.cards]
        return [c for score,c in sorted(scored,key=lambda row:(CHAR_BY_ID[row[1].character_id].name.casefold(),CHAR_BY_ID[row[1].character_id].title.casefold(),row[1].instance_id)) if score>=0.54]

    async def interaction_check(self,interaction):
        if not await super().interaction_check(interaction): return False
        if self.finished or self.saving:
            await interaction.response.send_message('This editor is closed or its confirmation is being saved.',ephemeral=True)
            return False
        return True

    def stage_card(self,iid):
        if self.finished or self.saving: raise ValueError('This editor is closed.')
        if iid not in self.by_id: raise ValueError('Choose a card from your collection.')
        updated = self.draft.copy()
        if iid in updated:
            other = updated.index(iid)
            updated[other],updated[self.position] = updated[self.position],updated[other]
        else:
            updated[self.position] = iid
        names = [CHAR_BY_ID[self.by_id[x].character_id].name for x in updated if x]
        if len(names) != len(set(names)):
            raise ValueError('A team can use only one version of each character name.')
        self.draft = updated

    async def build_embed(self):
        e=discord.Embed(title=f'Team Editor — Slot {self.slot}: {self.team_name}',color=PURPLE)
        lines=[]
        for (label,_,_),iid in zip(self.draft_positions,self.draft):
            card=self.by_id.get(iid)
            lines.append(f'**{label}:** '+(f'{display_title(CHAR_BY_ID[card.character_id])} +{card.merge_level} (`{iid}`)' if card else 'Empty'))
        e.description=f'**{self.team_format}v{self.team_format}**\n'+'\n'.join(lines)
        e.add_field(name='Pending changes',value='Select a position, then a replacement card. Selecting an existing member swaps positions. **Confirm** saves the whole lineup; **Cancel** discards changes.',inline=False)
        e.set_footer(text=f'Page {self.page+1}/{self.pages} • editing '+self.draft_positions[self.position][0])
        return e

    async def refresh_message(self):
        self.rebuild()
        if self.message: await self.message.edit(embed=await self.build_embed(),view=self)

    def rebuild(self):
        self.clear_items()
        self.page=min(self.page,self.pages-1)
        self.add_item(EditPositionSelect(self)); self.add_item(EditCardSelect(self))
        prev=discord.ui.Button(emoji='◀️',row=2,disabled=self.page==0)
        nxt=discord.ui.Button(emoji='▶️',row=2,disabled=self.page>=self.pages-1)
        search=discord.ui.Button(label='Search',row=2,style=discord.ButtonStyle.primary)
        reset=discord.ui.Button(label='Reset Changes',row=2)
        confirm=discord.ui.Button(label='Confirm',row=3,style=discord.ButtonStyle.success)
        cancel=discord.ui.Button(label='Cancel',row=3,style=discord.ButtonStyle.danger)
        async def page_cb(interaction,delta):
            self.page=max(0,min(self.pages-1,self.page+delta));self.rebuild()
            await interaction.response.edit_message(embed=await self.build_embed(),view=self)
        async def prev_cb(i): await page_cb(i,-1)
        async def next_cb(i): await page_cb(i,1)
        async def search_cb(i): await i.response.send_modal(SearchTeamModal(self))
        async def reset_cb(i):
            self.draft=list(self.original);self.team_format=self.original_format;self.position=0;self.search_query='';self.page=0;self.rebuild()
            await i.response.edit_message(embed=await self.build_embed(),view=self)
        async def confirm_cb(i):
            if self.finished or self.saving: return
            self.saving = True
            await i.response.defer()
            try:
                await self.save_draft()
            except ValueError as error:
                self.saving = False
                await i.followup.send(str(error),ephemeral=True);return
            self.finished=True;self.stop()
            await i.edit_original_response(content='✅ Team changes confirmed and saved.',embed=await self.build_embed(),view=None)
        async def cancel_cb(i):
            self.finished=True;self.stop()
            await i.response.edit_message(content='Team edit cancelled. No changes were saved.',embed=None,view=None)
        toggle=discord.ui.Button(label='Switch to 6v6' if self.team_format==3 else 'Switch to 3v3 (resets draft)',row=3)
        async def toggle_cb(i):
            self.toggle_format(); self.rebuild()
            await i.response.edit_message(embed=await self.build_embed(),view=self)
        toggle.callback=toggle_cb;self.add_item(toggle)
        for item,callback in ((prev,prev_cb),(nxt,next_cb),(search,search_cb),(reset,reset_cb),(confirm,confirm_cb),(cancel,cancel_cb)):
            item.callback=callback;self.add_item(item)


class TeamSlotSelect(discord.ui.Select):
    def __init__(self, parent: 'TeamSlotView', teams):
        self.parent_view = parent
        options = [
            discord.SelectOption(
                label=f'{t.name} — Slot {t.slot}'[:100],
                value=str(t.slot),
                description=f'{t.used}/{t.team_format} cards • {t.team_format}v{t.team_format}' + (' • complete' if t.complete else ' • incomplete'),
            ) for t in sorted(teams,key=lambda t:(t.name.casefold(),t.slot))
        ]
        super().__init__(placeholder='Choose a team slot to view…', options=options)

    async def callback(self, interaction: discord.Interaction):
        slot = int(self.values[0])
        await interaction.response.defer(ephemeral=False)
        await self.parent_view.cog.send_team_view(interaction.followup, self.parent_view.owner_id, slot, ephemeral=False)


class TeamSlotView(discord.ui.View):
    def __init__(self, cog: 'TeamsCog', owner_id: int, teams):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.add_item(TeamSlotSelect(self, teams))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This selector belongs to another player.', ephemeral=True)
            return False
        return True


class TeamsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def team_specs(self, owner_id: int, slot: int | None = None):
        team = await (self.bot.db.get_saved_team(owner_id, slot) if slot else self.bot.db.active_team(owner_id))
        if not team or not team.complete:
            return None, team
        specs = []
        for iid, lane in [(iid,lane) for _,iid,lane in team.entries]:
            card = await self.bot.db.get_card(iid)
            if not card:
                return None, team
            specs.append((card, CHAR_BY_ID[card.character_id], lane))
        return specs, team

    @commands.hybrid_command(name='teamcreate', aliases=['team'], description='Create or edit a saved team slot.')
    async def teamcreate(self, ctx, slot: int, *, name: str | None = None):
        if not 1 <= slot <= 15:
            await ctx.send('Team slot must be from **1 to 15**.')
            return
        cards = await self.bot.db.get_cards(ctx.author.id)
        if not cards:
            await ctx.send('You need cards before you can build a team. Use `;daily` first.')
            return
        team = await self.bot.db.ensure_saved_team(ctx.author.id, slot, name)
        selected = {CHAR_BY_ID[c.character_id].name for c in cards if c.instance_id in team.card_ids}
        view = TeamCreateView(self.bot, ctx.author.id, slot, team.name, cards, selected, team.team_format)
        msg = await ctx.send(embed=await view.build_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(name='teamedit', description='Edit or swap a saved lineup; Confirm saves your changes.')
    async def teamedit(self,ctx,slot:int):
        if not 1<=slot<=15:
            await ctx.send('Team slot must be from **1 to 15**.'); return
        team=await self.bot.db.get_saved_team(ctx.author.id,slot)
        if not team:
            await ctx.send('That team slot does not exist. Use `;teamcreate <slot>` first.');return
        cards=await self.bot.db.get_cards(ctx.author.id)
        if not cards:
            await ctx.send('You need cards to edit a team.');return
        view=TeamEditView(self.bot,ctx.author.id,team,cards)
        view.message=await ctx.send(embed=await view.build_embed(),view=view)

    async def send_team_view(self, sender, owner_id: int, slot: int, *, ephemeral: bool = False):
        async def send(*args, **kwargs):
            if ephemeral:
                kwargs['ephemeral'] = True
            return await sender.send(*args, **kwargs)

        team = await self.bot.db.get_saved_team(owner_id, slot)
        if not team:
            await send(f'Team slot **{slot}** does not exist.')
            return

        entries = []
        description = []
        for label, iid, lane in team.entries:
            card = await self.bot.db.get_card(iid) if iid else None
            char = CHAR_BY_ID.get(card.character_id) if card else None
            entries.append((card, char, lane))
            if card and char:
                description.append(f'**{label}:** {display_title(char)} +{card.merge_level} (`{card.instance_id}`)')
            else:
                description.append(f'**{label}:** Empty')

        image = await render_team_image(entries, f'Slot {slot} — {team.name}')
        file = discord.File(image, filename=f'team_{owner_id}_{slot}.png')
        e = discord.Embed(title=f'{team.name} — Slot {slot} • {team.team_format}v{team.team_format}', color=PURPLE)
        e.description = '\n'.join(description) if team.team_format==3 else '3 Frontline • 3 Backline — lineup below.'
        e.set_image(url=f'attachment://{file.filename}')
        user = await self.bot.db.get_user(owner_id)
        if team.complete and user.active_team_slot == slot:
            e.set_footer(text='Active battle team • complete and battle-ready')
        elif team.complete:
            e.set_footer(text='Complete team • use ;teamuse to make it active')
        else:
            e.set_footer(text=f'Incomplete team ({team.used}/{team.team_format}) • saved, but cannot enter battle, PvP, or gauntlets')
        await send(embed=e, file=file)

    @commands.hybrid_command(name='teamview', description='View a saved team in chat.')
    async def teamview(self, ctx, slot: int | None = None):
        if slot is not None:
            if not 1 <= slot <= 15:
                await ctx.send('Team slot must be from **1 to 15**.')
                return
            await self.send_team_view(ctx, ctx.author.id, slot)
            return
        teams = await self.bot.db.list_saved_teams(ctx.author.id)
        if not teams:
            await ctx.send('You have no saved teams yet. Start one with `;teamcreate 1`.')
            return
        e = discord.Embed(title='Choose a team to view', description='Select one of your team slots below.', color=PURPLE)
        await ctx.send(embed=e, view=TeamSlotView(self, ctx.author.id, teams))

    @commands.hybrid_command(name='teamuse', aliases=['teamselect'], description='Set your active offense/defense team.')
    async def teamuse(self, ctx, slot: int):
        try:
            team = await self.bot.db.set_active_team(ctx.author.id, slot)
        except ValueError as e:
            await ctx.send(str(e))
            return
        await ctx.send(f'✅ **{team.name}** (slot {slot}) is now your active team for `;battle`, `;pvp`, and `;gauntlet`.')


async def setup(bot):
    await bot.add_cog(TeamsCog(bot))
