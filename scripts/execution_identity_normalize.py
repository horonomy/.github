#!/usr/bin/env python3
"""Raw provider hook payload -> `ExecutionIdentity` (HORO-1599).

The pure, host-owned step between "a provider handed a product a JSON hook
payload" and "a product holds a conforming envelope". Fornax, Circinus and
Libra each used to read `session_id`/`agent_id`/`turn_id` out of the payload
on their own, and the audits found them disagreeing about which provider
exposes what (and one of them fabricating `unknown-session`). This module is
the single reading of those payloads, expressed against
`governance/product/execution-identity-contract.md`; a product in another
language mirrors its rules and its golden cases in
`test_execution_identity_normalize.py`.

What is read is limited to fields a provider has been observed (or, for
Claude Code `turn_id`, has not been observed) to send; see each function.
Anything else in the payload — `cwd`, `transcript_path`, `prompt_id`, process
ids — is deliberately ignored: those are the heuristics the contract forbids
as identity.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any

import execution_identity as ei


def _native(payload: Any, key: str) -> str | None:
    """The provider's own value for `key`, verbatim, or None.

    None for a non-mapping payload, a missing key, a non-string, or an empty/
    whitespace-only string. A malformed value is *unknown*, never coerced
    (`str(123)`) and never replaced by a placeholder: a coerced or invented id
    would let two sessions that both lack one share a key.
    """
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def normalize_claude_code_hook(
    payload: Any, *, host_id: str, observed_at: datetime.datetime
) -> ei.ExecutionIdentity:
    """Envelope for a Claude Code hook payload.

    `session_id` -> `provider_session_id`. `agent_id` is present only on a
    Task subagent's events (live-observed on PostToolUse, FORNX-433 AC3 Step
    0, 2026-10-11) and absent for the main session. `turn_id` is **not**
    mapped: no capture shows Claude Code sending one, and `prompt_id`, which
    it does send, has not been shown to be a turn identity.
    """
    return ei.ExecutionIdentity(
        envelope_version=ei.ENVELOPE_VERSION,
        observed_at=observed_at,
        host_id=host_id,
        tool_provider="claude_code",
        provider_session_id=_native(payload, "session_id"),
        agent_id=_native(payload, "agent_id"),
        lineage_status=ei.LineageStatus.UNKNOWN,
    )


def normalize_codex_hook(
    payload: Any, *, host_id: str, observed_at: datetime.datetime
) -> ei.ExecutionIdentity:
    """Envelope for a Codex hook payload.

    `session_id`, `turn_id` and (subagent events only) `agent_id` are the
    payload fields the HORO-1711 probe lists for Codex 0.160.0. Whether a
    subagent's `session_id` equals its parent's is not established from a
    live capture, so the value is copied as sent and the pair, not the
    session alone, names a subagent.
    """
    return ei.ExecutionIdentity(
        envelope_version=ei.ENVELOPE_VERSION,
        observed_at=observed_at,
        host_id=host_id,
        tool_provider="codex",
        provider_session_id=_native(payload, "session_id"),
        agent_id=_native(payload, "agent_id"),
        turn_id=_native(payload, "turn_id"),
        lineage_status=ei.LineageStatus.UNKNOWN,
    )


def narrowest_attributable_scope(identity: ei.ExecutionIdentity) -> ei.Scope:
    """The narrowest `Scope` `identity` can key a datum on, else `UNKNOWN`.

    Never `HOST`: `host_id` is always present, so reaching `HOST` from "no
    session id" would turn exactly the case the contract calls unattributable
    into a confident host-wide claim. `HOST` is a scope a datum's owner
    declares for genuinely host-wide state, not one inferred from a missing
    session.
    """
    for scope in (ei.Scope.TURN_TASK, ei.Scope.AGENT, ei.Scope.SESSION):
        try:
            identity.cache_key(scope)
        except ei.ScopeIdentityMissing:
            continue
        return scope
    return ei.Scope.UNKNOWN
