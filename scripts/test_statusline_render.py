"""Tests for the shared statusline presentation module (HORO-1565).

Two groups of assertions here are load-bearing rather than illustrative, and
should not be relaxed without reading why they exist:

- `TestEmojiPresentationSafety` and `TestGlyphTables` are the regression guard
  for a live defect: Libra emitted U+2696 and U+1F6E1 without U+FE0F, so
  terminals rendered them as narrow monochrome text and the column accounting
  around them went wrong. Adding an unsafe glyph to a table fails these.
- `TestNoUnknownFieldIsRendered` attaches secret-shaped decoy attributes to
  otherwise valid contract objects. If someone later teaches the renderer to
  read a field the contract never validated, the canary appears in the output
  and the test fails. Canaries are assembled at runtime so this file itself
  contains no secret-shaped literal.
"""

from __future__ import annotations

import re
import unicodedata
import unittest

import statusline_contract as contract
import statusline_render as render

MODES = tuple(render.PresentationMode)


def segment(**overrides) -> contract.Segment:
    """A valid minimal segment, overridable per test."""
    fields = {
        "key": "example",
        "state": contract.SegmentState.OK,
        "label": "Example state",
    }
    fields.update(overrides)
    return contract.Segment(**fields)


def status(**overrides) -> contract.ProviderStatus:
    """A valid minimal provider answer, overridable per test."""
    fields = {
        "provider": "example",
        "provider_version": "1.0.0",
        "scope": contract.Scope.SESSION,
        "availability": contract.Availability.AVAILABLE,
        "segments": (segment(),),
    }
    fields.update(overrides)
    return contract.ProviderStatus(**fields)


# The three real first providers, with the state values each product actually
# has. These are the fixtures the readability requirements were written against.
def fornax_status(**overrides) -> contract.ProviderStatus:
    fields = {
        "provider": "fornax",
        "provider_version": "0.4.1",
        "scope": contract.Scope.PROJECT,
        "availability": contract.Availability.AVAILABLE,
        "order_hint": 100,
        "segments": (
            segment(
                key="latest_verdict",
                state=contract.SegmentState.OK,
                label="Verified",
                count=3,
                total=3,
                count_label="claims",
                age_seconds=125,
                explain_key="fornax.latest_verdict",
            ),
        ),
    }
    fields.update(overrides)
    return contract.ProviderStatus(**fields)


def circinus_status(**overrides) -> contract.ProviderStatus:
    fields = {
        "provider": "circinus",
        "provider_version": "1.2.0",
        "scope": contract.Scope.HOST,
        "availability": contract.Availability.AVAILABLE,
        "order_hint": 200,
        "segments": (
            segment(
                key="would_block",
                state=contract.SegmentState.WARN,
                label="Shadow mode",
                hypothetical=True,
                count=2,
                total=14,
                count_label="tool calls",
                explain_key="circinus.would_block",
            ),
        ),
    }
    fields.update(overrides)
    return contract.ProviderStatus(**fields)


def libra_status(**overrides) -> contract.ProviderStatus:
    fields = {
        "provider": "libra-governor",
        "provider_version": "0.9.0",
        "scope": contract.Scope.SESSION,
        "availability": contract.Availability.AVAILABLE,
        "order_hint": 300,
        "segments": (
            # The escalation carries the lower order_hint because a person being
            # waited on outranks an estimate about work not yet attempted.
            segment(
                key="preflight",
                state=contract.SegmentState.OK,
                label="Preflight",
                confidence=contract.Confidence.HIGH,
                confidence_of=contract.ConfidenceSubject.PREFLIGHT_ESTIMATE,
                explain_key="libra.preflight",
                order_hint=10,
            ),
            segment(
                key="approval",
                state=contract.SegmentState.CRITICAL,
                label="Awaiting your approval",
                reason_code="escalated_to_human",
                order_hint=0,
            ),
        ),
    }
    fields.update(overrides)
    return contract.ProviderStatus(**fields)


UPSTREAM = "~/proj  main*  claude-opus-5  $0.42"


class TestPresentationModeParse(unittest.TestCase):
    def test_every_member_round_trips_from_its_value(self):
        for mode in MODES:
            self.assertIs(render.PresentationMode.parse(mode.value), mode)

    def test_a_member_is_returned_unchanged(self):
        for mode in MODES:
            self.assertIs(render.PresentationMode.parse(mode), mode)

    def test_case_and_surrounding_space_are_tolerated(self):
        self.assertIs(
            render.PresentationMode.parse("  COMPACT "), render.PresentationMode.COMPACT
        )

    def test_unrecognised_input_falls_back_rather_than_raising(self):
        for value in ("", "verbose", None, 7, [], object()):
            self.assertIs(render.PresentationMode.parse(value), render.PresentationMode.BALANCED)

    def test_the_fallback_is_overridable(self):
        self.assertIs(
            render.PresentationMode.parse("nope", render.PresentationMode.PLAIN),
            render.PresentationMode.PLAIN,
        )

    def test_only_plain_declines_glyphs(self):
        self.assertFalse(render.PresentationMode.PLAIN.uses_glyphs)
        self.assertTrue(render.PresentationMode.BALANCED.uses_glyphs)
        self.assertTrue(render.PresentationMode.COMPACT.uses_glyphs)


class TestGraphemeClusters(unittest.TestCase):
    def test_ascii_is_one_cluster_per_character(self):
        self.assertEqual(render.grapheme_clusters("abc"), ["a", "b", "c"])

    def test_empty_text_has_no_clusters(self):
        self.assertEqual(render.grapheme_clusters(""), [])

    def test_a_variation_selector_joins_its_base(self):
        self.assertEqual(render.grapheme_clusters("⚠️"), ["⚠️"])

    def test_a_zwj_sequence_is_one_cluster(self):
        family = "\U0001f468‍\U0001f469‍\U0001f467"
        self.assertEqual(render.grapheme_clusters(family), [family])

    def test_a_skin_tone_modifier_joins_its_base(self):
        self.assertEqual(render.grapheme_clusters("\U0001f44d\U0001f3fd"), ["\U0001f44d\U0001f3fd"])

    def test_a_keycap_sequence_is_one_cluster(self):
        self.assertEqual(render.grapheme_clusters("3️⃣"), ["3️⃣"])

    def test_a_combining_mark_joins_its_base(self):
        self.assertEqual(render.grapheme_clusters("é"), ["é"])

    def test_two_regional_indicators_form_one_flag(self):
        taiwan = "\U0001f1f9\U0001f1fc"
        self.assertEqual(render.grapheme_clusters(taiwan), [taiwan])

    def test_four_regional_indicators_form_two_flags(self):
        # The case that makes pairing rather than run-length the right rule: a
        # third indicator starts a new flag, it does not extend the first.
        two = "\U0001f1f9\U0001f1fc\U0001f1ef\U0001f1f5"
        self.assertEqual(render.grapheme_clusters(two), [two[:2], two[2:]])

    def test_a_tag_sequence_flag_is_one_cluster(self):
        scotland = "\U0001f3f4" + "".join(chr(cp) for cp in (0xE0067, 0xE0062, 0xE0073, 0xE0063, 0xE0074)) + "\U000E007F"
        self.assertEqual(render.grapheme_clusters(scotland), [scotland])

    def test_clusters_always_rejoin_to_the_original_text(self):
        for text in ("", "a", "⚠️ ok", "\U0001f1f9\U0001f1fc\U0001f1ef\U0001f1f5", "éx"):
            self.assertEqual("".join(render.grapheme_clusters(text)), text)


class TestDisplayWidth(unittest.TestCase):
    def test_ascii_is_one_column_per_character(self):
        self.assertEqual(render.display_width("hello"), 5)

    def test_empty_text_is_zero_columns(self):
        self.assertEqual(render.display_width(""), 0)

    def test_a_default_emoji_presentation_glyph_is_two_columns(self):
        self.assertEqual(render.display_width("✅"), 2)

    def test_a_variation_selector_sequence_is_two_columns(self):
        # U+26A0 alone is East-Asian-narrow; the selector is what makes it wide,
        # so charging 1 here is the under-count that wraps the line.
        self.assertEqual(render.display_width("⚠"), 1)
        self.assertEqual(render.display_width("⚠️"), 2)

    def test_a_zwj_sequence_is_charged_for_every_visible_component(self):
        # Deliberately conservative: terminals disagree between 2 and 6, and
        # over-charging can only leave a spare column.
        self.assertEqual(render.display_width("\U0001f468‍\U0001f469‍\U0001f467"), 6)

    def test_a_skin_tone_modifier_is_charged(self):
        self.assertEqual(render.display_width("\U0001f44d\U0001f3fd"), 4)

    def test_a_flag_is_two_columns(self):
        self.assertEqual(render.display_width("\U0001f1f9\U0001f1fc"), 2)

    def test_a_combining_mark_adds_nothing(self):
        self.assertEqual(render.display_width("é"), 1)

    def test_a_cluster_never_measures_zero(self):
        for text in ("️", "‍", "́"):
            self.assertGreaterEqual(render.display_width(text), 1)

    def test_width_is_additive_over_clusters(self):
        text = "ok ✅ ⚠️ done"
        self.assertEqual(
            render.display_width(text),
            sum(render.cluster_width(c) for c in render.grapheme_clusters(text)),
        )


class TestTruncateToWidth(unittest.TestCase):
    def test_text_within_budget_is_returned_unchanged(self):
        self.assertEqual(render.truncate_to_width("short", 10), "short")

    def test_text_exactly_at_budget_is_returned_unchanged(self):
        self.assertEqual(render.truncate_to_width("12345", 5), "12345")

    def test_a_nonpositive_budget_yields_nothing(self):
        for budget in (0, -1, -100):
            self.assertEqual(render.truncate_to_width("anything", budget), "")

    def test_a_budget_that_cannot_hold_the_marker_yields_nothing(self):
        for budget in (1, 2, 3):
            self.assertEqual(render.truncate_to_width("anything", budget), "")

    def test_the_result_never_exceeds_the_budget(self):
        text = "Awaiting your approval on a long running task"
        for budget in range(0, 60):
            self.assertLessEqual(render.display_width(render.truncate_to_width(text, budget)), budget)

    def test_a_multi_codepoint_cluster_is_never_split(self):
        text = "\U0001f468‍\U0001f469‍\U0001f467 family"
        for budget in range(0, 20):
            result = render.truncate_to_width(text, budget)
            for cluster in render.grapheme_clusters(result.removesuffix(render.ELLIPSIS)):
                self.assertIn(cluster, render.grapheme_clusters(text))

    def test_a_single_cluster_too_wide_to_fit_yields_nothing_not_a_lone_marker(self):
        # A bare "..." is the same nothing as an empty string and costs three
        # columns to say it.
        self.assertEqual(render.truncate_to_width("\U0001f468‍\U0001f469‍\U0001f467 x", 7), "")

    def test_trailing_space_is_not_left_before_the_marker(self):
        for budget in range(4, 14):
            with self.subTest(budget=budget):
                self.assertFalse(render.truncate_to_width("aaa bbb ccc", budget).endswith(" ..."))

    def test_the_marker_is_ascii_so_it_survives_a_terminal_that_cannot_render_glyphs(self):
        self.assertTrue(render.ELLIPSIS.isascii())


class TestEmojiPresentationSafety(unittest.TestCase):
    """The mechanical regression guard for the live Libra glyph defect."""

    def test_the_two_glyphs_libra_rendered_broken_are_refused(self):
        for codepoint in (0x2696, 0x1F6E1):  # SCALES, SHIELD — emitted bare
            with self.subTest(codepoint=hex(codepoint)):
                self.assertFalse(render.is_emoji_presentation_safe(chr(codepoint)))

    def test_adding_the_variation_selector_makes_those_glyphs_safe(self):
        for codepoint in (0x2696, 0x1F6E1):
            with self.subTest(codepoint=hex(codepoint)):
                self.assertTrue(render.is_emoji_presentation_safe(chr(codepoint) + "️"))

    def test_the_glyph_libra_rendered_correctly_is_accepted(self):
        # U+1F9ED COMPASS worked in the same broken line, which is why the bug
        # looked arbitrary rather than systematic.
        self.assertTrue(render.is_emoji_presentation_safe("\U0001f9ed"))

    def test_a_narrow_symbol_without_a_selector_is_refused(self):
        self.assertFalse(render.is_emoji_presentation_safe("●"))  # BLACK CIRCLE

    def test_an_empty_glyph_is_refused(self):
        self.assertFalse(render.is_emoji_presentation_safe(""))

    def test_the_rule_matches_east_asian_width_for_bare_codepoints(self):
        for codepoint in (0x2705, 0x1F7E0, 0x1F6D1, 0x26AA, 0x2754, 0x1F4BB):
            char = chr(codepoint)
            with self.subTest(codepoint=hex(codepoint)):
                self.assertEqual(
                    render.is_emoji_presentation_safe(char),
                    unicodedata.east_asian_width(char) in ("W", "F"),
                )


class TestGlyphTables(unittest.TestCase):
    def all_glyphs(self):
        yield from render.STATE_GLYPHS.items()
        yield from render.SCOPE_GLYPHS.items()

    def test_every_host_owned_glyph_is_presentation_safe(self):
        for key, glyph in self.all_glyphs():
            with self.subTest(key=key):
                self.assertTrue(render.is_emoji_presentation_safe(glyph))

    def test_every_host_owned_glyph_measures_two_columns(self):
        for key, glyph in self.all_glyphs():
            with self.subTest(key=key):
                self.assertEqual(render.display_width(glyph), 2)

    def test_every_host_owned_glyph_is_a_single_cluster(self):
        for key, glyph in self.all_glyphs():
            with self.subTest(key=key):
                self.assertEqual(len(render.grapheme_clusters(glyph)), 1)

    def test_glyphs_are_distinct_within_each_table(self):
        for table in (render.STATE_GLYPHS, render.SCOPE_GLYPHS):
            self.assertEqual(len(set(table.values())), len(table))

    def test_state_glyphs_cover_exactly_the_contract_states(self):
        self.assertEqual(
            set(render.STATE_GLYPHS), {member.value for member in contract.SegmentState}
        )

    def test_scope_glyphs_cover_exactly_the_contract_scopes(self):
        self.assertEqual(set(render.SCOPE_GLYPHS), {member.value for member in contract.Scope})

    def test_every_glyph_has_a_text_equivalent(self):
        self.assertEqual(set(render.STATE_GLYPHS), set(render.STATE_TEXT))
        self.assertEqual(set(render.SCOPE_GLYPHS), set(render.SCOPE_TEXT))

    def test_every_text_equivalent_is_ascii(self):
        for value in list(render.STATE_TEXT.values()) + list(render.SCOPE_TEXT.values()):
            with self.subTest(value=value):
                self.assertTrue(value.isascii())

    def test_scope_text_matches_the_contract_documented_tokens(self):
        # These three tokens are fixed by the provider contract, not chosen here.
        self.assertEqual(
            render.SCOPE_TEXT, {"host": "[host]", "session": "[session]", "project": "[project]"}
        )

    def test_every_contract_state_has_a_severity(self):
        self.assertEqual(
            set(render.STATE_SEVERITY), {member.value for member in contract.SegmentState}
        )

    def test_not_available_states_do_not_outrank_a_real_problem(self):
        self.assertLess(render.STATE_SEVERITY["neutral"], render.STATE_SEVERITY["warn"])
        self.assertLess(render.STATE_SEVERITY["unknown"], render.STATE_SEVERITY["critical"])
        self.assertEqual(render.STATE_SEVERITY["ok"], render.STATE_SEVERITY["neutral"])


class TestStateMarker(unittest.TestCase):
    def test_plain_mode_uses_the_word(self):
        for state, word in render.STATE_TEXT.items():
            with self.subTest(state=state):
                self.assertEqual(render.state_marker(state, render.PresentationMode.PLAIN), word)

    def test_balanced_mode_uses_the_glyph_beside_the_label(self):
        for state, glyph in render.STATE_GLYPHS.items():
            with self.subTest(state=state):
                self.assertEqual(
                    render.state_marker(state, render.PresentationMode.BALANCED), glyph
                )

    def test_compact_mode_keeps_the_word_for_an_emphatic_state(self):
        for state in render.EMPHATIC_STATES:
            with self.subTest(state=state):
                marker = render.state_marker(state, render.PresentationMode.COMPACT)
                self.assertIn(render.STATE_TEXT[state], marker)
                self.assertIn(render.STATE_GLYPHS[state], marker)

    def test_compact_mode_drops_the_word_for_a_calm_state(self):
        for state in ("ok", "neutral"):
            with self.subTest(state=state):
                self.assertEqual(
                    render.state_marker(state, render.PresentationMode.COMPACT),
                    render.STATE_GLYPHS[state],
                )

    def test_every_exception_state_is_emphatic(self):
        self.assertEqual(render.EMPHATIC_STATES, {"unknown", "attention", "warn", "critical"})

    def test_an_unrecognised_state_degrades_to_unknown_not_to_blank(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    render.state_marker("future_state", mode),
                    render.state_marker("unknown", mode),
                )

    def test_no_state_ever_renders_blank(self):
        for state in list(render.STATE_TEXT) + ["", "bogus"]:
            for mode in MODES:
                with self.subTest(state=state, mode=mode):
                    self.assertTrue(render.state_marker(state, mode).strip())


class TestFormatAge(unittest.TestCase):
    def test_seconds_minutes_hours_and_days(self):
        cases = {0: "0s", 59: "59s", 60: "1m", 3599: "59m", 3600: "1h", 86399: "23h", 86400: "1d"}
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    render.format_age(seconds, render.PresentationMode.COMPACT), expected
                )

    def test_rounding_is_down_so_freshness_is_never_overstated(self):
        self.assertEqual(render.format_age(119, render.PresentationMode.COMPACT), "1m")
        self.assertEqual(render.format_age(7199, render.PresentationMode.COMPACT), "1h")

    def test_a_negative_reading_is_clamped(self):
        self.assertEqual(render.format_age(-10, render.PresentationMode.COMPACT), "0s")

    def test_non_compact_modes_say_ago_so_it_is_not_read_as_a_budget(self):
        for mode in (render.PresentationMode.BALANCED, render.PresentationMode.PLAIN):
            with self.subTest(mode=mode):
                self.assertEqual(render.format_age(60, mode), "1m ago")

    def test_the_rendering_is_ascii_in_every_mode(self):
        for mode in MODES:
            self.assertTrue(render.format_age(90061, mode).isascii())


class TestFormatCount(unittest.TestCase):
    def test_a_count_without_a_total(self):
        for mode in MODES:
            self.assertEqual(render.format_count(3, None, "findings", mode), "3 findings")

    def test_a_count_out_of_a_total_reads_as_prose_when_there_is_room(self):
        self.assertEqual(
            render.format_count(2, 14, "tool calls", render.PresentationMode.BALANCED),
            "2 of 14 tool calls",
        )

    def test_compact_mode_uses_a_slash(self):
        self.assertEqual(
            render.format_count(2, 14, "tool calls", render.PresentationMode.COMPACT),
            "2/14 tool calls",
        )

    def test_the_noun_survives_every_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("tool calls", render.format_count(2, 14, "tool calls", mode))

    def test_zero_is_rendered_not_suppressed(self):
        # "0 blocked" and "" mean different things; the contract refuses a count
        # from a provider that cannot answer for exactly this reason.
        self.assertIn("0", render.format_count(0, 14, "tool calls", render.PresentationMode.COMPACT))


class TestFormatConfidence(unittest.TestCase):
    def test_a_preflight_confidence_says_it_is_a_preflight_confidence(self):
        self.assertEqual(
            render.format_confidence("high", "preflight_estimate", render.PresentationMode.BALANCED),
            "preflight confidence high",
        )

    def test_the_bare_value_is_never_the_whole_rendering(self):
        # This is the `pf:high` readability defect: a bare `high` reads as risk,
        # severity or priority rather than as confidence in an estimate.
        for subject in list(render.CONFIDENCE_SUBJECT_TEXT) + ["bogus", None]:
            for value in ("low", "medium", "high"):
                for mode in MODES:
                    with self.subTest(subject=subject, value=value, mode=mode):
                        result = render.format_confidence(value, subject, mode)
                        self.assertNotEqual(result, value)
                        self.assertTrue(result.startswith(tuple("abcdefghijklmnopqrstuvwxyz")))
                        self.assertTrue(result.endswith(value))

    def test_compact_mode_still_names_the_subject(self):
        self.assertEqual(
            render.format_confidence("high", "preflight_estimate", render.PresentationMode.COMPACT),
            "preflight high",
        )

    def test_a_verification_confidence_does_not_read_as_a_verdict(self):
        # "verified low" would claim something about the subject rather than
        # about how sure the provider is.
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertNotIn("verified", render.format_confidence("low", "verification", mode))

    def test_an_unrecognised_subject_falls_back_without_dropping_the_qualifier(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    render.format_confidence("high", "future_subject", mode),
                    render.format_confidence("high", "unspecified", mode),
                )

    def test_every_contract_subject_has_a_phrasing_in_both_tables(self):
        subjects = {member.value for member in contract.ConfidenceSubject}
        self.assertEqual(set(render.CONFIDENCE_SUBJECT_TEXT), subjects)
        self.assertEqual(set(render.CONFIDENCE_SUBJECT_TEXT_COMPACT), subjects)


class TestFormatReason(unittest.TestCase):
    def test_prose_is_preferred_over_the_machine_token(self):
        self.assertEqual(
            render.format_reason("daemon_not_running", "Daemon is not running"),
            "Daemon is not running",
        )

    def test_a_machine_token_is_made_readable(self):
        self.assertEqual(render.format_reason("daemon_not_running", None), "daemon not running")
        self.assertEqual(render.format_reason("no-receipt-store", None), "no receipt store")

    def test_no_reason_yields_nothing_rather_than_an_invented_one(self):
        # "unknown reason" and "no reason clause" are different claims, and only
        # the provider can tell them apart.
        for code, label in ((None, None), ("", ""), ("", None), (None, "")):
            with self.subTest(code=code, label=label):
                self.assertEqual(render.format_reason(code, label), "")


class TestSeparatorHierarchy(unittest.TestCase):
    ALLOWLISTED_PUNCTUATION = set(". , ' - — ( ) % + ? ! ≤ ≥".split())

    def separators(self):
        yield "detail", render.DETAIL_SEPARATOR
        for mode, value in render.SEGMENT_SEPARATORS.items():
            yield f"segment/{mode.value}", value
        for mode, value in render.UPSTREAM_SEPARATORS.items():
            yield f"upstream/{mode.value}", value

    def test_no_separator_uses_a_character_a_provider_label_may_contain(self):
        # Otherwise a comma in a `reason_label` draws a boundary the provider
        # never intended.
        for name, value in self.separators():
            with self.subTest(name=name):
                self.assertFalse(set(value.strip()) & self.ALLOWLISTED_PUNCTUATION)

    def test_every_separator_is_one_column_per_character(self):
        for name, value in self.separators():
            with self.subTest(name=name):
                self.assertEqual(render.display_width(value), len(value))

    def test_the_three_levels_are_distinguishable_within_a_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    len({
                        render.DETAIL_SEPARATOR,
                        render.SEGMENT_SEPARATORS[mode],
                        render.UPSTREAM_SEPARATORS[mode],
                    }),
                    3,
                )

    def test_plain_mode_separators_are_ascii(self):
        self.assertTrue(render.SEGMENT_SEPARATORS[render.PresentationMode.PLAIN].isascii())
        self.assertTrue(render.UPSTREAM_SEPARATORS[render.PresentationMode.PLAIN].isascii())
        self.assertTrue(render.DETAIL_SEPARATOR.isascii())

    def test_every_mode_has_a_separator_at_every_level(self):
        for mode in MODES:
            self.assertIn(mode, render.SEGMENT_SEPARATORS)
            self.assertIn(mode, render.UPSTREAM_SEPARATORS)


class TestRenderSegment(unittest.TestCase):
    def test_the_label_is_always_present(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("Example state", render.render_segment(segment(), mode))

    def test_a_segment_with_no_optional_fields_has_no_empty_parentheses(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertNotIn("()", render.render_segment(segment(), mode))

    def test_a_hypothetical_segment_always_says_it_was_not_enforced(self):
        # Shadow-mode would-block must never read as an executed block.
        rendered = {
            mode: render.render_segment(
                segment(state=contract.SegmentState.WARN, label="Shadow mode", hypothetical=True),
                mode,
            )
            for mode in MODES
        }
        for mode, text in rendered.items():
            with self.subTest(mode=mode):
                self.assertIn(f"[{render.HYPOTHETICAL_TEXT}]", text)

    def test_the_not_enforced_marker_is_welded_to_the_label(self):
        text = render.render_segment(
            segment(
                state=contract.SegmentState.WARN,
                label="Shadow mode",
                hypothetical=True,
                count=2,
                total=14,
                count_label="tool calls",
            ),
            render.PresentationMode.BALANCED,
        )
        self.assertLess(text.index(render.HYPOTHETICAL_TEXT), text.index("tool calls"))

    def test_a_count_keeps_its_noun(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn(
                    "claims", render.render_segment(fornax_status().segments[0], mode)
                )

    def test_a_count_without_a_label_is_not_rendered_as_a_bare_number(self):
        # The contract refuses this combination, so this only guards a stub.
        stub = segment(count=None, count_label=None)
        self.assertNotIn("None", render.render_segment(stub, render.PresentationMode.BALANCED))

    def test_a_reason_is_kept_in_compact_mode_only_for_an_exception(self):
        calm = segment(state=contract.SegmentState.OK, reason_code="all_good")
        loud = segment(state=contract.SegmentState.CRITICAL, reason_code="escalated_to_human")
        self.assertNotIn("all good", render.render_segment(calm, render.PresentationMode.COMPACT))
        self.assertIn(
            "escalated to human", render.render_segment(loud, render.PresentationMode.COMPACT)
        )

    def test_a_reason_is_kept_in_balanced_mode_for_any_state(self):
        calm = segment(state=contract.SegmentState.OK, reason_code="all_good")
        self.assertIn("all good", render.render_segment(calm, render.PresentationMode.BALANCED))

    def test_plain_mode_output_is_ascii_for_every_product_fixture(self):
        for status_ in (fornax_status(), circinus_status(), libra_status()):
            for seg in status_.segments:
                with self.subTest(provider=status_.provider, key=seg.key):
                    self.assertTrue(
                        render.render_segment(seg, render.PresentationMode.PLAIN).isascii()
                    )

    def test_a_wire_string_state_is_accepted_as_well_as_an_enum(self):
        class Stub:
            key = "k"
            state = "warn"
            label = "Stub"
            reason_code = None
            reason_label = None
            confidence = None
            confidence_of = None
            age_seconds = None
            count = None
            total = None
            count_label = None
            hypothetical = False

        self.assertIn(
            render.STATE_TEXT["warn"], render.render_segment(Stub(), render.PresentationMode.PLAIN)
        )

    def test_every_rendering_carries_at_least_one_readable_word(self):
        # Glyphs reinforce meaning; they are never the only carrier of it.
        for status_ in (fornax_status(), circinus_status(), libra_status()):
            for seg in status_.segments:
                for mode in MODES:
                    with self.subTest(key=seg.key, mode=mode):
                        text = render.render_segment(seg, mode)
                        self.assertRegex(text, r"[A-Za-z]{3,}")


class TestProviderDisplayName(unittest.TestCase):
    def test_a_simple_id_is_capitalised(self):
        self.assertEqual(render.provider_display_name("fornax"), "Fornax")

    def test_separators_become_spaces_and_each_word_is_capitalised(self):
        self.assertEqual(render.provider_display_name("libra-governor"), "Libra Governor")
        self.assertEqual(render.provider_display_name("a_b_c"), "A B C")

    def test_a_single_character_id_is_handled(self):
        self.assertEqual(render.provider_display_name("x"), "X")

    def test_an_unknown_provider_is_still_attributed(self):
        # Derived rather than looked up, so a provider the host has never heard
        # of does not render anonymously beside the ones it knows.
        self.assertEqual(render.provider_display_name("future-product"), "Future Product")


class TestScopeMarker(unittest.TestCase):
    def test_glyph_modes_use_the_glyph(self):
        for scope, glyph in render.SCOPE_GLYPHS.items():
            for mode in (render.PresentationMode.BALANCED, render.PresentationMode.COMPACT):
                with self.subTest(scope=scope, mode=mode):
                    self.assertEqual(render.scope_marker(scope, mode), glyph)

    def test_plain_mode_uses_the_contract_token(self):
        for scope, token in render.SCOPE_TEXT.items():
            with self.subTest(scope=scope):
                self.assertEqual(
                    render.scope_marker(scope, render.PresentationMode.PLAIN), token
                )

    def test_an_unrecognised_scope_renders_as_its_literal_rather_than_a_guess(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(render.scope_marker("cluster", mode), "[cluster]")

    def test_host_scope_is_always_marked(self):
        # Host-wide state read as session-scoped is a user believing their other
        # sessions are guarded, or unguarded, incorrectly.
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertTrue(render.scope_marker("host", mode))


class TestRenderProvider(unittest.TestCase):
    def test_the_provider_is_attributed(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("Fornax", render.render_provider(fornax_status(), mode))

    def test_the_scope_is_rendered(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn(
                    render.scope_marker("host", mode), render.render_provider(circinus_status(), mode)
                )

    def test_segments_are_ordered_by_the_contract_rule(self):
        # libra's `approval` carries the lower order_hint, so it renders first
        # even though it is declared second.
        text = render.render_provider(libra_status(), render.PresentationMode.BALANCED)
        self.assertLess(text.index("Awaiting your approval"), text.index("Preflight"))

    def test_a_provider_with_no_segments_says_so_rather_than_rendering_nothing(self):
        # Silence reads as all-clear, which is the one meaning it must not have.
        empty = status(availability=contract.Availability.UNAVAILABLE, segments=())
        for mode in MODES:
            with self.subTest(mode=mode):
                text = render.render_provider(empty, mode)
                self.assertIn(render.NO_SEGMENTS_LABEL, text)
                self.assertIn("Example", text)

    def test_fallback_text_is_used_only_when_there_are_no_segments(self):
        with_fallback = status(
            availability=contract.Availability.UNAVAILABLE,
            segments=(),
            fallback_text="Not configured",
        )
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("Not configured", render.render_provider(with_fallback, mode))
        both = status(fallback_text="Not configured")
        self.assertNotIn(
            "Not configured", render.render_provider(both, render.PresentationMode.BALANCED)
        )

    def test_an_absent_reading_is_marked_unknown_not_ok(self):
        empty = status(availability=contract.Availability.UNAVAILABLE, segments=())
        for mode in MODES:
            with self.subTest(mode=mode):
                text = render.render_provider(empty, mode)
                self.assertIn(render.state_marker("unknown", mode), text)
                self.assertNotIn(render.STATE_GLYPHS["ok"], text)

    def test_rendering_is_stable_across_repeated_calls(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                first = render.render_provider(libra_status(), mode)
                self.assertEqual(first, render.render_provider(libra_status(), mode))


class TestProviderSeverity(unittest.TestCase):
    def test_the_worst_segment_wins(self):
        self.assertEqual(render.provider_severity(libra_status()), render.STATE_SEVERITY["critical"])

    def test_a_calm_provider_ranks_lowest(self):
        self.assertEqual(render.provider_severity(fornax_status()), render.STATE_SEVERITY["ok"])

    def test_a_provider_with_no_segments_ranks_as_unknown_not_ok(self):
        # "Reported nothing" is something the user needs to see, not the first
        # thing to hide when the line is tight.
        empty = status(availability=contract.Availability.UNAVAILABLE, segments=())
        self.assertEqual(render.provider_severity(empty), render.STATE_SEVERITY["unknown"])


class TestModeLadder(unittest.TestCase):
    def test_the_requested_mode_is_tried_first(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIs(render._mode_candidates(mode)[0], mode)

    def test_the_ladder_never_reintroduces_a_glyph_a_plain_request_declined(self):
        for candidate in render._mode_candidates(render.PresentationMode.PLAIN):
            self.assertFalse(candidate.uses_glyphs)

    def test_a_compact_request_is_not_re_expanded_to_balanced(self):
        self.assertNotIn(
            render.PresentationMode.BALANCED,
            render._mode_candidates(render.PresentationMode.COMPACT),
        )

    def test_the_ladder_is_a_suffix_of_the_declared_order(self):
        for mode in MODES:
            candidates = render._mode_candidates(mode)
            with self.subTest(mode=mode):
                self.assertEqual(candidates, render.MODE_LADDER[render.MODE_LADDER.index(mode):])


class TestComposeUpstreamPreservation(unittest.TestCase):
    """The non-negotiable half: the user's own line is never touched."""

    def test_no_providers_returns_the_upstream_text_byte_for_byte(self):
        self.assertEqual(render.compose(UPSTREAM, ()), UPSTREAM)

    def test_no_upstream_and_no_providers_is_empty(self):
        self.assertEqual(render.compose(None, ()), "")
        self.assertEqual(render.compose("", ()), "")

    def test_no_upstream_is_a_valid_state_and_renders_the_block_alone(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                block = render.compose(None, (fornax_status(),), mode=mode)
                self.assertIn("Fornax", block)
                self.assertFalse(block.startswith(render.UPSTREAM_SEPARATORS[mode]))

    def test_the_upstream_text_is_always_a_verbatim_prefix(self):
        weird = "  spaced  \tand\ttabbed  [$] 100%  "
        for mode in MODES:
            for budget in (None, 300, 120, 60, 30, 10, 1, 0):
                with self.subTest(mode=mode, budget=budget):
                    out = render.compose(
                        weird,
                        (fornax_status(), circinus_status(), libra_status()),
                        mode=mode,
                        width_budget=budget,
                    )
                    self.assertTrue(out.startswith(weird))

    def test_the_upstream_text_is_never_counted_against_the_budget(self):
        # The budget is ours to spend; the user's line is not ours to shorten.
        long_upstream = "x" * 500
        out = render.compose(long_upstream, (fornax_status(),), width_budget=200)
        self.assertTrue(out.startswith(long_upstream))
        self.assertIn("Fornax", out)

    def test_a_provider_block_that_cannot_fit_leaves_the_upstream_line_alone(self):
        out = render.compose(UPSTREAM, (fornax_status(),), width_budget=1)
        self.assertEqual(out, UPSTREAM)

    def test_the_upstream_text_is_divided_by_the_strongest_separator(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out = render.compose(UPSTREAM, (fornax_status(),), mode=mode)
                self.assertTrue(out.startswith(UPSTREAM + render.UPSTREAM_SEPARATORS[mode]))

if __name__ == "__main__":
    unittest.main()
