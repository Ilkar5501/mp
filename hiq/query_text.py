"""Search text accepts possessive apostrophes as well as quoted phrases."""
import re
import shlex


def split_query(text):
    protected = re.sub(r"(?<=\w)['’](?=\w)", '\ue000', text)
    return [word.replace('\ue000', "'") for word in shlex.split(protected)]
