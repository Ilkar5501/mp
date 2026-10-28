from __future__ import annotations
import discord

PURPLE=discord.Color.from_rgb(72,42,115)


class SafeView(discord.ui.View):
    async def on_error(self,interaction,error,item):
        if isinstance(error,ValueError):
            if interaction.response.is_done():await interaction.followup.send(str(error),ephemeral=True)
            else:await interaction.response.send_message(str(error),ephemeral=True)
            return
        await super().on_error(interaction,error,item)


class EmbedPager(SafeView):
    def __init__(self,pages,owner=None):
        super().__init__(timeout=600)
        self.pages=pages;self.owner=owner;self.index=0;self.sync()

    async def interaction_check(self,i):
        if self.owner is not None and i.user.id!=self.owner:
            await i.response.send_message('This menu belongs to another player.',ephemeral=True);return False
        return True

    def sync(self):
        self.previous.disabled=self.index==0;self.next.disabled=self.index==len(self.pages)-1

    def embed(self):
        e=self.pages[self.index].copy();e.set_footer(text=f'Page {self.index+1}/{len(self.pages)}');return e

    @discord.ui.button(emoji='◀️',style=discord.ButtonStyle.secondary)
    async def previous(self,i,button):
        self.index=max(0,self.index-1);self.sync();await i.response.edit_message(embed=self.embed(),view=self)

    @discord.ui.button(emoji='▶️',style=discord.ButtonStyle.secondary)
    async def next(self,i,button):
        self.index=min(len(self.pages)-1,self.index+1);self.sync();await i.response.edit_message(embed=self.embed(),view=self)


def text_pages(title,lines,*,limit=3300):
    pages=[];chunk=[];size=0
    for line in lines:
        if chunk and size+len(line)>limit:
            pages.append(discord.Embed(title=title,description='\n'.join(chunk),color=PURPLE));chunk=[];size=0
        chunk.append(line);size+=len(line)+1
    pages.append(discord.Embed(title=title,description='\n'.join(chunk) or 'No entries yet.',color=PURPLE))
    return pages
