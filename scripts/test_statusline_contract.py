#!/usr/bin/env python3
"""Tests for the shared Horonom statusline provider contract (HORO-1564).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import dataclasses
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


class LabelWidthTest(unittest.TestCase):
    # A character bound is only a column bound if one character is one column.
    WIDE = (
        "Ｖerified",  # fullwidth latin, two columns per character
        "検証済み",  # CJK, two columns per character
    )
    HOMOGLYPH = (
        "верified",  # Cyrillic ве
        "Verified٣",  # Arabic-Indic digit three
    )

    def test_a_double_width_label_is_refused(self) -> None:
        for label in self.WIDE:
            with self.subTest(label=label):
                with self.assertRaises(sc.ContractViolation):
                    sc.require_label(label)

    def test_a_homoglyph_label_is_refused(self) -> None:
        for label in self.HOMOGLYPH:
            with self.subTest(label=label):
                with self.assertRaises(sc.ContractViolation):
                    sc.require_label(label)

    def test_the_character_bound_is_therefore_a_column_bound(self) -> None:
        # Every accepted character is one ASCII column wide, except the three
        # allowlisted symbols, which are single-column in a latin locale.
        allowed_symbols = set("—≤≥")
        for label in LabelAllowlistTest.LEGITIMATE:
            with self.subTest(label=label):
                for char in label:
                    if char not in allowed_symbols:
                        self.assertTrue(char.isascii())

    def test_legitimate_labels_are_unaffected(self) -> None:
        for label in LabelAllowlistTest.LEGITIMATE:
            with self.subTest(label=label):
                self.assertEqual(sc.require_label(label), label)


class SecretShapeTest(unittest.TestCase):
    # Assembled at runtime rather than written as literals so this test file
    # cannot trip a secret scanner or push-protection rule.
    SECRETS = (
        "sk-" + "a" * 20,
        "ghp_" + "b" * 20,
        "AKIA" + "IOSFODNN7EXAMPLE",
        "xoxb-" + "1-2-" + "c" * 12,
        "eyJ" + "hbGciOiJIUzI1NiJ9",
        "-----BEGIN" + " PRIVATE KEY",
        "aB3dE5fG7hJ9kL1mN3pQ5",
    )

    CREDENTIAL_WORDS = (
        "token abc",
        "api_key present",
        "Authorization Bearer x",
        "password rotated",
        "client secret ok",
    )

    def test_credential_shaped_values_are_rejected(self) -> None:
        for secret in self.SECRETS:
            with self.subTest(shape=secret[:4]):
                with self.assertRaises(sc.PrivacyViolation):
                    sc.assert_no_secret_shape(secret, "label")

    def test_naming_a_credential_is_rejected(self) -> None:
        for phrase in self.CREDENTIAL_WORDS:
            with self.subTest(phrase=phrase):
                with self.assertRaises(sc.PrivacyViolation):
                    sc.assert_no_secret_shape(phrase, "label")

    def test_the_error_message_never_echoes_the_value(self) -> None:
        secret = "ghp_" + "d" * 20
        with self.assertRaises(sc.PrivacyViolation) as caught:
            sc.assert_no_secret_shape(secret, "segment.label")
        message = str(caught.exception)
        self.assertIn("segment.label", message)
        self.assertNotIn(secret, message)
        # Not even a prefix, suffix or hash fragment of the value.
        self.assertNotIn(secret[:8], message)
        self.assertNotIn(secret[-8:], message)

    def test_legitimate_product_labels_are_not_false_positives(self) -> None:
        for label in (
            "Unverified",
            "escalated — awaiting approval",
            "P90 ≤ 12m",
            "would block (113 of 705)",
            "shadow mode",
            "no findings yet",
            "97% verified",
            "task a3f9c2d1",
            "sensor disabled",
        ):
            with self.subTest(label=label):
                self.assertEqual(sc.assert_no_secret_shape(label, "label"), label)

    def test_a_short_hex_task_id_is_not_treated_as_a_secret(self) -> None:
        # Libra shows a truncated task id; that must stay renderable.
        self.assertEqual(sc.assert_no_secret_shape("a3f9c2d1", "label"), "a3f9c2d1")

    def test_a_version_string_is_not_treated_as_a_secret(self) -> None:
        self.assertEqual(sc.assert_no_secret_shape("v0.0.2", "label"), "v0.0.2")


class HostShapeTest(unittest.TestCase):
    def test_internal_endpoints_are_rejected(self) -> None:
        for host in ("gateway.internal", "build.corp", "db.lan", "localhost"):
            with self.subTest(host=host):
                with self.assertRaises(sc.PrivacyViolation):
                    sc.assert_no_host_shape(f"talking to {host}", "label")

    def test_a_public_domain_is_also_rejected(self) -> None:
        with self.assertRaises(sc.PrivacyViolation):
            sc.assert_no_host_shape("api.example.com", "label")

    def test_version_and_duration_strings_are_not_host_shaped(self) -> None:
        for value in ("v0.0.2", "0.3s recon", "P90 ≤ 12m", "97% verified"):
            with self.subTest(value=value):
                self.assertEqual(sc.assert_no_host_shape(value, "label"), value)

    def test_privacy_safe_applies_both_checks(self) -> None:
        with self.assertRaises(sc.PrivacyViolation):
            sc.assert_privacy_safe("ghp_" + "e" * 20, "label")
        with self.assertRaises(sc.PrivacyViolation):
            sc.assert_privacy_safe("gateway.internal", "label")
        self.assertEqual(sc.assert_privacy_safe("Unverified", "label"), "Unverified")


class SegmentTest(unittest.TestCase):
    def test_a_minimal_segment_is_valid(self) -> None:
        segment = sc.Segment(
            key="latest_finding", state=sc.SegmentState.OK, label="Verified"
        )
        self.assertEqual(segment.key, "latest_finding")
        self.assertFalse(segment.hypothetical)
        self.assertIsNone(segment.count)

    def test_a_fully_populated_segment_is_valid(self) -> None:
        segment = sc.Segment(
            key="would_block",
            state=sc.SegmentState.NEUTRAL,
            label="shadow mode",
            reason_code="shadow_only",
            reason_label="shadow only",
            confidence=sc.Confidence.MEDIUM,
            confidence_of=sc.ConfidenceSubject.POLICY_DECISION,
            age_seconds=42,
            count=113,
            total=705,
            count_label="would block",
            hypothetical=True,
            explain_key="circinus.would_block",
            order_hint=2,
        )
        self.assertTrue(segment.hypothetical)
        self.assertEqual((segment.count, segment.total), (113, 705))

    def test_confidence_without_its_subject_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="plan",
                state=sc.SegmentState.OK,
                label="stable",
                confidence=sc.Confidence.HIGH,
            )

    def test_subject_without_a_confidence_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="plan",
                state=sc.SegmentState.OK,
                label="stable",
                confidence_of=sc.ConfidenceSubject.PREFLIGHT_ESTIMATE,
            )

    def test_a_count_without_a_noun_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="would_block", state=sc.SegmentState.NEUTRAL, label="shadow", count=113
            )

    def test_a_total_without_a_count_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="would_block",
                state=sc.SegmentState.NEUTRAL,
                label="shadow",
                total=705,
                count_label="would block",
            )

    def test_a_count_above_its_total_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="would_block",
                state=sc.SegmentState.NEUTRAL,
                label="shadow",
                count=706,
                total=705,
                count_label="would block",
            )

    def test_a_negative_age_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="latest", state=sc.SegmentState.OK, label="Verified", age_seconds=-1
            )

    def test_an_unbounded_count_is_rejected(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="n",
                state=sc.SegmentState.NEUTRAL,
                label="many",
                count=sc.MAX_COUNT + 1,
                count_label="events",
            )

    def test_a_secret_shaped_label_cannot_enter_a_segment(self) -> None:
        # Deliberately a value the character allowlist accepts (pure
        # alphanumerics), so this exercises the privacy layer rather than
        # passing because the charset check happened to fire first.
        with self.assertRaises(sc.PrivacyViolation):
            sc.Segment(
                key="latest", state=sc.SegmentState.OK, label="aB3dE5fG7hJ9kL1mN3pQ5"
            )

    def test_a_charset_rejection_is_not_reported_as_a_privacy_violation(self) -> None:
        # An underscore is a shape problem, not a disclosure; the two error
        # types must stay distinguishable so the host can degrade correctly.
        with self.assertRaises(sc.ContractViolation) as caught:
            sc.Segment(key="latest", state=sc.SegmentState.OK, label="ghp_token")
        self.assertNotIsInstance(caught.exception, sc.PrivacyViolation)

    def test_a_secret_shaped_count_label_cannot_enter_a_segment(self) -> None:
        with self.assertRaises(sc.PrivacyViolation):
            sc.Segment(
                key="n",
                state=sc.SegmentState.NEUTRAL,
                label="many",
                count=1,
                count_label="AKIA" + "IOSFODNN7EXAMPLE",
            )

    def test_a_path_shaped_reason_label_cannot_enter_a_segment(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(
                key="latest",
                state=sc.SegmentState.UNKNOWN,
                label="Unverified",
                reason_label="/var/log/fornax.log",
            )

    def test_state_must_be_the_enum_not_a_string(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.Segment(key="latest", state="ok", label="Verified")

    def test_a_segment_is_immutable(self) -> None:
        segment = sc.Segment(key="latest", state=sc.SegmentState.OK, label="Verified")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            segment.label = "Unverified"  # type: ignore[misc]


def _segment(**overrides: object) -> sc.Segment:
    """A valid minimal segment, with fields overridden per test."""
    fields: dict = {
        "key": "latest_finding",
        "state": sc.SegmentState.OK,
        "label": "Verified",
    }
    fields.update(overrides)
    return sc.Segment(**fields)  # type: ignore[arg-type]


def _status(**overrides: object) -> sc.ProviderStatus:
    """A valid minimal provider status, with fields overridden per test."""
    fields: dict = {
        "provider": "fornax",
        "provider_version": "0.0.8",
        "scope": sc.Scope.HOST,
        "availability": sc.Availability.AVAILABLE,
    }
    fields.update(overrides)
    return sc.ProviderStatus(**fields)  # type: ignore[arg-type]


class ProviderStatusTest(unittest.TestCase):
    def test_a_minimal_available_status_is_valid(self) -> None:
        status = _status()
        self.assertEqual(status.contract_version, sc.CONTRACT_VERSION)
        self.assertEqual(status.segments, ())
        self.assertEqual(status.cache_ttl_seconds, 0)

    def test_scope_has_no_default(self) -> None:
        with self.assertRaises(TypeError):
            sc.ProviderStatus(  # type: ignore[call-arg]
                provider="fornax",
                provider_version="0.0.8",
                availability=sc.Availability.AVAILABLE,
            )

    def test_an_unsupported_contract_version_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(contract_version=sc.CONTRACT_VERSION + 1)

    def test_segment_keys_must_be_unique_within_a_provider(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(segments=(_segment(), _segment()))

    def test_too_many_segments_is_refused(self) -> None:
        many = tuple(
            _segment(key=f"seg{i}") for i in range(sc.MAX_SEGMENTS_PER_PROVIDER + 1)
        )
        with self.assertRaises(sc.ContractViolation):
            _status(segments=many)

    def test_at_the_segment_bound_is_accepted(self) -> None:
        many = tuple(
            _segment(key=f"seg{i}") for i in range(sc.MAX_SEGMENTS_PER_PROVIDER)
        )
        self.assertEqual(len(_status(segments=many).segments), sc.MAX_SEGMENTS_PER_PROVIDER)


class NonCollapseTest(unittest.TestCase):
    """The rules that stop a non-answer being rendered as a good answer."""

    def test_an_unavailable_provider_cannot_claim_ok(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(
                availability=sc.Availability.UNAVAILABLE,
                segments=(_segment(state=sc.SegmentState.OK),),
            )

    def test_an_unknown_provider_cannot_claim_ok(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(
                availability=sc.Availability.UNKNOWN,
                segments=(_segment(state=sc.SegmentState.OK),),
            )

    def test_an_errored_provider_cannot_claim_ok(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(
                availability=sc.Availability.ERROR,
                segments=(_segment(state=sc.SegmentState.OK),),
            )

    def test_an_unavailable_provider_cannot_report_zero(self) -> None:
        # UNAVAILABLE != ZERO: a stopped daemon reporting `0 would block`
        # is a false all-clear.
        with self.assertRaises(sc.ContractViolation):
            _status(
                availability=sc.Availability.UNAVAILABLE,
                segments=(
                    _segment(
                        state=sc.SegmentState.UNKNOWN,
                        label="not running",
                        count=0,
                        count_label="would block",
                    ),
                ),
            )

    def test_an_unavailable_provider_may_still_explain_itself(self) -> None:
        # The point of the rules above is not to force silence: rendering
        # nothing is indistinguishable from "everything is fine".
        status = _status(
            availability=sc.Availability.UNAVAILABLE,
            segments=(
                _segment(
                    state=sc.SegmentState.UNKNOWN,
                    label="daemon not running",
                    reason_code="daemon_unreachable",
                ),
            ),
        )
        self.assertEqual(status.segments[0].reason_code, "daemon_unreachable")

    def test_an_available_provider_may_report_zero(self) -> None:
        status = _status(
            segments=(
                _segment(
                    state=sc.SegmentState.OK,
                    label="none would block",
                    count=0,
                    total=705,
                    count_label="would block",
                ),
            )
        )
        self.assertEqual(status.segments[0].count, 0)


class FreshnessTest(unittest.TestCase):
    def test_explicit_utc_is_accepted(self) -> None:
        self.assertEqual(
            _status(observed_at="2026-09-29T08:00:00Z").observed_at,
            "2026-09-29T08:00:00Z",
        )

    def test_fractional_seconds_are_accepted(self) -> None:
        self.assertEqual(
            _status(observed_at="2026-09-29T08:00:00.123456Z").observed_at,
            "2026-09-29T08:00:00.123456Z",
        )

    def test_a_local_offset_is_refused_not_converted(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(observed_at="2026-09-29T08:00:00+08:00")

    def test_a_naive_timestamp_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(observed_at="2026-09-29T08:00:00")

    def test_an_impossible_calendar_date_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(observed_at="2026-02-31T00:00:00Z")

    def test_a_long_cache_ttl_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(cache_ttl_seconds=sc.MAX_CACHE_TTL_SECONDS + 1)

    def test_a_short_cache_ttl_is_accepted(self) -> None:
        self.assertEqual(_status(cache_ttl_seconds=5).cache_ttl_seconds, 5)


class FallbackTextTest(unittest.TestCase):
    def test_a_convenience_rendering_is_optional(self) -> None:
        self.assertIsNone(_status().fallback_text)

    def test_a_convenience_rendering_obeys_the_same_privacy_rules(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(fallback_text="fornax: see /var/log/fornax.log")

    def test_a_convenience_rendering_may_be_longer_than_one_label(self) -> None:
        text = "v" * (sc.MAX_LABEL_CHARS + 1)
        self.assertEqual(_status(fallback_text=text).fallback_text, text)

    def test_a_convenience_rendering_is_still_bounded(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            _status(fallback_text="v" * (sc.MAX_FALLBACK_TEXT_CHARS + 1))


def _payload(**overrides: object) -> dict:
    """A valid minimal wire payload, with fields overridden per test.

    Built as a literal dict rather than from `_status().to_wire()` so a bug in
    the serialiser cannot make the parser tests pass by agreeing with it.
    """
    payload: dict = {
        "contract_version": sc.CONTRACT_VERSION,
        "provider": "fornax",
        "provider_version": "0.0.8",
        "scope": "host",
        "availability": "available",
        "segments": [{"key": "latest_finding", "state": "ok", "label": "Verified"}],
    }
    payload.update(overrides)
    return payload


class WireParsingTest(unittest.TestCase):
    def test_a_minimal_payload_parses(self) -> None:
        status = sc.provider_status_from_wire(_payload())
        self.assertEqual(status.provider, "fornax")
        self.assertIs(status.scope, sc.Scope.HOST)
        self.assertIs(status.availability, sc.Availability.AVAILABLE)
        self.assertIs(status.segments[0].state, sc.SegmentState.OK)

    def test_a_non_object_payload_is_refused(self) -> None:
        for payload in ("fornax: ok", ["fornax"], 1, None):
            with self.subTest(payload=payload):
                with self.assertRaises(sc.ContractViolation):
                    sc.provider_status_from_wire(payload)

    def test_segments_must_be_a_list(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(_payload(segments={"key": "x"}))

    def test_a_non_object_segment_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(_payload(segments=["ok"]))

    def test_a_payload_survives_a_round_trip(self) -> None:
        rich = _payload(
            observed_at="2026-09-29T08:00:00Z",
            cache_ttl_seconds=5,
            order_hint=20,
            fallback_text="fornax unverified (evidence gap, 2h)",
            segments=[
                {
                    "key": "latest_finding",
                    "state": "attention",
                    "label": "Unverified",
                    "reason_code": "evidence_gap",
                    "reason_label": "evidence gap",
                    "confidence": "medium",
                    "confidence_of": "verification",
                    "age_seconds": 7200,
                    "count": 113,
                    "total": 705,
                    "count_label": "findings",
                    "hypothetical": True,
                    "explain_key": "fornax.latest_finding",
                    "order_hint": 3,
                }
            ],
        )
        self.assertEqual(sc.provider_status_from_wire(rich).to_wire(), rich)

    def test_the_round_trip_fixture_exercises_every_segment_field(self) -> None:
        # Guards the test above from silently stopping at the fields that
        # existed when it was written.
        covered = set(_payload()["segments"][0]) | {
            "reason_code",
            "reason_label",
            "confidence",
            "confidence_of",
            "age_seconds",
            "count",
            "total",
            "count_label",
            "hypothetical",
            "explain_key",
            "order_hint",
        }
        declared = {f.name for f in dataclasses.fields(sc.Segment)}
        self.assertEqual(declared - covered, set())


class WireForwardCompatibilityTest(unittest.TestCase):
    def test_an_unknown_top_level_field_is_ignored_not_carried(self) -> None:
        status = sc.provider_status_from_wire(_payload(future_provider_key="keep?"))
        self.assertNotIn("future_provider_key", status.to_wire())

    def test_an_unknown_segment_field_is_ignored_not_carried(self) -> None:
        payload = _payload()
        payload["segments"][0]["future_segment_key"] = "keep?"
        status = sc.provider_status_from_wire(payload)
        self.assertNotIn("future_segment_key", status.to_wire()["segments"][0])

    def test_an_unrecognised_availability_degrades_to_unknown(self) -> None:
        payload = _payload(availability="quantum_pending")
        payload["segments"] = [{"key": "k", "state": "neutral", "label": "Pending"}]
        status = sc.provider_status_from_wire(payload)
        self.assertIs(status.availability, sc.Availability.UNKNOWN)

    def test_an_unrecognised_segment_state_degrades_to_unknown(self) -> None:
        payload = _payload()
        payload["segments"][0]["state"] = "glowing"
        status = sc.provider_status_from_wire(payload)
        self.assertIs(status.segments[0].state, sc.SegmentState.UNKNOWN)

    def test_an_unrecognised_availability_cannot_smuggle_an_ok_segment(self) -> None:
        # Degrading availability to unknown must not leave an `ok` segment
        # standing: the host would render a health claim it cannot support.
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(_payload(availability="quantum_pending"))

    def test_an_unrecognised_confidence_subject_degrades_to_unspecified(self) -> None:
        payload = _payload()
        payload["segments"][0].update(
            {"confidence": "high", "confidence_of": "vibes"}
        )
        status = sc.provider_status_from_wire(payload)
        self.assertIs(status.segments[0].confidence_of, sc.ConfidenceSubject.UNSPECIFIED)

    def test_an_unrecognised_confidence_level_is_refused(self) -> None:
        # There is no unknown confidence level, and guessing between low and
        # high is not a degradation, it is a fabrication.
        payload = _payload()
        payload["segments"][0].update({"confidence": "certain", "confidence_of": "verification"})
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(payload)


class WireStrictnessTest(unittest.TestCase):
    def test_an_unrecognised_scope_is_refused_not_guessed(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(_payload(scope="galaxy"))

    def test_a_missing_scope_is_refused(self) -> None:
        payload = _payload()
        del payload["scope"]
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(payload)

    def test_host_wide_state_cannot_arrive_scoped_to_a_session(self) -> None:
        # The specific failure the strict rule exists to prevent: no fallback
        # may land on a real scope.
        for bad in ("galaxy", "", "HOST", None, 1):
            with self.subTest(scope=bad):
                with self.assertRaises(sc.ContractViolation):
                    sc.provider_status_from_wire(_payload(scope=bad))

    def test_a_future_contract_version_is_refused(self) -> None:
        with self.assertRaises(sc.UnsupportedContractVersion):
            sc.provider_status_from_wire(
                _payload(contract_version=sc.CONTRACT_VERSION + 1)
            )

    def test_a_missing_contract_version_is_refused(self) -> None:
        payload = _payload()
        del payload["contract_version"]
        with self.assertRaises(sc.UnsupportedContractVersion):
            sc.provider_status_from_wire(payload)

    def test_an_unsupported_version_is_still_a_contract_violation(self) -> None:
        # Callers that only catch ContractViolation must not miss this.
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(_payload(contract_version="1"))

    def test_a_missing_provider_id_is_refused(self) -> None:
        payload = _payload()
        del payload["provider"]
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(payload)

    def test_a_parsed_payload_is_still_privacy_checked(self) -> None:
        # Parsing is not a bypass around the label rules: a provider cannot
        # reach the renderer with a path just because it arrived over the wire.
        payload = _payload()
        payload["segments"][0]["label"] = "see /var/log/fornax.log"
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(payload)

    def test_a_parsed_payload_is_still_bounds_checked(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.provider_status_from_wire(
                _payload(cache_ttl_seconds=sc.MAX_CACHE_TTL_SECONDS + 1)
            )


class NotAvailableConstructorTest(unittest.TestCase):
    def _built(self, availability: sc.Availability) -> sc.ProviderStatus:
        return sc.not_available(
            "circinus",
            "1.2.0",
            sc.Scope.HOST,
            availability,
            "daemon_not_running",
            "daemon not running",
        )

    def test_every_not_available_state_can_be_built(self) -> None:
        for availability in sc.Availability:
            if availability.has_live_readings:
                continue
            with self.subTest(availability=availability):
                status = self._built(availability)
                self.assertIs(status.availability, availability)

    def test_a_not_available_status_is_never_silent(self) -> None:
        # An empty segment list renders as nothing, which reads as all-clear.
        for availability in sc.Availability:
            if availability.has_live_readings:
                continue
            with self.subTest(availability=availability):
                self.assertEqual(len(self._built(availability).segments), 1)

    def test_a_not_available_status_never_claims_health(self) -> None:
        for availability in sc.Availability:
            if availability.has_live_readings:
                continue
            with self.subTest(availability=availability):
                state = self._built(availability).segments[0].state
                self.assertIsNot(state, sc.SegmentState.OK)

    def test_a_probe_failure_is_actionable_not_merely_neutral(self) -> None:
        # PROBE_FAILED != NOTHING_HAPPENED: an error the user may need to act
        # on must not render with the same weight as "not installed".
        self.assertIs(
            self._built(sc.Availability.ERROR).segments[0].state, sc.SegmentState.WARN
        )
        self.assertIs(
            self._built(sc.Availability.UNSUPPORTED).segments[0].state,
            sc.SegmentState.NEUTRAL,
        )

    def test_available_cannot_be_built_this_way(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            self._built(sc.Availability.AVAILABLE)

    def test_a_non_availability_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            self._built("unavailable")  # type: ignore[arg-type]

    def test_a_raw_error_string_cannot_become_the_reason(self) -> None:
        # The case this constructor exists to prevent: an exception message
        # carrying a path, reaching the user's terminal.
        with self.assertRaises(sc.ContractViolation):
            sc.not_available(
                "circinus",
                "1.2.0",
                sc.Scope.HOST,
                sc.Availability.ERROR,
                "probe_failed",
                "connect failed: /tmp/circinus.sock",
            )

    def test_every_not_available_state_has_a_segment_state_mapping(self) -> None:
        expected = {a for a in sc.Availability if not a.has_live_readings}
        self.assertEqual(set(sc._NOT_AVAILABLE_STATE), expected)


class OrderingTest(unittest.TestCase):
    def _at(self, provider: str, order_hint: int) -> sc.ProviderStatus:
        return _status(provider=provider, order_hint=order_hint)

    def test_providers_order_by_hint_then_id(self) -> None:
        ordered = sc.order_providers(
            [self._at("libra", 30), self._at("circinus", 30), self._at("fornax", 20)]
        )
        self.assertEqual([s.provider for s in ordered], ["fornax", "circinus", "libra"])

    def test_ordering_is_stable_across_repeated_renders(self) -> None:
        statuses = [self._at("libra", 30), self._at("circinus", 30)]
        first = [s.provider for s in sc.order_providers(statuses)]
        for _ in range(5):
            self.assertEqual([s.provider for s in sc.order_providers(statuses)], first)

    def test_input_order_cannot_change_the_output_order(self) -> None:
        a, b, c = self._at("libra", 30), self._at("circinus", 30), self._at("fornax", 20)
        self.assertEqual(
            [s.provider for s in sc.order_providers([a, b, c])],
            [s.provider for s in sc.order_providers([c, b, a])],
        )

    def test_a_duplicate_provider_is_refused_not_deduplicated(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.order_providers([self._at("libra", 10), self._at("libra", 20)])

    def test_a_bare_status_is_not_mistaken_for_a_collection(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.order_providers(self._at("libra", 10))

    def test_a_non_status_value_is_refused(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.order_providers([{"provider": "libra"}])

    def test_an_empty_registry_orders_to_nothing(self) -> None:
        self.assertEqual(sc.order_providers([]), ())

    def test_segments_order_by_hint_then_key(self) -> None:
        status = _status(
            segments=(
                _segment(key="zz", order_hint=0),
                _segment(key="aa", order_hint=5),
                _segment(key="bb", order_hint=0),
            )
        )
        self.assertEqual(
            [s.key for s in sc.order_segments(status)], ["bb", "zz", "aa"]
        )

    def test_segment_ordering_takes_a_status_not_a_list(self) -> None:
        with self.assertRaises(sc.ContractViolation):
            sc.order_segments([_segment()])


#
# AC 8 / AC 9 representability. These tests are the reason the contract has the
# shape it has: each one encodes a real field from a real product's existing
# state surface, so a later change that makes one of them unrepresentable fails
# here rather than during that product's integration.
#


def _fornax_verdict(verdict: str) -> sc.ProviderStatus:
    """Fornax's five-verdict vocabulary, one verdict at a time.

    The mapping is the host's, not Fornax's: `fornax-types` keeps five
    `Verdict` variants and this contract must render each one distinguishably.
    Note that verdict `Unavailable` — a finding whose signal could not be
    collected — is carried while the *provider* is `AVAILABLE`, because the
    daemon answered. Collapsing those two would lose exactly the semantics
    AC 8 protects.
    """
    by_verdict = {
        "verified": (sc.SegmentState.OK, "Verified"),
        "unverified": (sc.SegmentState.ATTENTION, "Unverified"),
        "contradicted": (sc.SegmentState.CRITICAL, "Contradicted"),
        "review": (sc.SegmentState.WARN, "Needs review"),
        "unavailable": (sc.SegmentState.NEUTRAL, "Signal unavailable"),
    }
    state, label = by_verdict[verdict]
    return sc.ProviderStatus(
        provider="fornax",
        provider_version="0.0.8",
        scope=sc.Scope.HOST,
        availability=sc.Availability.AVAILABLE,
        observed_at="2026-09-29T08:00:00Z",
        cache_ttl_seconds=5,
        order_hint=20,
        segments=(
            sc.Segment(
                key="latest_verdict",
                state=state,
                label=label,
                reason_code="evidence_gap" if verdict == "unverified" else None,
                reason_label="evidence gap" if verdict == "unverified" else None,
                age_seconds=7200,
                explain_key="fornax.latest_verdict",
            ),
        ),
    )


class FornaxRepresentabilityTest(unittest.TestCase):
    """AC 8 — existing Fornax DogFood output, no loss of truthful semantics."""

    VERDICTS = ("verified", "unverified", "contradicted", "review", "unavailable")

    def test_all_five_verdicts_are_representable(self) -> None:
        for verdict in self.VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertEqual(len(_fornax_verdict(verdict).segments), 1)

    def test_the_five_verdict_vocabulary_is_never_collapsed(self) -> None:
        rendered = {
            (s.segments[0].state, s.segments[0].label)
            for s in (_fornax_verdict(v) for v in self.VERDICTS)
        }
        self.assertEqual(len(rendered), len(self.VERDICTS))

    def test_an_unavailable_signal_is_not_an_unavailable_provider(self) -> None:
        status = _fornax_verdict("unavailable")
        self.assertIs(status.availability, sc.Availability.AVAILABLE)
        self.assertIsNot(status.segments[0].state, sc.SegmentState.UNKNOWN)

    def test_unverified_is_not_promoted_to_healthy_by_activity(self) -> None:
        # The founder wrapper's failure mode: a daemon with events is not a
        # verified claim. `unverified` must stay non-ok however busy Fornax is.
        status = _fornax_verdict("unverified")
        self.assertIsNot(status.segments[0].state, sc.SegmentState.OK)
        self.assertTrue(status.segments[0].state.makes_a_health_claim)

    def test_observing_is_a_host_state_not_a_sixth_verdict(self) -> None:
        # Provider reachable, no finding yet. Fornax has no OBSERVING verdict,
        # so this is presentation only and must not claim health.
        observing = _status(
            provider="fornax",
            segments=(
                _segment(key="latest_verdict", state=sc.SegmentState.NEUTRAL, label="Observing"),
            ),
        )
        self.assertIs(observing.availability, sc.Availability.AVAILABLE)
        self.assertFalse(observing.segments[0].state.makes_a_health_claim)
        verdict_labels = {
            _fornax_verdict(v).segments[0].label for v in self.VERDICTS
        }
        self.assertNotIn(observing.segments[0].label, verdict_labels)

    def test_an_unverified_reason_may_be_absent_without_becoming_a_guess(self) -> None:
        # "Do not infer a reason from event count" — unknown stays unknown.
        status = _status(
            provider="fornax",
            segments=(
                _segment(key="latest_verdict", state=sc.SegmentState.ATTENTION, label="Unverified"),
            ),
        )
        self.assertIsNone(status.segments[0].reason_code)

    def test_a_daemon_that_is_not_running_is_a_provider_level_state(self) -> None:
        status = sc.not_available(
            "fornax",
            "0.0.8",
            sc.Scope.HOST,
            sc.Availability.UNAVAILABLE,
            "daemon_not_running",
            "daemon not running",
        )
        self.assertFalse(status.availability.has_live_readings)
        self.assertEqual(status.segments[0].reason_code, "daemon_not_running")

    def test_a_transcript_rationale_cannot_be_carried(self) -> None:
        # Fornax `Finding.rationale` is free text derived from a transcript.
        # There is no field for it, and it would fail the label rules anyway.
        self.assertNotIn(
            "rationale", {f.name for f in dataclasses.fields(sc.Segment)}
        )


class CircinusRepresentabilityTest(unittest.TestCase):
    """AC 9 — planned Circinus fields, no product-specific host hacks."""

    def _status(self) -> sc.ProviderStatus:
        return sc.ProviderStatus(
            provider="circinus",
            provider_version="1.2.0",
            scope=sc.Scope.HOST,
            availability=sc.Availability.AVAILABLE,
            observed_at="2026-09-29T08:00:00Z",
            cache_ttl_seconds=5,
            order_hint=30,
            segments=(
                sc.Segment(
                    key="mode",
                    state=sc.SegmentState.NEUTRAL,
                    label="shadow mode",
                    explain_key="circinus.mode",
                    order_hint=1,
                ),
                sc.Segment(
                    key="latest_decision",
                    state=sc.SegmentState.OK,
                    label="allowed",
                    age_seconds=45,
                    explain_key="circinus.latest_decision",
                    order_hint=2,
                ),
                sc.Segment(
                    key="would_block",
                    state=sc.SegmentState.ATTENTION,
                    label="would have blocked",
                    count=113,
                    total=705,
                    count_label="decisions",
                    hypothetical=True,
                    explain_key="circinus.would_block",
                    order_hint=3,
                ),
            ),
        )

    def test_mode_latest_decision_and_would_block_all_fit(self) -> None:
        keys = {s.key for s in self._status().segments}
        self.assertEqual(keys, {"mode", "latest_decision", "would_block"})

    def test_a_shadow_would_block_is_marked_hypothetical(self) -> None:
        # Shadow-mode would-block must never read as an executed enforcement.
        would_block = self._status().segments[2]
        self.assertTrue(would_block.hypothetical)
        self.assertNotIn("blocked (", would_block.label)

    def test_the_hypothetical_marker_survives_the_wire(self) -> None:
        payload = self._status().to_wire()
        segment = next(s for s in payload["segments"] if s["key"] == "would_block")
        self.assertIs(segment["hypothetical"], True)

    def test_would_block_is_a_labelled_fraction_not_an_abbreviation(self) -> None:
        would_block = self._status().segments[2]
        self.assertEqual((would_block.count, would_block.total), (113, 705))
        self.assertEqual(would_block.count_label, "decisions")
        self.assertNotIn("wb", would_block.label)

    def test_a_running_mode_differing_from_config_is_representable(self) -> None:
        # Runtime truth wins; the stale marker is a state, not a footnote.
        stale = sc.Segment(
            key="mode",
            state=sc.SegmentState.WARN,
            label="shadow mode, restart required",
            reason_code="config_changed_since_start",
            reason_label="config changed since start",
            explain_key="circinus.mode",
        )
        self.assertIs(stale.state, sc.SegmentState.WARN)


class LibraRepresentabilityTest(unittest.TestCase):
    """AC 9 — planned Libra fields, no product-specific host hacks."""

    def _status(self) -> sc.ProviderStatus:
        return sc.ProviderStatus(
            provider="libra",
            provider_version="0.0.2",
            scope=sc.Scope.HOST,
            availability=sc.Availability.AVAILABLE,
            observed_at="2026-09-29T08:00:00Z",
            cache_ttl_seconds=5,
            order_hint=40,
            segments=(
                sc.Segment(
                    key="task",
                    state=sc.SegmentState.NEUTRAL,
                    label="planning",
                    explain_key="libra.task",
                    order_hint=1,
                ),
                sc.Segment(
                    key="preflight",
                    state=sc.SegmentState.OK,
                    label="high",
                    confidence=sc.Confidence.HIGH,
                    confidence_of=sc.ConfidenceSubject.PREFLIGHT_ESTIMATE,
                    explain_key="libra.preflight",
                    order_hint=2,
                ),
                sc.Segment(
                    key="remaining_p90",
                    state=sc.SegmentState.NEUTRAL,
                    label="P90 ≤ 12m",
                    explain_key="libra.remaining_p90",
                    order_hint=3,
                ),
                sc.Segment(
                    key="replan_state",
                    state=sc.SegmentState.ATTENTION,
                    label="escalated — awaiting approval",
                    explain_key="libra.replan_state",
                    order_hint=4,
                ),
            ),
        )

    def test_all_four_planned_segments_fit_within_the_bound(self) -> None:
        segments = self._status().segments
        self.assertEqual(len(segments), 4)
        self.assertLessEqual(len(segments), sc.MAX_SEGMENTS_PER_PROVIDER)

    def test_confidence_carries_its_subject_so_it_cannot_read_as_severity(self) -> None:
        preflight = self._status().segments[1]
        self.assertIs(preflight.confidence, sc.Confidence.HIGH)
        self.assertEqual(preflight.confidence_of.label, "preflight confidence")

    def test_the_confidence_subject_survives_the_wire(self) -> None:
        payload = self._status().to_wire()
        segment = next(s for s in payload["segments"] if s["key"] == "preflight")
        self.assertEqual(segment["confidence_of"], "preflight_estimate")

    def test_escalation_is_explicit_prose_not_a_bang_prefix(self) -> None:
        replan = self._status().segments[3]
        self.assertIn("awaiting approval", replan.label)
        self.assertFalse(replan.label.startswith("!"))

    def test_no_segment_uses_the_pf_abbreviation(self) -> None:
        for segment in self._status().segments:
            with self.subTest(key=segment.key):
                self.assertNotIn("pf:", segment.label)

    def test_a_stale_profile_is_representable_without_lying(self) -> None:
        stale = sc.Segment(
            key="profile",
            state=sc.SegmentState.WARN,
            label="balanced, restart required",
            reason_code="profile_changed_since_start",
            reason_label="profile changed since start",
            explain_key="libra.profile",
        )
        self.assertIs(stale.state, sc.SegmentState.WARN)

    def test_the_statusline_carries_no_approval_affordance(self) -> None:
        # Rendering is read-only: there is no field through which a provider
        # could offer an approve/deny action.
        fields = {f.name for f in dataclasses.fields(sc.Segment)}
        self.assertEqual(fields & {"action", "command", "callback", "on_click"}, set())


class MultiProductCompositionTest(unittest.TestCase):
    """AC 6 — adding a provider needs no edit to another product's renderer."""

    def _all(self) -> tuple[sc.ProviderStatus, ...]:
        return (
            _fornax_verdict("unverified"),
            CircinusRepresentabilityTest()._status(),
            LibraRepresentabilityTest()._status(),
        )

    def test_three_products_compose_in_one_deterministic_order(self) -> None:
        ordered = sc.order_providers(self._all())
        self.assertEqual(
            [s.provider for s in ordered], ["fornax", "circinus", "libra"]
        )

    def test_every_product_uses_the_same_segment_type(self) -> None:
        for status in self._all():
            for segment in status.segments:
                with self.subTest(provider=status.provider, key=segment.key):
                    self.assertIsInstance(segment, sc.Segment)

    def test_no_product_supplies_its_own_iconography(self) -> None:
        # The host owns glyphs. A provider emitting one would have to smuggle
        # it through a label, which the allowlist rejects.
        for status in self._all():
            for segment in status.segments:
                with self.subTest(provider=status.provider, key=segment.key):
                    for char in segment.label:
                        self.assertNotEqual(ord(char), 0xFE0F)
                        self.assertLess(ord(char), 0x2500)

    def test_every_segment_is_understandable_without_emoji(self) -> None:
        for status in self._all():
            for segment in status.segments:
                with self.subTest(provider=status.provider, key=segment.key):
                    self.assertTrue(segment.label.strip())
                    self.assertTrue(any(c.isalpha() for c in segment.label))

    def test_every_segment_offers_progressive_disclosure(self) -> None:
        for status in self._all():
            for segment in status.segments:
                with self.subTest(provider=status.provider, key=segment.key):
                    self.assertIsNotNone(segment.explain_key)

    def test_every_explain_key_is_namespaced_to_its_provider(self) -> None:
        for status in self._all():
            for segment in status.segments:
                with self.subTest(provider=status.provider, key=segment.key):
                    self.assertTrue(
                        segment.explain_key.startswith(f"{status.provider}.")
                    )

    def test_the_whole_composition_round_trips_over_the_wire(self) -> None:
        for status in self._all():
            with self.subTest(provider=status.provider):
                payload = status.to_wire()
                self.assertEqual(
                    sc.provider_status_from_wire(payload).to_wire(), payload
                )


if __name__ == "__main__":
    unittest.main()
