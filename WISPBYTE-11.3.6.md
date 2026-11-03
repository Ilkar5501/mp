# Grouped equipment picker — v11.3.6

From v11.3.5, stop the bot, replace VERSION, hiq/cogs/raids.py and hiq/raid_store.py, then restart. Keep your database and .env. No dependency changes.

The equipment picker on character cards first shows one entry per eligible owned item type, with total, unequipped and equipped counts. Select a type to inspect its effect and individual copies, ten per page. Every copy shows its item name and ID. Equipped copies show the current character name, title and card ID directly underneath. Previous/next and All item types controls allow browsing without losing the original type-list page.

Selecting an equipped copy transfers it from its current card to the viewed card atomically. The target's old item becomes unequipped and remains in inventory. The direct equip command uses the same transfer behavior. Ownership and eligibility are rechecked at selection time; failed or stale requests do not detach another card's item. Concurrent requests cannot equip one copy to multiple cards.

Character skill notes remain available through Click to learn more. Other users can read notes but cannot operate the owner's equipment controls.
