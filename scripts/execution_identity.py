#!/usr/bin/env python3
"""Shared Horonom execution identity contract (HORO-1597/1598).

Names *which host, session, agent, turn and event* a piece of state belongs
to, so a host-wide fact can never be presented or stored as if it belonged to
a specific concurrent session or agent. This module is the reference
implementation of
`governance/product/execution-identity-contract.md` — read that document
first; this module's docstrings reference it rather than restate it.

Stdlib only, matching the rest of `scripts/`. Deliberately not a shared
library across products: Fornax and Libra are Rust, Circinus is Python, so
each product implements its own capturer in its own language against the
contract document, the same split `statusline_contract.py` uses.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import hashlib
import re

# The envelope version a capturer must declare. Bumped only for a
# non-backward-compatible change; additive optional fields do not bump it.
# Full evolution rules: execution-identity-contract.md#version-evolution.
ENVELOPE_VERSION = 1

SUPPORTED_ENVELOPE_VERSIONS = frozenset({1})

_TOOL_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Recommended, non-exhaustive — tool_provider is an open string, not a closed
# enum, so a new provider is never a contract version bump.
KNOWN_TOOL_PROVIDERS = frozenset({"claude_code", "codex"})


def is_supported_envelope_version(version: object) -> bool:
    """Whether `version` is an envelope version this reader can parse.

    Strict about type on purpose: a capturer emitting the version as a
    string (`"1"`) has a real bug, and coercing it would let a future
    incompatible envelope be mis-parsed as v1.
    """
    return isinstance(version, int) and not isinstance(version, bool) and version in SUPPORTED_ENVELOPE_VERSIONS


def is_valid_tool_provider(value: object) -> bool:
    """Whether `value` is syntactically a legal `tool_provider` string.

    Deliberately permissive about *which* provider (open-ended, see module
    docstring) and strict about *shape* (bounded, lowercase, no path/shell
    metacharacters — this string reaches logs and cache keys).
    """
    return isinstance(value, str) and bool(_TOOL_PROVIDER_PATTERN.match(value))


class Scope(enum.Enum):
    """The smallest truthful dimension that owns one datum.

    Distinct from (and not interchangeable with) the statusline contract's
    `Scope` — see execution-identity-contract.md's "Relationship to the
    statusline contract's scope". In particular this enum has a legal,
    permanent `UNKNOWN` member because pre-attribution legacy data is a
    real, permanent case here, unlike the statusline contract's `scope`.
    """

    HOST = "host"
    SESSION = "session"
    AGENT = "agent"
    TURN_TASK = "turn_task"
    PROJECT_WORKTREE = "project_worktree"
    UNKNOWN = "unknown"


class LineageStatus(enum.Enum):
    """Whether an agent's parent relationship is proven, absent, or unknown.

    Not a nullable `parent_agent_id`: `None` would be ambiguous between "this
    agent is the root" and "the provider doesn't tell us". Collapsing those
    is the exact heuristic-joining failure this contract exists to prevent —
    see execution-identity-contract.md's "Agent lineage" section and
    HORO-1597's `agent_id known, parent_agent_id unavailable` example.
    """

    ROOT = "root"
    CHILD = "child"
    UNKNOWN = "unknown"


class ScopeIdentityMissing(ValueError):
    """Raised by `cache_key()` when the envelope lacks identity for a scope.

    Never caught to silently fall back to a broader scope's key — that
    fallback is the specific anti-pattern
    (`latest:<host_id>` for session-local state) HORO-1597 names explicitly.
    """


def _require_opaque(name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, got {type(value).__name__}")


@dataclasses.dataclass(frozen=True)
class ExecutionIdentity:
    """One instant's execution identity envelope.

    Every optional field's *absence* means "not known for this record", never
    "does not apply" and never a zero/empty-string stand-in. See
    execution-identity-contract.md for the full field table and the
    opacity/redaction/correlation rules this type implements below.
    """

    envelope_version: int
    observed_at: datetime.datetime

    # Host (Horonom-generated) — required.
    host_id: str

    # Provider identity (provider-native, opaque, verbatim).
    tool_provider: str
    tool_instance_id: str | None = None
    provider_session_id: str | None = None
    agent_id: str | None = None
    turn_id: str | None = None

    # Agent lineage (provider-native, explicit tri-state).
    lineage_status: LineageStatus = LineageStatus.UNKNOWN
    parent_agent_id: str | None = None

    # Horonom-generated correlation.
    session_lineage_id: str | None = None
    event_id: str | None = None

    # Context — never a substitute for the identity fields above.
    repo_id: str | None = None
    worktree_id: str | None = None

    def __post_init__(self) -> None:
        if not is_supported_envelope_version(self.envelope_version):
            raise ValueError(f"unsupported envelope_version: {self.envelope_version!r}")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware (UTC)")
        if self.observed_at.utcoffset() != datetime.timedelta(0):
            raise ValueError("observed_at must be UTC")
        if not isinstance(self.host_id, str) or not self.host_id:
            raise ValueError("host_id is required and must be a non-empty str")
        if not is_valid_tool_provider(self.tool_provider):
            raise ValueError(f"invalid tool_provider: {self.tool_provider!r}")
        for name in (
            "tool_instance_id",
            "provider_session_id",
            "agent_id",
            "turn_id",
            "parent_agent_id",
            "session_lineage_id",
            "event_id",
            "repo_id",
            "worktree_id",
        ):
            _require_opaque(name, getattr(self, name))

        if self.lineage_status is LineageStatus.CHILD and not self.parent_agent_id:
            raise ValueError("lineage_status=CHILD requires a non-empty parent_agent_id")
        if self.lineage_status is not LineageStatus.CHILD and self.parent_agent_id is not None:
            raise ValueError("parent_agent_id must be absent unless lineage_status=CHILD")

    def correlates_with(self, other: "ExecutionIdentity") -> bool:
        """Whether `self` and `other` describe the same identity position.

        Ignores `observed_at` and `event_id` (those vary per observation of
        the same position). Two envelopes with the same field set to `None`
        on both sides are **not** treated as matching on that field — "we
        don't know for either of them" must never collapse into "therefore
        they're the same." At least one provider-identity or lineage field
        must be concretely and identically populated on both sides.
        """
        comparable_fields = (
            "host_id",
            "tool_provider",
            "tool_instance_id",
            "provider_session_id",
            "agent_id",
            "turn_id",
            "parent_agent_id",
            "session_lineage_id",
        )
        any_concrete_match = False
        for name in comparable_fields:
            left = getattr(self, name)
            right = getattr(other, name)
            if left is None or right is None:
                continue
            if left != right:
                return False
            any_concrete_match = True
        return any_concrete_match

    def display_id(self, dimension: str) -> str:
        """A short, deterministic, non-reversible token for display/logs.

        Never used for correlation or cache keys — see
        execution-identity-contract.md#redaction. `dimension` is one of this
        envelope's own field names, included in the output so two different
        dimensions holding the same raw string never render identically.
        """
        raw = getattr(self, dimension, None)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"{dimension} has no value to redact")
        digest = hashlib.sha256(f"{dimension}:{raw}".encode("utf-8")).hexdigest()[:8]
        return f"{dimension}:{digest}"

    def cache_key(self, scope: Scope) -> tuple[str, ...]:
        """The minimal identity tuple that `scope`'s data is keyed on.

        Raises `ScopeIdentityMissing` rather than falling back to a broader
        scope's key — see execution-identity-contract.md#cache-keys-never-
        widen-on-their-own.
        """
        if scope is Scope.HOST:
            return (self.host_id,)
        if scope is Scope.SESSION:
            if not self.provider_session_id:
                raise ScopeIdentityMissing("SESSION scope requires provider_session_id")
            return (self.host_id, self.tool_provider, self.provider_session_id)
        if scope is Scope.AGENT:
            if not self.provider_session_id or not self.agent_id:
                raise ScopeIdentityMissing("AGENT scope requires provider_session_id and agent_id")
            return (self.host_id, self.tool_provider, self.provider_session_id, self.agent_id)
        if scope is Scope.TURN_TASK:
            if not self.provider_session_id or not self.agent_id or not self.turn_id:
                raise ScopeIdentityMissing("TURN_TASK scope requires provider_session_id, agent_id, and turn_id")
            return (self.host_id, self.tool_provider, self.provider_session_id, self.agent_id, self.turn_id)
        if scope is Scope.PROJECT_WORKTREE:
            if self.worktree_id:
                return (self.worktree_id,)
            if self.repo_id:
                return (self.repo_id,)
            raise ScopeIdentityMissing("PROJECT_WORKTREE scope requires worktree_id or repo_id")
        raise ScopeIdentityMissing(f"{scope} has no stable identity-keyed cache key by definition")

    def to_wire(self) -> dict:
        """Serialize to a JSON-compatible dict using wire field names."""
        out: dict = {
            "envelope_version": self.envelope_version,
            "observed_at": self.observed_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "host_id": self.host_id,
            "tool_provider": self.tool_provider,
            "lineage_status": self.lineage_status.value,
        }
        optional_fields = (
            "tool_instance_id",
            "provider_session_id",
            "agent_id",
            "turn_id",
            "parent_agent_id",
            "session_lineage_id",
            "event_id",
            "repo_id",
            "worktree_id",
        )
        for name in optional_fields:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out

    @staticmethod
    def from_wire(document: dict) -> "ExecutionIdentity":
        """Parse a wire document, per execution-identity-contract.md's
        version-evolution table: unknown top-level fields are ignored,
        unrecognised `Scope`/`lineage_status` values and an unknown
        `envelope_version` are refused (not guessed).
        """
        version = document.get("envelope_version")
        if not is_supported_envelope_version(version):
            raise ValueError(f"unsupported envelope_version: {version!r}")

        raw_lineage = document.get("lineage_status", LineageStatus.UNKNOWN.value)
        try:
            lineage_status = LineageStatus(raw_lineage)
        except ValueError as exc:
            raise ValueError(f"unrecognised lineage_status: {raw_lineage!r}") from exc

        observed_at_raw = document["observed_at"]
        observed_at = datetime.datetime.strptime(observed_at_raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=datetime.timezone.utc
        )

        known_fields = {
            "envelope_version",
            "observed_at",
            "host_id",
            "tool_provider",
            "tool_instance_id",
            "provider_session_id",
            "agent_id",
            "turn_id",
            "lineage_status",
            "parent_agent_id",
            "session_lineage_id",
            "event_id",
            "repo_id",
            "worktree_id",
        }
        # Unknown top-level fields are ignored by construction: anything not
        # named below is simply never read out of `document`.
        del known_fields

        return ExecutionIdentity(
            envelope_version=version,
            observed_at=observed_at,
            host_id=document["host_id"],
            tool_provider=document["tool_provider"],
            tool_instance_id=document.get("tool_instance_id"),
            provider_session_id=document.get("provider_session_id"),
            agent_id=document.get("agent_id"),
            turn_id=document.get("turn_id"),
            lineage_status=lineage_status,
            parent_agent_id=document.get("parent_agent_id"),
            session_lineage_id=document.get("session_lineage_id"),
            event_id=document.get("event_id"),
            repo_id=document.get("repo_id"),
            worktree_id=document.get("worktree_id"),
        )


def host_only_scope_for_legacy_record() -> Scope:
    """The scope a pre-contract, host-only historical record is assigned.

    Never `Scope.SESSION`/`Scope.AGENT` — see
    execution-identity-contract.md#backward-compatibility. A caller that
    cannot even reconstruct host attribution for an old record should use
    `Scope.UNKNOWN` directly rather than call this function.
    """
    return Scope.HOST
