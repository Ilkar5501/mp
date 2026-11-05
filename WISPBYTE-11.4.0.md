# Match Point v11.4.0

Stop the bot before uploading. Replace `bot.py`, `VERSION`, and the complete `hiq/` folder from this package, including its subfolders. The complete runtime package also includes `data/` and `requirements.txt`; these are unchanged in this release. Keep your live database, `.env`, and hosting configuration. Restart with your normal `python bot.py` command. New auction tables are created automatically on startup. Back up the live database before upgrading.

The Wispbyte guide and package manifest are reference files; they do not need to be uploaded. Do not upload a development database or replace your live database.

## Clan Contest

Player daily percentage = best daily damage / highest player damage that day × 100. Clan daily percentage = combined best daily member damage / highest clan total that day × 100. Weekly standings and top-three prizes use the sum of seven daily percentages, up to 700%. Missing days count as zero. Tied daily leaders each receive 100%. If all submitted scores on a day are zero, participating leaders tie at 100% until someone records damage. Raw damage is retained in run results.

`;contestboard`, `;ccboard` and `;cb` show each day's percentage and the cumulative weekly total for both clans and players. Percentages update as better results arrive; weekly settlement locks results and pays prizes exactly once. Existing raw scores in the current unfinished week are included automatically. Previously paid prizes are unchanged.

## Merging and teams

Merging a duplicate that is used on a saved team replaces it with the surviving card in that team's position. This applies to 3v3, 6v6 and legacy teams. If an older invalid saved lineup already contains both cards, it keeps the survivor's existing position and clears the duplicate position.

You can merge one unlocked +10 duplicate into an unlocked +0 through +9 duplicate of the exact same character and title. The selected survivor becomes +10, keeps its own boon/bane, ID, history and equipment, and consumes the +10 donor. The merge menu requires an extra **Confirm +10 Transfer** step. Extra materials and transfers into an already +10 card remain rejected. A consumed card's equipment remains in your inventory.

`;teamedit <slot> <new name>` renames a saved team. Names can contain spaces, up to 40 characters. `;teamedit <slot>` still opens the lineup editor.

## Global auctions

- `;market [search]`: browse the shared market across all servers.
- `;auction`: choose a card, inventory item, school shards or character shards to sell. Item shards cannot be sold.
- `;auction <auction ID>`: inspect and bid, buy out, refresh or cancel a listing.
- `;myauctions`: view your active listings and sale history.

Listing menus accept a minimum and optional buyout for Coins, Clan Dollars, or both. Leave a currency blank to disable it. Prices are positive whole numbers, up to 1 billion. Duration is 1–24 hours. Card and item listings contain one instance; shard listings contain your chosen quantity. Prices apply to the whole listing. A confirmation previews the asset and prices before listing it.

At expiry, each currency's highest bid is divided by that currency's minimum. The larger ratio wins; equal ratios favor the earlier currently leading bid. Example: 300 Coins against a 100-Coin minimum (3×) beats 500 Clan Dollars against a 200-Dollar minimum (2.5×). A buyout in either currency immediately wins, regardless of other bids. A bid above the buyout is charged only the buyout price.

Assets and bid currency are held in escrow. Outbid players are refunded automatically. Sellers may cancel at any time before expiry or sale, refunding all bids. No bids at expiry returns the asset. Sellers cannot bid on their own listings. Auctions survive restarts and settle automatically, including expired listings after downtime. Settlement, refunds, payment and delivery commit together, once. No trading fees are charged.

Listed cards leave saved teams and are unequipped; their equipment stays with the seller. Equipped items must be unequipped before listing. Original card IDs, natures, merges and ownership history survive a sale. Returning an unsold card does not rebuild its previous team slots. Winning bids and receipts can be checked with the auction ID; seller history is in `;myauctions`.

## Tournament CPU pool

An additional formation is eligible in `;to`. Existing formations remain available. Only Teradomari is +10; the other five are +0.

| Position | Character | Nature | Equipment |
| --- | --- | --- | --- |
| F1 | Echigo | +Qik / −Spd | Scouting Clipboard |
| F2 | Hinata — Wings of Dawn | +Spd / −HP | Burden of Expectation |
| F3 | Akifumi | +Spd / −HP | Smelling Salts |
| B1 | Atsumu | +Spd / −HP | Smelling Salts |
| B2 | Kageyama — Crown of Precision | +Pow / −HP | Freezing Stare |
| B3 | Teradomari +10 | +Spd / −HP | Burden of Expectation |

Kenma's uncapped Fatigue remains unchanged.
