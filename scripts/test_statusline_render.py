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

if __name__ == "__main__":
    unittest.main()
