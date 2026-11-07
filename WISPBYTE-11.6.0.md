# HiQ Battler v11.6.0

## Play

- `;tt` opens four Tiny Titans challenges; `;tt 1` fights Suga directly. Use your active normal 6v6 team.
- `;af1team [slot] [name]` creates or edits one of five AF1 teams. Choose a main, up to six supports, and each unit's row. Changes save automatically.
- `;af1use <slot>` selects the AF1 team; `;af1` opens twelve CPU opponents.
- `;items` shows signatures; `;craft` crafts them; `;equip` equips them to their exact card variants.
- `;eventreset` abandons a pending event without a reward.

Slash forms are also available. AF1 teams and ordinary teams are saved separately.

## Tiny Titans and crafting

All four supplied challenges are entered exactly, with every CPU card at +10. Lev's Atsumu is +Spd/−HP; Kenma is +Rec/−HP. Tanaka's fourth challenge uses the supplied six builds.

The existing Tiny Titans bonus remains `2 × (5 − stars)` to HP, active offense, Def, Rec, Spd and Jmp. Fukunaga is legal. No unit abilities are changed by this mode.

Each clear independently has a 50% chance to give one shard for that challenge's signature; otherwise nothing drops. Losses and draws give nothing. There is no shop, token reward, AF1 crafting currency or daily event reward cap. The shared twenty-second battle interval remains.

All four recipes cost **25 matching unique item shards + 25 matching character shards + 500 coins** through `;craft`. At 50% drops, one item takes 50 clears on average before character shards and coins. Signatures are excluded from raid drops and ordinary bulk crafting. Existing v11.5 signature shards migrate into counted item shards once; old tokens and marks are retained but unused.

## Signatures

Each item occupies the normal equipment slot and works only on its named variant.

- **Tactics Playbook 3rd-Year Edition — Suga, Relentless Setter:** Torch selects up to two distinct team members, including Suga. Initiating grants Spd+5 and Pow/Def/Rec+3. Spd+5 offsets his existing −5; it does not grant an additional net +5. Defending retains −5.
- **Rookie Pains & Rookie Gains — Lev, Young Lion:** initiating grants Qik/Spd+5. Each hit adds damage equal to 15% of live Qik, rounded down. When faster, neutralizes guaranteed foe follow-ups and effects preventing Lev's follow-up; when initiating and faster, denies counters from both attack types. The item supplies no automatic follow-up and no cooldown refund.
- **The Pursuit of the "Out of Nowhere" — Aran, Thunderous Cannon:** turn 1 cooldown−2 once. Gains Jmp equal to 70% of positive visible and in-combat Spd bonuses, rounded down with no cap; keeps the Spd. Excludes base stats, merges, natures, bonds and virtual skill-check Spd. Initiating grants Pow/Def+5.
- **A Mediocre Mountain Climber's Chalk — Tanaka, Unbreakable Wing:** permanent +2 HP and active combat stats; initiating Pow/Spd+5. His post-combat buff becomes +4 to all six combat stats for himself and allies. If he triggered his Ultimate, cleanses debuffs from himself and same-row allies after combat.

## Sakusa — Coiled Precision

5★ WS, Itachiyama. Base HP164 / Pow53 / Def28 / Rec34 / Spd36 / Jmp37.

- **Ultimate, Wrist Snap (CD5):** ignores 50% of the foe's applicable Def/Rec for the hit and restores HP equal to 50% of damage dealt.
- **A, Perfectionism:** after each combat, gains one named stack, up to five. Each grants Pow/Rec/Jmp+1 during combat and +2 percentage points of crit chance. His first combat starts at zero stacks.
- **B, Relentless Pressure:** before combat, strictly higher Jmp and a successful preliminary crit roll enable attacking twice. Actual hits roll crit independently. If either initial hit crits, one 50% follow-up roll is made. That follow-up also hits twice: four actual hits at most. Usual follow-up denial applies. Sakusa's offensive crit multiplier is fixed at 1.1×.
- **C, Unravel:** each hit has a 50% chance to Dispel one positive Named Buff. Visible stat bonuses and intrinsic Learning/Terror/Burden resources are excluded. Dispel removes one Perfectionism stack. Excellence is suppressed for the remainder of that combat.

He benefits from Iizuna's existing support and defensive-crit interactions without a new flat damage reduction effect. No portrait or icon was supplied, so those fields remain empty.

## AF1

The twelve supplied opponents are entered. Every main is +10; unspecified supports remain +0 Neutral, without active equipment. Open Play Hinata has +Spd/−HP and Burden of Expectation. Iron Resolve Aone has Weighted Wristbands and Daichi in Reon's slot.

Main HP, including permanent build modifiers and bonds, is multiplied by six. Supports contribute no HP and cannot attack, counter, save or become targets. Their listed compatible passive slots function; their equipment does not. All seven units can share a row. Row conditions and team comparisons still apply. Shibayama's C counts all team members whose Rec exceeds Def and buffs an eligible Nekoma main.

Fukunaga is banned as main and support. Iizuna is legal in either role. Guiding Break has up to two effectless stacks on the main, preserving Iizuna's damage and cooldown-refund interactions. Stacks expire and cleanse normally. PT main grants Open Play to himself; PT support grants it to an eligible MB/WS/OP main. AF1 awards no currency in this release.

## Install and checks

Prepared locally; not deployed. Stop the bot, back up the database, upload the complete runtime or apply the full incremental patch, and restart with existing environment settings and persistent database. The patch supports v11.5.0 and v11.4.1; include every supplied file. No dependency was added. Database migrations preserve existing cards, items and balances.

The 198-test suite completes successfully, with five pre-existing expected failures. Runtime data validation, 23 battle-rule groups and offline registration of 73 slash commands pass. See the accompanying CPU roster file for exact rows, natures and items. Earlier release guides are historical; this guide supersedes their event economy and Suga signature descriptions. Ranked PvP is unchanged.
