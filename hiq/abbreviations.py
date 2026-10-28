"""Shared user-facing abbreviations; canonical names still work everywhere."""
SCHOOLS = {
    'aob':'Aoba Johsai', 'nek':'Nekoma', 'kar':'Karasuno',
    'shi':'Shiratorizawa', 'kam':'Kamomedai', 'kamo':'Kamomedai',
    'tsu':'Tsubakihara', 'tsub':'Tsubakihara', 'muj':'Mujinazaka', 'dat':'Date Tech', 'fuk':'Fukurodani', 'ina':'Inarizaki',
}


def school_name(raw):
    raw = str(raw or '').strip()
    return SCHOOLS.get(raw.casefold(), raw)


def mode_name(raw):
    raw = str(raw or '').casefold().strip()
    return {'r':'regional','region':'regional','regionals':'regional',
            'n':'national','nation':'national','nationals':'national'}.get(raw,raw)
