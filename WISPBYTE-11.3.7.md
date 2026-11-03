# Private Clan Contest damage reports — v11.3.7

From v11.3.6, stop the bot and replace VERSION, hiq/contest.py, hiq/battle/engine.py, hiq/battle/equipment.py, hiq/cogs/contest.py and hiq/cogs/utility.py. Restart while keeping your database and .env. No dependencies change.

New Clan Contest results have a Damage Report button alongside the battle log. Only the run owner can open it, and the embed is ephemeral. It lists damage dealt, tanked and mitigated for all six player characters and the featured opponent. Noncombat/support damage is grouped under Misc. sources on each side rather than credited as a support character's combat damage. Self-inflicted damage is identified and is not credited to the other side. Mitigation uses the existing combat statistics. Damage accounting is saved with each new result without a database schema change. The controls have the same 15-minute lifetime as the existing battle-log button.

The existing ;cc <slot> alias is retained. ;cb is added for ;contestboard, with ;ccboard also retained. Both ;cc and ;cb are now listed once in ;abbreviations.
