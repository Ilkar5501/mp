"""Counted raid shards. Crafted equipment and cards retain their instance IDs."""
from __future__ import annotations

KINDS = ('item_shard', 'character_shard')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS shard_balances (
 owner_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('item_shard','character_shard')),
 detail TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
 PRIMARY KEY(owner_id,kind,detail)
);
'''


async def migrate_legacy_shards(conn):
    """Credit and remove old shard IDs in one transaction; repeat-safe on restart."""
    await conn.execute('BEGIN IMMEDIATE')
    try:
        await conn.execute('''
            INSERT INTO shard_balances(owner_id,kind,detail,quantity)
            SELECT owner_id,kind,detail,COUNT(*) FROM inventory_items
            WHERE kind IN ('item_shard','character_shard') GROUP BY owner_id,kind,detail
            ON CONFLICT(owner_id,kind,detail) DO UPDATE SET quantity=quantity+excluded.quantity
        ''')
        await conn.execute("DELETE FROM inventory_items WHERE kind IN ('item_shard','character_shard')")
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def balance(conn, owner_id, kind, detail):
    row = await (await conn.execute(
        'SELECT quantity FROM shard_balances WHERE owner_id=? AND kind=? AND detail=?',
        (owner_id, kind, detail),
    )).fetchone()
    return int(row['quantity']) if row else 0


async def credit(conn, owner_id, kind, detail, quantity=1):
    if kind not in KINDS or not detail or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError('Choose a shard type and a positive whole quantity.')
    await conn.execute('''
        INSERT INTO shard_balances VALUES (?,?,?,?)
        ON CONFLICT(owner_id,kind,detail) DO UPDATE SET quantity=quantity+excluded.quantity
    ''', (owner_id, kind, detail, quantity))
    return await balance(conn, owner_id, kind, detail)


async def consume(conn, owner_id, kind, detail, quantity):
    if kind not in KINDS or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError('Shard quantities must be positive whole numbers.')
    cur = await conn.execute('''
        UPDATE shard_balances SET quantity=quantity-?
        WHERE owner_id=? AND kind=? AND detail=? AND quantity>=?
    ''', (quantity, owner_id, kind, detail, quantity))
    if cur.rowcount != 1:
        have = await balance(conn, owner_id, kind, detail)
        raise ValueError(f'Requires {quantity} matching shards; you have {have}.')


def label(kind, detail):
    from .items import ITEM_BY_KEY
    return (ITEM_BY_KEY.get(detail, {}).get('name', detail) if kind == 'item_shard' else detail) + ' Shard'


def resolve(raw):
    """Resolve user-facing item/character names for quantity-based trading."""
    from .items import find_items, normalize
    from .data import find_character
    items = find_items(raw)
    chars = find_character(raw)
    exact = [('item_shard', i['key']) for i in items if normalize(raw) in (normalize(i['name']), normalize(i['key']))]
    exact += [('character_shard', c.id) for c in chars if normalize(raw) in (normalize(c.id), normalize(c.card_id), normalize(c.name))]
    matches = exact or [('item_shard', i['key']) for i in items] + [('character_shard', c.id) for c in chars]
    if len(matches) != 1:
        raise ValueError(f'Use an exact item name or character name and title for shards: {raw}')
    return matches[0]
