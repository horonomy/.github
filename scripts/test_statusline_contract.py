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


class ConfidenceTest(unittest.TestCase):
    def test_confidence_is_ordered_low_to_high(self) -> None:
        ranks = [sc.Confidence.LOW.rank, sc.Confidence.MEDIUM.rank, sc.Confidence.HIGH.rank]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(set(ranks)), 3)

    def test_every_confidence_has_a_rank(self) -> None:
        for c in sc.Confidence:
            with self.subTest(confidence=c):
                self.assertIsInstance(c.rank, int)

    def test_every_subject_has_a_disambiguating_noun(self) -> None:
        for subject in sc.ConfidenceSubject:
            with self.subTest(subject=subject):
                self.assertIn("confidence", subject.label)

    def test_preflight_subject_never_reads_as_risk_or_severity(self) -> None:
        label = sc.ConfidenceSubject.PREFLIGHT_ESTIMATE.label
        self.assertEqual(label, "preflight confidence")
        for forbidden in ("risk", "severity", "priority", "impact"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, label)

    def test_subject_labels_are_not_opaque_abbreviations(self) -> None:
        # HORO-1569: `pf:high` was unreadable. Every label must be a word.
        for subject in sc.ConfidenceSubject:
            with self.subTest(subject=subject):
                self.assertGreater(len(subject.label), 4)
                self.assertNotIn(":", subject.label)


class HostCapabilityTest(unittest.TestCase):
    def test_only_supported_may_install(self) -> None:
        installable = {c for c in sc.HostCapability if c.may_install}
        self.assertEqual(installable, {sc.HostCapability.SUPPORTED})

    def test_unsupported_unavailable_and_unknown_are_distinct(self) -> None:
        self.assertEqual(
            len(
                {
                    sc.HostCapability.UNSUPPORTED,
                    sc.HostCapability.UNAVAILABLE,
                    sc.HostCapability.UNKNOWN,
                }
            ),
            3,
        )


class OwnershipTest(unittest.TestCase):
    def test_user_owned_is_never_writable(self) -> None:
        self.assertFalse(sc.Ownership.USER_OWNED.is_writable_by_lifecycle)

    def test_unknown_owner_is_never_writable(self) -> None:
        self.assertFalse(sc.Ownership.UNKNOWN.is_writable_by_lifecycle)

    def test_shared_host_artifact_is_not_writable_as_a_whole(self) -> None:
        self.assertFalse(sc.Ownership.HOST_OWNED.is_writable_by_lifecycle)

    def test_exactly_the_two_horonom_classes_are_writable(self) -> None:
        writable = {o for o in sc.Ownership if o.is_writable_by_lifecycle}
        self.assertEqual(
            writable,
            {sc.Ownership.HORONOM_HOST_OWNED, sc.Ownership.PRODUCT_PROVIDER_OWNED},
        )

    def test_the_users_own_statusline_command_is_user_owned(self) -> None:
        self.assertIs(
            sc.classify_artifact("user_statusline_command"), sc.Ownership.USER_OWNED
        )

    def test_the_shared_settings_file_is_a_shared_host_artifact(self) -> None:
        self.assertIs(
            sc.classify_artifact("host_shared_settings_file"), sc.Ownership.HOST_OWNED
        )

    def test_the_provider_registry_is_horonom_owned(self) -> None:
        self.assertIs(
            sc.classify_artifact("horonom_provider_registry"),
            sc.Ownership.HORONOM_HOST_OWNED,
        )

    def test_an_unattributed_slot_value_fails_safe(self) -> None:
        self.assertIs(
            sc.classify_artifact("unattributed_statusline_command"),
            sc.Ownership.UNKNOWN,
        )

    def test_legacy_unmarked_state_fails_safe(self) -> None:
        self.assertIs(
            sc.classify_artifact("legacy_unmarked_state"), sc.Ownership.UNKNOWN
        )

    def test_an_unclassified_artifact_is_unknown_not_writable(self) -> None:
        owner = sc.classify_artifact("some_artifact_nobody_has_classified")
        self.assertIs(owner, sc.Ownership.UNKNOWN)
        self.assertFalse(owner.is_writable_by_lifecycle)

    def test_no_user_owned_artifact_is_writable(self) -> None:
        for artifact, owner in sc.ARTIFACT_OWNERSHIP.items():
            if owner is sc.Ownership.USER_OWNED:
                with self.subTest(artifact=artifact):
                    self.assertFalse(owner.is_writable_by_lifecycle)


class TokenValidatorTest(unittest.TestCase):
    def test_accepts_a_plain_lowercase_token(self) -> None:
        self.assertEqual(sc.require_token("evidence_gap", "reason_code"), "evidence_gap")

    def test_rejects_a_path_shaped_token(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_token("/etc/passwd", "reason_code")

    def test_rejects_uppercase_and_spaces(self) -> None:
        for bad in ("Evidence_Gap", "evidence gap", "evidence:gap", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(sc.ContractViolation):
                    sc.require_token(bad, "reason_code")

    def test_provider_id_allows_hyphens_but_not_separators(self) -> None:
        self.assertEqual(sc.require_provider_id("libra-governor"), "libra-governor")
        with self.assertRaises(sc.ContractViolation):
            sc.require_provider_id("libra/governor")

    def test_explain_key_is_bounded_to_four_dotted_parts(self) -> None:
        self.assertEqual(
            sc.require_explain_key("fornax.latest_finding"), "fornax.latest_finding"
        )
        with self.assertRaises(sc.ContractViolation):
            sc.require_explain_key("a.b.c.d.e")


class LabelAllowlistTest(unittest.TestCase):
    LEGITIMATE = (
        "Unverified",
        "escalated — awaiting approval",
        "P90 ≤ 12m",
        "would block (113 of 705)",
        "shadow mode",
        "no findings yet",
        "stale, restart required",
        "97% verified",
    )

    def test_accepts_the_labels_the_products_actually_need(self) -> None:
        for label in self.LEGITIMATE:
            with self.subTest(label=label):
                self.assertEqual(sc.require_label(label), label)

    def test_rejects_an_absolute_path(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("/home/someone/.claude/statusline.sh")

    def test_rejects_a_home_relative_path(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("~/.claude/settings.json")

    def test_rejects_a_windows_path(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("C:\\Users\\someone\\config")

    def test_rejects_a_url(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("https://example.invalid/status")

    def test_rejects_a_key_equals_value_pair(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("api_key=redacted")

    def test_rejects_a_shell_expansion(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("$HOME is set")

    def test_rejects_an_embedded_emoji(self) -> None:
        # The host owns iconography; a provider glyph would break consistency.
        for glyph in ("\N{SHIELD}", "\N{SCALES}", "\N{COMPASS}"):
            with self.subTest(glyph=glyph):
                with self.assertRaises(sc.ContractViolation):
                    sc.require_label(f"{glyph} fornax")

    def test_rejects_a_newline_so_a_provider_cannot_add_a_line(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("first line\nsecond line")

    def test_rejects_a_control_character(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("verified\x1b[31m")

    def test_rejects_an_overlong_label(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("v" * (sc.MAX_LABEL_CHARS + 1))

    def test_accepts_a_label_exactly_at_the_bound(self) -> None:
        label = "v" * sc.MAX_LABEL_CHARS
        self.assertEqual(sc.require_label(label), label)

    def test_rejects_an_empty_label(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label("")

    def test_rejects_a_non_string(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.require_label(None)


if __name__ == "__main__":
    unittest.main()
