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

import dataclasses
import enum
import unicodedata

# Ordering is the contract's rule, not a presentation choice, so it is reused
# rather than reimplemented here — two independent orderings would eventually
# disagree and the line would stop being stable between renders.
import statusline_contract


class PresentationMode(enum.Enum):
    """How much room the host is willing to spend on being legible.

    Two independent axes, not one scale: **density** (how many columns a reading
    is allowed) and **icon style** (whether glyphs may be used at all). They are
    independent because the constraints behind them are — a narrow terminal and a
    terminal whose font renders emoji badly are different problems, and a reader
    can easily have both. Every combination is therefore a member, so a user who
    needs a tight line *and* plain text can ask for exactly that instead of being
    handed whichever half the enum happened to offer.

    A text mode is not a degraded mode. It is the correct mode for a terminal
    whose font or width handling makes emoji unreliable, and it carries exactly
    the same state meaning as its glyph counterpart.
    """

    BALANCED = "balanced"
    COMPACT = "compact"
    # `plain` rather than `balanced_plain`, which would be the symmetric name:
    # registries written before the axes were separated already carry this value,
    # and renaming it would silently fall back to a glyph mode for anyone who had
    # asked for text. `parse` accepts the symmetric spelling as an alias.
    PLAIN = "plain"
    COMPACT_PLAIN = "compact_plain"

    @classmethod
    def parse(cls, value: object, default: "PresentationMode" = None) -> "PresentationMode":
        """Resolve a configured or environment-supplied mode name.

        Unrecognised input falls back rather than raising: an unreadable
        statusline is a worse outcome than an ignored preference, and unlike
        the provider contract's `scope` there is no wrong-answer risk here —
        every mode conveys the same semantics.

        The fallback is exactly why the accepted spellings are generous. It
        resolves to a glyph mode, so a near-miss on a *text* preference answers a
        request for plain text with emoji — the one failure that is visibly
        broken rather than merely unwanted. A hyphen instead of an underscore
        must not cost a reader that.
        """
        if default is None:
            default = cls.BALANCED
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            name = value.strip().lower().replace("-", "_")
            for member in cls:
                if member.value == name:
                    return member
            if name in _MODE_ALIASES:
                return _MODE_ALIASES[name]
        return default

    @classmethod
    def for_axes(cls, *, compact: bool, glyphs: bool) -> "PresentationMode":
        """The one mode with exactly these two properties.

        Exists so that a surface offering density and icon style as separate
        choices resolves them by inverting the axis table rather than by naming
        members. Naming members is where the two can disagree with what was
        asked for -- and a user who asked for text and was handed a glyph mode
        gets exactly the broken output the text modes exist to avoid.

        Total by construction: every combination of the two booleans is a
        member, which is what the fourth member was added to guarantee.
        """
        return _MODE_BY_AXES[(compact, glyphs)]

    @property
    def is_compact(self) -> bool:
        """Whether to spend fewer columns: shorter phrasings, exceptions only."""
        return _MODE_AXES[self][0]

    @property
    def uses_glyphs(self) -> bool:
        return _MODE_AXES[self][1]


# `(is_compact, uses_glyphs)` per member. A table rather than identity
# comparisons scattered through the module, because density and icon style are
# two independent reader constraints -- a narrow terminal and an unreliable
# emoji font are different problems with different answers. Written as
# `mode is COMPACT`, a density decision silently also asserts "and glyphs are
# fine", so every such site would quietly take the wrong branch for any mode
# that combined the axes differently. Asking the mode which axis is being
# consulted makes that impossible rather than merely unlikely.
_MODE_AXES = {
    PresentationMode.BALANCED: (False, True),
    PresentationMode.COMPACT: (True, True),
    PresentationMode.PLAIN: (False, False),
    PresentationMode.COMPACT_PLAIN: (True, False),
}

# The same table read the other way, for `for_axes`. Derived rather than written
# out so the two directions cannot disagree; a test asserts it is still a
# bijection, which is the property that would break if a future member claimed
# an axis pair some existing member already has.
_MODE_BY_AXES = {axes: mode for mode, axes in _MODE_AXES.items()}

# Names that are not wire values but mean one. `balanced_plain` is what naming
# the two axes independently produces for the mode whose value is historically
# `plain`; both spellings must resolve, because a reader who writes the
# symmetric one is asking for text and the fallback would give them glyphs.
_MODE_ALIASES = {"balanced_plain": PresentationMode.PLAIN}


class InformationDepth(enum.Enum):
    """How much of a provider's snapshot one reading is allowed to show.

    A third axis beside `PresentationMode`'s density and icon style, and
    deliberately *not* a member of it. The two existing axes answer "how much
    room may this reading spend"; this one answers "how much is there to say".
    Those are independent questions \u2014 a reader on a wide terminal may still want
    one line of posture per product, and a reader on a narrow one may still be
    mid-diagnosis and want everything \u2014 so folding depth into `PresentationMode`
    would have produced eight members whose only job was to re-express two
    orthogonal choices, and every site consulting density would silently also
    have asserted a depth.

    `CLEAR` is not `DETAIL` minus some fields. It is a per-product executive
    summary, selected by `clear_readings` from the *same* snapshot, so the
    primary state a reader sees is identical either way and only the supporting
    context changes. There is one state engine; this axis chooses how much of
    its output is rendered, never what it concluded.
    """

    CLEAR = "clear"
    DETAIL = "detail"

    @classmethod
    def parse(cls, value: object, default: "InformationDepth" = None) -> "InformationDepth":
        """Resolve a configured depth name, falling back rather than raising.

        Same reasoning as `PresentationMode.parse`: an unreadable statusline is
        worse than an ignored preference. The asymmetry with that method is
        deliberate, though \u2014 the fallback here is `CLEAR`, the *narrower* mode.
        A near-miss on a depth preference must not answer with more information
        than was asked for, because the extra information is the half that could
        be over-disclosure, whereas a near-miss on icon style only risks looking
        wrong.
        """
        if default is None:
            default = DEFAULT_INFORMATION_DEPTH
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            name = value.strip().lower().replace("-", "_")
            for member in cls:
                if member.value == name:
                    return member
        return default

    @property
    def shows_supporting_detail(self) -> bool:
        """Whether supporting context beside the primary state may be rendered.

        Read as a property rather than compared against a member for the same
        reason the density and icon-style axes are: the question a call site is
        actually asking should be visible in the code, so that adding a future
        depth cannot silently take the wrong branch at a site that happened to
        be written as `depth is DETAIL`.
        """
        return self is InformationDepth.DETAIL


# What a fresh install renders, and what an unrecognised preference falls back
# to. `CLEAR` rather than `DETAIL` because the default has to be the mode that
# is right for someone who has not asked for anything: a normal user wants to
# know whether a product needs them, not to read diagnostics. Detail is the
# deliberate opt-in of someone already looking into something.
DEFAULT_INFORMATION_DEPTH = InformationDepth.CLEAR


_ZWJ = "\u200d"  # ZERO WIDTH JOINER
_VARIATION_SELECTORS = frozenset(chr(cp) for cp in range(0xFE00, 0xFE10))
_EMOJI_MODIFIERS = frozenset(chr(cp) for cp in range(0x1F3FB, 0x1F400))
_KEYCAP_ENCLOSER = "\u20e3"  # COMBINING ENCLOSING KEYCAP
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
_VS16 = "\ufe0f"  # VARIATION SELECTOR-16: "render the previous codepoint as emoji"
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
    if _VS16 in cluster:
        total = max(total, 2)
    return max(total, 1)


def display_width(text: str) -> int:
    """Terminal columns `text` may occupy, by the conservative rule above."""
    return sum(cluster_width(cluster) for cluster in grapheme_clusters(text))


ELLIPSIS = "..."


def truncate_to_width(text: str, budget: int, *, ellipsis: str = ELLIPSIS) -> str:
    """Shorten `text` to at most `budget` columns without splitting a cluster.

    The ellipsis is ASCII rather than U+2026 on purpose: it is the one piece of
    punctuation guaranteed to survive a terminal that cannot render the glyphs
    this function exists to protect.

    Returns `""` when the budget cannot even hold the ellipsis, because a bare
    `...` conveys nothing and a partial cluster conveys mojibake. The caller's
    degradation ladder is the right place to recover from that, not here.
    """
    if budget <= 0:
        return ""
    if display_width(text) <= budget:
        return text
    marker_width = display_width(ellipsis)
    if marker_width >= budget:
        return ""
    kept: list[str] = []
    used = 0
    for cluster in grapheme_clusters(text):
        width = cluster_width(cluster)
        if used + width > budget - marker_width:
            break
        kept.append(cluster)
        used += width
    if not kept:
        # Even one cluster did not fit. A lone "..." is the same nothing as an
        # empty string but costs three columns to say it.
        return ""
    return "".join(kept).rstrip() + ellipsis


def is_emoji_presentation_safe(glyph: str) -> bool:
    """Whether `glyph` will actually render as a 2-column emoji.

    This is the mechanical form of a real defect. Live Libra output used U+2696
    SCALES and U+1F6E1 SHIELD bare; both carry `Emoji_Presentation=No`, so a
    terminal is entitled to draw them as 1-column monochrome text — which is
    what happened, and the column accounting around them went wrong with it.
    U+1F9ED COMPASS in the same line was fine, which is exactly why the bug
    looked arbitrary.

    `unicodedata` does not expose `Emoji_Presentation`, so the rule is
    expressed in terms that ship with the stdlib and hold for every glyph this
    module can choose: a glyph is safe if it ends with U+FE0F (which *requests*
    emoji presentation explicitly) or if every codepoint is East-Asian wide or
    fullwidth (which is how the default-emoji-presentation codepoints are
    classified). The table below is asserted against this rule by the test
    suite, so the Libra defect cannot be reintroduced by adding a glyph.
    """
    if not glyph:
        return False
    if glyph.endswith(_VS16):
        return True
    return all(
        unicodedata.east_asian_width(char) in _WIDE_EAST_ASIAN_WIDTHS
        or char in _ZERO_WIDTH
        or unicodedata.category(char) in _COMBINING_CATEGORIES
        for char in glyph
    )


# Iconography is host-owned. A product never picks a glyph, so the same
# semantic role looks the same everywhere and a new product cannot introduce a
# second visual vocabulary for "warning".
STATE_GLYPHS = {
    "ok": "✅",  # WHITE HEAVY CHECK MARK
    "attention": "⚠" + _VS16,  # WARNING SIGN, forced to emoji presentation
    "warn": "\U0001f7e0",  # LARGE ORANGE CIRCLE
    "critical": "\U0001f6d1",  # OCTAGONAL SIGN
    "neutral": "⚪",  # MEDIUM WHITE CIRCLE
    "unknown": "❔",  # WHITE QUESTION MARK ORNAMENT
}

SCOPE_GLYPHS = {
    "host": "\U0001f4bb",  # PERSONAL COMPUTER
    "session": "\U0001f4ac",  # SPEECH BALLOON
    "project": "\U0001f4c1",  # FILE FOLDER
}

# Every glyph above has a text equivalent that carries the same meaning, so a
# glyph is never the only carrier of meaning. These are deliberately words
# rather than sigils: a reader who has never seen this statusline before can
# decode `WARN` and cannot decode `!`.
STATE_TEXT = {
    "ok": "OK",
    "attention": "ATTENTION",
    "warn": "WARN",
    "critical": "CRITICAL",
    "neutral": "NEUTRAL",
    "unknown": "UNKNOWN",
}

# `[host]` / `[session]` / `[project]` are fixed by the provider contract, not
# chosen here — see governance/product/statusline-provider-contract.md, "Scope
# is explicit". Changing them is a contract change.
SCOPE_TEXT = {
    "host": "[host]",
    "session": "[session]",
    "project": "[project]",
}

# Severity drives two things: what survives the degradation ladder when the line
# will not fit, and what refuses to be abbreviated. `neutral` ranks with `ok`
# rather than below it because "not installed" is not a problem — the provider
# contract routes `unsupported` and `unavailable` here for that reason.
STATE_SEVERITY = {
    "ok": 0,
    "neutral": 0,
    "unknown": 1,
    "attention": 2,
    "warn": 3,
    "critical": 4,
}

# States that keep their word even in a compact mode, because an exception must
# become *more* explicit under pressure, not less. `unknown` is here deliberately:
# a provider that could not read its own state is the case a reader is most
# likely to misread as fine.
EMPHATIC_STATES = frozenset({"unknown", "attention", "warn", "critical"})

UNKNOWN_STATE = "unknown"


def state_marker(state: str, mode: PresentationMode) -> str:
    """The leading marker for one segment's state.

    In glyph modes this is normally the glyph alone, because the segment's own
    label is rendered beside it and is the human-readable carrier of meaning —
    `<check> Verified` says everything `<check> OK Verified` says.

    The exception is a compact mode, which shortens and may drop labels: there an
    emphatic state carries its own word, so an exception never depends on a
    reader decoding a glyph. An unrecognised state degrades to `unknown` rather
    than rendering blank, matching the provider contract's degradation rule —
    blank would read as "nothing to report", the one meaning it must not have.
    """
    if state not in STATE_TEXT:
        state = UNKNOWN_STATE
    if not mode.uses_glyphs:
        return STATE_TEXT[state]
    glyph = STATE_GLYPHS[state]
    if mode.is_compact and state in EMPHATIC_STATES:
        return f"{glyph} {STATE_TEXT[state]}"
    return glyph


def format_age(age_seconds: int, mode: PresentationMode) -> str:
    """Render a freshness reading as a single coarse unit.

    Coarse on purpose: a statusline reader wants to know whether a reading is
    seconds or days old, and the extra precision costs columns that a provider
    label needs more. Rounds *down*, so "2m" never overstates freshness.

    The balanced-density modes say "ago" because a bare "2m" beside a count reads
    as a duration or a budget rather than an age.
    """
    age_seconds = max(0, int(age_seconds))
    for limit, unit, divisor in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600)):
        if age_seconds < limit:
            value = age_seconds // divisor
            break
    else:
        value, unit = age_seconds // 86400, "d"
    token = f"{value}{unit}"
    return token if mode.is_compact else f"{token} ago"


# Descending, and each entry's divisor is the next one's unit, so the pair
# picked below is always adjacent — `5d20m` would imply a precision the coarser
# unit already discarded.
_DURATION_UNITS = ((86400, "d"), (3600, "h"), (60, "m"), (1, "s"))


def format_duration(duration_seconds: int, duration_label: str) -> str:
    """Render a span as at most two adjacent coarse units, with its noun.

    Takes no `PresentationMode`, unlike its siblings here, and the omission is
    deliberate rather than an oversight: the token is already ASCII and already
    as short as it can be, and the noun is load-bearing in every mode, so there
    is nothing for a mode to change. A parameter that is accepted and ignored
    would invite a caller to believe otherwise.

    Two units rather than `format_age`'s one, because the two fields answer
    different questions. An age is read as a threshold — "is this seconds or days
    old" — so one unit is enough and the truncation error does not matter. A span
    is read as a quantity someone plans against, and `5d` for anything from five
    to six days is a 20% error in the optimistic direction; `5d4h` bounds it to
    the smaller unit.

    Truncates rather than rounds, so the second unit is never carried into the
    first — `1d0h` would read as a suspiciously exact day and `2d` would be a
    whole unit of overstatement.

    The noun comes first, unlike `format_count`'s. `2 of 14 recent decisions`
    reads as a sentence with the number as its subject, but a span's noun is a
    qualifier on the number rather than what is being counted, so `P90 5d4h`
    reads correctly where `5d4h P90` reads as a typo. Never dropped, in any
    mode, for the same reason `count_label` is not.
    """
    seconds = max(0, int(duration_seconds))
    for index, (divisor, unit) in enumerate(_DURATION_UNITS):
        if seconds < divisor:
            continue
        whole, remainder = divmod(seconds, divisor)
        token = f"{whole}{unit}"
        if remainder and index + 1 < len(_DURATION_UNITS):
            next_divisor, next_unit = _DURATION_UNITS[index + 1]
            if remainder >= next_divisor:
                token = f"{token}{remainder // next_divisor}{next_unit}"
        break
    else:
        # Genuinely zero, not "too small to show": a provider that means "no
        # estimate" omits the field rather than sending 0.
        token = "0s"
    return f"{duration_label} {token}"


def format_count(count: int, total: int | None, count_label: str, mode: PresentationMode) -> str:
    """Render a count with the noun it counts.

    `count_label` is never dropped, in any mode. A bare `2/14` is exactly the
    opaque token this whole design replaces, and the noun is what makes the
    number mean something — the provider contract refuses a `count` without one
    for the same reason.
    """
    if total is None:
        quantity = str(count)
    elif mode.is_compact:
        quantity = f"{count}/{total}"
    else:
        quantity = f"{count} of {total}"
    return f"{quantity} {count_label}"


# `confidence_of` exists because a bare `high` reads as risk, severity or
# priority. These phrasings say what the confidence is *about*, which is the
# specific readability defect behind Libra's `pf:high`.
CONFIDENCE_SUBJECT_TEXT = {
    "preflight_estimate": "preflight confidence",
    "verification": "verification confidence",
    "policy_decision": "decision confidence",
    "unspecified": "confidence",
}

# A compact mode still names the subject, just shorter. It never degrades to the
# bare value, because "high" alone is the ambiguity this field was added to remove.
CONFIDENCE_SUBJECT_TEXT_COMPACT = {
    "preflight_estimate": "preflight",
    # Not "verified": `verified low` reads as a verdict about the subject rather
    # than as a confidence in one.
    "verification": "verification",
    "policy_decision": "decision",
    "unspecified": "conf",
}


def format_confidence(confidence: str, confidence_of: str, mode: PresentationMode) -> str:
    """Render a confidence together with what it is a confidence *in*.

    An unrecognised subject falls back to `unspecified` rather than being
    omitted: dropping the subject would leave the bare value this function
    exists to qualify.
    """
    table = CONFIDENCE_SUBJECT_TEXT_COMPACT if mode.is_compact else CONFIDENCE_SUBJECT_TEXT
    subject = table.get(confidence_of) or table["unspecified"]
    return f"{subject} {confidence}"


def format_reason(reason_code: str | None, reason_label: str | None) -> str:
    """Render *why* a segment is in its state, preferring the provider's prose.

    A `reason_code` is a machine token (`daemon_not_running`), so underscores
    become spaces — the token is for matching, not for reading, and the host is
    the right place to make it readable.

    Returns `""` when the provider supplied neither. That is deliberate: the
    host does not invent a reason, and it does not infer one from a count. A
    provider that genuinely does not know says so with an explicit reason code,
    because "unknown reason" and "no reason clause" are different claims and
    only the provider can tell them apart.
    """
    if reason_label:
        return reason_label
    if reason_code:
        return reason_code.replace("_", " ").replace("-", " ")
    return ""


# Uppercase, unabbreviated, and never dropped by any degradation step. Circinus
# shadow mode reports what a policy *would* have done; if that ever reads as an
# executed block the user believes their agent was stopped when it was not, so
# this marker is treated as load-bearing rather than decorative.
HYPOTHETICAL_TEXT = "NOT ENFORCED"

# Three shared lines' worth of segments need visible structure, so separators
# form a hierarchy: details are parenthesised and comma-joined inside a segment,
# segments and provider groups are dot-joined, and the user's own statusline is
# divided from the Horonom block by the strongest divider of all.
#
# Every separator character here is deliberately *absent* from the provider
# contract's label allowlist (`. , ' - — ( ) % + ? ! ≤ ≥`), so a separator can
# never be confused with a character a provider put there. Square brackets are
# reserved for host-owned semantic tokens, which is why `[host]` and
# `[NOT ENFORCED]` share a shape.
# A semicolon rather than the more natural comma: comma *is* allowlisted, and a
# `reason_label` is prose that may well contain one, which would draw a detail
# boundary the provider never intended.
DETAIL_SEPARATOR = "; "

# A text mode drops to ASCII for the same reason it drops glyphs: it exists for
# terminals whose character handling cannot be trusted, and U+00B7 is one more
# thing to get wrong for no gain.
#
# Keyed off the icon-style axis and derived for every member, rather than
# enumerated per mode. The separator has never been a density decision -- it is
# the same width either way -- so writing it per member would invite a future
# mode to be given a glyph separator it cannot render, or no separator at all.
_GLYPH_SEGMENT_SEPARATOR = " · "  # MIDDLE DOT
_TEXT_SEGMENT_SEPARATOR = " | "
_GLYPH_UPSTREAM_SEPARATOR = " ┃ "  # BOX DRAWINGS HEAVY VERTICAL
_TEXT_UPSTREAM_SEPARATOR = " || "

SEGMENT_SEPARATORS = {
    mode: _GLYPH_SEGMENT_SEPARATOR if mode.uses_glyphs else _TEXT_SEGMENT_SEPARATOR
    for mode in PresentationMode
}

UPSTREAM_SEPARATORS = {
    mode: _GLYPH_UPSTREAM_SEPARATOR if mode.uses_glyphs else _TEXT_UPSTREAM_SEPARATOR
    for mode in PresentationMode
}


def _enum_value(value: object) -> str | None:
    """Accept either an enum member from the contract or its raw wire string."""
    if value is None:
        return None
    return getattr(value, "value", value)


def render_segment(
    segment: object,
    mode: PresentationMode,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> str:
    """Render one provider segment as a single readable phrase.

    Takes a `statusline_contract.Segment` (or anything with the same
    attributes), so the renderer stays usable against a hand-built stub in
    tests. Every value it reads has already passed the contract's privacy
    allowlist; this function adds no field of its own, so it cannot widen that
    surface — and `depth` can only ever *remove* fields here, so Clear cannot be
    a wider surface than Detail either.

    `depth` defaults to `DETAIL` rather than to the product default of `CLEAR`
    because this function's job is to render the segment it was handed; choosing
    how much to show is the caller's, and a silent default of `CLEAR` would drop
    fields from every existing direct caller. The state marker, the label and the
    hypothetical marker are never depth-gated: they are what makes the reading a
    reading at all.

    The hypothetical marker is placed outside the parenthesised details, welded
    to the label, so no degradation step and no careless reading can separate
    "would block" from "not enforced".
    """
    state = _enum_value(segment.state)
    head = f"{state_marker(state, mode)} {segment.label}".strip()
    if getattr(segment, "hypothetical", False):
        head = f"{head} [{HYPOTHETICAL_TEXT}]"

    supporting = depth.shows_supporting_detail
    details: list[str] = []
    # Counters are supporting material even when they look like a headline. A
    # Circinus decision tally is the clearest case: `1520/3747` tells an operator
    # nothing they can act on, and spends the product's whole Clear line doing it.
    if supporting and segment.count is not None and segment.count_label:
        details.append(format_count(segment.count, segment.total, segment.count_label, mode))
    # Beside the count rather than beside the age: both are quantities the
    # segment is reporting, whereas the age qualifies the whole reading and so
    # stays last.
    #
    # `getattr` rather than attribute access, unlike the fields above it, because
    # this function's contract is "anything with the same attributes" and these
    # two arrived after that promise was made — a segment object from an older
    # copy of the contract must still render, minus the field it cannot supply.
    #
    # The one quantity Clear keeps. A duration carries its own noun -- "P90 6d7h"
    # -- and for a scheduling product the span *is* the reading; dropping it would
    # leave `Remaining work` saying nothing at all. Contrast the count above,
    # which is a tally of things the operator did not ask about.
    duration = getattr(segment, "duration_seconds", None)
    duration_label = getattr(segment, "duration_label", None)
    if duration is not None and duration_label:
        details.append(format_duration(duration, duration_label))
    # Confidence qualifies an estimate for someone deciding whether to trust it,
    # which is a Detail question. In Clear it also reads dangerously like severity
    # -- a bare "high" next to a state marker invites "high risk".
    confidence = _enum_value(segment.confidence)
    if supporting and confidence is not None:
        details.append(format_confidence(confidence, _enum_value(segment.confidence_of), mode))

    reason = format_reason(segment.reason_code, segment.reason_label)
    # A compact mode spends its remaining columns on exceptions only: a reason
    # for an `ok` segment is the least useful thing on the line, and a reason for
    # a `critical` one is the most.
    if supporting and reason and (not mode.is_compact or state in EMPHATIC_STATES):
        details.append(reason)
    # Clear is freshness-*aware* rather than freshness-*annotated*: staleness
    # decides in `clear_readings` whether a secondary reading survives at all, and
    # the reading that does survive says its state plainly instead of asking the
    # reader to date it. The age is one of the first things Detail adds back.
    if supporting and segment.age_seconds is not None:
        details.append(format_age(segment.age_seconds, mode))

    if not details:
        return head
    return f"{head} ({DETAIL_SEPARATOR.join(details)})"


# --------------------------------------------------------- the clear projection
#
# Which of a provider's facts earn its one Clear-mode phrase. This is the only
# place that decision is made, for every product, from declared semantics plus
# one documented fallback -- so Clear is a shared ladder rather than a branch per
# product, and a product the host has never heard of is summarised by the same
# rules as the three it ships with.

# The states that mean the operator is implicated, not merely informed. Taken
# from `STATE_MEANINGS` rather than from severity order, because severity and
# "does this need me" are different questions: `warn` is "something is wrong;
# work is not stopped", which is a posture worth showing beside the primary
# reading, whereas `critical` stops work and `attention` waits on a decision.
#
# Reading `warn` as action-required is the specific defect this set exists to
# prevent: Libra's live escalation segment is a warn-state replan-budget posture
# while Libra's own explain surface says nothing is blocked and nothing is
# waiting on an answer. Promoting it would make the line claim otherwise.
ACTION_REQUIRED_STATES = frozenset({"attention", "critical"})

# How many readings one provider may contribute to Clear: the primary state, and
# at most one secondary signal that changes how the primary reads. Not a layout
# budget -- a narrow terminal is handled by the width ladder -- but an editorial
# one. A third reading is a diagnosis, and the escalation path for a diagnosis is
# Detail, then explain.
MAX_CLEAR_READINGS = 2


def _has_quantified_reading(segment: object) -> bool:
    """Whether this segment carries a measurement rather than only a name.

    The one product-agnostic signal that distinguishes a fact worth a reader's
    one glance from a fact that merely identifies something. Libra's `task` and
    `estimate` segments are both `neutral` and adjacent in order, so severity and
    position cannot separate them — but one carries a remaining-work span and the
    other carries an id, and a statusline that leads with the id has spent the
    product's whole line on the least useful thing it knows.
    """
    if segment.count is not None and segment.count_label:
        return True
    duration = getattr(segment, "duration_seconds", None)
    return duration is not None and getattr(segment, "duration_label", None) is not None


def _has_live_readings(status: object) -> bool:
    """Whether this provider's segment values may be read as current.

    Accepts an enum or the raw wire string, like every other reader here, so a
    hand-built stub in a test still resolves. Anything unrecognised is treated as
    *not* live: the cost of being wrong in that direction is a reading suppressed
    from Clear, and the cost of being wrong in the other is a cached posture
    rendered as though the daemon behind it were answering.
    """
    availability = getattr(status, "availability", None)
    live = getattr(availability, "has_live_readings", None)
    if isinstance(live, bool):
        return live
    return _enum_value(availability) == "available"


def _worst_index(segments: tuple, indices: list) -> int:
    """The index of the highest-severity segment, earliest in contract order on a tie."""
    return max(
        indices,
        key=lambda i: (
            STATE_SEVERITY.get(_enum_value(segments[i].state), STATE_SEVERITY[UNKNOWN_STATE]),
            -i,
        ),
    )


def _unavailability_index(segments: tuple) -> int:
    """The one reading an unavailable provider is allowed to show.

    Deliberately *not* the highest-severity one. That was this function's first
    form and it was wrong in the exact way the rule exists to prevent: a wedged
    Circinus reporting `unknown "Not responding"` beside a cached
    `warn "Would have blocked"` renders the would-have-blocked, because `warn`
    outranks `unknown` on severity. The daemon that would do the blocking is not
    answering, and the line just implied it evaluated something.

    So, in order:

    1. The first segment in contract order that is not `hypothetical`. Contract
       order rather than severity, because the provider's own ordering is what
       this module defers to everywhere else, and because a provider whose config
       is invalid should lead with *that* rather than with a vaguer "could not
       determine". Excluding the hypothetical is the actual fix: a would-have is
       the one reading that cannot be a current statement about a product that is
       not running.
    2. Failing that, the first segment whose state is `unknown` — the contract's
       word for "the provider could not determine its own state", and so the
       reading that is certainly about now.
    3. Failing that, the first segment, so this always returns something.

    Rule 1 makes `Segment.hypothetical` load-bearing here rather than decorative:
    a provider that reports a shadow outcome without marking it has already
    broken the contract, and there is no second signal the host could use to
    recognise it.
    """
    for index, segment in enumerate(segments):
        if not getattr(segment, "hypothetical", False):
            return index
    for index, segment in enumerate(segments):
        if _enum_value(segment.state) == UNKNOWN_STATE:
            return index
    return 0


def clear_roles(status: object) -> tuple:
    """The Clear-mode part each of this provider's segments plays, in contract order.

    A provider's own `clear_role` declaration always wins; this fills in the rest.
    The fallback is deliberately documented behaviour rather than a guess, in this
    order:

    1. A state that stops work or waits on the operator is an exception wherever
       it sits. Position cannot demote it, because the whole point of the top rung
       is that it is not competing with routine readings.
    2. Exactly one posture, if no segment claimed the part: the first remaining
       segment that carries a measurement, else simply the first remaining one.
       Preferring a measurement is what keeps a task id from taking the line off a
       delivery estimate of equal severity.
    3. Everything left is a vital signal if it carries a measurement or an
       emphatic state, and supporting context otherwise.

    Rule 2's "if no segment claimed the part" matters for stability: a declared
    posture suppresses inference entirely, so a provider that adopts the field
    gets exactly what it asked for and nothing extra.
    """
    segments = statusline_contract.order_segments(status)
    roles: list = [getattr(segment, "clear_role", None) for segment in segments]

    for index, segment in enumerate(segments):
        if roles[index] is None and _enum_value(segment.state) in ACTION_REQUIRED_STATES:
            roles[index] = statusline_contract.ClearRole.EXCEPTION

    if statusline_contract.ClearRole.POSTURE not in roles:
        free = [index for index, role in enumerate(roles) if role is None]
        quantified = [index for index in free if _has_quantified_reading(segments[index])]
        for index in (quantified or free)[:1]:
            roles[index] = statusline_contract.ClearRole.POSTURE

    for index, segment in enumerate(segments):
        if roles[index] is None:
            roles[index] = (
                statusline_contract.ClearRole.VITAL
                if _has_quantified_reading(segment)
                or _enum_value(segment.state) in EMPHATIC_STATES
                else statusline_contract.ClearRole.SUPPORTING
            )
    return tuple(roles)


def clear_readings(status: object) -> tuple:
    """The segments Clear mode shows for one provider, in contract order.

    Clear is an executive summary, not Detail with fields removed — which is why
    this selects *segments* and `render_segment` separately restricts *fields*.
    The primary state it picks is the same state Detail leads with, because both
    read the same snapshot through the same role ladder; only the supporting
    material differs.

    Order of precedence, which is the shared priority rule for every product:

    1. A provider that could not read its own state shows exactly one reading.
       Not "the unavailability plus what we last knew" — a cached mode or outcome
       beside an unreachable daemon is read as current, which is how a line ends
       up implying enforcement that is not running.
    2. Otherwise, one exception if there is one, and nothing else. Something that
       stops work or waits on the operator is not improved by having a routine
       estimate next to it.
    3. Otherwise the posture, plus at most one still-fresh vital signal.

    Returned in contract order rather than in role order, so the rendered text is
    stable and so applying this twice is a no-op — `compose` projects for its
    width accounting and `render_provider` projects for its output, and the two
    must not be able to disagree.
    """
    segments = statusline_contract.order_segments(status)
    if not segments:
        return ()
    everything = list(range(len(segments)))
    if not _has_live_readings(status):
        kept = {_unavailability_index(segments)}
    else:
        roles = clear_roles(status)
        exceptions = [
            index
            for index in everything
            if roles[index] is statusline_contract.ClearRole.EXCEPTION
        ]
        if exceptions:
            kept = {_worst_index(segments, exceptions)}
        else:
            kept = {
                index
                for index in everything
                if roles[index] is statusline_contract.ClearRole.POSTURE
            }
            fresh_vitals = [
                index
                for index in everything
                if roles[index] is statusline_contract.ClearRole.VITAL
                and not getattr(segments[index], "is_stale", False)
            ]
            if fresh_vitals:
                kept.add(_worst_index(segments, fresh_vitals))
            if not kept:
                # Every segment was declared supporting. Showing nothing would
                # read as "this product reported nothing", so the provider's own
                # first reading stands in rather than its absence.
                kept = {0}
    return tuple(segments[index] for index in sorted(kept))


def project_to_depth(statuses: object, depth: InformationDepth) -> tuple:
    """Reduce every provider to the readings the requested depth shows.

    Detail is the identity: it shows the snapshot as the provider ordered it.
    Clear replaces each provider's segments with its Clear readings, which is
    what lets the width-pressure ladder below account for, drop and count the
    same readings the user is actually looking at. Without this, a Clear line
    under pressure would report `[+3 more]` for segments Clear was never going to
    show, which says there is news when there is none.

    A provider left with no readings is dropped entirely rather than rendered as
    an attributed nothing, matching `_without_dropped` — an empty group reads as
    "checked, all clear".
    """
    ordered = statusline_contract.order_providers(statuses)
    if depth.shows_supporting_detail:
        return ordered
    projected = []
    for status in ordered:
        readings = clear_readings(status)
        if readings:
            projected.append(dataclasses.replace(status, segments=readings))
    return tuple(projected)


def provider_display_name(provider_id: str) -> str:
    """Turn a contract provider id into something a human reads as a product.

    Derived rather than looked up, so a provider the host has never heard of is
    attributed correctly instead of appearing anonymously beside the ones it
    knows. The contract restricts provider ids to `[a-z][a-z0-9_-]*`, so this is
    total: `fornax` becomes `Fornax`, `libra-governor` becomes
    `Libra Governor`.
    """
    words = provider_id.replace("-", " ").replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words) or provider_id


def scope_marker(scope: str, mode: PresentationMode) -> str:
    """The marker identifying what a provider's state is scoped to.

    Always rendered, for every provider and every scope. Two columns is a cheap
    price for removing the ambiguity, and the failure it prevents is concrete:
    host-wide enforcement state read as if it applied only to this session is a
    user believing their other sessions are unguarded, or guarded, incorrectly.

    An unrecognised scope renders as its bracketed literal rather than being
    dropped. The contract refuses unknown scopes on parse, so this only fires
    for a hand-built value, and inventing a glyph for it would be a guess.
    """
    if mode.uses_glyphs and scope in SCOPE_GLYPHS:
        return SCOPE_GLYPHS[scope]
    return SCOPE_TEXT.get(scope, f"[{scope}]")


NO_SEGMENTS_LABEL = "No status reported"


def provider_parts(
    status: object,
    mode: PresentationMode,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> tuple[str, str, tuple[str, ...]]:
    """One provider split into who is speaking and what they said.

    Returns `(name, scope marker, readings)`. The two layouts this module
    supports — every provider on one shared line, and every provider on a
    physical line of its own — are the same rendering assembled differently, so
    they both build a provider from here rather than each walking the contract.
    A second assembler is how the vertical layout would eventually lose a scope
    marker that the horizontal one kept, or apply the Clear projection once where
    the other applies it twice.

    `readings` is never empty. A provider that reported no segments gets one
    reading saying so, because silence on a status line reads as all-clear; see
    `render_provider` for why the host states that rather than trusting the
    provider to.
    """
    segments = statusline_contract.order_segments(status)
    if segments and not depth.shows_supporting_detail:
        segments = clear_readings(status)
    if segments:
        readings = tuple(render_segment(segment, mode, depth) for segment in segments)
    elif getattr(status, "fallback_text", None):
        readings = (f"{state_marker(UNKNOWN_STATE, mode)} {status.fallback_text}",)
    else:
        readings = (f"{state_marker(UNKNOWN_STATE, mode)} {NO_SEGMENTS_LABEL}",)
    return (
        provider_display_name(status.provider),
        scope_marker(_enum_value(status.scope), mode),
        readings,
    )


def render_provider(
    status: object,
    mode: PresentationMode,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> str:
    """Render one provider's whole group: attribution, scope, then segments.

    Takes a `statusline_contract.ProviderStatus`. Segments are ordered by the
    contract's own `order_segments`, so ordering is the contract's concern and
    identical for every caller.

    At `CLEAR` this selects the provider's Clear readings itself, so a direct
    caller gets a correct summary without having to remember a second call. That
    makes it a second application of `clear_readings` when `compose` has already
    projected — which is safe because `clear_readings` is idempotent, and is worth
    the redundancy: the alternative is a public function that silently renders
    everything at `CLEAR` unless you knew to project first.

    The provider's name is always rendered, at every depth. Clear is a shorter
    reading, not an anonymous one — `🛡 Verified` is a sentence with the subject
    removed, and on a line with three products it is a guess.

    A provider with no segments does not render as nothing. The contract asks
    providers to always emit at least one segment precisely because silence
    reads as all-clear, but that is a rule providers can get wrong, so the host
    states the absence explicitly rather than trusting them. `fallback_text` is
    used for that only when present — it is a convenience rendering, never the
    primary one.
    """
    name, scope, readings = provider_parts(status, mode, depth)
    return f"{name} {scope} {SEGMENT_SEPARATORS[mode].join(readings)}"


def provider_severity(status: object) -> int:
    """The worst severity any of this provider's segments reports.

    Used to decide what survives when the line will not fit. A provider with no
    segments ranks as `unknown` rather than `ok`, because "reported nothing" is
    a thing the user needs to see, not the first thing to hide.
    """
    segments = getattr(status, "segments", ()) or ()
    if not segments:
        return STATE_SEVERITY[UNKNOWN_STATE]
    return max(
        STATE_SEVERITY.get(_enum_value(s.state), STATE_SEVERITY[UNKNOWN_STATE])
        for s in segments
    )


# Preference order when a budget is set: keep glyphs as long as they fit, and
# tighten density before abandoning them. Deliberately *measured* rather than
# assumed to shrink: a compact mode can be wider than a balanced one, because an
# emphatic state gains its word there, and that is the intended trade — an
# exception becomes more explicit under pressure, not less. So the ladder picks
# the first candidate that actually fits instead of trusting the order.
MODE_PREFERENCE = (
    PresentationMode.BALANCED,
    PresentationMode.COMPACT,
    PresentationMode.PLAIN,
    PresentationMode.COMPACT_PLAIN,
)

# What the ladder may try, per requested mode. Derived from the two axes rather
# than hand-listed per mode, so the rule below is stated once and cannot drift
# from the lists that implement it.
MODE_LADDER = {
    requested: tuple(
        candidate
        for candidate in MODE_PREFERENCE
        if (candidate.is_compact or not requested.is_compact)
        and (requested.uses_glyphs or not candidate.uses_glyphs)
    )
    for requested in PresentationMode
}


def _mode_candidates(mode: PresentationMode) -> tuple[PresentationMode, ...]:
    """The modes the ladder may try, given what the user asked for.

    Strictly downward on both axes: the ladder never hands back something the
    user declined. A text request is a statement about what the terminal can
    render, not about width, so no width pressure may reintroduce a glyph — that
    would produce exactly the broken output the text modes exist to avoid. A
    compact request is not re-expanded either, since the user asked for
    compactness and running out of room is not a reason to give them more prose.

    The second rule is why the two axes have to be independent: before
    `COMPACT_PLAIN` existed, a compact request under width pressure could only
    shed its glyphs by falling to `PLAIN`, which quietly gave the prose back.
    """
    return MODE_LADDER[mode]


def _hidden_marker(count: int) -> str:
    """How the line admits that it is not showing everything.

    Counts *segments*, uniformly, at every rung of the degradation ladder: a
    budget that silently swallows state is indistinguishable from a provider
    that reported nothing, and those mean opposite things. The marker is
    load-bearing enough that a rung which cannot fit it drops the Horonom block
    entirely rather than capping the line quietly.
    """
    return f"[+{count} more]"


def _segment_count(statuses: tuple) -> int:
    return sum(len(getattr(s, "segments", ()) or ()) for s in statuses)


def _render_groups(
    statuses: "tuple",
    mode: PresentationMode,
    hidden: int = 0,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> str:
    text = SEGMENT_SEPARATORS[mode].join(render_provider(s, mode, depth) for s in statuses)
    if hidden:
        marker = _hidden_marker(hidden)
        text = f"{text}{SEGMENT_SEPARATORS[mode]}{marker}" if text else marker
    return text


# ------------------------------------------------------------ the vertical layout
#
# At `DETAIL` every provider gets a physical line of its own (HORO-1628). Founder
# DogFooding of the horizontal layout found the failure this fixes: three products
# with reasons, ages and counters concatenated into one line is correct and
# unreadable, and the state a reader most needs — a product that has just gone
# degraded and has the most to say — is the one that pushes the line widest.
#
# Vertical space is the resource `DETAIL` has and `CLEAR` does not, so this is
# where it gets spent. `CLEAR` stays on one line: it is the glanceable depth, and
# a summary that costs three rows is no longer a summary.


# A continuation line is attached to its provider by indentation alone. That is
# the one attachment that survives a text-only terminal, a reader with colour
# off, and a copy-paste into a plain-text bug report — all three of which are
# states this line is read in. Two columns is enough to be visible without a
# narrow terminal paying much for it.
READING_INDENT = "  "

# Named because two different things divide a row from the next one here -- this,
# and the upstream separators above -- and a bare "\n" in the middle of an f-string
# is the one of the two that is easy to add without meaning to.
ROW_SEPARATOR = "\n"


def _wrap_provider(
    head: str,
    readings: tuple[str, ...],
    mode: PresentationMode,
    budget: int | None,
) -> list[str] | None:
    """Lay one provider out as its own line, wrapping only if it must.

    Readings are packed onto the head's line while they fit and onto indented
    continuation lines after that. A reading is never split: the segment renderer
    has already welded host-owned tokens to the prose they qualify — `[NOT
    ENFORCED]` to its label above all — and a wrap between them would leave a
    hypothetical reading as a line that says a block happened.

    Returns `None` when a single reading will not fit even on a line of its own,
    which is this function saying the terminal is too narrow for a vertical layout
    to be readable. It does not truncate and it does not drop: in a layout with
    rows to spare, both would be losing information to save space that exists.
    """
    if budget is None:
        return [f"{head} {SEGMENT_SEPARATORS[mode].join(readings)}"]
    if display_width(head) > budget:
        return None

    lines: list[str] = []
    current, carries_reading = head, False
    for reading in readings:
        separator = SEGMENT_SEPARATORS[mode] if carries_reading else " "
        joined = f"{current}{separator}{reading}"
        if display_width(joined) <= budget:
            current, carries_reading = joined, True
            continue
        lines.append(current)
        current = f"{READING_INDENT}{reading}"
        if display_width(current) > budget:
            return None
        carries_reading = True
    lines.append(current)
    return lines


def _detail_block(statuses: tuple, mode: PresentationMode, budget: int | None) -> str | None:
    """Every provider on a line of its own, in the contract's order.

    Product names are padded into a column so the state markers line up, because
    the eye finds "which product is this" by the left edge and the founder's
    reading of a three-product block is a vertical scan. The column is paid for
    out of slack only: the unpadded layout is measured too, and alignment is kept
    just when it costs no extra row. So a narrow terminal spends its columns on
    state rather than on a tidy left edge, and neither the layout's existence nor
    its correctness depends on the padding surviving.

    Padding is measured in display columns rather than characters, and nothing is
    aligned after the scope marker, so no claim here depends on how many cells an
    emoji occupies — a wide glyph moves the text after it and cannot silently
    shift a product's identity out from under its own line.

    Returns `None` when even one provider cannot be laid out within `budget`.
    """
    parts = [provider_parts(status, mode, InformationDepth.DETAIL) for status in statuses]
    widest = max(display_width(name) for name, _, _ in parts)
    laid_out = []
    for columns in dict.fromkeys((widest, 0)):
        lines: list[str] = []
        for name, scope, readings in parts:
            head = f"{name}{' ' * (columns - display_width(name))} {scope}"
            wrapped = _wrap_provider(head, readings, mode, budget)
            if wrapped is None:
                break
            lines.extend(wrapped)
        else:
            laid_out.append(lines)
    if not laid_out:
        return None
    # `min` is stable, so the padded layout wins a tie, which is the common case:
    # a terminal with room to align is a terminal that aligns.
    return ROW_SEPARATOR.join(min(laid_out, key=len))


def _without_dropped(statuses: tuple, dropped: set) -> tuple:
    """Rebuild the provider tuple minus the `(provider, segment)` pairs in `dropped`.

    A provider that loses every segment disappears entirely rather than
    rendering as an attributed nothing, which would read as "checked, all
    clear".
    """
    rebuilt = []
    for p_index, status in enumerate(statuses):
        kept = tuple(
            segment
            for s_index, segment in enumerate(getattr(status, "segments", ()) or ())
            if (p_index, s_index) not in dropped
        )
        if kept:
            rebuilt.append(dataclasses.replace(status, segments=kept))
    return tuple(rebuilt)


def _fit_by_dropping(
    statuses: tuple,
    mode: PresentationMode,
    budget: int,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> str | None:
    """Shed the least important segments until the block fits.

    Works one segment at a time rather than one provider at a time, so a
    provider with a `critical` segment and a chatty `ok` one keeps the part that
    matters and keeps its attribution. Dropping whole providers first would spend
    the remaining columns on nothing.

    Order: lowest segment severity first, and among equals the one furthest
    right — which is the one the contract's own ordering already judged least
    important. Never drops below one segment, because a block consisting only of
    `[+3 more]` says there is news without saying any of it.

    Returns `None` when even a single segment will not fit, so the caller can
    move to the next rung. It deliberately does *not* truncate: a rendered
    segment is shown whole or not at all. Cutting one mid-way produces fragments
    like `Shadow mode [NOT` — a mangled form of the marker whose entire job is
    to stop a hypothetical from reading as an enforced block.
    """
    pairs = [
        (p_index, s_index, segment)
        for p_index, status in enumerate(statuses)
        for s_index, segment in enumerate(getattr(status, "segments", ()) or ())
    ]
    total = len(pairs)
    order = sorted(
        pairs,
        key=lambda item: (
            STATE_SEVERITY.get(_enum_value(item[2].state), STATE_SEVERITY[UNKNOWN_STATE]),
            -item[0],
            -item[1],
        ),
    )
    dropped: set = set()
    for p_index, s_index, _ in order:
        if total - len(dropped) <= 1:
            break
        dropped.add((p_index, s_index))
        remaining = _without_dropped(statuses, dropped)
        text = _render_groups(remaining, mode, hidden=len(dropped), depth=depth)
        if display_width(text) <= budget:
            return text
    return None


# Below this many columns of prose a label stops being a label and becomes a
# riddle, so the ladder gives up and hands the whole line back to the user
# instead.
MIN_LABEL_COLUMNS = 6


def _fit_minimal(statuses: tuple, mode: PresentationMode, budget: int) -> str:
    """The narrowest honest rung: the single worst state, and what it hides.

    Reduces to one marker plus one truncated label plus the hidden-segment
    marker. Truncation is applied *only* to the provider's own prose, never to a
    host-owned token, which is why `[NOT ENFORCED]` and `[+3 more]` cannot be
    mangled here: both are charged to the budget in full before the label gets
    what is left, and a rung that cannot afford them renders nothing.

    `[NOT ENFORCED]` is charged even though it costs this rung most of its
    columns, because the alternative is what this function used to do — render
    `would have blocked` with the marker dropped, which is not a shortened
    reading of shadow mode but a different and false one.

    Returns `""` when the result would be unreadable or would have to hide
    things without saying so. The caller then emits the upstream line alone.
    """
    candidates = [
        (provider_severity(status), -index, status, segment)
        for index, status in enumerate(statuses)
        for segment in (getattr(status, "segments", ()) or ())
        if STATE_SEVERITY.get(_enum_value(segment.state)) is not None
    ]
    if not candidates:
        return ""
    _, _, _, worst = max(
        candidates,
        key=lambda item: (STATE_SEVERITY[_enum_value(item[3].state)], item[0], item[1]),
    )

    hidden = _segment_count(statuses) - 1
    suffix = f"{SEGMENT_SEPARATORS[mode]}{_hidden_marker(hidden)}" if hidden else ""
    if getattr(worst, "hypothetical", False):
        # Welded ahead of the hidden-segment marker so it sits immediately after
        # the label, exactly where `render_segment` puts it. Nothing may come
        # between a would-have and its disclaimer.
        suffix = f" [{HYPOTHETICAL_TEXT}]{suffix}"
    head = f"{state_marker(_enum_value(worst.state), mode)} "
    label_budget = budget - display_width(suffix) - display_width(head)
    if label_budget < MIN_LABEL_COLUMNS:
        return ""
    label = truncate_to_width(worst.label, label_budget)
    if not label:
        return ""
    return f"{head}{label}{suffix}"


def _appended(upstream: str, block: str) -> str:
    """Put the Horonom block on the row after the user's own output.

    The upstream text is passed through byte for byte, however many lines it has
    and whether or not it ends in one: a command that printed two rows meant to
    print two rows, and collapsing them for the sake of our alignment would be
    exactly the ownership this host does not take. An existing trailing newline is
    used as the separator rather than added to, so a user whose command ends in one
    does not get a blank row between their line and ours.
    """
    if not upstream:
        return block
    separator = "" if upstream.endswith(ROW_SEPARATOR) else ROW_SEPARATOR
    return f"{upstream}{separator}{block}"


def _detail_rows(
    upstream: str,
    ordered: tuple,
    mode: PresentationMode,
    width_budget: int | None,
) -> str | None:
    """One provider per row, at the widest allowed style that fits.

    The mode ladder is walked here as well as below because a narrower style is
    the cheaper concession: dropping the icons costs a reader some scanning speed,
    while wrapping costs them a row, and only when neither is enough does anything
    get dropped. `None` means no style could lay these providers out in rows.
    """
    for candidate in _mode_candidates(mode):
        block = _detail_block(ordered, candidate, width_budget)
        if block is not None:
            return _appended(upstream, block)
    return None


def _shared_line(
    upstream: str,
    ordered: tuple,
    mode: PresentationMode,
    width_budget: int | None,
    depth: InformationDepth,
) -> str:
    """Every provider on one shared line, shedding readings until it fits.

    What `CLEAR` uses, and what `DETAIL` falls back to when no row-per-provider
    layout fits at all. Degradation, in order, and only when a budget is set: take
    the first allowed style that measures within budget; then shed the least
    important provider groups, saying how many readings went; then fall back to the
    single worst state with its label truncated grapheme-safely. If even that
    cannot be said honestly, the upstream text is returned alone.
    """
    candidates = [
        (candidate, _render_groups(ordered, candidate, depth=depth))
        for candidate in _mode_candidates(mode)
    ]
    chosen_mode, block = candidates[0]
    if width_budget is not None:
        for candidate, text in candidates:
            if display_width(text) <= width_budget:
                chosen_mode, block = candidate, text
                break
        else:
            chosen_mode, _ = min(candidates, key=lambda pair: display_width(pair[1]))
            dropped = _fit_by_dropping(ordered, chosen_mode, width_budget, depth)
            block = (
                dropped if dropped is not None
                else _fit_minimal(ordered, chosen_mode, width_budget)
            )

    if not block:
        return upstream
    if not upstream:
        return block
    return f"{upstream}{UPSTREAM_SEPARATORS[chosen_mode]}{block}"


def compose(
    upstream_text: str | None,
    statuses: object,
    *,
    mode: PresentationMode = PresentationMode.BALANCED,
    width_budget: int | None = None,
    depth: InformationDepth = InformationDepth.DETAIL,
) -> str:
    """Build the final statusline from the user's own output plus provider state.

    `upstream_text` is whatever the user's pre-existing statusline command
    printed. It is passed through **verbatim** — never shortened, reordered,
    parsed or annotated — and `width_budget` therefore applies only to the
    Horonom block. That asymmetry is the point rather than an oversight: the
    user's line is theirs, this code cannot know which part of it matters, and
    the one budget we are entitled to spend is our own. A caller that wants a
    hard total must set a budget it can afford after the upstream text.

    No upstream text is a valid state — a user with no previous statusline gets
    the Horonom block alone. So is no provider state: an empty `statuses`
    returns the upstream text untouched, byte for byte.

    `depth` is applied *before* any of that, by reducing each provider to the
    readings that depth shows. Everything below then measures, sheds and counts
    the same readings the user is looking at — so `[+2 more]` in Clear means two
    readings lost to a narrow terminal, not two that Clear was never going to
    show. A depth-blind ladder would report news where there was none.

    Like the two functions it calls, `depth` defaults to `DETAIL`: this module
    renders what it is handed, and the product default of `CLEAR` for a fresh
    install belongs to whoever resolves the user's stored preference. Erring
    toward `DETAIL` here also makes a caller that forgets the argument verbose
    rather than quiet.

    Depth also chooses the layout. At `DETAIL` each provider is given a physical
    row of its own, wrapping onto indented continuation rows where one row will
    not hold it, so `width_budget` bounds each row rather than the block as a
    whole. `CLEAR` stays the single row its summary is meant to be. Only when no
    row-per-provider layout exists at all — a single reading wider than the entire
    budget — does `DETAIL` fall through to the rendering below, which is the one
    that sheds readings and the only way `DETAIL` ever hides anything.

    The two layouts are `_detail_rows` and `_shared_line`, which is where the
    degradation each one performs is described. Whichever answers, the user's own
    line is the last thing to go and never the first.
    """
    ordered = project_to_depth(statuses, depth)
    upstream = upstream_text or ""
    if not ordered:
        return upstream

    if depth.shows_supporting_detail:
        rows = _detail_rows(upstream, ordered, mode, width_budget)
        if rows is not None:
            return rows

    return _shared_line(upstream, ordered, mode, width_budget, depth)


# ------------------------------------------------------------ the explain surface
#
# A glanceable line is one that leaves its own key out. These tables are that key.
# They live beside the glyph tables rather than in the command that prints them,
# for the same reason the glyphs are here at all: the vocabulary is host-owned, so
# a new state should have exactly one place to be described, next to the line that
# gives it an icon.

# Phrased as what the token means for the reader, never as a synonym for its own
# name. `attention` glossed as "needs attention" is the opaque-abbreviation problem
# again, one indirection later.
STATE_MEANINGS = {
    "ok": "checked, and nothing here needs you",
    "attention": "something is waiting on a decision or an action from you",
    "warn": "something is wrong; work is not stopped",
    "critical": "something is wrong and is stopping work",
    "neutral": "a fact with no health claim, such as a mode name or a task id",
    "unknown": (
        "the provider could not determine its own state; this is not a quiet way of "
        "saying all clear"
    ),
}

# Named, unlike the other three marker meanings in the key below, because this is
# the one a reader may meet on a single reading rather than on the line as a
# whole -- so a surface explaining one segment has to be able to quote it, and a
# second copy of this particular sentence is the copy that must not drift.
HYPOTHETICAL_MEANING = "what a policy would have done. Nothing was blocked and nothing was stopped"

SCOPE_MEANINGS = {
    "host": "everything on this machine, including sessions other than this one",
    "session": "this Claude Code session only",
    "project": "this project or working directory only",
}

# Each entry says what the confidence is a confidence *in*, which is the entire
# reason the field exists — see `CONFIDENCE_SUBJECT_TEXT`.
CONFIDENCE_SUBJECT_MEANINGS = {
    "preflight_estimate": (
        "how far the product trusts its own estimate, made before the work runs. "
        "Not a risk level and not a severity"
    ),
    "verification": "how far the product trusts a verification result it has not confirmed",
    "policy_decision": "how far the product trusts a decision it reached",
    "unspecified": "the product did not say what the confidence is about",
}


@dataclasses.dataclass(frozen=True)
class LegendEntry:
    """One row of the key: the token as it appears, its word, and what it means.

    `token` is produced by the same functions that render the line, at the same
    mode, rather than written out. A key that shows glyphs to a reader whose line
    is in text is worse than no key at all — it explains something they are not
    looking at, and the modes exist precisely because that reader's terminal
    cannot be trusted with the glyph.

    `name` is the machine word for the same thing, so the row is still usable
    when the token is a glyph the reader cannot see.
    """

    token: str
    name: str
    meaning: str


@dataclasses.dataclass(frozen=True)
class LegendSection:
    """A group of rows under the question it answers."""

    title: str
    entries: tuple[LegendEntry, ...]


# The example age used by the freshness row. Not a round number, so the row
# demonstrates that the format rounds down to one coarse unit — `120` would render
# as an exact `2m` and teach the reader the opposite.
_LEGEND_AGE_SECONDS = 135

# The example confidence used by the confidence rows. Any value would do; `high`
# is the one most often misread as risk, which is what those rows exist to correct.
_LEGEND_CONFIDENCE = "high"

# The example hidden-segment count. Plural, because the singular would leave a
# reader to guess whether the marker ever carries a number.
_LEGEND_HIDDEN = 2


def legend(mode: PresentationMode) -> tuple[LegendSection, ...]:
    """The key to the line, rendered in the mode the line is rendered in.

    Derived from the rendering tables rather than listed, so a state, scope or
    confidence subject that exists cannot be missing from the key, and a token
    shown here cannot differ from the token shown in the line. The meaning tables
    are keyed identically and the test suite asserts that in both directions,
    which is what makes completeness a property rather than a promise.

    Pure, like everything else in this module: no provider is consulted and
    nothing is read. This is the half of the explain surface that is true before
    anything has been measured.
    """
    return (
        LegendSection(
            "state -- how to read a reading",
            tuple(
                LegendEntry(state_marker(state, mode), STATE_TEXT[state], STATE_MEANINGS[state])
                for state in STATE_TEXT
            ),
        ),
        LegendSection(
            "scope -- what a reading is about",
            tuple(
                LegendEntry(scope_marker(scope, mode), SCOPE_TEXT[scope], SCOPE_MEANINGS[scope])
                for scope in SCOPE_TEXT
            ),
        ),
        LegendSection(
            "confidence -- what a high, medium or low is a confidence in",
            tuple(
                LegendEntry(
                    format_confidence(_LEGEND_CONFIDENCE, subject, mode),
                    subject,
                    CONFIDENCE_SUBJECT_MEANINGS[subject],
                )
                for subject in CONFIDENCE_SUBJECT_TEXT
            ),
        ),
        LegendSection(
            "markers the host adds",
            (
                LegendEntry(f"[{HYPOTHETICAL_TEXT}]", "hypothetical", HYPOTHETICAL_MEANING),
                LegendEntry(
                    _hidden_marker(_LEGEND_HIDDEN),
                    "hidden",
                    "the line ran out of room, and this many readings are not shown",
                ),
                LegendEntry(
                    format_age(_LEGEND_AGE_SECONDS, mode),
                    "freshness",
                    "how old the reading is, rounded down so it is never overstated",
                ),
                LegendEntry(
                    UPSTREAM_SEPARATORS[mode].strip(),
                    "divider",
                    "everything to the left of this is your own statusline, verbatim",
                ),
            ),
        ),
    )
