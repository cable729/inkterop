"""GoodNotes 6 (.goodnotes) reader — independently reverse-engineered.

Format facts from franzthiemann/goodparse's README (format documentation)
plus our own probing of public samples; ALL code here is an independent
implementation (goodparse is GPL-3.0 and its source is deliberately
unread/unused). See docs/formats/goodnotes.md.
"""

from .reader import GoodnotesReader  # noqa: F401
