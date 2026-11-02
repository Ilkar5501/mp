# Equipment crafting and raid balance — v11.3.2

Stop the bot, replace `VERSION`, `data/items.json`, and `hiq/raid_store.py` from GitHub main, and restart. Keep your database and `.env`. No dependency changes are needed.

Equipment crafting costs change as follows:

| Previous | New |
| --- | --- |
| 5 | 3 |
| 10 | 6 |
| 20 | 9 |
| 25 | 12 |

All five raid themes now contain exactly 12 items. Four items move:

- Synchronized Attack: Offense → Tactics.
- Bonus Scrutiny: Tactics → Defense.
- Smelling Salts: Offense → Recovery.
- Resistance Bands: Offense → Recovery.

Individual drop weights, currency/character drop rules, item effects, equipment ownership, and recycling payouts are unchanged. Each theme continues to normalize its eligible item weights, so items are not all equally likely within a raid. Capped drops continue to be removed and the remaining pool reweighted.

Item shard allowances remain two recipes per item per day: 6, 12, 18, or 24 shards at the new costs. Unused allowances still accumulate during the week, up to 14 recipes per item. Already-earned shards and recorded raid usage are retained; no balance reset or conversion occurs. Character and school crafting costs are unchanged.

The website catalogue is updated separately with matching costs, raid themes, and shard allowances.
