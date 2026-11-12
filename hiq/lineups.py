def identities(character):
    return set(character.components or (character.name,))


def valid_characters(characters):
    seen=set()
    for char in characters:
        names=identities(char)
        if seen & names:return False
        seen.update(names)
    return True


def validate_characters(characters):
    if not valid_characters(characters):
        raise ValueError('A team cannot contain duplicate characters or a Duo alongside either of its component characters.')


def validate_duos(characters):
    """AF1 and scripted enemies retain their existing variant rules."""
    for duo in (c for c in characters if c.is_duo):
        if any(c is not duo and identities(duo) & identities(c) for c in characters):
            raise ValueError('A Duo cannot share a team with either component character.')
