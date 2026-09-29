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

import enum
import re

# The wire contract version a provider must declare. Bumped only when a
# change is not backward compatible for an existing host; additive optional
# fields do not bump it (see `docs/` and `is_supported_contract_version`).
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


def require_label(value: object, field: str = "label") -> str:
    """Validate a short human label against a strict character allowlist.

    Rejects emoji as well as separators: the shared host owns iconography, so
    a provider embedding its own glyph would defeat consistent rendering
    across products. Emoji are `Symbol, other` and so are not alphanumeric,
    which is why the allowlist check catches them without an emoji table.
    """
    if not isinstance(value, str):
        raise ContractViolation(f"{field} must be a string")
    if not value:
        raise ContractViolation(f"{field} must not be empty")
    if len(value) > MAX_LABEL_CHARS:
        raise ContractViolation(f"{field} must be at most {MAX_LABEL_CHARS} characters")
    for char in value:
        if not char.isalnum() and char not in _LABEL_EXTRA_CHARS:
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


def require_safe_label(value: object, field: str) -> str:
    """Validate a human label for both shape and privacy."""
    return assert_privacy_safe(require_label(value, field), field)


# A segment is one fact. A provider showing more than a handful of them is
# competing with the other products for a single shared line.
MAX_SEGMENTS_PER_PROVIDER = 4

# Upper bounds on the numeric fields, so a provider cannot push an arbitrary
# integer through the contract and break layout.
MAX_COUNT = 10**9
MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60


def require_bounded_int(value: object, field: str, maximum: int) -> int:
    """Validate a non-negative integer within an explicit upper bound."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractViolation(f"{field} must be an integer")
    if value < 0:
        raise ContractViolation(f"{field} must not be negative")
    if value > maximum:
        raise ContractViolation(f"{field} must be at most {maximum}")
    return value
