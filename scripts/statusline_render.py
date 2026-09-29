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


_ZWJ = "‍"
_VARIATION_SELECTORS = frozenset(chr(cp) for cp in range(0xFE00, 0xFE10))
_EMOJI_MODIFIERS = frozenset(chr(cp) for cp in range(0x1F3FB, 0x1F400))
_KEYCAP_ENCLOSER = "⃣"
_REGIONAL_INDICATORS = frozenset(chr(cp) for cp in range(0x1F1E6, 0x1F200))
_TAG_CHARACTERS = frozenset(chr(cp) for cp in range(0xE0020, 0xE0080))
_COMBINING_CATEGORIES = frozenset({"Mn", "Me", "Mc"})


def _continues_cluster(char: str, cluster: str) -> bool:
    """Whether `char` extends the cluster already accumulated in `cluster`."""
    previous = cluster[-1]
    if previous == _ZWJ:
        # A ZWJ always joins whatever follows it, whatever that is.
        return True
    if char == _ZWJ:
        return True
    if unicodedata.category(char) in _COMBINING_CATEGORIES:
        return True
    if char in _VARIATION_SELECTORS or char in _EMOJI_MODIFIERS:
        return True
    if char == _KEYCAP_ENCLOSER or char in _TAG_CHARACTERS:
        return True
    if char in _REGIONAL_INDICATORS:
        # Regional indicators pair up: two make one flag, a third starts a new
        # flag rather than extending the first.
        return previous in _REGIONAL_INDICATORS and len(
            [c for c in cluster if c in _REGIONAL_INDICATORS]
        ) % 2 == 1
    return False


def grapheme_clusters(text: str) -> list[str]:
    """Split `text` into units that must never be broken apart.

    A deliberate subset of UAX #29, not an implementation of it: this covers
    combining marks, variation-selector sequences, ZWJ sequences, skin-tone
    modifiers, keycaps, regional-indicator pairs and flag tag sequences, which
    is the whole of what actually reaches a statusline. Provider labels are
    ASCII by contract, so the only non-trivial clusters here are glyphs this
    module itself chose.

    Erring toward *over*-clustering is safe — it can only make truncation more
    cautious. Under-clustering is the bug, because it emits half an emoji.
    """
    clusters: list[str] = []
    for char in text:
        if clusters and _continues_cluster(char, clusters[-1]):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


_WIDE_EAST_ASIAN_WIDTHS = frozenset({"W", "F"})
_ZERO_WIDTH = _VARIATION_SELECTORS | _TAG_CHARACTERS | {_ZWJ}


def _codepoint_width(char: str) -> int:
    if char in _ZERO_WIDTH or unicodedata.category(char) in _COMBINING_CATEGORIES:
        return 0
    if unicodedata.east_asian_width(char) in _WIDE_EAST_ASIAN_WIDTHS:
        return 2
    return 1


def cluster_width(cluster: str) -> int:
    """Terminal columns one cluster may occupy, rounded *up*.

    Deliberately conservative, because the two errors are not symmetric: an
    over-estimate wastes a column, while an under-estimate wraps the line and
    corrupts the whole statusline including the user's own output.

    Two consequences worth knowing:

    - A ZWJ sequence is charged for each of its visible components. Terminals
      disagree about whether `<family emoji>` is 2 columns or 6; charging 6
      means we are never the reason the line wrapped.
    - A variation-selector-16 sequence is charged 2 even when its base
      codepoint is narrow, because U+FE0F *requests* emoji presentation and
      that is what makes a 1-column base render in 2 columns.
    """
    if not cluster:
        return 0
    total = sum(_codepoint_width(char) for char in cluster)
    if "️" in cluster:
        total = max(total, 2)
    return max(total, 1)


def display_width(text: str) -> int:
    """Terminal columns `text` may occupy, by the conservative rule above."""
    return sum(cluster_width(cluster) for cluster in grapheme_clusters(text))
