#!/usr/bin/env python3
"""Tests for the shared Horonom statusline provider contract (HORO-1564).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import unittest

import statusline_contract as sc


class ContractVersionTest(unittest.TestCase):
    def test_declared_version_is_supported(self) -> None:
        self.assertTrue(sc.is_supported_contract_version(sc.CONTRACT_VERSION))

    def test_future_version_is_not_guessed_at(self) -> None:
        self.assertFalse(sc.is_supported_contract_version(sc.CONTRACT_VERSION + 1))

    def test_version_as_string_is_rejected_not_coerced(self) -> None:
        self.assertFalse(sc.is_supported_contract_version(str(sc.CONTRACT_VERSION)))

    def test_bool_is_not_accepted_as_version_one(self) -> None:
        # `True == 1` in Python; a bool reaching this field is a provider bug.
        self.assertFalse(sc.is_supported_contract_version(True))


class ScopeTest(unittest.TestCase):
    def test_every_scope_has_an_emoji_free_text_fallback(self) -> None:
        self.assertEqual(
            {s: s.text_fallback for s in sc.Scope},
            {
                sc.Scope.HOST: "[host]",
                sc.Scope.SESSION: "[session]",
                sc.Scope.PROJECT: "[project]",
            },
        )

    def test_text_fallback_is_ascii_only(self) -> None:
        for scope in sc.Scope:
            with self.subTest(scope=scope):
                scope.text_fallback.encode("ascii")

    def test_scopes_are_distinct(self) -> None:
        values = [s.value for s in sc.Scope]
        self.assertEqual(len(values), len(set(values)))


class AvailabilityTest(unittest.TestCase):
    def test_only_available_has_live_readings(self) -> None:
        live = {a for a in sc.Availability if a.has_live_readings}
        self.assertEqual(live, {sc.Availability.AVAILABLE})

    def test_unknown_is_not_treated_as_healthy(self) -> None:
        self.assertIsNot(sc.Availability.UNKNOWN, sc.Availability.AVAILABLE)
        self.assertFalse(sc.Availability.UNKNOWN.has_live_readings)

    def test_unavailable_is_distinct_from_error_and_unsupported(self) -> None:
        distinct = {
            sc.Availability.UNAVAILABLE,
            sc.Availability.ERROR,
            sc.Availability.UNSUPPORTED,
            sc.Availability.UNKNOWN,
        }
        self.assertEqual(len(distinct), 4)

    def test_all_five_states_are_present(self) -> None:
        self.assertEqual(
            {a.value for a in sc.Availability},
            {"available", "unavailable", "unsupported", "unknown", "error"},
        )


class SegmentStateTest(unittest.TestCase):
    def test_neutral_and_unknown_make_no_health_claim(self) -> None:
        silent = {s for s in sc.SegmentState if not s.makes_a_health_claim}
        self.assertEqual(silent, {sc.SegmentState.NEUTRAL, sc.SegmentState.UNKNOWN})

    def test_unknown_is_not_ok(self) -> None:
        self.assertIsNot(sc.SegmentState.UNKNOWN, sc.SegmentState.OK)

    def test_states_are_distinct(self) -> None:
        values = [s.value for s in sc.SegmentState]
        self.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
