# HiQ Battler v12.1.0

This is the complete gameplay update. The earlier v11.7.4 ZIP added storage
retention; the v12.0.0 ZIP changed Sakusa's critical follow-up chance. Neither
earlier ZIP included these five units or the gameplay expansion.

## Install

Stop the bot and back up its SQLite database. Extract the changed-files ZIP over
your existing v11.7.4 installation (also works if the Sakusa v12.0.0 patch was
applied). Preserve your database, environment variables and `.env`. Restart with
`python bot.py`. The full runtime ZIP is an alternative for a fresh code upload,
using the same existing persistent database. No new dependencies are required.

Database migrations add preferences, shiny ownership, shield timers, AF1 daily
Mark counts, extra reward claims and weekly PvP settlement markers. They preserve
existing balances, collections, card IDs, shards, equipment, pity, teams and clan
records. On first PvP activity, the current monthly Elo board carries into the
five-week board. Pending legacy matches migrate with it. Completed legacy records
are preserved; old monthly prize sets are not paid again.

## Characters

- Ikejiri — Old Friend: 1-star WS, DPS/Support, Tokonami High.
- Hinata — Furious Upstart: 1-star WS, DPS/Support, Yukigaoka Junior High.
- Oikawa — Young Emperor: 3-star S, Support, Kitagawa Daiichi.
- Kageyama — King of the Lost Court: 3-star S, DPS, Kitagawa Daiichi.
- Oikawa & Kageyama — Kings of the Playground: Duo S, Support/DPS,
  Kitagawa Daiichi. Displays as Duo; counts as six stars for recycle/donate/shards.

The first four enter ordinary matching-rarity card pools and character-shard
crafting. The Duo is fusion-only, excluded from normal pulls, pity and ordinary
crafting. `;gallery` and `;cv` show the complete abilities and extended notes.
New variants do not inherit combat effects from their older namesakes. Existing
Clan Contest and tournament CPU variants remain unchanged.

Hinata's participating highest-merge copy can give normal economic rewards after
a loss at merge × 7%. This does not turn losses into wins, Elo or scores. Young
Oikawa's highest owned copy adds one reward allowance at merge × 5% currency,
including battle, both gauntlets, tournament, PvP, war, check-in, raid currencies
and contest. Training is unchanged. King of the Lost Court's highest owned copy
can duplicate eligible raid/TT non-currency drops at merge × 1%; original drops
consume the usual cap and duplicates do not. The Duo's highest owned copy adds
merge + 1 currency per reward payout outside caps. Copies do not stack. Transfers,
escrow refunds and purchases do not generate this bonus.

## Fusion and AF1 Marks

AF1 gives one Mark per successful run, capped at 25 per calendar day without
rollover. `;bal` shows the Mark wallet. `;fuse` selects Young Emperor and King of
the Lost Court at equal merges, then asks for confirmation. Both cards are
consumed, and the Duo preserves their merge level. The multiplier is max(1, merge):

| Ingredient merges | Coins | Marks | Result |
| --- | ---: | ---: | --- |
| +0 / +0 | 100 | 25 | +0 Duo |
| +1 / +1 | 100 | 25 | +1 Duo |
| +5 / +5 | 500 | 125 | +5 Duo |
| +10 / +10 | 1,000 | 250 | +10 Duo |

Locked/equipped cards and cards assigned to teams or active activities cannot
be fused. Ingredients' shiny unlocks belong to those individual cards and are
consumed; the Duo has its own unlocks. Normal Duo-to-Duo merges cost 100 coins
per merge gained. A Duo cannot share a team with either component character,
including alternate versions. This applies to saved teams, PvP, AF1, Prefecture,
Sealed and test teams.

## Shinies

Buy a Shiny Stone for 10,000 coins with `;buy shinystone`, or 15,000 Clan Dollars
in `;clanshop`. Stones are tradeable inventory consumables. `;use itemID` opens
a paged list of owned cards with an available shiny slot. It unlocks the next
slot and consumes the stone only after a valid selection. The supplied Duo
artwork is slot 1; the catalogue can define any number of slots.

Shiny cards keep their boon and lose their bane, and display a sparkle in card
and collection views. `;cv` has up/down art controls and a lock to save the
displayed unlocked art; horizontal equipment arrows retain their existing use.
`;gallery` previews all published art. Unlocks travel with the card and combine
on the surviving copy when matching cards merge. Shininess is independent of
whether the original artwork is currently selected.

## Other commands and limits

- `;dismantle cardID`: confirmation permanently converts an unlocked, unequipped,
  unused card into stars × (merge + 1) character shards. Team/activity guards are
  rechecked in the same transaction as consumption.
- `;autoclean` / `;aclean`: persistent paged equipment selections. New copies of
  selected equipment automatically recycle for Clan Dollars. Existing copies and
  incoming trades remain intact. Unselect types to keep future copies.
- Tiny Titans: each signature item earns its own 25-shard daily allowance, banked
  within the current week. Spending shards does not reopen an earned cap. Signature
  items share the ordinary item layout and recycle for 25 Clan Dollars.
- `;qv`: battle allowance is nine wins per day, plus Young Oikawa's extra if owned.
- PvP Shield: 500 coins with `;buy pvpshield`, or 750 Clan Dollars in `;clanshop`.
  Tradeable; `;use itemID` grants two hours without initiating or being targeted
  by PvP, including automatic matches. Next activation is six hours after use.
  Activation during an existing PvP match is rejected.
- `;clanshop`: choose an item, then enter its quantity. Existing supplies remain.
- `;help`: shorter paged descriptions include allrecycle, recycle quantities,
  autoclean, fuse, shiny use, shields and plain-language clan-war rules.

## Weekly PvP cycle

Weeks end Monday at 00:00 in the game timezone, like training. The cycle is
anchored to Monday, November 9, 2026. First place receives:

| Week | Pack | Coins |
| --- | --- | ---: |
| 1 | 1-star | 400 |
| 2 | 2-star | 300 |
| 3 | 3-star | 200 |
| 4 | 4-star | 100 |
| 5 | 5-star | 0 |

Elo and the board persist between weeks 1–5. After week 5, the new cycle starts
at 1,000 Elo. Settlement is transactional and repeat-safe, including delayed
matches across the cutoff. `;pvpboard` shows the current week and prize.

Using a saved team updates that format's PvP team unless its valid lineup is
locked with `;pvpset`. Legacy saved teams backfill both formats. Missing or invalid
registrations recover from valid saved teams or owned cards when possible;
an invalid lineup cannot be locked. A player with enough usable cards cannot
evade all PvP by clearing their saved teams.

## SQLite storage

All v11.7.4 storage changes remain in the full runtime. Applying the changed-files
ZIP over v11.7.4 preserves that code. Replay cleanup is triggered by writes and
batched after a delay; it does not add a permanent per-minute cleanup poll.
Ownership, wallets, caps, ratings, scores, records and active sessions remain.

To reclaim existing file space, stop the bot and follow `STORAGE-RETENTION.md`.
`python scripts/cleanup_storage.py /actual/database/path` first provides a read-only
size report; the documented backup/cleanup/VACUUM options perform the offline
cleanup. This release contains no player database or credentials. Physical space
reclaimed depends on the real database contents and cannot be estimated from its
file size alone.
