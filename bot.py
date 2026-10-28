from __future__ import annotations

import asyncio
import difflib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from hiq.config import PREFIX, TOKEN, MECHANICS_GUIDE_URL
from hiq.database import Database
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
    pages = [
        (
            '1/9 — How a battle works',
            '**HiQ Battler supports 3v3 and 6v6.** A 3v3 team has **1 Frontline + 2 Backline**; a 6v6 team has **3 Frontline + 3 Backline**. You choose the team; the bot resolves targeting, attacks, counters, Ultimates, and abilities.\n\n'
            '• All cards on your team share one combined team HP pool.\n'
            '• **POW** attackers deal damage against **DEF**. **QIK** attackers deal damage against **REC**.\n'
            '• **SPD** determines action order. **JMP** determines critical-hit checks.\n'
            '• Default 3v3 targeting is **40% / 30% / 30%**. In 6v6 each front has **21.67%** and each back **11.67%**. Targeting modifiers use a shared-X solver and are maintained separately against incoming POW and QIK attackers.\n'
            '• Units normally counter only the matching attack type unless an ability says otherwise.\n'
            '• Same-school teammates gain **+1 to all stats per same-school ally**, without a cap (+5 in a same-school 6v6 team).\n'
            '• Read each card carefully: many abilities change targeting, counters, follow-ups, cooldowns, formations, or damage rules.'
        ),
        (
            '2/9 — Your first cards & daily',
            'Use **`;daily`** once per calendar day for a free 3-card Base Pack. If you already claimed it, the bot tells you exactly when the next pack becomes available.\n\n'
            'Your **first-ever `;daily`** is a one-time starter pack containing exactly **one 2★, one 3★, and one 4★** card. Running `;start` again can never reset or duplicate this reward.\n\n'
            '**Stars represent acquisition rarity only**, not a direct power tier.'
        ),
        (
            '3/9 — Boons, banes & merges',
            'Every pulled card independently rolls a boon stat and a bane stat from **HP, its active POW/QIK stat, DEF, REC, SPD, and JMP**.\n\n'
            '• Different rolls create a **+3 Boon / −3 Bane**.\n'
            '• If both rolls land on the same stat, they cancel and the card is **Neutral**.\n'
            '• The character\'s zero POW/QIK stat is never eligible.\n\n'
            'Use **`;merge [name]`** to choose an unlocked surviving Card 1, then add matching card copies or same-rarity Merge Copy items. Card 1 retains its nature and ID. Each consumed card adds its merge level +1; each Merge Copy adds +1. The maximum is +10.'
        ),
        (
            '4/9 — Collection & teams',
            'Use **`;collection`** to browse your cards; the Sort menu supports name, stats, boon, bane, school, position, merge, and recent order.\n\n'
            '• **`;cardview <ID>`** opens one exact owned instance.\n'
            '• **`;cardsearch ...`** filters your own collection.\n'
            '• **`;gallery ...`** searches the full roster, including cards you do not own. Gallery filters include `-class` / `-cl`, `-star` / `-st`, and `-effect` / `-eff`.\n\n'
            'Use **`;teamcreate <1-15> [name]`** to save up to fifteen teams. The format toggle selects 3v3 or 6v6. Expanding preserves lanes; switching 6v6 → 3v3 resets the lineup. Only complete teams can battle. Training and Gauntlets use 3v3; Test supports either format when your saved bot matches your active team. Use **`;teamedit <slot>`** to replace players or swap positions, then **Confirm** to save. Teamcreate hides selected players until Reset.'
        ),
        (
            '5/9 — Battles, quests & gauntlets',
            '• **`;battle`** fights a fresh neutral CPU team. CPU teams match your selected team\'s size. Selected **Tanks** lead the frontline, then the best DEF+REC profiles.\n'
            '• **`;pvp @user`** fights another player\'s complete active team of the same format.\n'
            '• **`;questview`** shows battle quests and your separate daily tournament reward caps.\n'
            '• **`;to` / `;tournament`** opens an 8-player 6v6 double-elimination lobby; two losses eliminate you. Champion gets 100 coins with CPUs or 150 with eight humans, up to three wins daily across modes; losses give 0.\n'
            '• **`;gauntlet regional`** runs all **12** Regional matches in order.\n'
            '• **`;gauntlet national`** runs all **20** National matches in order.\n'
            'You must win every match to become Champion. Each mode pays **100 coins for the first daily clear and 50 for the second**. Further clears give no coins.\n\n'
            'Battle results include per-unit offense/defense stats, **OPOM**, and **DPOM**. Gauntlets provide per-game performance panels instead of one giant combined log. Run **`;help`** at any time for commands.'
        ),
        (
            '6/9 — Targeting: equal X, two attack types',
            'Targeting decides **which defender enters combat**, not how damage is divided. The selected unit uses its defenses; damage then comes off shared team HP.\n\n'
            'For each attack type, solve one equal **X**:\n'
            '**Front = X + 10 + applicable ability bonuses**\n'
            '**Back 1 = X + applicable bonuses**\n'
            '**Back 2 = X + applicable bonuses**\n'
            'All defender percentages must sum to 100. With no abilities, **3X+10=100**, so X=30: **40/30/30**. In 6v6, **6X+30=100**: each front 21.67%, each back 11.67%.\n\n'
            'POW and QIK targeting are calculated separately. **Tsuki front vs POW:** +30 to Tsuki gives X=20 → **60/20/20**. Against QIK he has no targeting bonus: **40/30/30**.\n'
            '**Komi back vs QIK:** +30 to Komi gives X=20 → **30/50/20**. Add Iwaizumi in the other back slot (+30 vs QIK): X=10 → **20/40/40**. Tsuki\'s POW distribution remains **60/20/20**.\n'
            'These are probabilities on each normal target selection. Saves, forced targets and formation swaps can change who actually fights.'
        ),
        (
            '7/9 — JMP and critical hits',
            '**JMP compares the two combatants\' current in-combat Jump.** Each point of difference gives the higher-JMP unit a **5%** critical chance, capped at 100%. Equal JMP means no critical chance.\n\n'
            '• If the attacker has higher JMP, an offensive critical multiplies its **active POW or QIK by 1.2** before defenses are subtracted.\n'
            '• If the defender has higher JMP, a defensive critical multiplies the relevant **DEF or REC by 1.2** for that hit.\n'
            '• A 6-point JMP advantage means a **30% chance**, not guaranteed damage. Each hit rolls separately.\n'
            '• Iwaizumi can raise allied offensive critical multipliers to **1.5**. Fukunaga\'s isolated combat disables criticals.\n'
            'Visible JMP buffs/penalties, merges and in-combat support all affect the comparison. Akaashi\'s penalty reversal includes JMP.'
        ),
        (
            '8/9 — Ultimate cost, remaining count and charge',
            'An Ultimate has a **maximum cost** and a **remaining count**. A 3-CD Ultimate begins at 3 remaining. **0 means ready**; it triggers when its own timing/conditions permit, then resets to its maximum cost.\n\n'
            '**Normal hit:** both combatants normally reduce remaining count by 1. The side triggering an Ultimate does not also charge from that same hit.\n'
            '**Charge +1 per your attack:** one extra point of progress, so your hit usually reduces remaining count by **2 instead of 1**. You reach 0 faster.\n'
            '**Foe cooldown charge −1:** denies one point of normal progress; the foe may gain no charge from a hit.\n'
            '**Foe cooldown count +1:** adds 1 to remaining count (up to maximum), so the foe needs one more point of progress. It delays the Ultimate.\n'
            '**Cooldown count −1 once:** reduces current remaining count by 1; maximum cost stays the same (Usuri\'s Siphon).\n'
            '**Accelerates Ultimate cooldown count −1 / maximum cost −1:** a permanent reduction of total cost; a 3-cost Ultimate becomes 2-cost, including later resets (e.g. Shirabu\'s designated ally).\n'
            'Read the timing: Kageyama activates after combat when ready; other cards can trigger on an attack, defense or save.'
        ),
        (
            '9/9 — Training, crafting, recycling & your reward',
            '**`;training <school>`** is one battle against a school formation. Win for **1 school shard**; lose for no shard and no progress. Every school allows **9 winning shard awards each week**.\n'
            '• Wins **1–3:** Easy **+0** opponents.\n'
            '• Wins **4–6:** Hard **+5** opponents.\n'
            '• Wins **7–9:** Extreme **+10** opponents.\n'
            'Progress resets **Monday 00:00 Eastern**; saved shards carry over. Schools include **Shiratorizawa** and **Mujinazaka**. Mujinazaka uses Unnan front, Usuri/Kiryu back. Training remains 3v3.\n\n'
            '**`;craft`** opens the Items/Characters recipe menu with owned/required shard counts. **`;craft <name>`:** school-shard costs are: **1–2★ = 3**, **3–4★ = 6**, **5★ = 9**. A crafted card is always **Neutral +0**, with a new instance ID.\n\n'
            '**`;recycle <ID>`:** review and **Confirm** deletion for **stars × 10 × (merge+1)** coins. Example: 4★ +5 = **240 coins**. Locked cards cannot be recycled. Saved team slots containing the card become empty.\n\n'
            '**Finish Tutorial** grants one **Neutral +0 Suga** on your first complete run. This reward can only be claimed once per account; tutorial reviews never grant another.'
        ),
    ]
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
        self.next.disabled = self.page == 8
        self.finish.disabled = self.page != 8 or self.review

    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=tutorial_embed(self.page, review=self.review), view=self)

    @discord.ui.button(label='Next', emoji='▶️', style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(8, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=tutorial_embed(self.page, review=self.review), view=self)


    @discord.ui.button(label='Finish Tutorial',style=discord.ButtonStyle.success,row=1)
    async def finish(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.page != 8:
            await interaction.response.send_message('Read through all nine pages first.',ephemeral=True)
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
            '`;raid [theme]` — choose 3v3/6v6 and Easy/Hard; win to advance and earn a drop.',
            '`;raidresume [ID]` — recover a raid or show a saved result.',
            '`;items [name:query] [text:query]` — equipment catalogue; ;item also works. Filters -n / -t.',
            '`;craft [item/player name]` — Items/Characters menu with shard counts, or craft by name.',
            '`;equip [cardID] [itemID]` — choose a card/item or equip directly. ;co and ;cv show equipment controls.',
            '`;clanleave`, `;clankick @member`, `;clanlead @member` — membership/leadership. Personal currency/items remain.',
            '`;clanwar ID 3v3` / `6v6` — challenge a full clan. `;clanwarview [ID]` reopens sends/team choices.',
            'War: private opening sends; then previous winning clan sends publicly first. Fresh teams each series. First to two simulations wins; one player is knocked out. Five knockouts end the war.',
            '`;donate cardID` — confirm-delete a card; every clan member earns stars × 10 × (merge+1) clan coins. Three daily donations.',
            '`;checkin` — 100 daily clan coins; all five checking in gives everyone another 100.',
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
