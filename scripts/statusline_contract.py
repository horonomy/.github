#!/usr/bin/env python3
"""Shared Horonom statusline provider contract (HORO-1564).

A host agent tool such as Claude Code exposes exactly *one* statusline
command. Several Horonom products each have live state worth showing there.
This module defines the one typed, versioned contract those products speak,
so that:

- no product needs to know how to rewrite the host tool's configuration;
- no product needs to know how the whole statusline is formatted;
- adding a product does not require editing another product's renderer;
- the shared host — not each product — owns iconography and layout.

The contract is deliberately a *wire* contract (versioned JSON on a
subprocess's stdout), not a library API. Horonom products are written in
different languages (Fornax and Libra are Rust, Circinus is Python), so a
shared library cannot serve them; a shared program plus a versioned wire
format can. This also keeps the single host-configuration patcher in one
place instead of reimplemented per product, per
`governance/product/product-integration-safety.md`.

Read alongside:

- `governance/product/product-integration-safety.md` — the non-destructive
  host-configuration invariant this contract's ownership model refines.
- `governance/product/host-config-ownership-test-contract.md` — the 14
  ownership property IDs the lifecycle commands must prove.

Stdlib only, matching the rest of `scripts/`.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import re

# The wire contract version a provider must declare. Bumped only when a
# change is not backward compatible for an existing host; additive optional
# fields do not bump it. The full evolution rules are in
# `governance/product/statusline-provider-contract.md`.
CONTRACT_VERSION = 1

# Versions this host can parse. A provider declaring anything else is
# reported as an explicit unsupported-version state, never guessed at.
SUPPORTED_CONTRACT_VERSIONS = frozenset({1})


def is_supported_contract_version(version: object) -> bool:
    """Return whether `version` is a contract version this host can parse.

    Deliberately strict about type: a provider that emits the version as a
    string (`"1"`) has a real bug, and silently coercing it would let a
    future incompatible provider be mis-parsed as v1.
    """
    return isinstance(version, int) and not isinstance(version, bool) and version in SUPPORTED_CONTRACT_VERSIONS


class Scope(enum.Enum):
    """Which breadth of state a provider is reporting.

    Scope is the *semantic* value. A renderer may draw it as a glyph, but the
    glyph is never the contract — `text_fallback` is always available, so a
    terminal with no emoji support still shows unambiguous scope. Host-wide
    state must never silently render as if it were session-scoped, which is
    why there is no default: a provider states its scope or fails validation.
    """

    HOST = "host"
    SESSION = "session"
    PROJECT = "project"

    @property
    def text_fallback(self) -> str:
        """The deterministic, emoji-free rendering of this scope."""
        return f"[{self.value}]"


class Availability(enum.Enum):
    """Whether a provider could answer at all, and if not, why not.

    These five states are never collapsed. In particular:

    - `UNKNOWN` is not `AVAILABLE`: "we did not find out" is not "healthy".
    - `UNAVAILABLE` is not a zero reading: a stopped daemon is not a daemon
      reporting no findings.
    - `ERROR` is not "nothing happened": a probe that failed is a fact worth
      rendering, not silence.
    - `UNSUPPORTED` is a legitimate terminal answer, not a defect to hide.

    Only `AVAILABLE` licenses a renderer to treat the provider's segment
    values as live readings.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    ERROR = "error"

    @property
    def has_live_readings(self) -> bool:
        """Whether segment values may be read as current measurements."""
        return self is Availability.AVAILABLE


class SegmentState(enum.Enum):
    """The shared semantic role of one segment, owned by the renderer.

    This is the enum that lets the host — not each product — pick the glyph.
    Two products reporting the same semantic role therefore get the same
    icon, which is what stops Fornax and Circinus from independently choosing
    incompatible emoji for "needs your attention".

    `NEUTRAL` is informational state with no health claim (a mode label, a
    task id). `UNKNOWN` is an explicit absence of state, never a stand-in for
    `OK`.
    """

    OK = "ok"
    ATTENTION = "attention"
    WARN = "warn"
    CRITICAL = "critical"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"

    @property
    def makes_a_health_claim(self) -> bool:
        """Whether this state asserts something about product health.

        `NEUTRAL` and `UNKNOWN` deliberately do not, so a renderer must not
        colour or sort them as if they were good news.
        """
        return self not in (SegmentState.NEUTRAL, SegmentState.UNKNOWN)


class Confidence(enum.Enum):
    """How much a producer trusts a value it computed without confirmation.

    Matches Libra's `Confidence` domain type (low < medium < high). This is
    *not* a risk, severity or impact scale, and the contract keeps it useless
    on its own: a confidence is only renderable together with a
    `ConfidenceSubject` saying what it is a confidence *in*.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        """Ordinal position, so a renderer can sort or threshold."""
        return _CONFIDENCE_RANK[self]


_CONFIDENCE_RANK = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


class ConfidenceSubject(enum.Enum):
    """What a `Confidence` value is a confidence *in*.

    Exists because a bare `high` next to a task id reads as "high risk" or
    "high priority" at a glance. Carrying the subject as its own enum lets
    the shared renderer always print a disambiguating noun, and keeps the
    choice of noun out of each product's formatting code.
    """

    PREFLIGHT_ESTIMATE = "preflight_estimate"
    VERIFICATION = "verification"
    POLICY_DECISION = "policy_decision"
    UNSPECIFIED = "unspecified"

    @property
    def label(self) -> str:
        """The short human noun a renderer prints next to the confidence."""
        return _CONFIDENCE_SUBJECT_LABEL[self]


_CONFIDENCE_SUBJECT_LABEL = {
    ConfidenceSubject.PREFLIGHT_ESTIMATE: "preflight confidence",
    ConfidenceSubject.VERIFICATION: "verification confidence",
    ConfidenceSubject.POLICY_DECISION: "decision confidence",
    ConfidenceSubject.UNSPECIFIED: "confidence",
}


class ClearRole(enum.Enum):
    """What part a segment plays in its product's one-line executive summary.

    A Clear-mode reading is not Detail with fields removed; it is the answer to
    "if this product gets one short phrase, which of its facts earns it". Only
    the product knows that. A task id and a delivery estimate are both `neutral`
    facts of equal severity, but one is an operator's whole reason to look at the
    line and the other is noise, and no amount of host-side severity arithmetic
    can tell them apart. So the judgement is declared here rather than inferred,
    and the host owns only the ladder that consumes it — which keeps Clear a
    single shared code path instead of one branch per product.

    Declaring it on the *segment* rather than shipping a per-product table in the
    host is what makes that true for a product the host has never heard of. The
    host's fallback for an undeclared segment (`statusline_render.clear_roles`)
    exists for providers written before this field, not as the intended path — and
    a product that wants the fallback off its projection entirely declares
    `ClearAuthority.PROVIDER` on the envelope.

    `EXCEPTION` is the top rung and deliberately narrow: the product is broken,
    unavailable, or genuinely waiting on the operator. It is not "the worst thing
    currently true" — a warn-state budget posture is still a posture, and
    promoting it to an exception is how a line starts claiming work is blocked
    when nothing is.
    """

    EXCEPTION = "exception"
    POSTURE = "posture"
    VITAL = "vital"
    SUPPORTING = "supporting"


class ClearAuthority(enum.Enum):
    """Who decides which of a provider's facts earns the Clear line.

    `ClearRole` made the judgement *expressible*; this makes it *binding*. The
    two are not the same thing, and the gap between them was a live defect: a
    provider could declare a posture and still have the host promote a different
    segment over it, because the host's fallback ladder ran regardless and had no
    way to tell a deliberate, complete declaration from a payload written before
    the field existed. Clear then summarised the wrong fact while every test
    passed — Circinus led its line with a hypothetical would-block instead of
    mode plus outcome.

    `HOST` is the default and keeps the documented fallback for every provider
    that has not spoken. `PROVIDER` means "this payload declares its own Clear
    projection in full", and the host answers by switching the ladder off rather
    than merging with it: a declaration that can be overridden on the host's
    severity arithmetic is not a declaration.

    Because it is a claim the host relies on, it is validated rather than
    trusted (`ProviderStatus._validate_clear_authority`), and a declaring payload
    that does not hold up is refused instead of quietly re-inferred. Falling back
    there would restore the exact failure this enum exists to close, and do it
    invisibly.
    """

    HOST = "host"
    PROVIDER = "provider"


class HostCapability(enum.Enum):
    """Whether a given agent host tool can support a composed statusline.

    `UNSUPPORTED` is a legitimate, final answer for a host with no statusline
    contract of its own — it is not a prompt to synthesise one out of ANSI
    escape sequences or background output, which would be a claim of support
    the host never made.

    `UNAVAILABLE` is different: the host *does* support this, but not on this
    machine right now (not installed, wrong version). `UNKNOWN` means the
    capability has not been established against the installed version, which
    is the honest default for a host nobody has checked.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    @property
    def may_install(self) -> bool:
        """Whether a lifecycle command may attempt to claim the slot."""
        return self is HostCapability.SUPPORTED


class Ownership(enum.Enum):
    """Who owns a given artifact or key in the composed-statusline surface.

    Refines ADR-0009's ownership classes for this one capability. The rule
    that matters: a lifecycle command may only write what it owns, and
    `USER_OWNED` plus `UNKNOWN` are both no-write classes.

    - `USER_OWNED` — the person's own statusline command/script, and every
      setting they wrote by hand. Read, invoke, preserve; never edit, never
      parse the source, never rewrite.
    - `HOST_OWNED` — the agent tool's own schema and keys inside a shared
      settings file. Shared artifact; patch at key level only.
    - `HORONOM_HOST_OWNED` — the composed-statusline host program, its
      provider registry, and the single `statusLine.command` value while
      Horonom holds the slot.
    - `PRODUCT_PROVIDER_OWNED` — one product's own provider entry in the
      registry, and that product's own state stores.
    - `UNKNOWN` — anything unattributable, including legacy state with no
      ownership marker. Fails safe: no automatic destructive action.
    """

    USER_OWNED = "user_owned"
    HOST_OWNED = "host_owned"
    HORONOM_HOST_OWNED = "horonom_host_owned"
    PRODUCT_PROVIDER_OWNED = "product_provider_owned"
    UNKNOWN = "unknown"

    @property
    def is_writable_by_lifecycle(self) -> bool:
        """Whether enable/disable/upgrade may mutate state in this class.

        `HOST_OWNED` is absent on purpose: the shared settings file is a
        shared artifact, so the writable unit there is the Horonom-owned key
        inside it, never the host's own keys.
        """
        return self in (
            Ownership.HORONOM_HOST_OWNED,
            Ownership.PRODUCT_PROVIDER_OWNED,
        )


# Ownership class of each concrete artifact/key this capability touches.
# HORO-1564 requires this be stated exactly rather than left to each
# lifecycle implementation to decide locally. Keys are stable identifiers,
# not file paths, so nothing here hard-codes a personal directory.
ARTIFACT_OWNERSHIP = {
    # The person's own statusline program, whatever it is and wherever it
    # lives. Invoked as a provider, never read as source, never edited.
    "user_statusline_command": Ownership.USER_OWNED,
    # Everything else the person wrote in the host's shared settings file.
    "user_settings_keys": Ownership.USER_OWNED,
    # The host tool's own settings file as a whole: a shared artifact.
    "host_shared_settings_file": Ownership.HOST_OWNED,
    # The host tool's own schema keys inside that file.
    "host_settings_schema_keys": Ownership.HOST_OWNED,
    # The one slot value, only while Horonom holds it.
    "statusline_command_value": Ownership.HORONOM_HOST_OWNED,
    # The composed-statusline program Horonom installs and the registry of
    # which providers are enabled.
    "horonom_statusline_program": Ownership.HORONOM_HOST_OWNED,
    "horonom_provider_registry": Ownership.HORONOM_HOST_OWNED,
    # The record of what the slot looked like before Horonom took it. Owned
    # by the host, but evidence only: never restoration authority on its own.
    "horonom_preserved_upstream_record": Ownership.HORONOM_HOST_OWNED,
    # One product's registry entry, and that product's own state store.
    "product_provider_entry": Ownership.PRODUCT_PROVIDER_OWNED,
    "product_state_store": Ownership.PRODUCT_PROVIDER_OWNED,
    # A statusline slot value with no recognised owner, and pre-marker
    # legacy state. Both fail safe.
    "unattributed_statusline_command": Ownership.UNKNOWN,
    "legacy_unmarked_state": Ownership.UNKNOWN,
}


def classify_artifact(artifact: str) -> Ownership:
    """Return the ownership class of a known artifact identifier.

    An unrecognised identifier is `UNKNOWN`, not a guess: a new artifact
    nobody has classified must not inherit write permission by accident.
    """
    return ARTIFACT_OWNERSHIP.get(artifact, Ownership.UNKNOWN)


class ContractViolation(ValueError):
    """A provider payload does not satisfy this contract.

    Raised rather than returned so that a malformed payload cannot be
    accidentally rendered as if it were valid. The compositor catches this
    per provider and degrades that one provider, never the whole line.
    """


class UnsupportedContractVersion(ContractViolation):
    """A provider declared a contract version this host cannot parse.

    Separate from a shape error because the remedy is different and worth
    reporting differently: the provider is newer than the host, so the fix is
    to upgrade the host, not to change the provider.
    """


class PrivacyViolation(ContractViolation):
    """A provider payload carries a value this contract forbids emitting.

    A distinct type because the correct response differs: a shape error is a
    provider bug to fix, while this is a potential disclosure, and the host
    must drop the provider's output entirely rather than render part of it.
    """


# Provider ids, segment keys and reason codes are machine tokens, not prose.
# A narrow charset here is the first line of the privacy defence: a value
# that cannot contain `/`, `:` or `=` cannot smuggle a path, a URL or a
# key=value pair through a field the renderer will print.
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_EXPLAIN_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}$")


def require_token(value: object, field: str) -> str:
    """Validate a lowercase machine token (segment key, reason code)."""
    if not isinstance(value, str) or not _TOKEN_RE.match(value):
        raise ContractViolation(
            f"{field} must be a lowercase token matching {_TOKEN_RE.pattern}"
        )
    return value


def require_provider_id(value: object) -> str:
    """Validate a provider id, which may also contain hyphens."""
    if not isinstance(value, str) or not _PROVIDER_ID_RE.match(value):
        raise ContractViolation(
            f"provider must be a lowercase id matching {_PROVIDER_ID_RE.pattern}"
        )
    return value


def require_explain_key(value: object) -> str:
    """Validate a dotted key naming an entry in the shared explain surface.

    Bounded to four dotted parts so a provider cannot use this field as a
    general-purpose string channel.
    """
    if not isinstance(value, str) or not _EXPLAIN_KEY_RE.match(value):
        raise ContractViolation(
            f"explain_key must be a dotted key matching {_EXPLAIN_KEY_RE.pattern}"
        )
    return value


# Longest human label a provider may emit. The statusline is one line shared
# by several products; a long label is a layout bug, not a feature.
MAX_LABEL_CHARS = 48

# The only non-alphanumeric characters allowed in a human label. Everything
# a path, URL, shell expansion or key=value pair needs is absent: no `/`,
# `\`, `:`, `=`, `@`, `~`, `$`, quote or angle bracket. That makes those
# disclosures structurally impossible rather than merely detected.
_LABEL_EXTRA_CHARS = frozenset(" .,'-—()%+?!≤≥")


# A digit immediately before a percent sign, optionally spaced as some locales
# write it. Matched on the label as a whole rather than on tokens, because the
# defect is about the label having no noun anywhere, not about adjacency.
_PERCENTAGE_RE = re.compile(r"\d\s*%")

# Any run of letters long enough to be a word. Two rather than one, so a stray
# initial or a unit letter does not pass as the missing noun.
_LABEL_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def require_percentage_axis(value: str, field: str) -> str:
    """Refuse a percentage that never says what it is a percentage *of*.

    `62%` is the budget-display defect in its purest form: the reader cannot
    tell whether 62 percent has been used or 62 percent remains, and those are
    opposite readings of the same glanced-at number. The fix is not a longer
    label but a named axis — `62% budget used` or `38% budget left` — and this
    is the same rule as `count` needing `count_label` and `duration_seconds`
    needing `duration_label`, applied to the one quantity that can be embedded
    in a label instead of carried in its own field.

    Deliberately a "has a word" test rather than an allowlist of axis words. An
    allowlist would have rejected `97% verified`, which is a perfectly
    unambiguous label, and every such rejection pushes a product towards
    phrasing that satisfies the validator instead of the reader. What cannot be
    tolerated is a number with no noun at all, and that is exactly what this
    catches.
    """
    if _PERCENTAGE_RE.search(value) and not _LABEL_WORD_RE.search(value):
        raise ContractViolation(
            f"{field} contains a percentage with no word saying what it measures; "
            "a bare 62% could mean used or left, which are opposite readings"
        )
    return value


def require_label(
    value: object, field: str = "label", max_chars: int = MAX_LABEL_CHARS
) -> str:
    """Validate a short human label against a strict character allowlist.

    Rejects emoji as well as separators: the shared host owns iconography, so
    a provider embedding its own glyph would defeat consistent rendering
    across products. Emoji are `Symbol, other` and so are not alphanumeric,
    which is why the allowlist check catches them without an emoji table.

    Alphanumerics must be ASCII. Two reasons, and the second is the load-bearing
    one: it removes a homoglyph channel (Cyrillic `а` is alphanumeric), and it
    keeps `max_chars` an honest bound. `str.isalnum()` is true for fullwidth
    and CJK characters, which occupy two terminal columns each, so without this
    a 48-character label could be 96 columns wide and silently break the shared
    line's layout. Widening this is a deliberate contract change that has to
    come with column-aware bounds, not an incidental one.

    `max_chars` is explicit because the same character rules apply to a
    provider's optional whole-line convenience rendering, which is legitimately
    longer than one segment label.
    """
    if not isinstance(value, str):
        raise ContractViolation(f"{field} must be a string")
    if not value:
        raise ContractViolation(f"{field} must not be empty")
    if len(value) > max_chars:
        raise ContractViolation(f"{field} must be at most {max_chars} characters")
    for char in value:
        if not (char.isascii() and char.isalnum()) and char not in _LABEL_EXTRA_CHARS:
            raise ContractViolation(
                f"{field} contains a disallowed character {char!r}; "
                "labels are prose, and the host owns iconography and separators"
            )
    return value


# Credential prefixes that identify a secret on sight. Not exhaustive by
# design — the character allowlist above is the structural defence and this
# is the second layer, aimed at the shapes that would otherwise slip through
# it because they are pure alphanumerics.
_SECRET_PREFIXES = (
    "sk-",
    "sk_live_",
    "sk_test_",
    "rk_live_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xapp-",
    "AKIA",
    "ASIA",
    "AIza",
    "ya29.",
    "dop_v1_",
    "hf_",
    "npm_",
    "eyJ",
    "-----BEGIN",
)

# A credential word immediately followed by an assignment or a value.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|"
    r"credential|authorization|bearer|private[_-]?key)\b\s*[:=]?\s*\S",
)

# A long run from the base64/hex/token alphabet mixing character classes.
# Human prose does not contain one; an opaque credential almost always does.
_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9_+/=-]{20,}")


def _looks_high_entropy(run: str) -> bool:
    """Whether a token-alphabet run mixes classes like a credential does."""
    return (
        any(c.islower() for c in run)
        and any(c.isupper() for c in run)
        and any(c.isdigit() for c in run)
    )


def assert_no_secret_shape(value: str, field: str) -> str:
    """Raise `PrivacyViolation` if `value` looks like or names a credential.

    Deliberately reports only the field name. The offending value is never
    included in the message, not even truncated or hashed: an error string
    ends up in logs and in a `doctor` transcript, which is exactly where a
    credential fragment must not appear.
    """
    for prefix in _SECRET_PREFIXES:
        if prefix in value:
            raise PrivacyViolation(f"{field} contains a credential-shaped prefix")
    if _SECRET_ASSIGNMENT_RE.search(value):
        raise PrivacyViolation(f"{field} names a credential")
    for run in _HIGH_ENTROPY_RE.findall(value):
        if _looks_high_entropy(run):
            raise PrivacyViolation(f"{field} contains a high-entropy secret-shaped run")
    return value


# A URL needs `:` and `/`, which the label allowlist already forbids, so what
# is left to catch is a bare host or domain name — which is how an internal
# endpoint would actually leak through a label.
_HOST_SUFFIXES = (
    "com",
    "net",
    "org",
    "io",
    "co",
    "dev",
    "app",
    "cloud",
    "ai",
    "internal",
    "intranet",
    "corp",
    "local",
    "localdomain",
    "lan",
    "test",
    "invalid",
    "onion",
)
_HOSTNAME_RE = re.compile(
    r"(?i)\b(?:localhost\b|[a-z0-9][a-z0-9-]*\.(?:" + "|".join(_HOST_SUFFIXES) + r")\b)"
)


def assert_no_host_shape(value: str, field: str) -> str:
    """Raise `PrivacyViolation` if `value` contains a host or domain name.

    Catches the residual case the character allowlist cannot: a bare
    `something.internal` or `localhost` needs no separator to disclose an
    internal endpoint.
    """
    if _HOSTNAME_RE.search(value):
        raise PrivacyViolation(f"{field} contains a host or domain name")
    return value


def assert_privacy_safe(value: str, field: str) -> str:
    """Run every privacy check this contract applies to an emitted string."""
    assert_no_secret_shape(value, field)
    assert_no_host_shape(value, field)
    return value


def require_safe_label(
    value: object, field: str, max_chars: int = MAX_LABEL_CHARS
) -> str:
    """Validate a human label for shape, privacy, and unit honesty.

    The percentage rule is applied here rather than only to `segment.label`
    because a bare number is ambiguous wherever it is rendered, and this is the
    one function every provider-emitted human string passes through. Adding it
    to a single field would have left the same defect reachable via a reason or
    a fallback rendering.
    """
    checked = assert_privacy_safe(require_label(value, field, max_chars), field)
    return require_percentage_axis(checked, field)


# A segment is one fact. A provider showing more than a handful of them is
# competing with the other products for a single shared line.
MAX_SEGMENTS_PER_PROVIDER = 4

# Upper bounds on the numeric fields, so a provider cannot push an arbitrary
# integer through the contract and break layout.
MAX_COUNT = 10**9

# Ordering is a hint within a fixed range rather than an absolute index, so a
# product can leave room between itself and its neighbours without negotiating.
MAX_ORDER_HINT = 1000
MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60

# A *forward-looking* span, as distinct from `age_seconds` looking backwards:
# Libra's remaining-work P90 is the first one. Bounded separately from
# `MAX_AGE_SECONDS` despite sharing its value today, because the two answer to
# different things — an age is capped by how long the machine has existed, while
# an estimate is capped only by a model's willingness to extrapolate, and a
# runaway estimator is exactly the case this bound exists to catch.
MAX_DURATION_SECONDS = 10 * 365 * 24 * 60 * 60


def require_bounded_int(value: object, field: str, maximum: int) -> int:
    """Validate a non-negative integer within an explicit upper bound."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractViolation(f"{field} must be an integer")
    if value < 0:
        raise ContractViolation(f"{field} must not be negative")
    if value > maximum:
        raise ContractViolation(f"{field} must be at most {maximum}")
    return value


@dataclasses.dataclass(frozen=True)
class Segment:
    """One bounded fact a provider contributes to the shared statusline.

    Every field is either a bounded enum, a bounded integer, or a short human
    label that has passed the privacy allowlist. There is deliberately no
    free-text field: a provider with something longer to say puts it behind
    `explain_key`, which the shared explain surface resolves, rather than
    widening the statusline contract into a string channel.

    Validated at construction, so a `Segment` that exists is renderable.
    """

    key: str
    state: SegmentState
    label: str
    reason_code: str | None = None
    reason_label: str | None = None
    confidence: Confidence | None = None
    confidence_of: ConfidenceSubject | None = None
    age_seconds: int | None = None
    count: int | None = None
    total: int | None = None
    count_label: str | None = None
    duration_seconds: int | None = None
    duration_label: str | None = None
    hypothetical: bool = False
    explain_key: str | None = None
    order_hint: int = 0
    # Optional because it arrived after the first three providers shipped, and a
    # segment that does not declare a part still has to render. `None` means "the
    # provider has not judged this", which the host answers with a documented
    # default -- not with a guess dressed up as a declaration.
    clear_role: ClearRole | None = None
    # How long this reading stays worth acting on. Declared by the provider for
    # the same reason `clear_role` is: only the product knows whether five
    # minutes is stale. A shared host guess would be one number applied to a
    # security verification and a policy decision alike.
    fresh_for_seconds: int | None = None

    @property
    def is_stale(self) -> bool:
        """Whether this reading has outlived the horizon its provider declared.

        `False` when either half is missing, and deliberately so: a segment that
        declared no horizon has made no claim to be judged against, and treating
        silence as staleness would hide readings whose provider never said they
        expire. The contract refuses the one combination that would make this
        answer a guess -- a horizon with no age.
        """
        if self.fresh_for_seconds is None or self.age_seconds is None:
            return False
        return self.age_seconds > self.fresh_for_seconds

    def __post_init__(self) -> None:
        require_token(self.key, "segment.key")
        if not isinstance(self.state, SegmentState):
            raise ContractViolation("segment.state must be a SegmentState")
        require_safe_label(self.label, "segment.label")
        if self.reason_code is not None:
            require_token(self.reason_code, "segment.reason_code")
        if self.reason_label is not None:
            require_safe_label(self.reason_label, "segment.reason_label")
        self._validate_confidence()
        self._validate_counts()
        self._validate_duration()
        if self.age_seconds is not None:
            require_bounded_int(self.age_seconds, "segment.age_seconds", MAX_AGE_SECONDS)
        if not isinstance(self.hypothetical, bool):
            raise ContractViolation("segment.hypothetical must be a bool")
        if self.explain_key is not None:
            require_explain_key(self.explain_key)
        require_bounded_int(self.order_hint, "segment.order_hint", MAX_ORDER_HINT)
        if self.clear_role is not None and not isinstance(self.clear_role, ClearRole):
            raise ContractViolation("segment.clear_role must be a ClearRole")
        self._validate_freshness()

    def _validate_freshness(self) -> None:
        """A horizon with nothing to measure against cannot be judged, so refuse it.

        Same shape as the count and duration rules above, and the same reason: a
        field that can only be read in combination with another must not be
        settable alone. The specific failure is that the host would have to pick
        between two wrong answers -- treat an unmeasurable reading as fresh and
        show a possibly stale secondary, or treat it as stale and hide a good
        one -- and neither is a choice the host is entitled to make silently.
        """
        if self.fresh_for_seconds is not None:
            require_bounded_int(
                self.fresh_for_seconds, "segment.fresh_for_seconds", MAX_AGE_SECONDS
            )
            if self.age_seconds is None:
                raise ContractViolation(
                    "segment.fresh_for_seconds requires segment.age_seconds; a "
                    "freshness horizon with no age cannot be judged either way"
                )

    def _validate_confidence(self) -> None:
        """A confidence without its subject reads as risk; forbid the pair split."""
        if (self.confidence is None) != (self.confidence_of is None):
            raise ContractViolation(
                "segment.confidence and segment.confidence_of must be set together; "
                "a bare high/medium/low reads as risk or priority"
            )
        if self.confidence is not None and not isinstance(self.confidence, Confidence):
            raise ContractViolation("segment.confidence must be a Confidence")
        if self.confidence_of is not None and not isinstance(
            self.confidence_of, ConfidenceSubject
        ):
            raise ContractViolation("segment.confidence_of must be a ConfidenceSubject")

    def _validate_counts(self) -> None:
        """A bare number needs a noun, and a numerator cannot exceed its total."""
        if self.count is not None:
            require_bounded_int(self.count, "segment.count", MAX_COUNT)
            if self.count_label is None:
                raise ContractViolation(
                    "segment.count requires segment.count_label; an unlabelled "
                    "number is the opaque abbreviation this contract replaces"
                )
        if self.total is not None:
            require_bounded_int(self.total, "segment.total", MAX_COUNT)
            if self.count is None:
                raise ContractViolation("segment.total requires segment.count")
            if self.count > self.total:
                raise ContractViolation("segment.count must not exceed segment.total")
        if self.count_label is not None:
            require_safe_label(self.count_label, "segment.count_label")

    def _validate_duration(self) -> None:
        """A span needs to say what span it is, for the same reason a count does.

        `P90 5d4h` is readable; a bare `5d4h` beside a task id is not — it could
        be elapsed, remaining, a budget or a timeout. So the noun is mandatory,
        mirroring `count`/`count_label` exactly rather than inventing a second
        rule for the same defect.

        The host owns the *formatting* (`statusline_render.format_duration`); the
        provider supplies seconds and the noun. A product that formatted its own
        `5d4h` would be one more place for two products to disagree about what a
        day is, which is the drift this contract exists to prevent.
        """
        if self.duration_seconds is not None:
            require_bounded_int(
                self.duration_seconds, "segment.duration_seconds", MAX_DURATION_SECONDS
            )
            if self.duration_label is None:
                raise ContractViolation(
                    "segment.duration_seconds requires segment.duration_label; an "
                    "unlabelled span could be elapsed, remaining or a budget"
                )
        if self.duration_label is not None:
            require_safe_label(self.duration_label, "segment.duration_label")


# A provider's own version token, for capability negotiation and for a
# `doctor` surface to report. Version-shaped only: no free text.
_PROVIDER_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")

# `observed_at` is UTC with an explicit `Z`. A local-offset timestamp makes
# freshness ambiguous across machines, which is the one thing this field is
# for, so offsets are rejected rather than converted.
_OBSERVED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

# Ceiling on a provider's declared cache lifetime. The statusline is a hot
# path rendered on every host refresh, so caching is expected — but a long
# TTL turns a live indicator into a stale one without saying so.
MAX_CACHE_TTL_SECONDS = 60

# Longest optional convenience rendering a provider may supply. The shared
# host composes from segments; this exists only so a provider can be run
# standalone and still print something useful.
MAX_FALLBACK_TEXT_CHARS = 120


def require_provider_version(value: object) -> str:
    """Validate a provider's own version token."""
    if not isinstance(value, str) or not _PROVIDER_VERSION_RE.match(value):
        raise ContractViolation(
            f"provider_version must match {_PROVIDER_VERSION_RE.pattern}"
        )
    return value


def require_observed_at(value: object) -> str:
    """Validate an explicit-UTC ISO 8601 timestamp.

    Checked for real calendar validity too: a regex alone would accept
    `2026-02-31T00:00:00Z`, and a nonsense date is worse than no timestamp
    because a renderer would compute an age from it.
    """
    if not isinstance(value, str) or not _OBSERVED_AT_RE.match(value):
        raise ContractViolation(
            "observed_at must be an explicit-UTC ISO 8601 timestamp ending in Z"
        )
    try:
        # The regex above fixes the layout up to seconds at 19 characters, so
        # dropping any fractional part means slicing, not splitting on "." —
        # splitting would discard the trailing "Z" the format string needs.
        datetime.datetime.strptime(value[:19] + "Z", "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError as exc:
        raise ContractViolation(f"observed_at is not a real UTC instant: {exc}") from exc
    return value


@dataclasses.dataclass(frozen=True)
class ProviderStatus:
    """One provider's complete, validated answer for one render.

    Constructing this is the contract. A provider that cannot answer still
    returns one of these with a non-`AVAILABLE` availability and, usually, a
    segment explaining why — because "unavailable" rendering as an empty
    statusline is indistinguishable from "everything is fine".
    """

    provider: str
    provider_version: str
    scope: Scope
    availability: Availability
    segments: tuple[Segment, ...] = ()
    observed_at: str | None = None
    cache_ttl_seconds: int = 0
    order_hint: int = 500
    fallback_text: str | None = None

    # Envelope-level rather than per-segment because it is a claim about the
    # projection as a whole: "every segment here has been assigned its part".
    # A per-segment flag could not express that, and so could not tell a
    # deliberately supporting segment from one nobody had got around to.
    clear_authority: ClearAuthority = ClearAuthority.HOST

    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not is_supported_contract_version(self.contract_version):
            raise UnsupportedContractVersion(
                f"contract_version {self.contract_version!r} is not supported by "
                f"this host (supported: {sorted(SUPPORTED_CONTRACT_VERSIONS)})"
            )
        require_provider_id(self.provider)
        require_provider_version(self.provider_version)
        if not isinstance(self.scope, Scope):
            raise ContractViolation("scope must be a Scope; it has no default")
        if not isinstance(self.availability, Availability):
            raise ContractViolation("availability must be an Availability")
        self._validate_segments()
        self._validate_clear_authority()
        if self.observed_at is not None:
            require_observed_at(self.observed_at)
        require_bounded_int(
            self.cache_ttl_seconds, "cache_ttl_seconds", MAX_CACHE_TTL_SECONDS
        )
        require_bounded_int(self.order_hint, "order_hint", MAX_ORDER_HINT)
        if self.fallback_text is not None:
            require_safe_label(
                self.fallback_text, "fallback_text", MAX_FALLBACK_TEXT_CHARS
            )

    def _validate_segments(self) -> None:
        if not isinstance(self.segments, tuple):
            raise ContractViolation("segments must be a tuple")
        if len(self.segments) > MAX_SEGMENTS_PER_PROVIDER:
            raise ContractViolation(
                f"a provider may contribute at most {MAX_SEGMENTS_PER_PROVIDER} segments"
            )
        for segment in self.segments:
            if not isinstance(segment, Segment):
                raise ContractViolation("every entry in segments must be a Segment")
        keys = [segment.key for segment in self.segments]
        if len(keys) != len(set(keys)):
            raise ContractViolation("segment keys must be unique within a provider")
        self._validate_non_collapse()

    def _validate_non_collapse(self) -> None:
        """Forbid the ways a non-answer gets rendered as a good answer.

        A provider that could not read its state must not claim `OK`, and must
        not report a count: an unavailable daemon reporting `0 would block` is
        a false all-clear, which is the `UNAVAILABLE != ZERO` rule.

        Nor may it report a confidence. A confidence is a claim about a result
        the provider computed, so `unavailable` carrying `high` is incoherent in
        the most misleading direction available. Nor a `duration_seconds`: a
        remaining-work estimate is a reading about live work, and `remaining P90
        5d4h` beside an unreachable daemon is the same false-currency claim a
        count would be.

        `age_seconds` is deliberately still allowed, and it is the one field that
        should be: "last read two hours ago, unavailable now" is a true and
        useful thing to say, and it is how a reader tells a provider that just
        went down from one that was never up.
        """
        if self.availability.has_live_readings:
            return
        for segment in self.segments:
            if segment.state is SegmentState.OK:
                raise ContractViolation(
                    f"availability {self.availability.value!r} cannot carry an "
                    "'ok' segment; unknown is not healthy"
                )
            if segment.count is not None or segment.total is not None:
                raise ContractViolation(
                    f"availability {self.availability.value!r} cannot carry a "
                    "count; an unavailable provider reporting zero is a false "
                    "all-clear"
                )
            if segment.confidence is not None:
                raise ContractViolation(
                    f"availability {self.availability.value!r} cannot carry a "
                    "confidence; a provider that could not read its state has "
                    "no result to be confident about"
                )
            if segment.duration_seconds is not None:
                raise ContractViolation(
                    f"availability {self.availability.value!r} cannot carry a "
                    "duration; an estimate about work in progress cannot be "
                    "current when the state behind it could not be read"
                )

    def _validate_clear_authority(self) -> None:
        """Check a provider-declared Clear projection instead of trusting it.

        Under `ClearAuthority.PROVIDER` the host switches its fallback ladder
        off, so these four rules are the whole guarantee that something still
        comes out — and a declaration the host cannot rely on is worse than no
        declaration, because the ladder it replaced did always produce a primary.

        1. At least one segment. Authority over an empty projection is a claim
           about nothing; the ladder it disables had nothing to run on either.
        2. Every segment declares a role. A payload that declared two of its four
           segments has not decided the projection, and the two it skipped would
           need inferring — which is precisely the merge this mode exists to
           prevent, and the state that cannot be told from "written before the
           field existed".
        3. At most one posture. Two postures is two primaries, and picking
           between them would be the host's editorial judgement again.
        4. A posture or an exception. Without one there is no primary at all, and
           the host would be reduced to showing the first reading and hoping.

        Refusing, rather than falling back, is the point: a `ContractViolation`
        surfaces as *this provider* being unreadable, which is visible and
        fixable. A silent re-inference looks exactly like success.
        """
        if not isinstance(self.clear_authority, ClearAuthority):
            raise ContractViolation("clear_authority must be a ClearAuthority")
        if self.clear_authority is not ClearAuthority.PROVIDER:
            return
        if not self.segments:
            raise ContractViolation(
                "clear_authority 'provider' requires at least one segment; there "
                "is no projection to declare authority over"
            )
        undeclared = [s.key for s in self.segments if s.clear_role is None]
        if undeclared:
            raise ContractViolation(
                "clear_authority 'provider' requires a clear_role on every "
                f"segment; missing on {sorted(undeclared)}"
            )
        postures = [s.key for s in self.segments if s.clear_role is ClearRole.POSTURE]
        if len(postures) > 1:
            raise ContractViolation(
                "clear_authority 'provider' allows at most one 'posture' segment; "
                f"declared on {sorted(postures)}"
            )
        if not postures and not any(
            s.clear_role is ClearRole.EXCEPTION for s in self.segments
        ):
            raise ContractViolation(
                "clear_authority 'provider' requires a 'posture' or 'exception' "
                "segment; without one the projection names no primary reading"
            )

    def to_wire(self) -> dict:
        """Serialise to the JSON-compatible form a provider prints on stdout.

        Omits every field left at its default, so a minimal provider emits a
        minimal payload and a reader can tell "not set" from "set to zero".
        """
        payload: dict = {
            "contract_version": self.contract_version,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "scope": self.scope.value,
            "availability": self.availability.value,
        }
        if self.observed_at is not None:
            payload["observed_at"] = self.observed_at
        if self.cache_ttl_seconds:
            payload["cache_ttl_seconds"] = self.cache_ttl_seconds
        payload["order_hint"] = self.order_hint
        if self.fallback_text is not None:
            payload["fallback_text"] = self.fallback_text
        if self.clear_authority is not ClearAuthority.HOST:
            payload["clear_authority"] = self.clear_authority.value
        if self.segments:
            payload["segments"] = [_segment_to_wire(s) for s in self.segments]
        return payload


_NOT_AVAILABLE_STATE = {
    Availability.UNAVAILABLE: SegmentState.NEUTRAL,
    Availability.UNSUPPORTED: SegmentState.NEUTRAL,
    Availability.UNKNOWN: SegmentState.UNKNOWN,
    Availability.ERROR: SegmentState.WARN,
}


def not_available(
    provider: str,
    provider_version: str,
    scope: Scope,
    availability: Availability,
    reason_code: str,
    reason_label: str,
    *,
    explain_key: str | None = None,
    order_hint: int = 500,
) -> ProviderStatus:
    """Build the status a provider returns when it has no live reading.

    Exists so the four not-available cases are expressed identically by every
    product instead of each inventing its own shape — and so none of them can
    reach for `AVAILABLE` with an empty segment list, which renders as silence
    and reads as "all clear". `ERROR` maps to a `warn` segment because a failed
    probe is something the user may need to act on; the other three are
    `neutral`/`unknown`, which state a fact without claiming health.

    The reason is a bounded code plus a short prose label, never a raw error
    string: an exception message routinely carries a path, a URL or a command
    line, and this value is rendered into the user's terminal.
    """
    if not isinstance(availability, Availability):
        raise ContractViolation("availability must be an Availability")
    if availability.has_live_readings:
        raise ContractViolation(
            "not_available() cannot build an available status; construct "
            "ProviderStatus directly with the readings you actually have"
        )
    return ProviderStatus(
        provider=provider,
        provider_version=provider_version,
        scope=scope,
        availability=availability,
        segments=(
            Segment(
                key="availability",
                state=_NOT_AVAILABLE_STATE[availability],
                label=reason_label,
                reason_code=reason_code,
                explain_key=explain_key,
            ),
        ),
        order_hint=order_hint,
    )


def _segment_to_wire(segment: Segment) -> dict:
    """Serialise one segment, omitting unset optional fields."""
    payload: dict = {
        "key": segment.key,
        "state": segment.state.value,
        "label": segment.label,
    }
    for name in (
        "reason_code",
        "reason_label",
        "count_label",
        "duration_label",
        "explain_key",
    ):
        value = getattr(segment, name)
        if value is not None:
            payload[name] = value
    for name in ("age_seconds", "count", "total", "duration_seconds", "fresh_for_seconds"):
        value = getattr(segment, name)
        if value is not None:
            payload[name] = value
    if segment.confidence is not None:
        payload["confidence"] = segment.confidence.value
        # Never emitted alone; `Segment` already refuses the split pair.
        payload["confidence_of"] = segment.confidence_of.value  # type: ignore[union-attr]
    if segment.hypothetical:
        payload["hypothetical"] = True
    if segment.order_hint:
        payload["order_hint"] = segment.order_hint
    if segment.clear_role is not None:
        payload["clear_role"] = segment.clear_role.value
    return payload


def order_providers(statuses: object) -> tuple[ProviderStatus, ...]:
    """Order providers for rendering: `order_hint` first, then provider id.

    The statusline is re-rendered on a timer, so a non-deterministic order is
    a visible defect — segments would appear to shuffle while nothing changed.
    Sorting is total (the tie-break is the provider id, which is unique here),
    and `sorted` is stable, so equal keys keep their input order.

    A duplicate provider id is refused rather than deduplicated: two answers
    from one provider means the registry is wrong, and silently picking one
    would hide that while rendering a possibly stale reading.
    """
    if isinstance(statuses, (str, bytes)) or not hasattr(statuses, "__iter__"):
        raise ContractViolation("order_providers() takes an iterable of ProviderStatus")
    ordered = tuple(statuses)
    for status in ordered:
        if not isinstance(status, ProviderStatus):
            raise ContractViolation("order_providers() takes ProviderStatus values")
    seen: set[str] = set()
    for status in ordered:
        if status.provider in seen:
            raise ContractViolation(
                f"provider {status.provider!r} answered more than once; the "
                "registry has a duplicate entry"
            )
        seen.add(status.provider)
    return tuple(sorted(ordered, key=lambda s: (s.order_hint, s.provider)))


def order_segments(status: ProviderStatus) -> tuple[Segment, ...]:
    """Order one provider's segments by `order_hint`, then key.

    Same determinism requirement as `order_providers`, one level down. Segment
    keys are already unique within a provider, so this needs no duplicate
    check of its own.
    """
    if not isinstance(status, ProviderStatus):
        raise ContractViolation("order_segments() takes a ProviderStatus")
    return tuple(sorted(status.segments, key=lambda s: (s.order_hint, s.key)))


def _parse_enum_strict(enum_cls: type, value: object, field: str):
    """Parse an enum value, refusing anything unrecognised.

    Used where there is no honest "I don't recognise this" member, so guessing
    would silently mean something specific and wrong. `scope` is the case that
    matters: an unrecognised scope must never land on `host` or `session`,
    because that is how host-wide state starts rendering as session-scoped.
    """
    for member in enum_cls:
        if member.value == value:
            return member
    allowed = ", ".join(sorted(m.value for m in enum_cls))
    raise ContractViolation(f"{field} must be one of: {allowed}")


def _parse_enum_lenient(enum_cls: type, value: object, field: str, unknown):
    """Parse an enum value, mapping anything unrecognised to `unknown`.

    Used where the enum has an explicit unknown member, so a provider from a
    future contract minor version degrades to an honest "not recognised"
    instead of failing the whole render. This is not collapsing: `unknown` is
    the truthful reading of a state this host has never heard of.
    """
    if value is None:
        raise ContractViolation(f"{field} is required")
    for member in enum_cls:
        if member.value == value:
            return member
    return unknown


def _parse_clear_role(value: object) -> ClearRole | None:
    """Parse an optional `clear_role`, treating anything unrecognised as undeclared.

    A third parsing policy beside the strict and lenient ones above, because
    neither is right here. Strict would reject a whole provider payload over a
    presentation hint, which is a health claim lost to a cosmetic field. Lenient
    needs an `unknown` member to degrade to, and this enum deliberately has none:
    every member is a real editorial judgement, so a made-up one would either
    promote a routine reading to an exception or hide one that matters.

    Undeclared is already a state the host handles correctly, with a documented
    default, so an unrecognised value resolves to exactly that.
    """
    if value is None:
        return None
    for member in ClearRole:
        if member.value == value:
            return member
    return None


def _parse_clear_authority(value: object) -> ClearAuthority:
    """Parse `clear_authority`, degrading anything unrecognised to `HOST`.

    A fourth value this host has never heard of describes a selection policy it
    cannot carry out, so the one honest answer is to use the policy it does have.
    `HOST` is a documented, complete fallback rather than a shrug, which is why
    degrading here is safe in a way degrading a *malformed* `PROVIDER` payload
    would not be: that one is a provider claiming this exact contract and failing
    it, so it is refused.
    """
    if value is None:
        return ClearAuthority.HOST
    for member in ClearAuthority:
        if member.value == value:
            return member
    return ClearAuthority.HOST


def _segment_from_wire(payload: object, index: int) -> Segment:
    """Parse one segment, ignoring fields this contract version does not know."""
    if not isinstance(payload, dict):
        raise ContractViolation(f"segments[{index}] must be an object")
    field = f"segments[{index}]"
    confidence_raw = payload.get("confidence")
    confidence = (
        _parse_enum_strict(Confidence, confidence_raw, f"{field}.confidence")
        if confidence_raw is not None
        else None
    )
    confidence_of = (
        _parse_enum_lenient(
            ConfidenceSubject,
            payload.get("confidence_of"),
            f"{field}.confidence_of",
            ConfidenceSubject.UNSPECIFIED,
        )
        if confidence is not None
        else None
    )
    return Segment(
        key=payload.get("key"),
        state=_parse_enum_lenient(
            SegmentState, payload.get("state"), f"{field}.state", SegmentState.UNKNOWN
        ),
        label=payload.get("label"),
        reason_code=payload.get("reason_code"),
        reason_label=payload.get("reason_label"),
        confidence=confidence,
        confidence_of=confidence_of,
        age_seconds=payload.get("age_seconds"),
        count=payload.get("count"),
        total=payload.get("total"),
        count_label=payload.get("count_label"),
        duration_seconds=payload.get("duration_seconds"),
        duration_label=payload.get("duration_label"),
        hypothetical=bool(payload.get("hypothetical", False)),
        explain_key=payload.get("explain_key"),
        order_hint=payload.get("order_hint", 0),
        clear_role=_parse_clear_role(payload.get("clear_role")),
        fresh_for_seconds=payload.get("fresh_for_seconds"),
    )


def provider_status_from_wire(payload: object) -> ProviderStatus:
    """Parse a provider's JSON payload into a validated `ProviderStatus`.

    Forward compatibility is deliberate and narrow: unknown *fields* are
    ignored, and an unknown value for an enum that has an `unknown` member
    degrades to that member. Everything else — an unknown `contract_version`,
    an unrecognised `scope`, a missing required field — is refused, so a
    future provider can add information without a host silently inventing a
    meaning for information it does not understand.

    One consequence is deliberate: an unrecognised `availability` degrades to
    `unknown`, and the non-collapse rule then refuses any `ok` segment or any
    count in the same payload. That rejects the whole provider rather than
    rendering a health claim this host cannot stand behind — a caller that
    catches `ContractViolation` and renders the provider as unknown is
    truthful; one that had accepted the `ok` would not be.
    """
    if not isinstance(payload, dict):
        raise ContractViolation("provider payload must be a JSON object")
    version = payload.get("contract_version")
    if not is_supported_contract_version(version):
        raise UnsupportedContractVersion(
            f"contract_version {version!r} is not supported by this host "
            f"(supported: {sorted(SUPPORTED_CONTRACT_VERSIONS)})"
        )
    raw_segments = payload.get("segments", [])
    if not isinstance(raw_segments, list):
        raise ContractViolation("segments must be a list")
    return ProviderStatus(
        provider=payload.get("provider"),
        provider_version=payload.get("provider_version"),
        scope=_parse_enum_strict(Scope, payload.get("scope"), "scope"),
        availability=_parse_enum_lenient(
            Availability,
            payload.get("availability"),
            "availability",
            Availability.UNKNOWN,
        ),
        segments=tuple(
            _segment_from_wire(s, i) for i, s in enumerate(raw_segments)
        ),
        observed_at=payload.get("observed_at"),
        cache_ttl_seconds=payload.get("cache_ttl_seconds", 0),
        order_hint=payload.get("order_hint", 500),
        fallback_text=payload.get("fallback_text"),
        clear_authority=_parse_clear_authority(payload.get("clear_authority")),
        contract_version=version,
    )
