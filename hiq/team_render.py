from __future__ import annotations

import asyncio
import io

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import CardInstance, CharacterDef

CANVAS = (900, 600)


async def _fetch_square(session: aiohttp.ClientSession, url: str, size: int = 180) -> Image.Image | None:
    if not url:
        return None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), headers={'User-Agent': 'Mozilla/5.0'}) as r:
            if r.status != 200:
                return None
            data = await r.read()
        img = Image.open(io.BytesIO(data)).convert('RGB')
        return ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS)
    except Exception:
        return None


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf', size)
    except Exception:
        return ImageFont.load_default()


async def render_team_image(
    entries: list[tuple[CardInstance | None, CharacterDef | None, str]],
    team_name: str,
) -> io.BytesIO:
    """Render a 1-front / 2-back formation, including visible empty saved slots."""
    if len(entries)==6:return await _render_six(entries,team_name)
    img = Image.new('RGB', CANVAS, (20, 24, 40))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((35, 35, 865, 565), radius=32, fill=(35, 42, 67), outline=(112, 104, 168), width=4)
    draw.line((450, 70, 450, 530), fill=(92, 100, 130), width=3)
    draw.line((80, 300, 820, 300), fill=(92, 100, 130), width=3)
    draw.text((55, 50), team_name, fill='white', font=_font(32, True))
    draw.text((55, 92), 'FRONTLINE', fill=(205, 207, 230), font=_font(18, True))
    draw.text((55, 332), 'BACKLINE', fill=(205, 207, 230), font=_font(18, True))

    fronts = [x for x in entries if x[2] == 'front']
    backs = [x for x in entries if x[2] == 'back']
    # Always draw all three formation slots. Missing cards stay as clean outlined blanks.
    front = fronts[0] if fronts else (None, None, 'front')
    back1 = backs[0] if len(backs) > 0 else (None, None, 'back')
    back2 = backs[1] if len(backs) > 1 else (None, None, 'back')
    placements = [
        (front, (360, 95), 'Frontline'),
        (back1, (190, 350), 'Backline 1'),
        (back2, (530, 350), 'Backline 2'),
    ]

    async with aiohttp.ClientSession() as session:
        fetched = await asyncio.gather(*[
            _fetch_square(session, char.character_icon) if char else asyncio.sleep(0, result=None)
            for (card, char, lane), _, _ in placements
        ])

    for (((card, char, lane), (x, y), slot_label), icon) in zip(placements, fetched):
        box = (x - 10, y - 10, x + 190, y + 190)
        draw.rounded_rectangle(box, radius=18, fill=(10, 13, 24), outline=(230, 231, 246), width=5)
        if card and char:
            if icon:
                img.paste(icon, (x, y))
            else:
                draw.rectangle((x, y, x + 180, y + 180), fill=(48, 53, 76))
                initials = ''.join(part[0] for part in char.name.split()[:2]).upper()
                draw.text((x + 65, y + 58), initials, fill='white', font=_font(42, True))
            label = f'{char.name} +{card.merge_level}'
            draw.text((x, y + 188), label[:28], fill='white', font=_font(18, True))
            draw.text((x, y + 212), char.title[:32] if char.title else char.position, fill=(200, 203, 222), font=_font(14))
        else:
            draw.rectangle((x, y, x + 180, y + 180), fill=(29, 34, 53), outline=(78, 86, 118), width=2)
            empty_text = 'EMPTY'
            bbox = draw.textbbox((0, 0), empty_text, font=_font(28, True))
            tw = bbox[2] - bbox[0]
            draw.text((x + (180 - tw) / 2, y + 67), empty_text, fill=(142, 149, 177), font=_font(28, True))
            draw.text((x, y + 188), slot_label, fill=(200, 203, 222), font=_font(17, True))
            draw.text((x, y + 212), 'No card assigned', fill=(142, 149, 177), font=_font(14))

    out = io.BytesIO()
    img.save(out, format='PNG', optimize=True)
    out.seek(0)
    return out


async def _render_six(entries,team_name):
    from .stats import permanent_stats
    from .battle.equipment import add_base_equipment,equipment_key
    from .battle_render import fit_text
    img=Image.new('RGB',(1380,900),(20,24,40));draw=ImageDraw.Draw(img)
    draw.rounded_rectangle((25,25,1355,875),radius=32,fill=(35,42,67),outline=(112,104,168),width=4)
    draw.text((55,46),fit_text(draw,team_name,_font(30,True),1270),fill='white',font=_font(30,True))
    draw.text((55,94),'6v6 • 3 FRONTLINE / 3 BACKLINE',fill=(205,207,230),font=_font(19,True))
    async with aiohttp.ClientSession() as session:
        icons=await asyncio.gather(*[_fetch_square(session,c.character_icon,140) if c else asyncio.sleep(0,result=None) for _,c,_ in entries])
    for i,((card,c,lane),icon) in enumerate(zip(entries,icons)):
        x=55+(i%3)*425;y=142+(i//3)*356
        draw.rounded_rectangle((x,y,x+395,y+328),radius=18,fill=(15,18,31),outline=(78,86,118),width=2)
        draw.text((x+18,y+15),f'{"FRONTLINE" if lane=="front" else "BACKLINE"} {i%3+1}',fill=(189,193,218),font=_font(16,True))
        if icon:img.paste(icon,(x+18,y+50))
        else:
            draw.rounded_rectangle((x+18,y+50,x+158,y+190),radius=10,fill=(48,53,76))
            draw.text((x+45,y+98),c.name[:2].upper() if c else '—',fill='white',font=_font(35,True))
        if card and c:
            draw.text((x+176,y+57),fit_text(draw,c.name,_font(23,True),200),fill='white',font=_font(23,True))
            words=c.title.split();lines=['']
            for word in words:
                if draw.textlength((lines[-1]+' '+word).strip(),font=_font(16))>200:lines.append(word)
                else:lines[-1]=(lines[-1]+' '+word).strip()
            for j,line in enumerate(lines[:3]):draw.text((x+176,y+93+j*23),line,fill=(200,203,222),font=_font(16))
            draw.text((x+176,y+169),f'{c.rarity}★ • +{card.merge_level} • {c.position}',fill=(221,222,236),font=_font(16,True))
            st=add_base_equipment(permanent_stats(c,card.merge_level,card.boon_stat,card.bane_stat),equipment_key(card));off=c.offense_key
            draw.text((x+18,y+213),f'HP {st["hp"]}   {off.upper()} {st[off]}   DEF {st["def"]}',fill='white',font=_font(20,True))
            draw.text((x+18,y+248),f'REC {st["rec"]}   SPD {st["spd"]}   JMP {st["jmp"]}',fill=(205,207,230),font=_font(20))
            draw.text((x+18,y+291),f'ID {card.instance_id}',fill=(156,164,192),font=_font(15))
        else:draw.text((x+176,y+90),'Empty slot',fill=(200,203,222),font=_font(24,True))
    out=io.BytesIO();img.save(out,format='PNG',optimize=True);out.seek(0);return out
