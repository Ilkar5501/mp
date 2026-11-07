# HiQ Battler v11.6.1

Sakusa's base Pow is **50**, down from 53. Total base stats are **349**. His supplied card art and character icon are installed. The final punctuation after the supplied card-art URL was removed; the actual `.jpg` URL was verified to decode.

## Equipment eligibility

Eight existing items now check the card's position tag:

- Rock Solid, All Out, Tricky, Tiring and Wicked Game Plans: **Setters (S)**.
- Synchronized Attack and Athletic Tape: **Middle Blockers (MB)**.
- Padded Arm Sleeves: **Liberos (Li)**.

A newly added card with the matching position is eligible automatically. The data uses `eligible_positions`, for example `"eligible_positions": ["MB"]`. The existing `eligible` field is a short display label. Exact `eligible_character_ids` take precedence, so signatures remain exclusive to their specified card variants. Captain's Armband retains its captain name list because captaincy is not a volleyball position. Other unrestricted Tactics-category items remain unrestricted.

Both local gallery copies and item pages show the same position labels. Sakusa appears in the gallery with his updated stats and embedded artwork.

## Outstanding work

**Elo PvP has not been implemented.** The current `;pvp` requires an opponent and uses both active teams. Untargeted matchmaking, separate 3v3/6v6 PvP teams and locks, five-match opponent avoidance, Elo ratings, the monthly reset/prizes and `;pvpboard` are outstanding.

Five older audit issues still reproduce. They were reviewed, not changed in this patch:

1. Simultaneous daily claims can issue duplicate free packs.
2. Paid packs can spend coins before card delivery fails. Payment, pity and delivery need one transaction.
3. Prefecture omits passive-save fallback.
4. Kiryu — Last Hope incorrectly inherits Reluctant Titan's anti-save behavior in Prefecture.
5. Smelling Salts' virtual comparison Spd increases Hinata's Open Play damage scaling.

The pack transaction issues are the highest priority. The earlier Prefecture Untargetable finding is already fixed and its regression passes. All five remaining findings are explicitly marked expected failures in the existing audit tests. Passing combat tests do not establish competitive balance; the new modes, signatures and Sakusa still need player matchup testing. AF1 currently has no reward currency, following removal of the cross-mode crafting requirement.

## Install and validation

Prepared locally, not deployed. The full runtime includes v11.6.0's modes and signatures. For a v11.6.0 installation, stop the bot, back up the database, replace every file in the incremental patch and restart with the same environment and database. No schema or dependency change is introduced by v11.6.1.

The gallery ZIP is separate: upload both `index.html` and `items.html` together. It is not a bot patch. Browser checks confirm both images decode, all 67 cards load, eight position rules match, mobile fits and no JavaScript errors occur. Eligibility tests cover every current card and synthetic future cards, plus preservation of signature/captain restrictions. The 202-test regression suite completes successfully with the same five pre-existing expected failures; runtime validation also passes.
