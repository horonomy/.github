#!/usr/bin/env python3
"""Tests for raw-payload -> ExecutionIdentity normalization (HORO-1599).

Payload shapes below are *recorded shapes*, not captures made by this repo:
Claude Code subagent `agent_id` is from the FORNX-433 AC3 Step 0 live
verification (fornax-core docs/research/adapter-capability-matrix.md,
2026-10-11); Codex `session_id`/`turn_id`/`agent_id` from the HORO-1711 probe
matrix (codex-cli 0.160.0, 2026-10-12). A real capture replaces them without
changing the assertions.

Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import datetime
import unittest

import execution_identity as ei
import execution_identity_normalize as norm

NOW = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.timezone.utc)
HOST = "host-abc123"


def claude(payload):
    return norm.normalize_claude_code_hook(payload, host_id=HOST, observed_at=NOW)


def codex(payload):
    return norm.normalize_codex_hook(payload, host_id=HOST, observed_at=NOW)


class ClaudeCodeTest(unittest.TestCase):
    def test_main_session_has_session_but_no_agent(self) -> None:
        ident = claude({"session_id": "s-1", "hook_event_name": "PostToolUse", "cwd": "/w"})
        self.assertEqual(ident.provider_session_id, "s-1")
        self.assertIsNone(ident.agent_id)
        self.assertEqual(norm.narrowest_attributable_scope(ident), ei.Scope.SESSION)

    def test_subagent_event_carries_agent_id(self) -> None:
        ident = claude({"session_id": "s-1", "agent_id": "a-9", "agent_type": "general-purpose"})
        self.assertEqual((ident.provider_session_id, ident.agent_id), ("s-1", "a-9"))
        self.assertEqual(norm.narrowest_attributable_scope(ident), ei.Scope.AGENT)

    def test_agent_id_does_not_prove_lineage(self) -> None:
        ident = claude({"session_id": "s-1", "agent_id": "a-9"})
        self.assertIs(ident.lineage_status, ei.LineageStatus.UNKNOWN)
        self.assertIsNone(ident.parent_agent_id)

    def test_turn_id_and_prompt_id_are_not_mapped(self) -> None:
        ident = claude({"session_id": "s-1", "turn_id": "t-1", "prompt_id": "p-1"})
        self.assertIsNone(ident.turn_id)


class CodexTest(unittest.TestCase):
    def test_session_and_turn(self) -> None:
        ident = codex({"session_id": "c-1", "turn_id": "t-7"})
        self.assertEqual((ident.provider_session_id, ident.turn_id), ("c-1", "t-7"))
        self.assertIsNone(ident.agent_id)
        # TURN_TASK keying needs an agent too, so a main-agent turn is session-scoped.
        self.assertEqual(norm.narrowest_attributable_scope(ident), ei.Scope.SESSION)

    def test_subagent_with_shared_session_is_keyed_by_the_agent_pair(self) -> None:
        # Fixture assumption (not a live capture): the subagent reuses the parent's session_id.
        # Holds either way because the AGENT key includes agent_id.
        parent = codex({"session_id": "c-1", "turn_id": "t-7"})
        child = codex({"session_id": "c-1", "turn_id": "t-8", "agent_id": "a-2"})
        self.assertEqual(child.provider_session_id, parent.provider_session_id)
        self.assertNotEqual(child.cache_key(ei.Scope.AGENT), parent.cache_key(ei.Scope.SESSION))
        self.assertEqual(norm.narrowest_attributable_scope(child), ei.Scope.TURN_TASK)
        self.assertIs(child.lineage_status, ei.LineageStatus.UNKNOWN)


class AgentIdWithoutSessionTest(unittest.TestCase):
    def test_agent_id_alone_is_unattributable(self) -> None:
        for normalize in (claude, codex):
            ident = normalize({"agent_id": "a-1"})
            self.assertEqual(ident.agent_id, "a-1")
            self.assertEqual(norm.narrowest_attributable_scope(ident), ei.Scope.UNKNOWN)


class UnknownStaysUnknownTest(unittest.TestCase):
    BAD = [None, [], "s-1", 7, {}, {"session_id": ""}, {"session_id": "   "}, {"session_id": 123},
           {"session_id": ["s-1"]}, {"session_id": None}, {"sessionId": "s-1"}]

    def test_malformed_payloads_yield_no_session_and_unknown_scope(self) -> None:
        for normalize in (claude, codex):
            for payload in self.BAD:
                with self.subTest(normalize=normalize.__name__, payload=payload):
                    ident = normalize(payload)
                    self.assertIsNone(ident.provider_session_id)
                    self.assertEqual(norm.narrowest_attributable_scope(ident), ei.Scope.UNKNOWN)

    def test_never_falls_back_to_host_scope(self) -> None:
        # host_id is always present; its presence must not become a HOST claim.
        self.assertNotEqual(norm.narrowest_attributable_scope(claude({})), ei.Scope.HOST)

    def test_heuristic_fields_are_never_identity(self) -> None:
        ident = codex({"cwd": "/w", "transcript_path": "/t.jsonl", "pid": 42, "prompt_id": "p", "model": "m"})
        self.assertIsNone(ident.provider_session_id)
        self.assertIsNone(ident.agent_id)
        self.assertIsNone(ident.turn_id)

    def test_two_sessions_lacking_ids_do_not_share_a_key(self) -> None:
        a, b = claude({}), claude({"cwd": "/same"})
        for ident in (a, b):
            with self.assertRaises(ei.ScopeIdentityMissing):
                ident.cache_key(ei.Scope.SESSION)


class VerbatimAndIsolationTest(unittest.TestCase):
    def test_ids_are_copied_verbatim(self) -> None:
        raw = " Sess/ID:ü-1  "
        self.assertEqual(claude({"session_id": raw}).provider_session_id, raw)

    def test_two_concurrent_sessions_never_share_a_key_or_correlate(self) -> None:
        a = claude({"session_id": "s-A"})
        b = claude({"session_id": "s-B"})
        self.assertNotEqual(a.cache_key(ei.Scope.SESSION), b.cache_key(ei.Scope.SESSION))
        self.assertFalse(a.correlates_with(b))

    def test_same_session_id_across_providers_does_not_share_a_key(self) -> None:
        self.assertNotEqual(
            claude({"session_id": "x"}).cache_key(ei.Scope.SESSION),
            codex({"session_id": "x"}).cache_key(ei.Scope.SESSION),
        )

    def test_envelope_round_trips_on_the_wire(self) -> None:
        ident = codex({"session_id": "c-1", "turn_id": "t-1", "agent_id": "a-1"})
        self.assertEqual(ei.ExecutionIdentity.from_wire(ident.to_wire()), ident)


if __name__ == "__main__":
    unittest.main()
