"""The featured icon above a formation of six inactive support icons."""
import asyncio,io
import aiohttp
from PIL import Image,ImageDraw,ImageFont


def _font(size,bold=False):
    for name in ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf',
                 'C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf'):
        try:return ImageFont.truetype(name,size)
        except OSError:pass
    return ImageFont.load_default()


async def render_contest(specs):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        async def fetch(char):
            try:
                async with session.get(char.character_icon) as response:
                    response.raise_for_status();data=await response.read()
                return Image.open(io.BytesIO(data)).convert('RGB')
            except Exception:return Image.new('RGB',(160,160),'#5b6478')
        images=await asyncio.gather(*(fetch(char) for _,char,_ in specs))
    canvas=Image.new('RGB',(1080,530),'#171e30');draw=ImageDraw.Draw(canvas)
    draw.text((32,20),'CLAN CONTEST',font=_font(25,True),fill='white')
    canvas.paste(images[0].resize((150,150)),(465,65))
    feature=specs[0][1]
    draw.text((540,225),f'{feature.name} — {feature.school}',font=_font(21,True),fill='white',anchor='mt')
    bond=sum(char.school==feature.school for _,char,_ in specs[1:])
    draw.text((540,258),f'Featured unit • School Bond +{bond}',font=_font(18),fill='#c3cbdd',anchor='mt')
    for i,(_,char,_) in enumerate(specs[1:]):
        x=35+i*175;canvas.paste(images[i+1].resize((110,110)),(x+25,320))
        draw.text((x+80,441),char.name,font=_font(16,True),fill='white',anchor='mt')
        draw.text((x+80,466),char.school,font=_font(13),fill='#c3cbdd',anchor='mt')
    draw.text((540,503),'Supports cannot act or be targeted. Only their listed support slots apply.',font=_font(16),fill='#c3cbdd',anchor='mt')
    out=io.BytesIO();canvas.save(out,format='PNG');out.seek(0);return out
