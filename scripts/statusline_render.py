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

# States that keep their word even in COMPACT, because an exception must become
# *more* explicit under pressure, not less. `unknown` is in here deliberately:
# a provider that could not read its own state is the case a reader is most
# likely to misread as fine.
EMPHATIC_STATES = frozenset({"unknown", "attention", "warn", "critical"})

UNKNOWN_STATE = "unknown"


def state_marker(state: str, mode: PresentationMode) -> str:
    """The leading marker for one segment's state.

    In glyph modes this is normally the glyph alone, because the segment's own
    label is rendered beside it and is the human-readable carrier of meaning —
    `<check> Verified` says everything `<check> OK Verified` says.

    The exception is COMPACT, which shortens and may drop labels: there an
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
    if mode is PresentationMode.COMPACT and state in EMPHATIC_STATES:
        return f"{glyph} {STATE_TEXT[state]}"
    return glyph


def format_age(age_seconds: int, mode: PresentationMode) -> str:
    """Render a freshness reading as a single coarse unit.

    Coarse on purpose: a statusline reader wants to know whether a reading is
    seconds or days old, and the extra precision costs columns that a provider
    label needs more. Rounds *down*, so "2m" never overstates freshness.

    BALANCED and PLAIN say "ago" because a bare "2m" beside a count reads as a
    duration or a budget rather than an age.
    """
    age_seconds = max(0, int(age_seconds))
    for limit, unit, divisor in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600)):
        if age_seconds < limit:
            value = age_seconds // divisor
            break
    else:
        value, unit = age_seconds // 86400, "d"
    token = f"{value}{unit}"
    return token if mode is PresentationMode.COMPACT else f"{token} ago"


def format_count(count: int, total: int | None, count_label: str, mode: PresentationMode) -> str:
    """Render a count with the noun it counts.

    `count_label` is never dropped, in any mode. A bare `2/14` is exactly the
    opaque token this whole design replaces, and the noun is what makes the
    number mean something — the provider contract refuses a `count` without one
    for the same reason.
    """
    if total is None:
        quantity = str(count)
    elif mode is PresentationMode.COMPACT:
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

# COMPACT still names the subject, just shorter. It never degrades to the bare
# value, because "high" alone is the ambiguity this field was added to remove.
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
    table = (
        CONFIDENCE_SUBJECT_TEXT_COMPACT
        if mode is PresentationMode.COMPACT
        else CONFIDENCE_SUBJECT_TEXT
    )
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

# PLAIN drops to ASCII for the same reason it drops glyphs: it exists for
# terminals whose character handling cannot be trusted, and U+00B7 is one more
# thing to get wrong for no gain.
SEGMENT_SEPARATORS = {
    PresentationMode.BALANCED: " · ",  # MIDDLE DOT
    PresentationMode.COMPACT: " · ",
    PresentationMode.PLAIN: " | ",
}

UPSTREAM_SEPARATORS = {
    PresentationMode.BALANCED: " ┃ ",  # BOX DRAWINGS HEAVY VERTICAL
    PresentationMode.COMPACT: " ┃ ",
    PresentationMode.PLAIN: " || ",
}


def _enum_value(value: object) -> str | None:
    """Accept either an enum member from the contract or its raw wire string."""
    if value is None:
        return None
    return getattr(value, "value", value)


def render_segment(segment: object, mode: PresentationMode) -> str:
    """Render one provider segment as a single readable phrase.

    Takes a `statusline_contract.Segment` (or anything with the same
    attributes), so the renderer stays usable against a hand-built stub in
    tests. Every value it reads has already passed the contract's privacy
    allowlist; this function adds no field of its own, so it cannot widen that
    surface.

    The hypothetical marker is placed outside the parenthesised details, welded
    to the label, so no degradation step and no careless reading can separate
    "would block" from "not enforced".
    """
    state = _enum_value(segment.state)
    head = f"{state_marker(state, mode)} {segment.label}".strip()
    if getattr(segment, "hypothetical", False):
        head = f"{head} [{HYPOTHETICAL_TEXT}]"

    details: list[str] = []
    if segment.count is not None and segment.count_label:
        details.append(format_count(segment.count, segment.total, segment.count_label, mode))
    confidence = _enum_value(segment.confidence)
    if confidence is not None:
        details.append(format_confidence(confidence, _enum_value(segment.confidence_of), mode))

    reason = format_reason(segment.reason_code, segment.reason_label)
    # COMPACT spends its remaining columns on exceptions only: a reason for an
    # `ok` segment is the least useful thing on the line, and a reason for a
    # `critical` one is the most.
    if reason and (mode is not PresentationMode.COMPACT or state in EMPHATIC_STATES):
        details.append(reason)
    if segment.age_seconds is not None:
        details.append(format_age(segment.age_seconds, mode))

    if not details:
        return head
    return f"{head} ({DETAIL_SEPARATOR.join(details)})"


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


def render_provider(status: object, mode: PresentationMode) -> str:
    """Render one provider's whole group: attribution, scope, then segments.

    Takes a `statusline_contract.ProviderStatus`. Segments are ordered by the
    contract's own `order_segments`, so ordering is the contract's concern and
    identical for every caller.

    A provider with no segments does not render as nothing. The contract asks
    providers to always emit at least one segment precisely because silence
    reads as all-clear, but that is a rule providers can get wrong, so the host
    states the absence explicitly rather than trusting them. `fallback_text` is
    used for that only when present — it is a convenience rendering, never the
    primary one.
    """
    segments = statusline_contract.order_segments(status)
    if segments:
        body = SEGMENT_SEPARATORS[mode].join(render_segment(s, mode) for s in segments)
    elif getattr(status, "fallback_text", None):
        body = f"{state_marker(UNKNOWN_STATE, mode)} {status.fallback_text}"
    else:
        body = f"{state_marker(UNKNOWN_STATE, mode)} {NO_SEGMENTS_LABEL}"

    name = provider_display_name(status.provider)
    scope = scope_marker(_enum_value(status.scope), mode)
    return f"{name} {scope} {body}"


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


# Tried in order when a budget is set. Deliberately *measured* rather than
# assumed to shrink: COMPACT can be wider than BALANCED, because an emphatic
# state gains its word there, and that is the intended trade — an exception
# becomes more explicit under pressure, not less. So the ladder picks the first
# candidate that actually fits instead of trusting the order.
MODE_LADDER = (PresentationMode.BALANCED, PresentationMode.COMPACT, PresentationMode.PLAIN)


def _mode_candidates(mode: PresentationMode) -> tuple[PresentationMode, ...]:
    """The modes the ladder may try, given what the user asked for.

    Strictly downward: the ladder never hands back something the user declined.
    A PLAIN request is a statement about what the terminal can render, not about
    width, so no width pressure may reintroduce a glyph — that would produce
    exactly the broken output PLAIN exists to avoid. A COMPACT request is not
    re-expanded to BALANCED either, since the user asked for compactness and
    running out of room is not a reason to give them more prose.
    """
    start = MODE_LADDER.index(mode)
    return MODE_LADDER[start:]


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


def _render_groups(statuses: "tuple", mode: PresentationMode, hidden: int = 0) -> str:
    text = SEGMENT_SEPARATORS[mode].join(render_provider(s, mode) for s in statuses)
    if hidden:
        marker = _hidden_marker(hidden)
        text = f"{text}{SEGMENT_SEPARATORS[mode]}{marker}" if text else marker
    return text


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


def _fit_by_dropping(statuses: tuple, mode: PresentationMode, budget: int) -> str | None:
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
        text = _render_groups(remaining, mode, hidden=len(dropped))
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
    mangled here — a hypothetical segment that cannot render its marker in full
    is not shown at this rung at all.

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
    head = f"{state_marker(_enum_value(worst.state), mode)} "
    label_budget = budget - display_width(suffix) - display_width(head)
    if label_budget < MIN_LABEL_COLUMNS:
        return ""
    label = truncate_to_width(worst.label, label_budget)
    if not label:
        return ""
    return f"{head}{label}{suffix}"


def compose(
    upstream_text: str | None,
    statuses: object,
    *,
    mode: PresentationMode = PresentationMode.BALANCED,
    width_budget: int | None = None,
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

    Degradation, in order, and only when a budget is set: try each allowed mode
    and take the first that measures within budget; then shed the least
    important provider groups, saying how many segments went; then fall back to
    the single worst state with its label truncated grapheme-safely. If even
    that cannot be said honestly, the upstream text is returned alone — the
    user's own line is the last thing to go, never the first.
    """
    ordered = statusline_contract.order_providers(statuses)
    upstream = upstream_text or ""
    if not ordered:
        return upstream

    candidates = [(candidate, _render_groups(ordered, candidate)) for candidate in _mode_candidates(mode)]
    chosen_mode, block = candidates[0]
    if width_budget is not None:
        for candidate, text in candidates:
            if display_width(text) <= width_budget:
                chosen_mode, block = candidate, text
                break
        else:
            chosen_mode, _ = min(candidates, key=lambda pair: display_width(pair[1]))
            dropped = _fit_by_dropping(ordered, chosen_mode, width_budget)
            block = (
                dropped if dropped is not None
                else _fit_minimal(ordered, chosen_mode, width_budget)
            )

    if not block:
        return upstream
    if not upstream:
        return block
    return f"{upstream}{UPSTREAM_SEPARATORS[chosen_mode]}{block}"
