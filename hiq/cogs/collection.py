from __future__ import annotations

import math

import discord
from discord.ext import commands

from ..cards import card_embed, compact_card_line, display_title, nature_text, template_embed
from ..config import GAME_CONFIG
from ..data import CHARACTERS, CHAR_BY_ID, character_search_score
from ..search import filter_cards, filter_characters, filters_summary, parse_filters, stat_label
from ..stats import card_bst, permanent_stats

PURPLE = discord.Color.from_rgb(72, 42, 115)
SPLIT_CARD_OWNER_ID = 626255484411969536


class OwnerOnlyView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This menu belongs to the player who opened it.', ephemeral=True)
            return False
        return True


def sort_cards(cards, sort_by: str):
    key = (sort_by or 'recent').casefold().strip()
    if key in ('recent', 'obtained', 'date'):
        return sorted(cards, key=lambda c: c.obtained_at, reverse=True), 'recent'
    if key == 'name':
        return sorted(cards, key=lambda c: (CHAR_BY_ID[c.character_id].name.casefold(), CHAR_BY_ID[c.character_id].title.casefold(), c.instance_id)), 'name'
    if key in ('stats', 'bst'):
        return sorted(cards, key=lambda c: card_bst(CHAR_BY_ID[c.character_id], c.merge_level, c.boon_stat, c.bane_stat), reverse=True), 'stats/BST'
    if key in ('boon', 'boons'):
        return sorted(cards, key=lambda c: ((c.boon_stat or 'zz-neutral'), CHAR_BY_ID[c.character_id].name)), 'boon'
    if key in ('bane', 'banes'):
        return sorted(cards, key=lambda c: ((c.bane_stat or 'zz-neutral'), CHAR_BY_ID[c.character_id].name)), 'bane'
    if key == 'school':
        return sorted(cards, key=lambda c: (CHAR_BY_ID[c.character_id].school, CHAR_BY_ID[c.character_id].name)), 'school'
    if key in ('position', 'pos'):
        return sorted(cards, key=lambda c: (CHAR_BY_ID[c.character_id].position, CHAR_BY_ID[c.character_id].name)), 'position'
    if key == 'merge':
        return sorted(cards, key=lambda c: (c.merge_level, CHAR_BY_ID[c.character_id].name), reverse=True), 'merge'
    if key in ('hp', 'pow', 'qik', 'def', 'rec', 'spd', 'jmp'):
        return sorted(cards, key=lambda c: permanent_stats(CHAR_BY_ID[c.character_id], c.merge_level, c.boon_stat, c.bane_stat).get(key, 0), reverse=True), key.upper()
    return sorted(cards, key=lambda c: c.obtained_at, reverse=True), 'recent'


class CollectionSortSelect(discord.ui.Select):
    def __init__(self, parent: 'CollectionPager'):
        self.parent_view = parent
        options = [
            discord.SelectOption(label='Recently obtained', value='recent'),
            discord.SelectOption(label='Name', value='name'),
            discord.SelectOption(label='Stats / BST', value='stats'),
            discord.SelectOption(label='Boon', value='boon'),
            discord.SelectOption(label='Bane', value='bane'),
            discord.SelectOption(label='School', value='school'),
            discord.SelectOption(label='Position', value='position'),
            discord.SelectOption(label='Merge', value='merge'),
        ]
        super().__init__(placeholder=f'Sort collection • Current: {parent.sort_by}', options=sorted(options,key=lambda o:o.label.casefold()), row=0)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.cards, self.parent_view.sort_by = sort_cards(self.parent_view.source_cards, self.values[0])
        self.parent_view.page = 0
        self.parent_view.rebuild()
        await interaction.response.edit_message(embed=self.parent_view.embed(interaction.user.display_name), view=self.parent_view)


class CollectionPager(OwnerOnlyView):
    def __init__(self, owner_id: int, cards, *, sort_by: str, per_page: int = 20):
        super().__init__(owner_id)
        self.source_cards = list(cards)
        self.cards, self.sort_by = sort_cards(self.source_cards, sort_by)
        self.per_page = per_page
        self.page = 0
        self.rebuild()

    @property
    def pages(self):
        return max(1, math.ceil(len(self.cards) / self.per_page))

    def rebuild(self):
        self.clear_items()
        self.add_item(CollectionSortSelect(self))
        prev = discord.ui.Button(emoji='◀️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page <= 0)
        nxt = discord.ui.Button(emoji='▶️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page >= self.pages - 1)

        async def prev_cb(interaction):
            self.page = max(0, self.page - 1)
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(interaction.user.display_name), view=self)

        async def next_cb(interaction):
            self.page = min(self.pages - 1, self.page + 1)
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(interaction.user.display_name), view=self)

        prev.callback = prev_cb
        nxt.callback = next_cb
        self.add_item(prev)
        self.add_item(nxt)

    def embed(self, display_name: str) -> discord.Embed:
        start = self.page * self.per_page
        chunk = self.cards[start:start + self.per_page]
        desc = '\n'.join(compact_card_line(c) for c in chunk) or 'No cards on this page.'
        e = discord.Embed(title=f'{display_name} — Collection', description=desc, color=PURPLE)
        e.set_footer(text=f'{len(self.cards)} cards • Page {self.page + 1}/{self.pages} • Sort: {self.sort_by} • ;cardview <ID>')
        return e


class ResultSelect(discord.ui.Select):
    def __init__(self, parent: 'SearchResultsView'):
        self.parent_view = parent
        super().__init__(placeholder='Select a card to inspect…', min_values=1, max_values=1, options=parent.options_for_page(), row=0)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if self.parent_view.mode == 'collection':
            card = next((c for c in self.parent_view.items if c.instance_id == value), None)
            if not card:
                await interaction.response.send_message('That card is no longer available.', ephemeral=True)
                return
            from .raids import EquipmentView
            card=await self.parent_view.bot.db.get_card(card.instance_id)
            if not card or card.owner_id!=interaction.user.id:
                await interaction.response.send_message('That card is no longer yours.',ephemeral=True);return
            await interaction.response.send_message(embed=card_embed(self.parent_view.bot, card),view=await EquipmentView.create(self.parent_view.bot,interaction.user.id,card), ephemeral=False)
        else:
            c = CHAR_BY_ID.get(value)
            if not c:
                await interaction.response.send_message('That roster entry is no longer available.', ephemeral=True)
                return
            await interaction.response.send_message(embed=template_embed(self.parent_view.bot, c), ephemeral=False)


class SearchResultsView(OwnerOnlyView):
    def __init__(self, bot, owner_id: int, items, *, mode: str, query_summary: str, per_page: int = 20):
        super().__init__(owner_id)
        self.bot = bot
        self.items = sorted(items,key=lambda obj:((CHAR_BY_ID[obj.character_id].name.casefold(),CHAR_BY_ID[obj.character_id].title.casefold(),obj.instance_id) if mode=='collection' else (obj.name.casefold(),obj.title.casefold(),obj.id)))
        self.mode = mode
        self.query_summary = query_summary
        self.per_page = per_page
        self.page = 0
        self.rebuild()

    @property
    def pages(self):
        return max(1, math.ceil(len(self.items) / self.per_page))

    def page_items(self):
        s = self.page * self.per_page
        return self.items[s:s + self.per_page]

    def options_for_page(self):
        options = []
        if self.mode == 'collection':
            for card in self.page_items():
                c = CHAR_BY_ID[card.character_id]
                nature = 'Neutral' if not card.boon_stat else f'{stat_label(card.boon_stat)}/{stat_label(card.bane_stat)}'
                options.append(discord.SelectOption(
                    label=display_title(c)[:100],
                    value=card.instance_id,
                    description=f'{c.rarity}★ | +{card.merge_level} | {nature} | {card.instance_id}'[:100],
                ))
        else:
            for c in self.page_items():
                options.append(discord.SelectOption(
                    label=display_title(c)[:100],
                    value=c.id,
                    description=f'{c.rarity}★ | {c.position} | {c.school} | BST {c.bst}'[:100],
                ))
        return options

    def rebuild(self):
        self.clear_items()
        if self.items:
            self.add_item(ResultSelect(self))
        prev = discord.ui.Button(emoji='◀️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page <= 0)
        nxt = discord.ui.Button(emoji='▶️', style=discord.ButtonStyle.secondary, row=1, disabled=self.page >= self.pages - 1)

        async def prev_cb(interaction):
            self.page = max(0, self.page - 1)
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def next_cb(interaction):
            self.page = min(self.pages - 1, self.page + 1)
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        prev.callback = prev_cb
        nxt.callback = next_cb
        self.add_item(prev)
        self.add_item(nxt)

    def embed(self):
        if self.mode == 'collection':
            lines = [compact_card_line(c) for c in self.page_items()]
            title = 'Card Search — Your Collection'
        else:
            lines = [f'**{display_title(c)} {c.rarity}★** | **{c.position}** | {c.school} | **BST:** {c.bst}' for c in self.page_items()]
            title = 'Card Gallery'
        e = discord.Embed(title=title, description='\n'.join(lines) or 'No matching cards.', color=PURPLE)
        e.add_field(name='Filters', value=self.query_summary, inline=False)
        e.set_footer(text=f'{len(self.items)} matches • Page {self.page + 1}/{self.pages} • Select a result below to inspect it.')
        return e


# -------------------- Merge builder --------------------

class MergeSearchModal(discord.ui.Modal, title='Search merge cards'):
    query = discord.ui.TextInput(label='Card Name / Title / ID', placeholder='Oikawa, Grand Conductor, or an instance ID', max_length=100)

    def __init__(self, parent: 'MergeBuilderView'):
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.search_query = str(self.query.value).strip()
        self.parent_view.page = 0
        self.parent_view.pending_id = None
        self.parent_view.rebuild()
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class MergeCardSelect(discord.ui.Select):
    def __init__(self, parent: 'MergeBuilderView'):
        self.parent_view = parent
        options = []
        for card in parent.page_cards():
            c = CHAR_BY_ID[card.character_id]
            nature = 'Neutral' if not card.boon_stat else f'+{stat_label(card.boon_stat)}/-{stat_label(card.bane_stat)}'
            options.append(discord.SelectOption(
                label=display_title(c)[:100],
                value=card.instance_id,
                description=f'+{card.merge_level} | {nature} | ID {card.instance_id}'[:100],
            ))
        if not options:
            options = [discord.SelectOption(label='No matching unlocked cards', value='__none__')]
        super().__init__(placeholder='Choose an unlocked card…', options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == '__none__':
            await interaction.response.send_message('No card is selectable on this page.', ephemeral=True)
            return
        self.parent_view.pending_id = value
        self.parent_view.rebuild()
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class MergeBuilderView(OwnerOnlyView):
    def __init__(self, bot, owner_id: int, cards, card1_id: str | None = None, card2_id: str | None = None):
        super().__init__(owner_id, timeout=300)
        self.bot = bot
        self.cards = [c for c in cards if not c.locked]
        self.card1_id = card1_id
        self.card2_id = card2_id
        self.pending_id: str | None = None
        self.search_query = ''
        self.page = 0
        self.per_page = 20
        self.finished = False
        self.rebuild()

    def card(self, iid: str | None):
        return next((c for c in self.cards if c.instance_id == iid), None) if iid else None

    def filtered_cards(self):
        cards = [c for c in self.cards if c.instance_id not in {self.card1_id, self.card2_id}]
        c1 = self.card(self.card1_id)
        if c1 and not self.card2_id:
            cards = [c for c in cards if c.character_id == c1.character_id]
        q = self.search_query.casefold().strip()
        if q:
            ranked = []
            for card in cards:
                c = CHAR_BY_ID[card.character_id]
                id_hit = q in card.instance_id.casefold() or q in card.full_instance_id.casefold()
                score = max(1.0 if id_hit else 0.0, character_search_score(c, self.search_query))
                if score >= 0.54:
                    ranked.append((score, card))
            ranked.sort(key=lambda x: (-x[0], CHAR_BY_ID[x[1].character_id].name, x[1].instance_id))
            cards = [c for _, c in ranked]
        else:
            cards.sort(key=lambda c: (CHAR_BY_ID[c.character_id].name.casefold(), CHAR_BY_ID[c.character_id].title.casefold(), c.merge_level, c.instance_id))
        return cards

    @property
    def pages(self):
        return max(1, math.ceil(len(self.filtered_cards()) / self.per_page))

    def page_cards(self):
        cards = self.filtered_cards()
        s = self.page * self.per_page
        return cards[s:s + self.per_page]

    def _line(self, label: str, card):
        if not card:
            return f'**{label}:** —'
        c = CHAR_BY_ID[card.character_id]
        return f'**{label}:** {display_title(c)} **+{card.merge_level}** • {nature_text(card, compact=True)} • `{card.instance_id}`'

    def preview_level(self):
        c1, c2 = self.card(self.card1_id), self.card(self.card2_id)
        if not c1 or not c2 or c1.character_id != c2.character_id:
            return None
        return c1.merge_level + c2.merge_level + 1

    def embed(self):
        c1, c2, pending = self.card(self.card1_id), self.card(self.card2_id), self.card(self.pending_id)
        e = discord.Embed(title='Merge Cards', color=PURPLE)
        e.description = (
            '**Card 1 is the surviving card.** It keeps its ID, original owner, obtained date, and boon/bane.\n'
            '**Card 2 is permanently consumed.** If Card 2 is assigned to a saved team, that team slot becomes empty.\n\n'
            + self._line('Card 1 — Survivor', c1) + '\n'
            + self._line('Card 2 — Consumed', c2)
        )
        if pending:
            e.add_field(name='Selected candidate', value=self._line('Selected', pending).replace('**Selected:** ', ''), inline=False)
        new_level = self.preview_level()
        if new_level is not None:
            valid = new_level <= int(GAME_CONFIG['merge']['max_level'])
            e.add_field(name='Merge preview', value=f'**+{c1.merge_level} + +{c2.merge_level} + 1 copy = +{new_level}**\n' + ('✅ Valid' if valid else '❌ Above the +10 cap'), inline=False)
        elif c1 and not c2:
            e.add_field(name='Next', value='The dropdown is now limited to unlocked copies with the same name + title as Card 1.', inline=False)
        filtered = self.filtered_cards()
        e.set_footer(text=f'{len(filtered)} selectable unlocked cards • Page {self.page + 1}/{self.pages}' + (f' • Search: {self.search_query}' if self.search_query else ''))
        return e

    def rebuild(self):
        self.clear_items()
        if self.finished:
            return
        self.page = min(self.page, self.pages - 1)
        self.add_item(MergeCardSelect(self))

        add1 = discord.ui.Button(label='Add as Card 1', style=discord.ButtonStyle.primary, row=1, disabled=not bool(self.pending_id))
        add2 = discord.ui.Button(label='Add as Card 2', style=discord.ButtonStyle.secondary, row=1, disabled=not bool(self.pending_id and self.card1_id))
        search = discord.ui.Button(label='Search Name/Title/ID', style=discord.ButtonStyle.secondary, row=1)
        merge = discord.ui.Button(label='Merge', style=discord.ButtonStyle.success, row=2)
        prev = discord.ui.Button(emoji='◀️', style=discord.ButtonStyle.secondary, row=2, disabled=self.page <= 0)
        nxt = discord.ui.Button(emoji='▶️', style=discord.ButtonStyle.secondary, row=2, disabled=self.page >= self.pages - 1)
        clear = discord.ui.Button(label='Clear', style=discord.ButtonStyle.danger, row=2)

        new_level = self.preview_level()
        merge.disabled = not (self.card1_id and self.card2_id and new_level is not None and new_level <= int(GAME_CONFIG['merge']['max_level']))

        async def add1_cb(interaction):
            candidate = self.card(self.pending_id)
            if not candidate:
                await interaction.response.send_message('That card is no longer selectable.', ephemeral=True); return
            self.card1_id = candidate.instance_id
            if self.card2_id and self.card(self.card2_id).character_id != candidate.character_id:
                self.card2_id = None
            self.pending_id = None; self.page = 0; self.search_query = ''
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def add2_cb(interaction):
            c1 = self.card(self.card1_id); candidate = self.card(self.pending_id)
            if not c1 or not candidate:
                await interaction.response.send_message('Choose Card 1 and another candidate first.', ephemeral=True); return
            if candidate.character_id != c1.character_id:
                await interaction.response.send_message('Card 2 must have the same character name and title as Card 1.', ephemeral=True); return
            self.card2_id = candidate.instance_id
            self.pending_id = None
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def search_cb(interaction):
            await interaction.response.send_modal(MergeSearchModal(self))

        async def prev_cb(interaction):
            self.page = max(0, self.page - 1); self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def next_cb(interaction):
            self.page = min(self.pages - 1, self.page + 1); self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def clear_cb(interaction):
            self.card1_id = self.card2_id = self.pending_id = None
            self.search_query = ''; self.page = 0; self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        async def merge_cb(interaction):
            c1, c2 = self.card(self.card1_id), self.card(self.card2_id)
            if not c1 or not c2:
                await interaction.response.send_message('Choose both cards first.', ephemeral=True); return
            consumed_on_team = await self.bot.db.is_card_on_team(c2.instance_id)
            try:
                merged = await self.bot.db.merge_cards(self.owner_id, c1.instance_id, c2.instance_id, int(GAME_CONFIG['merge']['max_level']))
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True); return
            self.finished = True
            self.rebuild()
            mc = CHAR_BY_ID[merged.character_id]
            e = discord.Embed(title='✨ Merge Complete', color=discord.Color.green())
            e.description = (
                f'**{display_title(mc)}** is now **+{merged.merge_level}**.\n'
                f'**Surviving ID:** `{merged.instance_id}`\n'
                f'**Boon/Bane kept from Card 1:** {nature_text(merged)}\n'
                f'**Consumed ID:** `{c2.instance_id}`'
            )
            if consumed_on_team:
                e.add_field(name='Saved team updated', value='The consumed instance was assigned to a team. That exact slot is now empty; incomplete teams remain saved but cannot battle.', inline=False)
            await interaction.response.edit_message(embed=e, view=None)

        add1.callback = add1_cb; add2.callback = add2_cb; search.callback = search_cb
        prev.callback = prev_cb; nxt.callback = next_cb; clear.callback = clear_cb; merge.callback = merge_cb
        for item in (add1, add2, search, merge, prev, nxt, clear):
            self.add_item(item)


class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='collection', aliases=['col','co'], description='Browse or filter your collection.')
    async def collection(self, ctx, *, query: str = ''):
        cards = await self.bot.db.get_cards(ctx.author.id)
        if not cards:
            await ctx.send('You do not own any cards yet. Use `;daily` to open your free pack.')
            return
        q=query.strip()
        sort_keys={'recent','obtained','date','name','stats','bst','boon','boons','bane','banes','school','position','pos','merge','hp','pow','qik','def','rec','spd','jmp'}
        if not q or q.casefold() in sort_keys:
            view = CollectionPager(ctx.author.id, cards, sort_by=q or 'recent')
            await ctx.send(embed=view.embed(ctx.author.display_name), view=view)
            return
        await self._search_collection(ctx, q)

    async def _search_collection(self, ctx, raw: str):
        cards = await self.bot.db.get_cards(ctx.author.id)
        filters = parse_filters(raw, gallery=False)
        matches = filter_cards(cards, CHAR_BY_ID, filters)
        if not matches:
            await ctx.send(f'No cards in your collection matched **{filters_summary(filters)}**.')
            return
        if len(matches) == 1:
            from .raids import EquipmentView
            await ctx.send(embed=card_embed(self.bot,matches[0]),view=await EquipmentView.create(self.bot,ctx.author.id,matches[0]))
            return
        view = SearchResultsView(self.bot, ctx.author.id, matches, mode='collection', query_summary=filters_summary(filters))
        await ctx.send(embed=view.embed(), view=view)

    @commands.hybrid_command(name='cardsearch', aliases=['cards'], description='Search/filter cards you own.')
    async def cardsearch(self, ctx, *, filters: str = ''):
        """Search owned cards. Example: ;cardsearch name:oikawa boon:jmp spd:>30"""
        await self._search_collection(ctx, filters)

    @commands.hybrid_command(name='cardview', aliases=['card','cv'], description='View one exact owned card instance.')
    async def cardview(self, ctx, instance_id: str | None = None):
        if not instance_id:
            await ctx.send('Use `;cardview <instance ID>` for one exact owned card. Use `;cardsearch` when you want to filter/search your collection.')
            return
        exact = await self.bot.db.get_card(instance_id.strip())
        if not exact or exact.owner_id != ctx.author.id:
            await ctx.send('No owned card matched that exact instance/full ID. Try `;cardsearch id:<partial ID>` or search by name.')
            return
        from .raids import EquipmentView
        await ctx.send(embed=card_embed(self.bot, exact),view=await EquipmentView.create(self.bot,ctx.author.id,exact))

    @commands.hybrid_command(name='gallery', aliases=['roster'], description='Search the complete HiQ roster.')
    async def gallery(self, ctx, *, filters: str = ''):
        f = parse_filters(filters, gallery=True)
        matches = filter_characters(list(CHARACTERS), f)
        if not matches:
            await ctx.send(f'No roster templates matched **{filters_summary(f)}**.')
            return
        if len(matches) == 1:
            await ctx.send(embed=template_embed(self.bot, matches[0]))
            return
        view = SearchResultsView(self.bot, ctx.author.id, matches, mode='gallery', query_summary=filters_summary(f))
        await ctx.send(embed=view.embed(), view=view)

    @commands.hybrid_command(name='lock', description='Lock/unlock a card so it cannot be used in a merge.')
    async def lock(self, ctx, instance_id: str):
        state = await self.bot.db.toggle_lock(ctx.author.id, instance_id)
        if state is None:
            await ctx.send('That card is not in your collection.')
            return
        await ctx.send(f'`{instance_id}` is now **{"locked" if state else "unlocked"}**.')

    @commands.command(name='splitcard', hidden=True)
    async def splitcard(self, ctx, user: discord.User, card_id: str):
        """Owner-only correction: split one exact merged card into +0 copies."""
        if ctx.author.id != SPLIT_CARD_OWNER_ID:
            await ctx.send('Only <@626255484411969536> can use this command.', allowed_mentions=discord.AllowedMentions.none())
            return
        try:
            original, copies = await self.bot.db.split_card(user.id, card_id, user.display_name)
        except ValueError as exc:
            await ctx.send(str(exc), allowed_mentions=discord.AllowedMentions.none())
            return
        char = CHAR_BY_ID[original.character_id]
        ids = ', '.join(f'`{card.instance_id}`' for card in copies)
        await ctx.send(
            f'✅ Split **{display_title(char)} +{len(copies)}** for <@{user.id}> into **{len(copies) + 1} +0 cards**.\n'
            f'Original: `{original.instance_id}` — {nature_text(original, compact=True)}; its ID, history, lock and team slots are preserved.\n'
            f'**{len(copies)} Neutral +0 copies:** {ids}',
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(name='merge', description='Merge matching cards or Merge Copy items; search by name or IDs.')
    async def merge(self, ctx, *, query: str = ''):
        from .merging import MultiMergeView
        from ..community_store import CommunityStore
        cards = await self.bot.db.get_cards(ctx.author.id)
        items = await CommunityStore(self.bot.db).items(ctx.author.id)
        if not any(not c.locked for c in cards):
            await ctx.send('You need an unlocked surviving card to merge.'); return
        base = None; materials = []; name_query = query
        tokens = query.split()
        first = await self.bot.db.get_card(tokens[0]) if tokens else None
        if first:
            if first.owner_id != ctx.author.id or first.locked:
                await ctx.send('Card 1 must be an unlocked card you own.'); return
            base = first.instance_id; name_query = ''
            for token in tokens[1:]:
                card = await self.bot.db.get_card(token)
                if card:
                    if card.owner_id != ctx.author.id or card.locked or card.character_id != first.character_id or card.instance_id == base:
                        await ctx.send('Materials must be distinct unlocked copies of Card 1’s exact variant.'); return
                    materials.append(card.instance_id)
                elif any(it['id'] == token and it['kind'] == 'mergecopy' and it['stars'] == CHAR_BY_ID[first.character_id].rarity for it in items):
                    materials.append(token)
                else:
                    await ctx.send(f'Unknown or ineligible material: `{token}`.'); return
            if len(materials) != len(set(materials)):
                await ctx.send('Each material may be selected only once.'); return
        view = MultiMergeView(self.bot, ctx.author.id, cards, items, query=name_query, base=base, materials=materials)
        await ctx.send(embed=view.embed(), view=view)


async def setup(bot):
    await bot.add_cog(CollectionCog(bot))
