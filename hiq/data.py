from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from .config import DATA_DIR
from .models import AbilityDef, CharacterDef


def load_characters(path: Path | None = None) -> list[CharacterDef]:
    path = path or DATA_DIR / 'characters.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    out = []
    for row in raw:
        out.append(CharacterDef(
            id=row['id'],
            name=row['name'],
            title=row['title'],
            position=row['position'],
            school=row['school'],
            rarity=int(row['rarity']),
            role_class=row.get('role_class', ''),
            card_art=row.get('card_art', ''),
            character_icon=row.get('character_icon', ''),
            stats={k: int(v) for k, v in row['stats'].items()},
            ult_cd=int(row['ult_cd']),
            abilities={k: AbilityDef(**v) for k, v in row['abilities'].items()},
            extended=row.get('extended', {}),
            clarification_count=int(row.get('clarification_count', 0)),
        ))
    return out


CHARACTERS = load_characters()
CHAR_BY_ID = {c.id: c for c in CHARACTERS}
CHAR_BY_CARD_ID = {c.card_id: c for c in CHARACTERS}


def _norm(text: str) -> str:
    return ' '.join(text.casefold().replace('—', ' ').replace('-', ' ').split())


def character_search_score(c: CharacterDef, query: str) -> float:
    q = _norm(query)
    if not q:
        return 1.0
    name = _norm(c.name)
    title = _norm(c.title)
    cid = _norm(c.id)
    card = _norm(c.card_id)
    haystacks = [name, title, cid, card, f'{name} {title}'.strip(), f'{title} {name}'.strip()]
    if c.name=='Kosuke' and c.title=='Behind the Iron Wall':
        haystacks.extend(['sakunami','kosuke sakunami','sakunami kosuke'])
    if q == name or q == cid or q == card:
        return 1.0
    if q in haystacks:
        return 0.98
    if any(q in h for h in haystacks if h):
        return 0.92
    q_tokens = set(q.split())
    token_score = max((len(q_tokens & set(h.split())) / max(1, len(q_tokens)) for h in haystacks if h), default=0.0)
    seq = max((SequenceMatcher(None, q, h).ratio() for h in haystacks if h), default=0.0)
    return max(seq, token_score * 0.95)


def find_character(query: str, *, cutoff: float = 0.54) -> list[CharacterDef]:
    q = _norm(query)
    if not q:
        return list(CHARACTERS)

    exact_id = [c for c in CHARACTERS if _norm(c.id) == q or _norm(c.card_id) == q]
    if exact_id:
        return exact_id

    # Exact character-name queries deliberately return every version with that name.
    exact_name = [c for c in CHARACTERS if _norm(c.name) == q]
    if exact_name:
        return exact_name

    scored = [(character_search_score(c, query), c) for c in CHARACTERS]
    scored = [(s, c) for s, c in scored if s >= cutoff]
    scored.sort(key=lambda x: (-x[0], x[1].name, x[1].title))
    if not scored:
        return []
    best = scored[0][0]
    # Keep strong near-ties so same-name variants and close title matches are not silently hidden.
    return [c for s, c in scored if s >= max(cutoff, best - 0.12)]


def best_character(query: str) -> CharacterDef | None:
    matches = find_character(query)
    return matches[0] if matches else None


def match_roster_names(query):
    """Match names first; only duplicated player names need title matching."""
    import re
    def norm(text):
        return re.sub(r'[^a-z0-9]+',' ',text.casefold().replace("'",'').replace('’','')).strip()
    q=norm(query)
    if not q:return list(CHARACTERS)
    groups={}
    for c in CHARACTERS:groups.setdefault(norm(c.name),[]).append(c)
    aliases={name:name for name in groups}
    aliases.update({'sakunami':'kosuke','kosuke sakunami':'kosuke','sakunami kosuke':'kosuke'})
    if q in aliases:return groups[aliases[q]]
    exact=[c for c in CHARACTERS if q in (norm(c.id),norm(c.card_id))]
    if exact:return exact
    named=[a for a in aliases if q.startswith(a+' ') or q.endswith(' '+a)]
    if named:
        name=max(named,key=len);variants=groups[aliases[name]]
        if len(variants)==1:return variants
        title=q.removeprefix(name).removesuffix(name).strip()
        exact=[c for c in variants if norm(c.title)==title]
        if exact:return exact
        partial=[c for c in variants if title in norm(c.title)]
        if partial:return partial
        scores=[(SequenceMatcher(None,title,norm(c.title)).ratio(),c) for c in variants]
        best=max(score for score,_ in scores)
        return [c for score,c in scores if score>=max(.65,best-.06)]
    names=[name for name in aliases if name.startswith(q)]
    if not names:names=[name for name in aliases if q in name]
    if names:
        wanted={aliases[name] for name in names}
        return [c for c in CHARACTERS if norm(c.name) in wanted]
    # Standalone title searches are useful only for players with multiple variants.
    titles=[c for name,variants in groups.items() if len(variants)>1 for c in variants if q in norm(c.title)]
    if titles:return titles
    scores={name:SequenceMatcher(None,q,name).ratio() for name in aliases}
    best=max(scores.values())
    wanted={aliases[name] for name,score in scores.items() if score>=max(.68,best-.06)}
    return [c for c in CHARACTERS if norm(c.name) in wanted]
