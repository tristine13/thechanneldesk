"""The one list of work types ("sections") used by The Channel Desk.

An identical copy of this file lives in BOTH projects (channeldesk and channeldesk-news).
If you change it, change both, so a work gets the same label and the same section everywhere.

posts.type and tasks.type are free text in the database, and older rows can hold names such as
"College News", "Sports News", "Op/Ed", "Column", a different capitalisation, or even an empty
value. normalize_type() turns any of those into one of the eight official labels below.
"""
import re

# (url slug on the student site, label shown everywhere, in display order)
SECTION_LIST = [
    ("news", "News"),
    ("editorial", "Editorial"),
    ("opinion", "Opinion"),
    ("feature", "Feature"),
    ("literary", "Literary"),
    ("sci-tech", "Sci-Tech"),
    ("sports", "Sports"),
    ("lokal", "Lokal"),
]
TYPES = [label for _, label in SECTION_LIST]
SLUG_OF = {label: slug for slug, label in SECTION_LIST}
LABEL_OF = {slug: label for slug, label in SECTION_LIST}
DEFAULT_TYPE = "News"

# keys are lower-case letters and digits only (spaces, "-", "/" and "&" are ignored when matching)
_ALIASES = {
    "news": "News", "collegenews": "News", "campusnews": "News", "hardnews": "News",
    "editorial": "Editorial", "editorials": "Editorial",
    "opinion": "Opinion", "opinions": "Opinion", "column": "Opinion", "columns": "Opinion",
    "oped": "Opinion", "opeds": "Opinion", "commentary": "Opinion",
    "feature": "Feature", "features": "Feature", "featurestory": "Feature",
    "literary": "Literary", "literature": "Literary",
    "scitech": "Sci-Tech", "sciencetech": "Sci-Tech", "sciencetechnology": "Sci-Tech",
    "scienceandtechnology": "Sci-Tech", "science": "Sci-Tech", "technology": "Sci-Tech",
    "tech": "Sci-Tech",
    "sports": "Sports", "sport": "Sports", "sportsnews": "Sports",
    "lokal": "Lokal", "local": "Lokal",
}


def normalize_type(raw):
    """Any stored type -> one of TYPES. Unknown or empty values count as News."""
    key = re.sub(r"[^a-z0-9]", "", str(raw or "").lower())
    return _ALIASES.get(key, DEFAULT_TYPE)


def slug_of(raw):
    """The student-site section slug for any stored type."""
    return SLUG_OF[normalize_type(raw)]
