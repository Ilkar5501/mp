"""AF1 result text: the two fighting mains, with no player-of-match awards."""
import discord
from .ui import PURPLE


def af1_embed(result,a,b,name_a,name_b,marks):
    winner=name_a if result.winner_side=='A' else name_b if result.winner_side=='B' else 'Draw'
    embed=discord.Embed(title=f'AF1 Result — {winner}',color=PURPLE)
    for side,specs,name,hp in [('A',a,name_a,result.team_a_hp),('B',b,name_b,result.team_b_hp)]:
        _,char,_=specs[0];stats=result.stats.get(f'{side}0:{char.id}',{})
        embed.add_field(name=('🏆 ' if result.winner_side==side else '')+name,
            value=f'**{char.name} — {char.title}** · {hp} HP\n'
                  f'Dealt **{stats.get("damage_dealt",0):,}** · Tanked **{stats.get("damage_taken",0):,}** · Mitigated **{stats.get("damage_mitigated",0):,}**',inline=False)
    embed.add_field(name='Turns',value=str(result.turns),inline=True)
    embed.add_field(name='AF1 Marks earned',value=f'+{marks}',inline=True)
    embed.set_footer(text='Main vs main · Six support slots per side · AF1 Marks: up to 25 per day · ;fuse')
    embed.set_image(url='attachment://af1_result.png')
    return embed
