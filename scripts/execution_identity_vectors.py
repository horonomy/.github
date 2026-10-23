#!/usr/bin/env python3
"""Language-neutral conformance vectors for the execution identity contract (HORO-1599).

Fornax and Libra (Rust) and Circinus (Python) each implement the contract in
their own language, so Python-only tests prove nothing about their code. This
module builds `governance/product/execution-identity-conformance-vectors.json`
from the reference implementation: a product loads that file, runs its own
implementation over every case, and compares. The hand-written inputs live
here; the expected outputs are generated, and `test_execution_identity_vectors.py`
pins both that the committed file equals a fresh build (no drift) and a few
literal expectations written independently of the reference (so a reference bug
is not silently blessed).

Regenerate:  python3 scripts/execution_identity_vectors.py
"""

from __future__ import annotations

import datetime
import json
import pathlib

import execution_identity as ei
import execution_identity_normalize as norm

VECTORS_PATH = pathlib.Path(__file__).resolve().parent.parent / "governance" / "product" / "execution-identity-conformance-vectors.json"
VECTORS_VERSION = 1
_OBSERVED = "2026-10-24T12:00:00.000Z"
_NOW = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.timezone.utc)

_SCOPES = {
    "host": ei.Scope.HOST,
    "session": ei.Scope.SESSION,
    "agent": ei.Scope.AGENT,
    "turn_task": ei.Scope.TURN_TASK,
    "project_worktree": ei.Scope.PROJECT_WORKTREE,
}
_DISPLAY_DIMENSIONS = ("provider_session_id", "agent_id", "turn_id", "host_id")


def _wire(**fields: object) -> dict:
    base: dict = {
        "envelope_version": 1,
        "observed_at": _OBSERVED,
        "host_id": "host-1",
        "tool_provider": "claude_code",
        "lineage_status": "unknown",
    }
    base.update(fields)
    return base


# (name, wire document) — each is a valid envelope.
VALID = [
    ("host-only", _wire()),
    ("session", _wire(provider_session_id="s-1")),
    ("agent", _wire(provider_session_id="s-1", agent_id="a-1")),
    ("turn-task", _wire(provider_session_id="s-1", agent_id="a-1", turn_id="t-1")),
    ("turn-without-agent", _wire(tool_provider="codex", provider_session_id="s-1", turn_id="t-1")),
    ("agent-without-session", _wire(agent_id="a-1")),
    ("child-with-parent", _wire(provider_session_id="s-1", agent_id="a-2", lineage_status="child", parent_agent_id="a-1")),
    ("root", _wire(provider_session_id="s-1", agent_id="a-1", lineage_status="root")),
    ("repo-only-context", _wire(repo_id="repo-1")),
    ("worktree-over-repo", _wire(repo_id="repo-1", worktree_id="wt-1")),
    ("verbatim-unicode-and-padding", _wire(provider_session_id=" Sess/ID:ü-1  ", agent_id="a:b/c")),
    ("unknown-field-ignored", _wire(provider_session_id="s-1", cost_center_id="cc-9")),
]

# (name, document) — each must be refused by `from_wire`.
INVALID = [
    ("version-2", _wire(envelope_version=2)),
    ("version-string", _wire(envelope_version="1")),
    ("version-bool", _wire(envelope_version=True)),
    ("unknown-lineage-status", _wire(lineage_status="orphan")),
    ("child-without-parent", _wire(lineage_status="child")),
    ("root-with-parent", _wire(lineage_status="root", parent_agent_id="a-1")),
    ("unknown-with-parent", _wire(parent_agent_id="a-1")),
    ("empty-host-id", _wire(host_id="")),
    ("uppercase-provider", _wire(tool_provider="Claude")),
    ("provider-with-path", _wire(tool_provider="../x")),
    ("session-not-a-string", _wire(provider_session_id=7)),
    ("bad-observed-at", _wire(observed_at="2026-10-24 12:00:00")),
]

# (name, left-name, right-name) over VALID names — the expected result is generated.
CORRELATIONS = [
    ("same-session", "session", "session"),
    ("host-only-vs-host-only", "host-only", "host-only"),
    ("session-vs-agent", "session", "agent"),
    ("agent-vs-turn", "agent", "turn-task"),
    ("claude-vs-codex-session", "session", "turn-without-agent"),
]

# (name, provider, payload) — raw provider hook payloads for the normalizers.
PAYLOADS = [
    ("claude-main", "claude_code", {"session_id": "s-1", "hook_event_name": "PostToolUse", "cwd": "/w", "prompt_id": "p-1"}),
    ("claude-subagent", "claude_code", {"session_id": "s-1", "agent_id": "a-9", "agent_type": "general-purpose"}),
    ("claude-turn-id-not-mapped", "claude_code", {"session_id": "s-1", "turn_id": "t-1"}),
    ("claude-no-session", "claude_code", {"cwd": "/w", "agent_id": "a-1"}),
    ("claude-blank-session", "claude_code", {"session_id": "   "}),
    ("claude-non-string-session", "claude_code", {"session_id": 123}),
    ("claude-not-a-mapping", "claude_code", ["s-1"]),
    ("codex-main", "codex", {"session_id": "c-1", "turn_id": "t-7"}),
    ("codex-subagent", "codex", {"session_id": "c-1", "turn_id": "t-8", "agent_id": "a-2"}),
    ("codex-heuristics-only", "codex", {"cwd": "/w", "transcript_path": "/t", "pid": 42}),
]


def _identity(document: dict) -> ei.ExecutionIdentity:
    return ei.ExecutionIdentity.from_wire(document)


def _keys(identity: ei.ExecutionIdentity) -> dict:
    out: dict = {}
    for name, scope in _SCOPES.items():
        try:
            out[name] = list(identity.cache_key(scope))
        except ei.ScopeIdentityMissing:
            out[name] = None
    return out


def _display(identity: ei.ExecutionIdentity) -> dict:
    out: dict = {}
    for dimension in _DISPLAY_DIMENSIONS:
        if getattr(identity, dimension, None):
            out[dimension] = identity.display_id(dimension)
    return out


def _normalizer(provider: str):
    return norm.normalize_claude_code_hook if provider == "claude_code" else norm.normalize_codex_hook


def build() -> dict:
    valid = []
    by_name = {}
    for name, document in VALID:
        identity = _identity(document)
        by_name[name] = identity
        valid.append({
            "name": name,
            "wire": document,
            "expect": {
                "valid": True,
                "round_trip": identity.to_wire(),
                "cache_keys": _keys(identity),
                "display_ids": _display(identity),
                "narrowest_attributable_scope": norm.narrowest_attributable_scope(identity).value,
            },
        })
    invalid = []
    for name, document in INVALID:
        try:
            _identity(document)
        except (ValueError, TypeError, KeyError):
            invalid.append({"name": name, "wire": document, "expect": {"valid": False}})
        else:
            raise AssertionError(f"vector {name} unexpectedly parsed")
    correlations = [
        {"name": name, "left": left, "right": right, "expect": {"correlates": by_name[left].correlates_with(by_name[right])}}
        for name, left, right in CORRELATIONS
    ]
    normalizers = []
    for name, provider, payload in PAYLOADS:
        identity = _normalizer(provider)(payload, host_id="host-1", observed_at=_NOW)
        normalizers.append({
            "name": name,
            "provider": provider,
            "host_id": "host-1",
            "payload": payload,
            "expect": {
                "provider_session_id": identity.provider_session_id,
                "agent_id": identity.agent_id,
                "turn_id": identity.turn_id,
                "lineage_status": identity.lineage_status.value,
                "narrowest_attributable_scope": norm.narrowest_attributable_scope(identity).value,
            },
        })
    return {
        "vectors_version": VECTORS_VERSION,
        "contract": "governance/product/execution-identity-contract.md",
        "display_id": "dimension + ':' + first 8 hex of sha256(utf8(dimension + ':' + value))",
        "cache_key_null": "null means ScopeIdentityMissing: the implementation must refuse, never widen to a broader key",
        "valid": valid,
        "invalid": invalid,
        "correlations": correlations,
        "normalizers": normalizers,
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    VECTORS_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {VECTORS_PATH}")
