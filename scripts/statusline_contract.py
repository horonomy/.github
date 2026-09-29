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
