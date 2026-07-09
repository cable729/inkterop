"""Notability: legacy .note reader, modern .ntb reader + writer.

Legacy format facts: Julia Evans' 2018 teardown (jvns.ca) verified alive on
2026-era public samples (GLKeyedArchiver + parallel float arrays). The
modern .ntb export (Mac app 16.x) was decoded in-repo — see
docs/formats/notability.md.
"""

from .ntb import NtbReader  # noqa: F401
from .reader import NotabilityReader  # noqa: F401
from .writer import NtbWriter  # noqa: F401
