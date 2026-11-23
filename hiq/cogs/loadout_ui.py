"""Explicit unlock previews shared by normal teams, AF1 and PvP."""
import discord
from ..ui import SafeView
from ..loadouts import locked
from ..items import ITEM_BY_KEY
from ..data import CHAR_BY_ID

async def preview(db,uid,kind,slot):
    lines=[]
    async with db.connect() as c:
        entries=await locked(c,uid,kind,slot)
        for entry in filter(None,entries or []):
            card=await (await c.execute('SELECT * FROM cards WHERE owner_id=? AND instance_id=?',(uid,entry['id']))).fetchone()
            if not card:continue
            old_id=entry.get('item_id');new_id=card['equipped_item_id']
            if old_id==new_id:continue
            async def name(iid):
                if not iid:return 'No item'
                row=await (await c.execute('SELECT detail FROM inventory_items WHERE owner_id=? AND id=?',(uid,iid))).fetchone()
                return ITEM_BY_KEY.get(row['detail'],{}).get('name','Missing item') if row else 'Missing item'
            char=CHAR_BY_ID[card['character_id']]
            lines.append(f'{char.name} — {char.title}: {await name(old_id)} → {await name(new_id)}')
    return '\n'.join(lines) or 'Items will follow each character’s current equipped item.'

class UnlockConfirm(SafeView):
    def __init__(self,uid,action,refresh=None):
        super().__init__(timeout=120);self.uid=uid;self.action=action;self.refresh=refresh;self.closed=False
    async def interaction_check(self,i):
        if i.user.id!=self.uid or self.closed:
            await i.response.send_message('This unlock prompt is closed or belongs to another player.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Confirm Unlock',style=discord.ButtonStyle.primary)
    async def confirm(self,i,button):
        if self.closed:return
        self.closed=True;self.stop();await i.response.defer()
        await self.action()
        await i.edit_original_response(content='Unlock confirmed.',view=None)
        if self.refresh:await self.refresh()
    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):
        self.closed=True;self.stop();await i.response.edit_message(content='Unlock cancelled.',view=None)
