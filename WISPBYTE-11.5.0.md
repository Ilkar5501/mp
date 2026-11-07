# Tiny Titans and All For One — v11.5.0

## Play

- `;tt` / `;tinytitans`: choose from twelve Tiny Titans CPU teams. Uses the active normal 6v6 team. `;tt 4` fights CPU team 4 directly.
- `;af1team [slot] [name]`: create/edit AF1 slots 1–5. Pick Main/Support, a card, and Front/Back. Changes save automatically. The Use Team button activates a complete team.
- `;af1use <slot>`: choose the active AF1 team.
- `;af1` / `;allforone`: choose from twelve AF1 CPU teams. `;af1 4` fights CPU team 4 directly.
- `;eventshop` / `;sigshop`: buy signature shards and craft. Equip the crafted item through the existing `;equip` menu.
- `;eventreset`: abandon a pending event without a reward. Running battles cannot be reset mid-simulation.

All commands also have slash forms. Existing gameplay/tutorial checks apply. The ordinary 3v3/6v6 active team and the active AF1 team are separate.

## Tiny Titans

Every card gains `2 × (5 − stars)` to HP, its existing offense, Def, Rec, Spd and Jmp. The unused offensive stat remains zero. These changes use mode-local templates and do not alter collection cards, rarity, abilities or ordinary battles. Merges, natures, bonds and equipment retain their usual behavior. Fukunaga is legal.

## AF1

Each side has one main and up to six inactive supports. The main's HP, including normal permanent build modifiers and bonds, is multiplied by six. Support HP is not added to the team's health. The main can be in either row and all six supports may share a row. Use distinct card variants; Crown and PT may support the same main.

Supports use their owned merges/natures and existing school bonds/team comparisons. Only explicitly listed CC-compatible support slots activate. Supports do not act, attack, counter, save or become targets. Support equipment is inactive; only the main's equipment functions. Row-limited skills keep their real positional conditions. This mode does not inherit CC's preset-specific positional exceptions or endlessly refilling HP.

Fukunaga is banned as both main and support. Iizuna is legal in either role. Guiding Break can stack twice on the opposing main but does not block targeting or otherwise protect it. Both stacks count toward Iizuna's damage bonus and his two-mark cooldown refund. They expire and cleanse normally. Ordinary modes keep their existing Guiding Break behavior.

PT's C grants Open Play to himself every turn when he is the main. A PT support can grant it to an eligible MB/WS/OP main. Main-only transfer Ultimates that require another active ally have no recipient; inactive supports never receive an action.

## Mapo Tofu Bowls

Only **Suga — Relentless Setter** can equip this item, in either ordinary modes or events.

> Passing the Torch targets up to 2 different team members, including Suga. If Suga initiates, ignores Soft Serve's Spd penalty and grants Spd+5 and Pow/Def/Rec+3 during combat.

This cancels the initiating −5 and then grants +5; the difference from ordinary initiating Suga is +10 Spd. When defending, the original −5 remains. Recipients are selected without replacement. AF1's single eligible main receives Torch once, never twice.

## Starting economy

- Each win: 2 Titan Tokens in Tiny Titans, or 2 AF1 Marks in AF1.
- 1 Titan Token buys 1 Mapo Tofu Bowls signature shard.
- Recipe: 60 signature shards + 30 AF1 Marks + 10 Suga character shards.
- Total event wins per item: 30 Tiny Titans + 15 AF1. Replays are unlimited, with a shared twenty-second event battle interval. Losses/draws give no currency and spend none.
- Balances persist. Event shards are separate from raid item shards. The item is not in raid drops and cannot bypass the event recipe through ordinary crafting.

Prices are editable in `data/signature_items.json`; rewards, CPU teams and the interval are editable in `data/event_teams.json`. Difficulty labels are starter progression estimates, not measured win-rate rankings. CPU natures start Neutral and merge tiers are +0/+3/+6/+10. Suga's signature appears on selected CPU builds for testing.

## Persistence and validation

Saved AF1 teams survive restart. Merges replace AF1 card references; removed cards clear their slots. Battles persist both team snapshots, CPU identity, seed and reward amount. A pending match resumes with its saved build; reward settlement is atomic and once-only. Signature purchasing/crafting deduct all required balances transactionally. Views check ownership and reject stale team edits.

The full regression suite completed 185 tests successfully, with five existing expected failures in the unrelated audit suite. Eighteen event tests cover mode boosts, card eligibility, ×6 HP, optional inactive supports, PT/Guiding Break exceptions, Suga's exact effects, CPU simulations, menus, persistence, concurrent settlement, crafting rollback, merge references and recovery. All 23 battle-rule groups, runtime validation and offline registration of 74 slash commands pass.

## Install

Stop the bot and back up its existing database. The complete runtime ZIP contains all current files; preserve the live database, environment settings and installed dependencies. Restart after uploading the code and data. The four new event tables are created automatically without resetting existing collections or balances. No new dependency is required.

For the incremental patch, start from v11.4.1 and replace every file in the patch, including both new JSON files and the new event modules. Partial installation will leave imports incomplete. The patch does not modify the production database itself.

This release is prepared locally, not deployed. Sakusa and the proposed Lev/Aran/Tanaka signatures are drafts only. PvP Elo and matchmaking are not changed.
