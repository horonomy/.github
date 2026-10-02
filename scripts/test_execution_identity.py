#!/usr/bin/env python3
"""Tests for the shared Horonom execution identity contract (HORO-1597/1598).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import unittest

import execution_identity as ei


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 10, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _make(**overrides) -> ei.ExecutionIdentity:
    fields = dict(
        envelope_version=ei.ENVELOPE_VERSION,
        observed_at=_now(),
        host_id="host-abc123",
        tool_provider="claude_code",
        provider_session_id="sess-1",
        agent_id="agent-1",
        lineage_status=ei.LineageStatus.ROOT,
    )
    fields.update(overrides)
    return ei.ExecutionIdentity(**fields)


class EnvelopeVersionTest(unittest.TestCase):
    def test_declared_version_is_supported(self) -> None:
        self.assertTrue(ei.is_supported_envelope_version(ei.ENVELOPE_VERSION))

    def test_future_version_is_not_guessed_at(self) -> None:
        self.assertFalse(ei.is_supported_envelope_version(ei.ENVELOPE_VERSION + 1))

    def test_version_as_string_is_rejected_not_coerced(self) -> None:
        self.assertFalse(ei.is_supported_envelope_version(str(ei.ENVELOPE_VERSION)))

    def test_bool_is_not_accepted_as_version_one(self) -> None:
        self.assertFalse(ei.is_supported_envelope_version(True))

    def test_constructing_with_unsupported_version_raises(self) -> None:
        with self.assertRaises(ValueError):
            _make(envelope_version=999)


class ToolProviderTest(unittest.TestCase):
    def test_known_providers_are_valid(self) -> None:
        for provider in ei.KNOWN_TOOL_PROVIDERS:
            self.assertTrue(ei.is_valid_tool_provider(provider))

    def test_a_new_unknown_provider_is_still_syntactically_valid(self) -> None:
        # tool_provider is open-ended by design: a new provider is a new
        # string, never a contract version bump.
        self.assertTrue(ei.is_valid_tool_provider("future_agent_tool"))

    def test_uppercase_is_rejected(self) -> None:
        self.assertFalse(ei.is_valid_tool_provider("ClaudeCode"))

    def test_path_like_value_is_rejected(self) -> None:
        self.assertFalse(ei.is_valid_tool_provider("../etc/passwd"))

    def test_empty_string_is_rejected(self) -> None:
        self.assertFalse(ei.is_valid_tool_provider(""))


class ConstructionValidationTest(unittest.TestCase):
    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make(observed_at=datetime.datetime(2026, 10, 2, 12, 0, 0))

    def test_non_utc_timezone_is_rejected(self) -> None:
        tz = datetime.timezone(datetime.timedelta(hours=8))
        with self.assertRaises(ValueError):
            _make(observed_at=datetime.datetime(2026, 10, 2, 12, 0, 0, tzinfo=tz))

    def test_empty_host_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make(host_id="")

    def test_invalid_tool_provider_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make(tool_provider="Not Valid")

    def test_non_string_optional_field_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            _make(turn_id=12345)  # type: ignore[arg-type]


class LineageTriStateTest(unittest.TestCase):
    """Covers HORO-1597's `agent_id known, parent_agent_id unavailable` case."""

    def test_root_requires_no_parent_id(self) -> None:
        identity = _make(lineage_status=ei.LineageStatus.ROOT)
        self.assertIsNone(identity.parent_agent_id)

    def test_unknown_lineage_requires_no_parent_id(self) -> None:
        identity = _make(lineage_status=ei.LineageStatus.UNKNOWN)
        self.assertIsNone(identity.parent_agent_id)

    def test_child_requires_parent_id(self) -> None:
        with self.assertRaises(ValueError):
            _make(lineage_status=ei.LineageStatus.CHILD, parent_agent_id=None)

    def test_child_with_parent_id_is_valid(self) -> None:
        identity = _make(lineage_status=ei.LineageStatus.CHILD, parent_agent_id="agent-root")
        self.assertEqual(identity.parent_agent_id, "agent-root")

    def test_root_with_parent_id_set_is_rejected(self) -> None:
        # A provider cannot claim both "no parent" and "parent is X".
        with self.assertRaises(ValueError):
            _make(lineage_status=ei.LineageStatus.ROOT, parent_agent_id="agent-root")

    def test_root_and_unknown_are_distinct_states(self) -> None:
        # The specific failure this tri-state prevents: collapsing "proven
        # root" into "lineage not supported" (or vice versa).
        root = _make(lineage_status=ei.LineageStatus.ROOT)
        unknown = _make(lineage_status=ei.LineageStatus.UNKNOWN)
        self.assertNotEqual(root.lineage_status, unknown.lineage_status)
        self.assertNotEqual(root, unknown)


class EqualityTest(unittest.TestCase):
    def test_identical_envelopes_are_equal(self) -> None:
        self.assertEqual(_make(), _make())

    def test_different_observed_at_makes_envelopes_unequal(self) -> None:
        a = _make(observed_at=_now())
        b = _make(observed_at=_now() + datetime.timedelta(seconds=1))
        self.assertNotEqual(a, b)

    def test_different_event_id_makes_envelopes_unequal(self) -> None:
        a = _make(event_id="evt-1")
        b = _make(event_id="evt-2")
        self.assertNotEqual(a, b)


class CorrelatesWithTest(unittest.TestCase):
    def test_same_session_and_agent_correlates(self) -> None:
        a = _make(event_id="evt-1")
        b = _make(event_id="evt-2", observed_at=_now() + datetime.timedelta(minutes=5))
        self.assertTrue(a.correlates_with(b))

    def test_different_provider_session_id_does_not_correlate(self) -> None:
        a = _make(provider_session_id="sess-1")
        b = _make(provider_session_id="sess-2")
        self.assertFalse(a.correlates_with(b))

    def test_both_missing_the_same_field_does_not_prove_correlation(self) -> None:
        # Neither carries agent_id: that is "we don't know for either", not
        # "therefore they're the same agent".
        a = _make(agent_id=None, lineage_status=ei.LineageStatus.UNKNOWN)
        b = _make(agent_id=None, lineage_status=ei.LineageStatus.UNKNOWN, provider_session_id="sess-9")
        self.assertFalse(a.correlates_with(b))

    def test_no_concretely_populated_shared_field_does_not_correlate(self) -> None:
        a = ei.ExecutionIdentity(
            envelope_version=ei.ENVELOPE_VERSION,
            observed_at=_now(),
            host_id="host-a",
            tool_provider="claude_code",
        )
        b = ei.ExecutionIdentity(
            envelope_version=ei.ENVELOPE_VERSION,
            observed_at=_now(),
            host_id="host-b",
            tool_provider="claude_code",
        )
        self.assertFalse(a.correlates_with(b))


class RedactionTest(unittest.TestCase):
    def test_display_id_is_deterministic(self) -> None:
        identity = _make()
        self.assertEqual(identity.display_id("provider_session_id"), identity.display_id("provider_session_id"))

    def test_display_id_does_not_contain_the_raw_value(self) -> None:
        identity = _make(provider_session_id="super-secret-session-token")
        redacted = identity.display_id("provider_session_id")
        self.assertNotIn("super-secret-session-token", redacted)

    def test_different_dimensions_with_the_same_raw_value_redact_differently(self) -> None:
        identity = _make(provider_session_id="same-value", agent_id="same-value")
        self.assertNotEqual(
            identity.display_id("provider_session_id"),
            identity.display_id("agent_id"),
        )

    def test_redacting_an_absent_field_raises(self) -> None:
        identity = _make(turn_id=None)
        with self.assertRaises(ValueError):
            identity.display_id("turn_id")

    def test_display_id_is_not_used_for_cache_keys(self) -> None:
        identity = _make()
        key = identity.cache_key(ei.Scope.SESSION)
        self.assertIn(identity.provider_session_id, key)
        self.assertNotIn(identity.display_id("provider_session_id"), key)


class CacheKeyScopeNarrowingTest(unittest.TestCase):
    """Covers HORO-1598 AC #3: scope must not silently narrow."""

    def test_host_scope_key(self) -> None:
        identity = _make()
        self.assertEqual(identity.cache_key(ei.Scope.HOST), (identity.host_id,))

    def test_session_scope_requires_provider_session_id(self) -> None:
        identity = _make(provider_session_id=None)
        with self.assertRaises(ei.ScopeIdentityMissing):
            identity.cache_key(ei.Scope.SESSION)

    def test_agent_scope_requires_agent_id(self) -> None:
        identity = _make(agent_id=None, lineage_status=ei.LineageStatus.UNKNOWN)
        with self.assertRaises(ei.ScopeIdentityMissing):
            identity.cache_key(ei.Scope.AGENT)

    def test_turn_task_scope_requires_turn_id(self) -> None:
        identity = _make(turn_id=None)
        with self.assertRaises(ei.ScopeIdentityMissing):
            identity.cache_key(ei.Scope.TURN_TASK)

    def test_session_scope_key_never_falls_back_to_host_only(self) -> None:
        # The exact anti-pattern named in HORO-1597: a `latest:<host>` lookup
        # standing in for session-local state. Confirms the key actually
        # includes session identity rather than silently returning a
        # host-only tuple when asked for SESSION scope.
        identity = _make()
        key = identity.cache_key(ei.Scope.SESSION)
        self.assertIn(identity.provider_session_id, key)
        self.assertGreater(len(key), 1)

    def test_project_worktree_scope_prefers_worktree_over_repo(self) -> None:
        identity = _make(worktree_id="wt-1", repo_id="repo-1")
        self.assertEqual(identity.cache_key(ei.Scope.PROJECT_WORKTREE), ("wt-1",))

    def test_project_worktree_scope_without_either_id_raises(self) -> None:
        identity = _make()
        with self.assertRaises(ei.ScopeIdentityMissing):
            identity.cache_key(ei.Scope.PROJECT_WORKTREE)

    def test_unknown_scope_has_no_cache_key(self) -> None:
        identity = _make()
        with self.assertRaises(ei.ScopeIdentityMissing):
            identity.cache_key(ei.Scope.UNKNOWN)


class SerializationTest(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self) -> None:
        original = _make(
            tool_instance_id="inst-1",
            turn_id="turn-1",
            session_lineage_id="lineage-1",
            event_id="evt-1",
            repo_id="repo-1",
            worktree_id="wt-1",
        )
        restored = ei.ExecutionIdentity.from_wire(original.to_wire())
        self.assertEqual(original, restored)

    def test_absent_optional_fields_are_not_emitted_on_the_wire(self) -> None:
        identity = _make(turn_id=None, event_id=None)
        document = identity.to_wire()
        self.assertNotIn("turn_id", document)
        self.assertNotIn("event_id", document)

    def test_unknown_top_level_field_is_ignored(self) -> None:
        document = _make().to_wire()
        document["a_future_field_this_reader_has_never_heard_of"] = "surprise"
        restored = ei.ExecutionIdentity.from_wire(document)
        self.assertFalse(hasattr(restored, "a_future_field_this_reader_has_never_heard_of"))

    def test_unrecognised_lineage_status_is_refused_not_guessed(self) -> None:
        document = _make().to_wire()
        document["lineage_status"] = "grandparent"
        with self.assertRaises(ValueError):
            ei.ExecutionIdentity.from_wire(document)

    def test_unknown_envelope_version_is_refused(self) -> None:
        document = _make().to_wire()
        document["envelope_version"] = 999
        with self.assertRaises(ValueError):
            ei.ExecutionIdentity.from_wire(document)

    def test_observed_at_round_trips_to_the_same_instant(self) -> None:
        original = _make()
        restored = ei.ExecutionIdentity.from_wire(original.to_wire())
        self.assertEqual(original.observed_at, restored.observed_at)


class BackwardCompatibilityTest(unittest.TestCase):
    def test_legacy_record_scope_is_host_not_session(self) -> None:
        self.assertEqual(ei.host_only_scope_for_legacy_record(), ei.Scope.HOST)

    def test_legacy_scope_is_never_silently_promoted(self) -> None:
        self.assertNotEqual(ei.host_only_scope_for_legacy_record(), ei.Scope.SESSION)
        self.assertNotEqual(ei.host_only_scope_for_legacy_record(), ei.Scope.AGENT)


class ImmutabilityTest(unittest.TestCase):
    def test_envelope_is_frozen(self) -> None:
        identity = _make()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            identity.host_id = "other-host"  # type: ignore[misc]

    def test_deep_copy_remains_equal(self) -> None:
        identity = _make()
        self.assertEqual(identity, copy.deepcopy(identity))


if __name__ == "__main__":
    unittest.main()
