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


if __name__ == "__main__":
    unittest.main()
