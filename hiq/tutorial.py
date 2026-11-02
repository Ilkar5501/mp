"""A practical first-run guide, also available to returning players."""
import os

PAGES=[
('Welcome! Here’s where to start',
 'Build a team, let them battle, and use what you earn to grow your collection. You choose the lineup; the bot handles the match.\n\n'
 'Start with `;daily` for your first cards. Then open `;collection` to see what you pulled, and `;teamcreate 1` to put a team together. Use `;teamuse 1` to make it your active team.\n\n'
 'You can come back to `;start` whenever you need a refresher. `;help` lists commands, and `;abbreviations` shows the shortcuts. Finish this guide for your one-time Neutral +0 Suga.'),
('What do I earn?',
 '**Coins** buy Base, Position, and School Packs. You earn them by playing modes such as Battles, Raids, Gauntlets, Tournaments, and PvP.\n\n'
 '**Clan Dollars** buy Blessings, Merge Copies, and packs from `;clanshop`. Check in, donate spare cards, win wars, or collect raid drops to earn them.\n\n'
 '**Character Shards** craft a specific character variant. **School Shards** come from Training and craft characters from that school. **Item Shards** craft equipment. Open `;craft` to see your recipes and balances.'),
('Packs, boons, and banes',
 '`;daily` gives you one free three-card Base Pack each day. Your first-ever daily contains exactly one 2★, one 3★, and one 4★. Reopening this guide never resets that reward.\n\n'
 'Base, Position, and School Packs contain three cards. Position Packs narrow the pool to a position; School Packs narrow it to an eligible school. Clan-shop Star Packs contain one card of the selected rarity. Use `;info p b`, `;info p p ws`, or `;info p s kar` to check pools and odds before buying.\n\n'
 'Each pulled card rolls a boon (+3) and bane (−3), a little like a Pokémon nature. Rolls use HP, its active Pow/Qik, Def, Rec, Spd, and Jmp. Matching rolls cancel into Neutral.'),
('Pity and your inventory',
 'Set a character target with `;pity <name>`. A same-rarity miss activates the guarantee; your next eligible pull of that rarity gives you the target. Check `;pity` to see its state. Clan-shop packs also use your pity.\n\n'
 'Open `;inv` to see equipment, unopened packs, Blessings, and Merge Copies. Choose a pack to open it. `;inv toggle` switches between stacked quantities and individual item IDs.'),
('Making your team',
 '`;teamcreate 1` opens the team builder. Choose 3v3 (one front, two back) or 6v6 (three front, three back), fill the slots, then confirm. Each player name can appear only once, even if you own several variants.\n\n'
 '`;teamedit 1` lets you change the lineup or positions; `;teamuse 1` selects it for activities. You can save up to 15 teams. Training and Gauntlets use 3v3.\n\n'
 'Use `;cv <card ID>` to inspect a card. When a character has extra notes, **Click to learn more** opens the explanations privately, one topic at a time.'),
('Equipment and Blessings',
 'Equipment changes how a character plays. Browse `;items`, earn shards in Raids, then use `;craft` to make an item. `;equip` lets you choose a card and an available item. Each item copy equips to one card at a time.\n\n'
 'A Blessing lets you choose a card’s boon and bane. Buy one matching the card’s rarity from `;clanshop`, open `;inv`, select the Blessing, then choose the card and stats. Choosing the same stat twice makes the card Neutral. The Blessing is consumed when applied.'),
('Merging and spare cards',
 '`;merge <name>` opens the merge builder. Pick an unlocked card to keep, then add unlocked copies of the exact same variant or matching-rarity Merge Copies. Review the result before confirming.\n\n'
 'The surviving card keeps its ID and nature. Each consumed card adds its merge level +1; each Merge Copy adds +1. The limit is +10.\n\n'
 'Keep copies you want for merges or trades. Otherwise, `;recycle <card ID>` shows a confirmation and pays stars × 10 × (merge+1) coins. Recycling deletes the card and empties its saved-team slots. Lock cards you want to protect. `;recycle water bottle 5` recycles five unequipped item copies for Clan Dollars.'),
('Your daily and weekly activities',
 '`;qv` is your checklist: Coins, Clan Dollars, Shards, then a short rollover guide. Its caps grow as eligible allowances carry over during the week.\n\n'
 '`;battle` is a quick CPU match. `;pvp @player` uses both players’ active teams. `;training <school>` gives a school shard on a win, up to nine per school each week.\n\n'
 '`;gn r` and `;gn n` run the Regional and National Gauntlets. You need to win every match. `;raid` opens the raid setup menu; each drop rolls independently, so Coins are a possible drop rather than a guaranteed win reward. `;to` opens a tournament lobby.'),
('How combat works',
 'Your characters share one team HP pool. The chosen defender uses their own defenses, but damage comes off that shared pool. A team at 0 HP is defeated; healing cannot bring it back.\n\n'
 '**Pow attacks into Def; Qik attacks into Rec.** A high-Def character usually handles Pow well, while high Rec helps against Qik. Matching attack types can normally counterattack; some skills let a character counter either type.\n\n'
 '**Spd** decides who acts first. Tied characters start in a random order, then rotate that order each turn. Visible Spd changes can change the order of characters still waiting to act.\n\n'
 'Same-school allies give each other +1 to every stat per teammate: up to +5 in 6v6 or +8 in Prefecture. We call this a **School Bond**.'),
('Targets, counters, and follow-ups',
 'Each attack picks a defender using targeting percentages. In a normal 3v3, the front has 40% and each back has 30%. Skills can redirect those odds. In 6v6 the front-line modifier is +10: about 21.67% per front and 11.67% per back before skills. All chances together still add up to 100%.\n\n'
 'The calculation gives everyone the same starting X, then adds front-line and skill modifiers. In 3v3, (X+10)+X+X=100, so X=30.\n\n'
 'Prefecture is three 3v3 pods: each has one front and two backs. A pod is chosen first, then a character inside it.\n\n'
 'The usual order is: initiator’s first attack, defender’s counter, defender’s follow-up, then initiator’s follow-up. A counter or follow-up only happens if a skill allows it. Some characters earn follow-ups; high Spd alone does not grant one. Skills can also move counters or follow-ups earlier, prevent them, or make an attack hit twice.'),
('Jmp and critical actions',
 'Higher Jmp gives a character a chance to make a critical action. Each point of difference gives 5%, capped at 100%.\n\n'
 'For example, 41 Jmp against 29 is a difference of 12: a 60% chance. If the attacker has higher Jmp, an offensive crit normally multiplies Pow/Qik by 1.2. If the defender has higher Jmp, a defensive crit normally multiplies the defense used for that hit by 1.2. Skills can change these rules.\n\n'
 '**Damage Tanked** is damage the character took. **Damage Mitigated** is damage they prevented.'),
('Ultimate cooldowns',
 'CD is the charge still needed before an Ultimate is ready. A character normally gains one charge when they attack and one when they receive a hit: each reduces the remaining count by one.\n\n'
 'Reaching 0 makes it ready, but it still needs the right trigger. Some Ultimates fire on an attack; others on defense, a save, or after combat. When it fires, its cooldown resets. The hit that triggers it does not also give its usual charge.\n\n'
 '“Cooldown count −1” gives an immediate step toward ready. “Cooldown charge +1” speeds up charging on qualifying hits. “Maximum cooldown −1” changes the cost itself. Read the timing, not just the number.'),
('Clans and the next step',
 'Join a clan to use `;checkin`, `;donate`, and `;clanshop`. Check-in gives 150 Clan Dollars, plus 50 when all five members check in. Donations consume an unlocked card and reward your clan; you can donate six times daily.\n\n'
 'Try `;clancontest` to see today’s featured unit and six passive supports. Enter with `;clancontest <6v6 team slot>` and deal damage over 20 turns. Retry to improve your best daily score. Participation gives 200 Clan Dollars once a day; `;contestboard` shows weekly clan and player rankings.\n\nLeaders and co-leaders use `;clanwar` to arrange Quick (three-player) or Long (five-player) wars. Opening bans and sends share a 24-hour deadline. In later rounds the winner sends within 24 hours, then the opponent gets 24 hours to respond. Once both sends are in, both players get 24 hours to choose teams. Missing a ban/send loses the war; missing a team loses that player’s match. Officers get DM reminders; check `;clanwarview` for timers.\n\n'
 'Read your cards carefully, try a few teams, and ask when something is unclear. `;help` and `;abbreviations` are always there. Finish below to claim your one-time Neutral +0 Suga; reviewing the tutorial never grants another.')
]

if os.getenv('COMMUNITY_SERVER_URL','https://discord.gg/pnAhQaKUNN'):
    title,text=PAGES[-1]
    PAGES[-1]=(title,text+'\n\nJoin our main server for questions and redeem codes: '+os.getenv('COMMUNITY_SERVER_URL','https://discord.gg/pnAhQaKUNN'))
