from __future__ import annotations

import asyncio
import difflib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from hiq.config import PREFIX, TOKEN, MECHANICS_GUIDE_URL
from hiq.database import Database
from hiq.tutorial import PAGES
from hiq.activity import ActivityRegistry
from hiq.ui import EmbedPager, text_pages

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('hiq-battler')

EXTENSIONS = (
    'hiq.cogs.economy',
    'hiq.cogs.collection',
    'hiq.cogs.teams',
    'hiq.cogs.battle',
    'hiq.cogs.progression',
    'hiq.cogs.testing',
    'hiq.cogs.tournament',
    'hiq.cogs.clans',
    'hiq.cogs.community',
    'hiq.cogs.utility',
    'hiq.cogs.raids',
    'hiq.cogs.sealed',
    'hiq.cogs.contest',
)
PURPLE = discord.Color.from_rgb(72, 42, 115)


class TutorialRequired(commands.CheckFailure):
    pass


class HiQTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command = interaction.command
        root = command.qualified_name.split()[0].casefold() if command else ''
        if root in {'start','help'}:
            return True
        user = await interaction.client.db.get_user(interaction.user.id)
        if user.tutorial_started:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message('Run **`/start`** once to complete the HiQ Battler tutorial and unlock gameplay commands.', ephemeral=True)
        return False


class HiQBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=PREFIX, intents=intents, help_command=None, case_insensitive=True, tree_cls=HiQTree)
        self.db = Database()
        self.session_registry = ActivityRegistry()

    def get_command(self, name: str):
        """Exact command lookup first, then unique-prefix / close fuzzy lookup."""
        exact = super().get_command(name)
        if exact is not None:
            return exact
        q = name.casefold().strip()
        if not q:
            return None
        choices = {}
        for key, cmd in self.all_commands.items():
            choices.setdefault(key.casefold(), cmd)
        prefix_matches = {cmd for key, cmd in choices.items() if key.startswith(q)}
        if len(prefix_matches) == 1:
            return next(iter(prefix_matches))
        match = difflib.get_close_matches(q, list(choices), n=1, cutoff=0.72)
        return choices[match[0]] if match else None

    async def setup_hook(self):
        await self.db.init()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        try:
            synced = await self.tree.sync()
            log.info('Synced %s application commands.', len(synced))
        except Exception:
            log.exception('Application command sync failed; prefix commands remain available.')


bot = HiQBot()


@bot.check
async def tutorial_gate(ctx: commands.Context) -> bool:
    if not ctx.command:
        return True
    root = ctx.command.qualified_name.split()[0].casefold()
    if root in {'start', 'help'}:
        return True
    user = await bot.db.get_user(ctx.author.id)
    if not user.tutorial_started:
        raise TutorialRequired()
    return True


def tutorial_embed(page: int, *, review: bool = False) -> discord.Embed:
    from hiq.tutorial import PAGES
    pages=[(f'{i+1}/{len(PAGES)} — {title}',text) for i,(title,text) in enumerate(PAGES)]
    title, description = pages[page]
    prefix = 'Tutorial Complete — Review' if review else 'HiQ Battler Tutorial'
    e = discord.Embed(title=f'{prefix} — {title}', description=description, color=PURPLE)
    if page == 0:
        e.add_field(
            name='Status',
            value='✅ Tutorial completed for this account.' if review else '✅ Starting this tutorial unlocks the rest of the bot for this account.',
            inline=False,
        )
    return e


class TutorialView(discord.ui.View):
    def __init__(self, owner_id: int, *, review: bool = False):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.page = 0
        self.review = review
        self._sync()
        if MECHANICS_GUIDE_URL:
            self.add_item(discord.ui.Button(label='Mechanics Guide', emoji='📘', url=MECHANICS_GUIDE_URL, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This tutorial belongs to another player.', ephemeral=True)
            return False
        return True

    def _sync(self):
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page == len(PAGES)-1
        self.finish.disabled = self.page != len(PAGES)-1 or self.review

    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=tutorial_embed(self.page, review=self.review), view=self)

    @discord.ui.button(label='Next', emoji='▶️', style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(PAGES)-1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=tutorial_embed(self.page, review=self.review), view=self)


    @discord.ui.button(label='Finish Tutorial',style=discord.ButtonStyle.success,row=1)
    async def finish(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.page != len(PAGES)-1:
            await interaction.response.send_message('Read through the guide first, then finish on the last page.',ephemeral=True)
            return
        await interaction.response.defer()
        card=await interaction.client.db.complete_tutorial(self.owner_id,interaction.user.name)
        self.review=True;self._sync()
        e=tutorial_embed(self.page,review=True)
        text=(f'🎁 **Neutral +0 Suga — Relentless Setter** added to your collection! ID: `{card.instance_id}`' if card else 'Your one-time Suga reward was already claimed. No additional card was created.')
        e.add_field(name='Tutorial complete',value=text,inline=False)
        await interaction.edit_original_response(embed=e,view=self)


@bot.hybrid_command(name='start', description='Complete or review the HiQ Battler tutorial.')
async def start_cmd(ctx: commands.Context):
    user = await bot.db.get_user(ctx.author.id)
    review = await bot.db.tutorial_reward_claimed(ctx.author.id)
    if not user.tutorial_started:
        await bot.db.set_user_fields(ctx.author.id, tutorial_started=1)
    await ctx.send(embed=tutorial_embed(0, review=review), view=TutorialView(ctx.author.id, review=review))


@bot.hybrid_command(name='help', description='Show HiQ Battler commands and shortcuts.')
async def help_cmd(ctx: commands.Context):
    groups = [
        ('HiQ Battler — Cards, Teams & Packs', [
            'Prefix: **`;`**. Public commands also have `/slash` forms. Run `;start` once before gameplay. `;ab` lists abbreviations.',
            '**Cards:** `;co [filters]`, `;cv ID`, `;cardsearch [filters]`, `;gallery [filters]`, `;lock ID`.',
            '**Merge:** `;merge [name]` or `;merge survivorID [materialIDs…]`. Set Card 1 once, then select any matching copies or Merge Copy items.',
            '**Teams:** `;teamcreate slot [name]`, `;teamedit slot`, `;teamview [slot]`, `;teamuse slot`. Fifteen slots; 3v3 and 6v6.',
            '**Packs:** `;daily` / `;d`, `;shop`, `;p b`, `;p p WS`, `;p s kar`. School abbreviations work.',
            '**Pack details:** `;info p b`, `;info p p WS`, `;info p s kar` — units and individual pull rates.',
            '**Inventory:** `;inv`, `;open packID`, `;bless [itemID] [cardID]`. Shards and tradeable inventory items.',
            '**Trade:** `;trade @user` — cards, items, coins and counted school/item/character shards. Both players confirm.',
            '**Progression:** `;training school`, `;craft player`, `;recycle ID`, `;pity [player]`, `;redeem code`.',
            '**Filters:** `;co -n oikawa -spd > 30 -pos S -tm aob`; colon filters still work.',
            '**Gallery filters:** `;gallery -class support -star 5`, `;gallery -cl sup -st 5`, or `;gallery -eff critical hit`.',
        ]),
        ('HiQ Battler — Battles & Quests', [
            '`;battle [slot]` — matching-format random CPU. `;pvp @user` — active-team PvP; 50 coins per unique opponent defeated, up to 400 daily.',
            '`;gn r` / `;gn n` — Regional / National Gauntlet. Results use regular 3v3 images/logs with school/game navigation.',
            '`;to` / `;tournament` — 8-player 6v6 double elimination; host Auto-fill supports CPUs.',
            '`;pref` / `;prefecture` — join three distinct 3v3 pods into a shared 9v9 team. Host starts. 50 coins per win, up to 300 daily.',
            '`;botset` — personal 3v3/6v6 CPU builder with virtual equipment, merges and boon/bane controls. `;test` — matching-format test with regular results/logs.',
            '`;qv` — daily currency caps, weekly shard caps, and training progress.',
            '`;bal` — coins and pack affordability.',
            '`;refresh` — leave pending/broken activities. Healthy wars cannot be closed this way; use Forfeit to concede.',
        ]),
        ('HiQ Battler — Clans', [
            '`;createclan` — select a standard emoji and name; unique public clan ID. Maximum five members.',
            '`;clanedit` — leader-only name and emoji editor.',
            '`;clans` — public board. `;clanview [ID]` — profile, leader/co-leader, war record.',
            '`;clanapply ID` — apply. Leaders/co-leaders receive DM Accept/Decline controls; `;clanapplications` is a fallback.',
            '`;clanco @member` — leader appoints/removes the single co-leader.',
            '`;memberview` — any clan member can see their clan’s usernames and last check-in dates.',
            '`;raid [theme]` — Easy: 1 drop; Hard: 1–2 drops; Extreme: 1–3 drops, 6v6 only. Currency drops are 100 each, capped at 300 coins / 300 clan coins daily.',
            '`;raidresume [ID]` — recover a raid or show a saved result.',
            '`;items [name:query] [text:query]` — equipment catalogue; ;item also works. Filters -n / -t.',
            '`;craft [item/player name]` — Items/Characters menu with shard counts, or craft by name.',
            '`;equip [cardID] [itemID]` — choose a card/item or equip directly. ;co and ;cv show equipment controls.',
            '`;clanleave`, `;clankick @member`, `;clanlead @member` — membership/leadership. Personal currency/items remain.',
            '`;clanwar` — choose a clan, 3v3/6v6, and Quick (3 players) or Long (5 players). Officers may ban; send and team submission share a 24-hour deadline. `;clanwarview [ID]` reopens controls.',
            '`;sealed @User1 @User2 …` — 2–7 players draft 8 private characters, build 6v6 teams, then play an automatic best-of-three single-elimination tournament. `;sealedview [ID]` reopens; expires after 30 minutes.',
            '`;inv toggle` / `;inv view` — switch stacked and ID views. `;recycle water bottle 5` — recycle exactly five unequipped copies. `;allcraft` — craft all item and character shards; school shards are preserved.',
            'War: private opening sends; then previous winning clan sends publicly first. Fresh teams each series. First to two simulations wins; one player is knocked out. Three knockouts end Quick Wars; five end Long Wars.',
            '`;donate cardID` — confirm-delete a card; every clan member earns stars × 10 × (merge+1) clan coins. Six daily donations.',
            '`;clancontest [team slot]` — see today’s featured unit or enter with a 6v6 team; `;contestboard` shows weekly rankings.',
            '`;checkin` — 150 daily Clan Dollars; all five checking in gives everyone another 50.',
            '`;clanshop`, `;clanbuy mergecopy|blessing|starpack stars` — inventory items. Use `;inv` for IDs.',
        ]),
    ]
    pages=[discord.Embed(title=title,description='\n\n'.join(lines),color=PURPLE) for title,lines in groups]
    pager=EmbedPager(pages,ctx.author.id)
    await ctx.send(embed=pager.embed(),view=pager)


@bot.before_invoke
async def track_activity(ctx):
    root=ctx.command.qualified_name.split()[0] if ctx.command else ''
    if root in {'battle','pvp','training','gauntlet','test'}:
        bot.session_registry.begin(ctx.author.id,root)


@bot.after_invoke
async def finish_activity(ctx):
    root=ctx.command.qualified_name.split()[0] if ctx.command else ''
    if root in {'battle','pvp','training','gauntlet','test'}:
        bot.session_registry.end(ctx.author.id,root,failed=ctx.command_failed)


@bot.event
async def on_ready():
    log.info('Logged in as %s (%s)', bot.user, bot.user.id if bot.user else '?')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, TutorialRequired):
        await ctx.send('Run **`;start`** once to complete the HiQ Battler tutorial and unlock gameplay commands.')
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f'Try again in **{error.retry_after:.1f}s**.')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f'Missing `{error.param.name}`. Use `;help` for command syntax.')
    elif isinstance(error, commands.BadArgument):
        await ctx.send('I could not parse one of those arguments. Use `;help` for syntax.')
    elif isinstance(error, commands.NotOwner):
        await ctx.send('That command is restricted to the bot owner.')
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send('I could not match that command. Use `;help` to see the command list.')
    else:
        log.exception('Command error', exc_info=error)
        await ctx.send('That command hit an unexpected error. Check the bot console for details.')


async def main():
    if not TOKEN:
        raise RuntimeError('DISCORD_TOKEN is missing. Put it in .env or the host environment.')
    async with bot:
        await bot.start(TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
