"""Private, separately paged explanations for character skills and named effects."""
import re
import discord
from .data import CHARACTERS
from .ui import EmbedPager
from .cards import rarity_color


def explanations(character):
    definitions={}
    for card in CHARACTERS:
        for key,text in card.extended.items():
            if not text:continue
            match=re.match(r'\[([^]]+)\]\s*:?\s*',text)
            if match:definitions[match[1]]=text[match.end():]
            elif key not in ('U','A','B','C'):definitions[key]=text
    definitions.setdefault('Cannot Counter','The affected character cannot counterattack.')
    notes={}
    for slot,ability in character.abilities.items():
        for name in re.findall(r'\[([^]]+)\]',ability.text):
            if name in definitions:notes[f'[{name}]']=definitions[name]
        text=character.extended.get(slot,'')
        if text:
            match=re.match(r'\[([^]]+)\]\s*:?\s*',text)
            if match:notes[f'[{match[1]}]']=text[match.end():]
            else:notes[f'{"Ultimate" if slot=="U" else slot+" skill"} — {ability.name}']=text
    return list(notes.items())


def add_learn_more(view,character):
    notes=explanations(character)
    if not notes:return
    button=discord.ui.Button(label='Click to learn more',style=discord.ButtonStyle.secondary,row=2,custom_id='card:learnmore')
    async def show(interaction):
        pages=[discord.Embed(title=f'{character.name} — {name}',description=text,color=rarity_color(character)) for name,text in notes]
        pager=EmbedPager(pages,interaction.user.id)
        await interaction.response.send_message(embed=pager.embed(),view=pager,ephemeral=True)
    button.callback=show;view.add_item(button)


class CharacterNotesView(discord.ui.View):
    def __init__(self,character):
        super().__init__(timeout=600)
        add_learn_more(self,character)
        self.art_slot=0
        if character.shiny_arts:
            for text,d in [('↑',-1),('↓',1)]:
                button=discord.ui.Button(label=text,row=0)
                async def nav(i,delta=d):
                    from .cards import template_embed
                    self.art_slot=(self.art_slot+delta)%(1+len(character.shiny_arts))
                    await i.response.edit_message(embed=template_embed(i.client,character,art_slot=self.art_slot),view=self)
                button.callback=nav;self.add_item(button)
