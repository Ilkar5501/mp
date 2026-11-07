# HiQ Battler v11.6.3

## Links

`;links` and `/links` show the community invite and the Characters, Items and Tier List pages at https://ilkar5501.github.io/mpgallery/. This informational command is available before the tutorial. The invite uses the existing `COMMUNITY_SERVER_URL` setting; `BOT_WEBSITE_URL` can override the website base URL. Defaults are the existing tutorial invite and the published mpgallery project site.

## Inventory

- `;inv`: up to 25 entries per page, with previous/next arrows.
- `;inv suga`: search owned character shards, item shards, equipment and other inventory items. School shards are included too.
- `;inv captans armband`: fuzzy names are accepted. Case, spaces and punctuation are ignored; aliases and item IDs also work.
- `;inv toggle`, `;inv stacked`, `;inv regular`: retain the existing saved view preference. A mode may precede a search, such as `;inv regular captain`.
- Search, Clear search and Stack items/Show IDs buttons work within the menu. Only its owner can operate it.

Counted shards stay counted. Regular equipment rows keep their unique IDs and mark equipped copies. Stacked equipment includes every copy, including equipped ones. Search and paging do not consume, trade or otherwise change inventory. The view is a snapshot when opened; reopen `;inv` after inventory changes. A description-length guard can create a shorter page if necessary to fit Discord's embed limit.

## Included updates and integration

This release includes the previously prepared Tiny Titans and AF1 modes, four signature items, Sakusa, requested media updates, positional equipment rules and the PvP-only eight-stack Fatigue cap. The earlier release guides describe those changes; the latest rules take precedence.

Both repositories were fetched before integration. Their remote main branches contained merge commits with identical trees to the local bases. The local changes were fast-forwarded onto those commits before adding the requested updates. No force push, reset, file deletion, production database edit or credential change is required. Unrelated pre-existing local audit files remain untouched.

Ranked Elo PvP and the previously documented audit issues remain outstanding. A GitHub push updates source; the bot host must pull/redeploy and restart to use the new code. Keep its existing persistent database and environment settings.

## Validation

Focused inventory checks cover 25-row paging, arrows, search/clear, fuzzy spelling, punctuation/case, aliases, IDs, counted shards, stacked counts, ownership and saved preferences. The links test checks the invite and all three website URLs. Runtime data, all 23 battle-rule groups and offline registration of 74 slash commands pass.
