from __future__ import annotations
import asyncio
import io
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps


def _font(size, bold=False):
    try: return ImageFont.truetype('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf',size)
    except OSError: return ImageFont.load_default()


def fit_text(draw,text,font,width):
    text=str(text)
    if draw.textlength(text,font=font)<=width:return text
    while text and draw.textlength(text+'…',font=font)>width:text=text[:-1]
    return text+'…'


def trophy(draw,x,y):
    gold=(247,198,76)
    draw.arc((x-4,y+2,x+28,y+24),0,180,fill=gold,width=4)
    draw.polygon([(x,y),(x+24,y),(x+20,y+19),(x+12,y+24),(x+4,y+19)],fill=gold)
    draw.rectangle((x+10,y+22,x+14,y+30),fill=gold)
    draw.rounded_rectangle((x+3,y+30,x+21,y+34),radius=2,fill=gold)


async def _fetch(session,url,size=64):
    if not url:return None
    try:
        async with session.get(url,timeout=aiohttp.ClientTimeout(total=6),headers={'User-Agent':'Mozilla/5.0'}) as r:
            if r.status!=200:return None
            data=await r.read()
        return ImageOps.fit(Image.open(io.BytesIO(data)).convert('RGB'),(size,size),method=Image.Resampling.LANCZOS)
    except Exception:return None


async def render_battle_summary(result,a_specs,b_specs,name_a,name_b):
    six=True  # Consistent graphic layout for 3v3, 6v6 and 9v9.
    width=1440 if six else 1180
    step=180 if six else 165
    height=145+len(a_specs)*step+80
    margin=55;mid=width//2;card_width=mid-80
    img=Image.new('RGB',(width,height),(19,23,39));draw=ImageDraw.Draw(img)
    draw.rounded_rectangle((25,25,width-25,height-25),radius=28,fill=(35,42,67),outline=(112,104,168),width=4)
    draw.text((margin,43),f'BATTLE RESULT • {len(a_specs)}v{len(b_specs)}',fill=(189,193,218),font=_font(20,True))
    for side,name,hp,x in [('A',name_a,result.team_a_hp,margin),('B',name_b,result.team_b_hp,mid+35)]:
        tx=x
        if result.winner_side==side:trophy(draw,x,79);tx+=40
        draw.text((tx,76),fit_text(draw,name,_font(28,True),card_width-(tx-x)),fill='white',font=_font(28,True))
        draw.text((x,113),f'Team HP: {hp}',fill=(210,214,235),font=_font(19,True))
    draw.line((mid,145,mid,height-75),fill=(91,99,132),width=3)
    all_specs=[('A',a_specs,margin),('B',b_specs,mid+35)]
    async with aiohttp.ClientSession() as session:
        icons=await asyncio.gather(*[_fetch(session,c.character_icon,80 if six else 64) for _,specs,_ in all_specs for _,c,_ in specs])
    icon_i=0
    for side,specs,x in all_specs:
        for i,(card,c,lane) in enumerate(specs):
            y=158+i*step;icon=icons[icon_i];icon_i+=1
            draw.rounded_rectangle((x,y,x+card_width,y+step-15),radius=18,fill=(15,18,31),outline=(75,82,112),width=2)
            isize=80 if six else 64
            if icon:img.paste(icon,(x+16,y+19))
            else:
                draw.rectangle((x+16,y+19,x+16+isize,y+19+isize),fill=(48,53,76))
                draw.text((x+26,y+36),c.name[:2].upper(),fill='white',font=_font(23,True))
            tx=x+isize+32;textwidth=card_width-isize-48
            heading=(f'Pod {i//3+1} • ' if len(specs)==9 else '')+f'{"Frontline" if lane=="front" else "Backline"} • {c.name} +{card.merge_level if card else 0}'
            draw.text((tx,y+14),fit_text(draw,heading,_font(21 if six else 19,True),textwidth),fill='white',font=_font(21 if six else 19,True))
            draw.text((tx,y+43),fit_text(draw,c.title,_font(16),textwidth),fill=(189,193,218),font=_font(16))
            st=result.stats.get(f'{side}{i}:{c.id}',{})
            draw.text((tx,y+75),f'Dealt {st.get("damage_dealt",0)} • Tanked {st.get("damage_taken",0)}',fill=(232,233,242),font=_font(19 if six else 16))
            draw.text((tx,y+103),f'Attacks {st.get("attacks_made",0)} • Faced {st.get("attacks_tanked",0)}',fill=(181,186,211),font=_font(17 if six else 15))
            draw.text((tx,y+128 if six else y+123),f'Mitigated {st.get("damage_mitigated",0)}',fill=(181,186,211),font=_font(17 if six else 14))
    draw.text((margin,height-58),f'Turns: {result.turns}',fill=(197,201,224),font=_font(19,True))
    out=io.BytesIO();img.save(out,format='PNG',optimize=True);out.seek(0);return out


async def render_af1_summary(result,a_specs,b_specs,name_a,name_b,marks):
    """Two main panels, each above six support slots in two rows of three."""
    width,height=1440,760
    img=Image.new('RGB',(width,height),(19,23,39));draw=ImageDraw.Draw(img)
    def font(size,bold=False):
        for name in (('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),('C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf')):
            try:return ImageFont.truetype(name,size)
            except OSError:pass
        try:return ImageFont.load_default(size=size)
        except TypeError:return ImageFont.load_default()  # Pillow 10.0 compatibility.
    draw.rounded_rectangle((25,25,width-25,height-25),radius=28,fill=(35,42,67),outline=(112,104,168),width=4)
    draw.text((55,43),'ALL FOR ONE • 1v1',fill=(189,193,218),font=font(20,True))
    draw.line((720,145,720,690),fill=(91,99,132),width=3)
    teams=[('A',a_specs,name_a,result.team_a_hp,55),('B',b_specs,name_b,result.team_b_hp,755)]
    async with aiohttp.ClientSession() as session:
        images=await asyncio.gather(*[_fetch(session,c.character_icon,80) for _,specs,_,_,_ in teams for _,c,_ in specs[:7]])
    cursor=0
    for side,specs,name,hp,x in teams:
        icons=images[cursor:cursor+min(len(specs),7)];cursor+=min(len(specs),7)
        tx=x
        if result.winner_side==side:trophy(draw,x,80);tx+=40
        draw.text((tx,77),fit_text(draw,name,font(28,True),610-(tx-x)),fill='white',font=font(28,True))
        draw.text((x,115),f'Team HP: {hp:,}',fill=(210,214,235),font=font(19,True))
        card,c,_=specs[0];stats=result.stats.get(f'{side}0:{c.id}',{})
        draw.rounded_rectangle((x,158,x+610,338),radius=18,fill=(15,18,31),outline=(75,82,112),width=2)
        if icons[0]:img.paste(icons[0],(x+16,180))
        else:draw.rectangle((x+16,180,x+96,260),fill=(48,53,76))
        draw.text((x+112,175),fit_text(draw,f'{c.name} +{card.merge_level if card else 0}',font(24,True),480),fill='white',font=font(24,True))
        draw.text((x+112,208),fit_text(draw,c.title,font(18),480),fill=(189,193,218),font=font(18))
        draw.text((x+112,249),f'Dealt {stats.get("damage_dealt",0):,} • Tanked {stats.get("damage_taken",0):,}',fill=(232,233,242),font=font(21))
        draw.text((x+112,282),f'Mitigated {stats.get("damage_mitigated",0):,}',fill=(181,186,211),font=font(21))
        draw.text((x,351),'SUPPORTS',fill=(189,193,218),font=font(17,True))
        for index in range(6):
            sx=x+(index%3)*207;sy=382+(index//3)*146
            draw.rounded_rectangle((sx,sy,sx+196,sy+134),radius=12,fill=(15,18,31),outline=(75,82,112),width=1)
            if index+1>=len(specs):
                draw.text((sx+16,sy+57),'Empty support',fill=(143,151,178),font=font(17));continue
            card,support,_=specs[index+1]
            if icons[index+1]:img.paste(icons[index+1].resize((54,54)),(sx+12,sy+12))
            else:draw.rectangle((sx+12,sy+12,sx+66,sy+66),fill=(48,53,76))
            draw.text((sx+76,sy+25),f'+{card.merge_level if card else 0}',fill=(189,193,218),font=font(20,True))
            draw.text((sx+12,sy+77),fit_text(draw,support.name,font(17,True),172),fill='white',font=font(17,True))
            draw.text((sx+12,sy+103),fit_text(draw,support.title,font(14),172),fill=(189,193,218),font=font(14))
    draw.text((55,704),f'Turns: {result.turns}    •    AF1 Marks earned: +{marks}',fill=(197,201,224),font=font(21,True))
    out=io.BytesIO();img.save(out,format='PNG',optimize=True);out.seek(0);return out
