"""The Downloads folder, and picking files in it by keyword. Needs OpenCC (pip install opencc)."""
import os

import opencc

DOWNLOADS = os.path.join(os.path.expanduser('~'), 'Downloads')
TO_SIMPLIFIED = opencc.OpenCC('t2s')


def name_matches(name, keyword):
    """True when the file name contains every space-separated word of the keyword,
    ignoring case and simplified/traditional Chinese differences (both sides compared as simplified)."""
    name = TO_SIMPLIFIED.convert(name).lower()
    return all(word in name for word in TO_SIMPLIFIED.convert(keyword).lower().split())
