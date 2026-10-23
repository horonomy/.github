#!/usr/bin/env python3
"""Tests for the execution identity conformance vectors (HORO-1599).

Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import hashlib
import json
import unittest

import execution_identity_vectors as vec


def _load() -> dict:
    return json.loads(vec.VECTORS_PATH.read_text(encoding="utf-8"))


def _case(document: dict, section: str, name: str) -> dict:
    return next(case for case in document[section] if case["name"] == name)


class VectorFileTest(unittest.TestCase):
    def test_committed_file_matches_a_fresh_build(self) -> None:
        self.assertEqual(vec.VECTORS_PATH.read_text(encoding="utf-8"), vec.render(), "run: python3 scripts/execution_identity_vectors.py")

    def test_every_section_is_populated_and_names_are_unique(self) -> None:
        document = _load()
        for section in ("valid", "invalid", "correlations", "normalizers"):
            names = [case["name"] for case in document[section]]
            self.assertTrue(names, section)
            self.assertEqual(len(names), len(set(names)), section)


class LiteralExpectationsTest(unittest.TestCase):
    """Written by hand, independent of the reference implementation's own output."""

    def setUp(self) -> None:
        self.document = _load()

    def test_session_key_is_host_provider_session(self) -> None:
        keys = _case(self.document, "valid", "session")["expect"]["cache_keys"]
        self.assertEqual(keys["session"], ["host-1", "claude_code", "s-1"])
        self.assertEqual(keys["host"], ["host-1"])

    def test_missing_dimension_is_null_never_a_wider_key(self) -> None:
        keys = _case(self.document, "valid", "session")["expect"]["cache_keys"]
        self.assertIsNone(keys["agent"])
        self.assertIsNone(keys["turn_task"])
        self.assertIsNone(_case(self.document, "valid", "agent-without-session")["expect"]["cache_keys"]["session"])

    def test_turn_task_needs_session_agent_and_turn(self) -> None:
        full = _case(self.document, "valid", "turn-task")["expect"]["cache_keys"]
        self.assertEqual(full["turn_task"], ["host-1", "claude_code", "s-1", "a-1", "t-1"])
        self.assertIsNone(_case(self.document, "valid", "turn-without-agent")["expect"]["cache_keys"]["turn_task"])

    def test_worktree_wins_over_repo_for_project_scope(self) -> None:
        self.assertEqual(_case(self.document, "valid", "worktree-over-repo")["expect"]["cache_keys"]["project_worktree"], ["wt-1"])
        self.assertEqual(_case(self.document, "valid", "repo-only-context")["expect"]["cache_keys"]["project_worktree"], ["repo-1"])

    def test_display_id_preimage(self) -> None:
        expected = "provider_session_id:" + hashlib.sha256(b"provider_session_id:s-1").hexdigest()[:8]
        self.assertEqual(_case(self.document, "valid", "session")["expect"]["display_ids"]["provider_session_id"], expected)

    def test_ids_are_verbatim(self) -> None:
        wire = _case(self.document, "valid", "verbatim-unicode-and-padding")["expect"]["round_trip"]
        self.assertEqual(wire["provider_session_id"], " Sess/ID:ü-1  ")

    def test_unknown_field_is_dropped_on_round_trip(self) -> None:
        self.assertNotIn("cost_center_id", _case(self.document, "valid", "unknown-field-ignored")["expect"]["round_trip"])

    def test_without_a_session_nothing_is_attributable(self) -> None:
        self.assertEqual(_case(self.document, "valid", "host-only")["expect"]["narrowest_attributable_scope"], "unknown")
        self.assertEqual(_case(self.document, "valid", "agent-without-session")["expect"]["narrowest_attributable_scope"], "unknown")

    def test_correlation_requires_a_concrete_match_and_rejects_conflicts(self) -> None:
        by = {case["name"]: case["expect"]["correlates"] for case in self.document["correlations"]}
        self.assertTrue(by["same-session"])
        self.assertFalse(by["claude-vs-codex-session"])
        for name in ("different-session", "different-agent", "different-host"):
            self.assertFalse(by[name], name)

    def test_empty_string_id_is_not_a_session(self) -> None:
        case = _case(self.document, "valid", "empty-session-id")["expect"]
        self.assertIsNone(case["cache_keys"]["session"])
        self.assertEqual(case["narrowest_attributable_scope"], "unknown")

    def test_normalizers(self) -> None:
        by = {case["name"]: case["expect"] for case in self.document["normalizers"]}
        self.assertEqual(by["claude-main"]["provider_session_id"], "s-1")
        self.assertIsNone(by["claude-main"]["turn_id"])  # prompt_id is not a turn id
        self.assertIsNone(by["claude-turn-id-not-mapped"]["turn_id"])
        self.assertEqual(by["claude-subagent"]["narrowest_attributable_scope"], "agent")
        self.assertEqual(by["codex-subagent"]["narrowest_attributable_scope"], "turn_task")
        for name in ("claude-no-session", "claude-blank-session", "claude-non-string-session", "claude-not-a-mapping", "codex-heuristics-only"):
            self.assertIsNone(by[name]["provider_session_id"], name)
            self.assertEqual(by[name]["narrowest_attributable_scope"], "unknown", name)
        for case in by.values():
            self.assertEqual(case["lineage_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
