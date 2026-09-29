"""Presentation for the shared Horonom statusline host (HORO-1565).

This module is the *only* place iconography, separators, widths, truncation and
presentation modes are decided. Products emit semantics — see
`governance/product/statusline-provider-contract.md` and
`scripts/statusline_contract.py` — and never choose a glyph, because two
products independently picking an emoji for "warning" is how a shared line
stops being readable.

It is deliberately pure: no I/O, no subprocesses, no clock. Everything here is
a function of its arguments, so the whole presentation surface is testable
without a filesystem or a host tool. The I/O half lives in
`scripts/statusline_compositor.py`.

The design bias is *glanceability over compression*. A statusline that is
technically correct but takes three seconds to decode has failed, which is the
finding that produced this module's existence.
"""

from __future__ import annotations

import enum
import unicodedata


class PresentationMode(enum.Enum):
    """How much room the host is willing to spend on being legible.

    Ordered least to most compressed. `PLAIN` is not a degraded mode — it is
    the correct mode for a terminal whose font or width handling makes emoji
    unreliable, and it must carry exactly the same state meaning as `BALANCED`.
    """

    BALANCED = "balanced"
    COMPACT = "compact"
    PLAIN = "plain"

    @classmethod
    def parse(cls, value: object, default: "PresentationMode" = None) -> "PresentationMode":
        """Resolve a configured or environment-supplied mode name.

        Unrecognised input falls back rather than raising: an unreadable
        statusline is a worse outcome than an ignored preference, and unlike
        the provider contract's `scope` there is no wrong-answer risk here —
        every mode conveys the same semantics.
        """
        if default is None:
            default = cls.BALANCED
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for member in cls:
                if member.value == value.strip().lower():
                    return member
        return default

    @property
    def uses_glyphs(self) -> bool:
        return self is not PresentationMode.PLAIN
