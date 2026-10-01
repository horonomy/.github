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

import dataclasses
import itertools
import re
import unicodedata
import unittest

import statusline_contract as contract
import statusline_render as render

MODES = tuple(render.PresentationMode)
DEPTHS = tuple(render.InformationDepth)
CLEAR = render.InformationDepth.CLEAR
DETAIL = render.InformationDepth.DETAIL


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
            # The remaining-work estimate: seconds and a noun, with the host
            # owning the formatting. A product that sent `label="P90 ≤ 12m"`
            # instead would be the drift the duration pair exists to prevent.
            segment(
                key="remaining",
                state=contract.SegmentState.NEUTRAL,
                label="Remaining work",
                duration_seconds=720,
                duration_label="P90",
                explain_key="libra.remaining",
                order_hint=20,
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

    def test_the_symmetric_name_for_plain_resolves_to_it(self):
        # Not an alias for convenience: the fallback is a glyph mode, so a text
        # preference that misses would render emoji on a terminal that cannot.
        self.assertIs(
            render.PresentationMode.parse("balanced_plain"), render.PresentationMode.PLAIN
        )

    def test_a_hyphen_is_accepted_where_a_member_uses_an_underscore(self):
        for spelling in ("compact-plain", "balanced-plain"):
            with self.subTest(spelling=spelling):
                self.assertFalse(render.PresentationMode.parse(spelling).uses_glyphs)

    def test_unrecognised_input_falls_back_rather_than_raising(self):
        for value in ("", "verbose", None, 7, [], object()):
            self.assertIs(render.PresentationMode.parse(value), render.PresentationMode.BALANCED)

    def test_the_fallback_is_overridable(self):
        self.assertIs(
            render.PresentationMode.parse("nope", render.PresentationMode.PLAIN),
            render.PresentationMode.PLAIN,
        )

    def test_density_and_icon_style_are_independent(self):
        # Every combination of the two axes is reachable. The one that matters is
        # compact-and-text: a narrow terminal and an unreliable emoji font are
        # different problems, and a reader with both must not have to pick one.
        self.assertEqual(
            {(mode.is_compact, mode.uses_glyphs) for mode in MODES},
            {(False, True), (True, True), (False, False), (True, False)},
        )

    def test_a_mode_can_be_looked_up_by_its_two_axes(self):
        # Round-trips for every member, which is what a surface offering the two
        # axes as separate choices needs: whatever the user asked for, the mode it
        # resolves to reports back exactly that.
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIs(
                    render.PresentationMode.for_axes(
                        compact=mode.is_compact, glyphs=mode.uses_glyphs
                    ),
                    mode,
                )

    def test_no_two_modes_claim_the_same_pair_of_axes(self):
        # The round trip above would still pass if a future member shadowed an
        # existing one -- the survivor would answer for both. Then a user asking
        # for one would silently get the other.
        self.assertEqual(len(render._MODE_BY_AXES), len(MODES))


class TestInformationDepth(unittest.TestCase):
    def test_every_member_round_trips_from_its_value(self):
        for depth in DEPTHS:
            with self.subTest(depth=depth):
                self.assertIs(render.InformationDepth.parse(depth.value), depth)

    def test_a_member_is_returned_unchanged(self):
        for depth in DEPTHS:
            self.assertIs(render.InformationDepth.parse(depth), depth)

    def test_case_and_surrounding_space_are_tolerated(self):
        self.assertIs(
            render.InformationDepth.parse("  DETAIL "), render.InformationDepth.DETAIL
        )

    def test_unrecognised_input_falls_back_to_the_narrower_depth(self):
        # Not merely "falls back": it falls back *downward*. A near-miss on a
        # depth preference answering with more information than was asked for is
        # the one failure mode this axis can have that the other two cannot.
        for value in ("", "verbose", "deatil", None, 7, [], object()):
            with self.subTest(value=repr(value)):
                self.assertIs(
                    render.InformationDepth.parse(value), render.InformationDepth.CLEAR
                )

    def test_the_fallback_is_overridable(self):
        self.assertIs(
            render.InformationDepth.parse("nope", render.InformationDepth.DETAIL),
            render.InformationDepth.DETAIL,
        )

    def test_a_fresh_install_defaults_to_clear(self):
        # A user who has asked for nothing wants to know whether a product needs
        # them, not to read diagnostics. Detail is an opt-in.
        self.assertIs(render.DEFAULT_INFORMATION_DEPTH, render.InformationDepth.CLEAR)

    def test_only_detail_shows_supporting_context(self):
        self.assertEqual(
            {depth: depth.shows_supporting_detail for depth in DEPTHS},
            {
                render.InformationDepth.CLEAR: False,
                render.InformationDepth.DETAIL: True,
            },
        )

    def test_depth_is_not_a_presentation_mode(self):
        # The axes have to stay separable at the type level, not just by
        # convention: the moment a depth is accepted where a mode is expected,
        # `mode.is_compact` starts answering a question nobody asked it.
        for depth in DEPTHS:
            with self.subTest(depth=depth):
                self.assertNotIsInstance(depth, render.PresentationMode)
                self.assertFalse(hasattr(depth, "is_compact"))
        self.assertEqual(
            {mode.value for mode in MODES} & {depth.value for depth in DEPTHS}, set()
        )

    def test_depth_does_not_change_the_rendering_mode_ladder(self):
        # The width-pressure ladder is a statement about the two rendering axes.
        # Depth must not appear in it, or a narrow terminal would start deciding
        # how much a provider is allowed to report.
        for candidates in render.MODE_LADDER.values():
            for candidate in candidates:
                self.assertIsInstance(candidate, render.PresentationMode)


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


class TestFormatDuration(unittest.TestCase):
    def test_a_single_unit_when_the_span_divides_cleanly(self):
        cases = {60: "1m", 3600: "1h", 86400: "1d", 45: "45s"}
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(render.format_duration(seconds, "P90"), f"P90 {expected}")

    def test_a_second_unit_appears_when_the_remainder_reaches_it(self):
        # 5d4h. The second unit is the reason this is not `format_age`: `5d` for
        # anything up to six days is a 20% understatement of work remaining.
        self.assertEqual(render.format_duration(447120, "P90"), "P90 5d4h")

    def test_the_second_unit_is_the_adjacent_one_or_nothing(self):
        # 1d 0h 1m. `1d1m` would imply a precision the day already discarded, so
        # the minutes are dropped rather than promoted past the empty hour.
        self.assertEqual(render.format_duration(86460, "P90"), "P90 1d")

    def test_truncation_never_carries_into_the_larger_unit(self):
        # 1d 23h 59m 59s: not `2d`, which would overstate by a whole unit, and
        # not `1d24h`, which is not a thing.
        self.assertEqual(render.format_duration(172799, "P90"), "P90 1d23h")

    def test_zero_is_rendered_not_suppressed(self):
        # A provider that means "no estimate" omits the field; 0 means 0.
        self.assertEqual(render.format_duration(0, "P90"), "P90 0s")

    def test_a_negative_span_is_clamped(self):
        self.assertEqual(render.format_duration(-10, "P90"), "P90 0s")

    def test_the_noun_comes_first_and_survives(self):
        # `5d4h P90` reads as a typo; `P90 5d4h` reads as a qualified quantity.
        # And the noun is the whole point — an unlabelled span could be elapsed,
        # remaining, a budget or a timeout.
        for seconds in (0, 45, 447120):
            with self.subTest(seconds=seconds):
                self.assertTrue(render.format_duration(seconds, "remaining").startswith("remaining "))

    def test_the_rendering_is_ascii(self):
        self.assertTrue(render.format_duration(447120, "P90").isascii())


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

    def test_text_mode_separators_are_ascii(self):
        for mode in MODES:
            if mode.uses_glyphs:
                continue
            with self.subTest(mode=mode):
                self.assertTrue(render.SEGMENT_SEPARATORS[mode].isascii())
                self.assertTrue(render.UPSTREAM_SEPARATORS[mode].isascii())
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

    def test_a_duration_keeps_its_noun(self):
        remaining = libra_status().segments[-1]
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("P90 12m", render.render_segment(remaining, mode))

    def test_a_duration_sits_with_the_count_and_before_the_age(self):
        # Both are quantities the segment reports; the age qualifies the whole
        # reading, so it stays last whatever else is present.
        text = render.render_segment(
            segment(
                count=2,
                total=14,
                count_label="tool calls",
                duration_seconds=720,
                duration_label="P90",
                age_seconds=125,
            ),
            render.PresentationMode.BALANCED,
        )
        self.assertLess(text.index("tool calls"), text.index("P90"))
        self.assertLess(text.index("P90"), text.index("ago"))

    def test_a_segment_object_predating_the_duration_fields_still_renders(self):
        # `render_segment` promises to accept anything with the same attributes,
        # and the duration pair arrived after that promise. A provider pinned to
        # an older copy of the contract must degrade to its other fields rather
        # than raise. `Stub` below relies on the same tolerance.
        class Older:
            key = "k"
            state = contract.SegmentState.OK
            label = "Older"
            reason_code = None
            reason_label = None
            confidence = None
            confidence_of = None
            age_seconds = None
            count = None
            total = None
            count_label = None
            hypothetical = False

        self.assertIn("Older", render.render_segment(Older(), render.PresentationMode.PLAIN))

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


ROLES = tuple(contract.ClearRole)

# The live host as read from the three installed providers on 2026-09-30. Kept
# distinct from the three fixtures above, which were written against the
# readability requirements rather than against a running machine: the fixtures
# have a `critical` and a `hypothetical` the live host does not, and the live host
# has the two-neutrals-of-equal-severity case the fixtures do not.
def live_fornax() -> contract.ProviderStatus:
    return status(
        provider="fornax",
        scope=contract.Scope.HOST,
        order_hint=300,
        segments=(
            segment(
                key="latest_finding",
                state=contract.SegmentState.ATTENTION,
                label="Unverified",
                reason_code="reason_not_recorded",
                age_seconds=99883,
            ),
        ),
    )


def live_circinus() -> contract.ProviderStatus:
    return status(
        provider="circinus",
        scope=contract.Scope.HOST,
        availability=contract.Availability.UNKNOWN,
        order_hint=400,
        segments=(
            segment(
                key="availability",
                state=contract.SegmentState.UNKNOWN,
                label="Not responding",
                reason_code="daemon_unreachable",
                reason_label="Socket exists but did not answer",
            ),
        ),
    )


def live_libra() -> contract.ProviderStatus:
    return status(
        provider="libra",
        scope=contract.Scope.HOST,
        order_hint=500,
        segments=(
            segment(
                key="task",
                state=contract.SegmentState.NEUTRAL,
                label="Task 0dd57ff3",
                order_hint=10,
            ),
            segment(
                key="estimate",
                state=contract.SegmentState.NEUTRAL,
                label="Remaining work",
                duration_seconds=543530,
                duration_label="P90",
                confidence=contract.Confidence.HIGH,
                confidence_of=contract.ConfidenceSubject.PREFLIGHT_ESTIMATE,
                order_hint=20,
            ),
            segment(
                key="escalation",
                state=contract.SegmentState.WARN,
                label="Replan budget spent",
                reason_code="next_replan_needs_human_approval",
                order_hint=30,
            ),
        ),
    )


LIVE_HOST = (live_fornax, live_circinus, live_libra)
ALL_FIXTURES = LIVE_HOST + (fornax_status, circinus_status, libra_status)


class TestClearRoles(unittest.TestCase):
    """The fallback that infers a Clear part for a provider that declared none."""

    def roles(self, status_) -> dict:
        return {
            seg.key: role
            for seg, role in zip(contract.order_segments(status_), render.clear_roles(status_))
        }

    def test_a_state_that_stops_work_is_an_exception_wherever_it_sits(self):
        # Last in contract order, so position cannot be what promoted it.
        roles = self.roles(
            status(
                segments=(
                    segment(key="a", label="Routine", order_hint=0),
                    segment(
                        key="b",
                        state=contract.SegmentState.CRITICAL,
                        label="Stopped",
                        order_hint=90,
                    ),
                )
            )
        )
        self.assertIs(roles["b"], contract.ClearRole.EXCEPTION)

    def test_a_state_waiting_on_the_operator_is_an_exception(self):
        roles = self.roles(live_fornax())
        self.assertIs(roles["latest_finding"], contract.ClearRole.EXCEPTION)

    def test_a_warn_state_is_not_an_exception(self):
        # The live Libra case. `warn` means "something is wrong; work is not
        # stopped", and Libra's own explain surface says nothing is blocked and
        # nothing is waiting on an answer. Reading it as an exception would make
        # the line claim otherwise -- and would drop the estimate beside it,
        # since an exception is shown alone.
        roles = self.roles(live_libra())
        self.assertNotIn(contract.ClearRole.EXCEPTION, roles.values())
        self.assertIs(roles["escalation"], contract.ClearRole.VITAL)

    def test_a_measurement_outranks_a_bare_fact_of_equal_severity(self):
        # Both `neutral`, adjacent in order, so neither severity nor position can
        # separate them. A line that leads with the task id has spent Libra's
        # whole reading on the least useful thing it knows.
        roles = self.roles(live_libra())
        self.assertIs(roles["estimate"], contract.ClearRole.POSTURE)
        self.assertIs(roles["task"], contract.ClearRole.SUPPORTING)

    def test_the_first_segment_is_the_posture_when_nothing_is_measured(self):
        roles = self.roles(
            status(
                segments=(
                    segment(key="first", label="Ready", order_hint=1),
                    segment(key="second", label="Also ready", order_hint=2),
                )
            )
        )
        self.assertIs(roles["first"], contract.ClearRole.POSTURE)
        self.assertIs(roles["second"], contract.ClearRole.SUPPORTING)

    def test_exactly_one_posture_is_inferred_per_provider(self):
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                roles = render.clear_roles(status_)
                postures = [r for r in roles if r is contract.ClearRole.POSTURE]
                self.assertLessEqual(len(postures), 1)

    def test_a_declared_role_is_never_overridden(self):
        # A provider that calls its stopped-work segment supporting context is
        # making a claim the host has no standing to correct. Inference exists for
        # providers written before the field, not as the intended path.
        roles = self.roles(
            status(
                segments=(
                    segment(
                        key="b",
                        state=contract.SegmentState.CRITICAL,
                        label="Stopped",
                        clear_role=contract.ClearRole.SUPPORTING,
                    ),
                )
            )
        )
        self.assertIs(roles["b"], contract.ClearRole.SUPPORTING)

    def test_a_declared_posture_suppresses_inference_entirely(self):
        # Without the suppression, the measured segment would be inferred as a
        # second posture and the provider would get more than it asked for.
        roles = self.roles(
            status(
                segments=(
                    segment(
                        key="named",
                        label="Shadow mode",
                        clear_role=contract.ClearRole.POSTURE,
                        order_hint=1,
                    ),
                    segment(
                        key="measured",
                        label="Decisions",
                        count=4,
                        count_label="today",
                        order_hint=2,
                    ),
                )
            )
        )
        self.assertIs(roles["named"], contract.ClearRole.POSTURE)
        self.assertIsNot(roles["measured"], contract.ClearRole.POSTURE)

    def test_every_segment_gets_a_role(self):
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                roles = render.clear_roles(status_)
                self.assertEqual(len(roles), len(status_.segments))
                for role in roles:
                    self.assertIn(role, ROLES)


class TestClearReadings(unittest.TestCase):
    """Which readings survive into Clear, which is where the priority rule lives."""

    def keys(self, status_) -> list:
        return [seg.key for seg in render.clear_readings(status_)]

    def test_a_provider_that_cannot_read_its_own_state_shows_one_reading(self):
        self.assertEqual(self.keys(live_circinus()), ["availability"])

    def test_a_cached_outcome_cannot_ride_along_with_an_unreachable_daemon(self):
        # The defect this rule exists for: `Circinus UNKNOWN Not responding ·
        # WARN Would have blocked` reads as though shadow evaluation were running,
        # when the thing that would run it is not answering. The cached outcome is
        # still in the snapshot and Detail may show it; Clear may not.
        #
        # Deliberately a `warn` hypothetical rather than a quiet `neutral` mode
        # name: a neutral one would be classified supporting and omitted anyway,
        # so the test would pass with this rule deleted. This one is a reading the
        # role ladder *would* keep, which is what makes the assertion bite.
        wedged = dataclasses.replace(
            live_circinus(),
            segments=live_circinus().segments
            + (
                segment(
                    key="last_decision",
                    state=contract.SegmentState.WARN,
                    label="Would have blocked",
                    hypothetical=True,
                    order_hint=50,
                ),
            ),
        )
        self.assertEqual(self.keys(wedged), ["availability"])
        clear = render.render_provider(wedged, render.PresentationMode.PLAIN, CLEAR)
        detail = render.render_provider(wedged, render.PresentationMode.PLAIN, DETAIL)
        self.assertNotIn("Would have blocked", clear)
        self.assertIn("Would have blocked", detail)

    def test_an_unavailable_provider_skips_a_hypothetical_even_when_ordered_first(self):
        # Severity must not decide here, and neither may position. `warn` outranks
        # `unknown`, and this cache sorts ahead of the admission, so both a
        # severity-ranked and a position-ranked choice pick the would-have-blocked
        # -- which is how the line ends up sounding busier than the product that
        # is not running.
        wedged = dataclasses.replace(
            live_circinus(),
            segments=(
                segment(
                    key="last_decision",
                    state=contract.SegmentState.WARN,
                    label="Would have blocked",
                    hypothetical=True,
                    order_hint=1,
                ),
                dataclasses.replace(live_circinus().segments[0], order_hint=50),
            ),
        )
        self.assertEqual(self.keys(wedged), ["availability"])

    def test_an_unavailable_provider_otherwise_leads_with_its_own_first_reading(self):
        # Nothing hypothetical, so the provider's own ordering decides. Still not
        # severity: the second segment is the more alarming one, and an invalid
        # config is the more actionable thing to say.
        broken = status(
            availability=contract.Availability.UNAVAILABLE,
            segments=(
                segment(
                    key="config",
                    state=contract.SegmentState.ATTENTION,
                    label="Config invalid",
                    order_hint=1,
                ),
                segment(
                    key="cached",
                    state=contract.SegmentState.CRITICAL,
                    label="Last run failed",
                    order_hint=2,
                ),
            ),
        )
        self.assertEqual(self.keys(broken), ["config"])

    def test_an_all_hypothetical_unavailable_provider_still_says_something(self):
        # The last rung. A provider whose every reading is a would-have has broken
        # the contract, and the host still has to render one line rather than an
        # attributed nothing -- which would read as "checked, all clear".
        only_hypothetical = status(
            availability=contract.Availability.UNKNOWN,
            segments=(
                segment(
                    key="a",
                    state=contract.SegmentState.UNKNOWN,
                    label="Cannot tell",
                    hypothetical=True,
                    order_hint=2,
                ),
                segment(
                    key="b",
                    state=contract.SegmentState.WARN,
                    label="Would have blocked",
                    hypothetical=True,
                    order_hint=1,
                ),
            ),
        )
        self.assertEqual(self.keys(only_hypothetical), ["a"])

    def test_an_exception_is_shown_alone(self):
        # Nothing is improved by a remaining-work estimate next to an approval
        # the operator is being asked for.
        self.assertEqual(self.keys(libra_status()), ["approval"])

    def test_the_worst_exception_wins_when_there_are_two(self):
        both = status(
            segments=(
                segment(
                    key="waiting",
                    state=contract.SegmentState.ATTENTION,
                    label="Awaiting approval",
                    order_hint=1,
                ),
                segment(
                    key="stopped",
                    state=contract.SegmentState.CRITICAL,
                    label="Stopped",
                    order_hint=2,
                ),
            )
        )
        self.assertEqual(self.keys(both), ["stopped"])

    def test_a_posture_may_keep_one_vital_beside_it(self):
        self.assertEqual(self.keys(live_libra()), ["estimate", "escalation"])

    def test_no_provider_contributes_more_than_the_reading_budget(self):
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                self.assertLessEqual(
                    len(render.clear_readings(status_)), render.MAX_CLEAR_READINGS
                )

    def test_a_stale_secondary_reading_is_dropped(self):
        fresh = dataclasses.replace(
            live_libra(),
            segments=tuple(
                dataclasses.replace(seg, age_seconds=30, fresh_for_seconds=60)
                if seg.key == "escalation"
                else seg
                for seg in live_libra().segments
            ),
        )
        stale = dataclasses.replace(
            fresh,
            segments=tuple(
                dataclasses.replace(seg, age_seconds=3600, fresh_for_seconds=60)
                if seg.key == "escalation"
                else seg
                for seg in fresh.segments
            ),
        )
        # The only difference between the two snapshots is the age, so the age is
        # what the assertion is about.
        self.assertEqual(self.keys(fresh), ["estimate", "escalation"])
        self.assertEqual(self.keys(stale), ["estimate"])

    def test_a_stale_exception_is_still_shown(self):
        # Freshness may retire a secondary signal. It may not retire the reason
        # the operator would have looked: an old unanswered approval request is
        # still an unanswered approval request.
        old = status(
            segments=(
                segment(
                    key="waiting",
                    state=contract.SegmentState.ATTENTION,
                    label="Awaiting approval",
                    age_seconds=99999,
                    fresh_for_seconds=60,
                ),
            )
        )
        self.assertEqual(self.keys(old), ["waiting"])

    def test_a_reading_with_no_declared_horizon_is_never_stale(self):
        # `fresh_for_seconds` is opt-in, so an age alone must not retire anything.
        aged = dataclasses.replace(
            live_libra(),
            segments=tuple(
                dataclasses.replace(seg, age_seconds=10**6)
                if seg.key == "escalation"
                else seg
                for seg in live_libra().segments
            ),
        )
        self.assertIn("escalation", self.keys(aged))

    def test_readings_come_back_in_contract_order(self):
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                ordered = list(contract.order_segments(status_))
                positions = [ordered.index(seg) for seg in render.clear_readings(status_)]
                self.assertEqual(positions, sorted(positions))

    def test_the_projection_is_idempotent(self):
        # compose projects for its width accounting and render_provider projects
        # for its output. If applying it twice could differ, those two would
        # disagree about what is on the line.
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                once = render.clear_readings(status_)
                twice = render.clear_readings(dataclasses.replace(status_, segments=once))
                self.assertEqual(once, twice)

    def test_every_clear_reading_is_one_of_the_providers_own_segments(self):
        # The one-state-engine invariant, stated structurally: Clear selects from
        # the snapshot, it does not compute a state of its own. An identity check
        # rather than a state comparison, so no amount of agreeing-by-coincidence
        # can pass it.
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                for reading in render.clear_readings(status_):
                    self.assertTrue(any(reading is seg for seg in status_.segments))

    def test_clear_never_lowers_the_severity_a_provider_reports(self):
        # Clear may say less. It may not say "calmer".
        for factory in ALL_FIXTURES:
            status_ = factory()
            with self.subTest(provider=status_.provider):
                projected = dataclasses.replace(
                    status_, segments=render.clear_readings(status_)
                )
                self.assertEqual(
                    render.provider_severity(projected), render.provider_severity(status_)
                )

    def test_a_provider_that_reported_nothing_still_says_so_in_clear(self):
        empty = status(segments=())
        self.assertEqual(render.clear_readings(empty), ())
        text = render.render_provider(empty, render.PresentationMode.PLAIN, CLEAR)
        self.assertIn(render.NO_SEGMENTS_LABEL, text)

    def test_a_provider_whose_every_segment_is_declared_supporting_still_shows_one(self):
        # Rendering nothing would be indistinguishable from "this product
        # reported nothing", which means the opposite.
        all_supporting = status(
            segments=(
                segment(key="a", label="First", clear_role=contract.ClearRole.SUPPORTING),
                segment(key="b", label="Second", clear_role=contract.ClearRole.SUPPORTING),
            )
        )
        self.assertEqual(self.keys(all_supporting), ["a"])


def declared(*segments, **overrides) -> contract.ProviderStatus:
    """A provider that declares its own Clear projection authoritative."""
    fields = {
        "clear_authority": contract.ClearAuthority.PROVIDER,
        "segments": tuple(segments),
    }
    fields.update(overrides)
    return status(**fields)


class TestDeclaredClearAuthority(unittest.TestCase):
    """A declared projection the host may not infer over.

    The ladder in `clear_roles` is a good default and a bad override. These
    assertions are about the override: each one pairs a declaration with the
    inference the host would otherwise have made, so a case that reads the same
    either way is not one of them.

    `test_the_ladder_cannot_touch_any_projection_the_contract_accepts` is the load-
    bearing one. The protection is arithmetical rather than conditional — every
    rung only fires on an undeclared segment, and an authoritative payload has
    none — so it is proven by enumerating the projections instead of by asserting
    that a flag was read.
    """

    def roles(self, status_) -> dict:
        return {
            seg.key: role
            for seg, role in zip(contract.order_segments(status_), render.clear_roles(status_))
        }

    def keys(self, status_) -> list:
        return [seg.key for seg in render.clear_readings(status_)]

    def test_the_ladder_cannot_touch_any_projection_the_contract_accepts(self):
        # Exhaustive over every role assignment `clear_authority='provider'`
        # admits, for one to three segments, with a state mix chosen to arm all
        # three rungs of the ladder: a `critical` that rule 1 would promote, a
        # measured `neutral` that rule 2 would hand the posture to, and a bare
        # `neutral` that rule 3 would call supporting.
        armed = (
            {"state": contract.SegmentState.CRITICAL, "label": "Stopped"},
            {
                "state": contract.SegmentState.NEUTRAL,
                "label": "Decisions",
                "count": 4,
                "count_label": "today",
            },
            {"state": contract.SegmentState.NEUTRAL, "label": "Task 0dd57ff3"},
        )
        checked = 0
        for size in (1, 2, 3):
            for assignment in itertools.product(ROLES, repeat=size):
                try:
                    projection = declared(
                        *(
                            segment(
                                key=f"s{index}",
                                clear_role=role,
                                order_hint=index,
                                **armed[index],
                            )
                            for index, role in enumerate(assignment)
                        )
                    )
                except contract.ContractViolation:
                    continue  # Not a projection the contract accepts at all.
                checked += 1
                with self.subTest(assignment=[r.value for r in assignment]):
                    self.assertEqual(render.clear_roles(projection), assignment)
        # Guards the guard: an enumeration the contract rejected wholesale would
        # pass vacuously. 2 + 11 + 46 is the number of assignments that satisfy
        # at-most-one-posture and at-least-one-primary over one to three segments.
        self.assertEqual(checked, 59)

    def test_a_declared_projection_comes_back_verbatim(self):
        roles = self.roles(
            declared(
                segment(key="mode", label="Shadow", clear_role=contract.ClearRole.POSTURE),
                segment(
                    key="decision",
                    label="Would block",
                    clear_role=contract.ClearRole.VITAL,
                ),
                segment(
                    key="counters",
                    label="Decisions",
                    count=4,
                    count_label="today",
                    clear_role=contract.ClearRole.SUPPORTING,
                ),
            )
        )
        self.assertEqual(
            roles,
            {
                "mode": contract.ClearRole.POSTURE,
                "decision": contract.ClearRole.VITAL,
                "counters": contract.ClearRole.SUPPORTING,
            },
        )

    def test_an_action_required_state_is_not_promoted_over_a_declaration(self):
        # Under host authority this is an exception by rule 1, and an exception is
        # shown alone. The product called it supporting, which means its own
        # posture is what the line should lead with.
        projection = declared(
            segment(key="mode", label="Enforcing", clear_role=contract.ClearRole.POSTURE),
            segment(
                key="noisy",
                state=contract.SegmentState.CRITICAL,
                label="Stopped",
                clear_role=contract.ClearRole.SUPPORTING,
            ),
        )
        self.assertIs(self.roles(projection)["noisy"], contract.ClearRole.SUPPORTING)
        self.assertEqual(self.keys(projection), ["mode"])
        # The same two segments with nothing declared: the ladder promotes the
        # critical state and shows it alone, so the suppression is what differs.
        inferred = status(
            segments=tuple(
                dataclasses.replace(seg, clear_role=None) for seg in projection.segments
            )
        )
        self.assertEqual(self.keys(inferred), ["noisy"])

    def test_no_posture_is_invented_beside_a_declared_exception(self):
        # Rule 2 of the ladder would hand the measured segment the posture part,
        # giving the provider a primary it did not nominate.
        projection = declared(
            segment(
                key="unreachable",
                state=contract.SegmentState.UNKNOWN,
                label="Not responding",
                clear_role=contract.ClearRole.EXCEPTION,
            ),
            segment(
                key="counters",
                label="Decisions",
                count=4,
                count_label="today",
                clear_role=contract.ClearRole.SUPPORTING,
            ),
        )
        self.assertNotIn(contract.ClearRole.POSTURE, self.roles(projection).values())
        self.assertEqual(self.keys(projection), ["unreachable"])

    def test_a_declared_supporting_measurement_is_not_defaulted_to_vital(self):
        # Rule 3 reads a measurement as a vital signal. A product that calls its
        # counters supporting has said the opposite, and the difference is whether
        # a raw counter reaches Clear at all.
        projection = declared(
            segment(key="mode", label="Shadow", clear_role=contract.ClearRole.POSTURE),
            segment(
                key="counters",
                label="Decisions",
                count=4,
                total=14,
                count_label="today",
                clear_role=contract.ClearRole.SUPPORTING,
            ),
        )
        self.assertIs(self.roles(projection)["counters"], contract.ClearRole.SUPPORTING)
        self.assertEqual(self.keys(projection), ["mode"])
        inferred = dataclasses.replace(
            projection,
            clear_authority=contract.ClearAuthority.HOST,
            segments=tuple(
                dataclasses.replace(seg, clear_role=None) for seg in projection.segments
            ),
        )
        self.assertIn("counters", self.keys(inferred))

    def test_a_declared_exception_may_carry_the_vital_the_product_nominated(self):
        # The founder's `Approval · 12% budget left`: a bare `Approval` does not
        # say what the approval costs, and only Libra knows which reading does.
        projection = declared(
            segment(
                key="approval",
                state=contract.SegmentState.ATTENTION,
                label="Approval",
                clear_role=contract.ClearRole.EXCEPTION,
                order_hint=10,
            ),
            segment(
                key="budget",
                label="12% budget left",
                clear_role=contract.ClearRole.VITAL,
                order_hint=20,
            ),
            segment(
                key="task",
                label="Task 0dd57ff3",
                clear_role=contract.ClearRole.SUPPORTING,
                order_hint=30,
            ),
        )
        self.assertEqual(self.keys(projection), ["approval", "budget"])

    def test_an_inferred_vital_may_not_ride_along_with_an_exception(self):
        # The same two segments with nothing declared. The host guessed the second
        # was a vital, and a guessed reading beside a stop-work state reads as a
        # diagnosis of the stoppage.
        inferred = status(
            segments=(
                segment(
                    key="approval",
                    state=contract.SegmentState.ATTENTION,
                    label="Approval",
                    order_hint=10,
                ),
                segment(
                    key="budget",
                    label="Budget",
                    count=12,
                    count_label="percent left",
                    order_hint=20,
                ),
            )
        )
        self.assertIs(
            render.clear_roles(inferred)[1], contract.ClearRole.POSTURE
        )
        self.assertEqual(self.keys(inferred), ["approval"])

    def test_a_declared_vital_that_went_stale_leaves_the_exception_alone(self):
        def with_age(age: int) -> contract.ProviderStatus:
            return declared(
                segment(
                    key="approval",
                    state=contract.SegmentState.ATTENTION,
                    label="Approval",
                    clear_role=contract.ClearRole.EXCEPTION,
                    order_hint=10,
                ),
                segment(
                    key="budget",
                    label="12% budget left",
                    clear_role=contract.ClearRole.VITAL,
                    age_seconds=age,
                    fresh_for_seconds=60,
                    order_hint=20,
                ),
            )

        # The only difference between the two snapshots is the age.
        self.assertEqual(self.keys(with_age(30)), ["approval", "budget"])
        self.assertEqual(self.keys(with_age(3600)), ["approval"])

    def test_at_most_one_declared_vital_joins_an_exception(self):
        projection = declared(
            segment(
                key="exhausted",
                state=contract.SegmentState.CRITICAL,
                label="Budget exhausted",
                clear_role=contract.ClearRole.EXCEPTION,
                order_hint=10,
            ),
            segment(
                key="first",
                label="First vital",
                clear_role=contract.ClearRole.VITAL,
                order_hint=20,
            ),
            segment(
                key="second",
                label="Second vital",
                clear_role=contract.ClearRole.VITAL,
                order_hint=30,
            ),
        )
        self.assertEqual(self.keys(projection), ["exhausted", "first"])
        self.assertLessEqual(
            len(render.clear_readings(projection)), render.MAX_CLEAR_READINGS
        )

    def test_an_unavailable_declaring_provider_still_shows_one_reading(self):
        # Declaring authority buys a product its choice of primary, not an
        # exemption from the rule that a product which could not read its state
        # shows nothing that could be read as current.
        wedged = declared(
            segment(
                key="availability",
                state=contract.SegmentState.UNKNOWN,
                label="Not responding",
                clear_role=contract.ClearRole.EXCEPTION,
                order_hint=10,
            ),
            segment(
                key="mode",
                state=contract.SegmentState.NEUTRAL,
                label="Shadow",
                clear_role=contract.ClearRole.POSTURE,
                order_hint=20,
            ),
            availability=contract.Availability.UNKNOWN,
        )
        self.assertEqual(self.keys(wedged), ["availability"])

    def test_host_authority_keeps_the_documented_fallback(self):
        # The regression guard for every provider already in the field: the three
        # live snapshots must select exactly as they did before this field existed.
        self.assertEqual(self.keys(live_fornax()), ["latest_finding"])
        self.assertEqual(self.keys(live_circinus()), ["availability"])
        self.assertEqual(self.keys(live_libra()), ["estimate", "escalation"])

    def test_a_per_segment_declaration_alone_does_not_suppress_inference(self):
        # The two mechanisms stay distinct. A provider may adopt `clear_role`
        # incrementally and keep the fallback for the segments it has not judged;
        # only the envelope declaration turns the ladder off.
        partial = status(
            segments=(
                segment(key="mode", label="Shadow", clear_role=contract.ClearRole.POSTURE),
                segment(
                    key="noisy",
                    state=contract.SegmentState.CRITICAL,
                    label="Stopped",
                ),
            )
        )
        self.assertIs(self.roles(partial)["noisy"], contract.ClearRole.EXCEPTION)

    def test_detail_shows_every_segment_whatever_the_authority(self):
        # Clear's selection is Clear's business. Detail is the identity at both
        # authorities, or the two depths would stop being one snapshot.
        for authority in contract.ClearAuthority:
            with self.subTest(authority=authority):
                projection = declared(
                    segment(
                        key="mode", label="Shadow", clear_role=contract.ClearRole.POSTURE
                    ),
                    segment(
                        key="counters",
                        label="Decisions",
                        count=4,
                        count_label="today",
                        clear_role=contract.ClearRole.SUPPORTING,
                    ),
                    clear_authority=authority,
                )
                text = render.render_provider(
                    projection, render.PresentationMode.PLAIN, DETAIL
                )
                self.assertIn("Shadow", text)
                self.assertIn("Decisions", text)

    def test_a_declared_primary_is_the_state_detail_leads_with(self):
        # The cross-depth invariant, under authority: Clear may say less, never
        # something else.
        projection = declared(
            segment(key="mode", label="Enforcing", clear_role=contract.ClearRole.POSTURE),
            segment(
                key="noisy",
                state=contract.SegmentState.CRITICAL,
                label="Stopped",
                clear_role=contract.ClearRole.SUPPORTING,
            ),
        )
        reading = render.clear_readings(projection)[0]
        self.assertTrue(any(reading is seg for seg in projection.segments))
        self.assertIn(
            reading.label,
            render.render_provider(projection, render.PresentationMode.PLAIN, DETAIL),
        )

    def test_a_declared_projection_is_idempotent_and_in_contract_order(self):
        projection = declared(
            segment(
                key="approval",
                state=contract.SegmentState.ATTENTION,
                label="Approval",
                clear_role=contract.ClearRole.EXCEPTION,
                order_hint=30,
            ),
            segment(
                key="budget",
                label="12% budget left",
                clear_role=contract.ClearRole.VITAL,
                order_hint=10,
            ),
        )
        once = render.clear_readings(projection)
        self.assertEqual([seg.key for seg in once], ["budget", "approval"])
        twice = render.clear_readings(dataclasses.replace(projection, segments=once))
        self.assertEqual(once, twice)


def detail_atoms(text: str) -> set:
    """The parenthesised supporting phrases of one rendered segment."""
    if "(" not in text:
        return set()
    inner = text[text.index("(") + 1 : text.rindex(")")]
    return set(inner.split(render.DETAIL_SEPARATOR))


class TestDepthFieldGating(unittest.TestCase):
    """What Clear removes from one reading, and what it may never remove."""

    def clear(self, seg, mode=render.PresentationMode.BALANCED) -> str:
        return render.render_segment(seg, mode, CLEAR)

    def test_the_state_marker_and_label_survive_every_depth(self):
        for factory in ALL_FIXTURES:
            for seg in factory().segments:
                for mode in MODES:
                    for depth in DEPTHS:
                        with self.subTest(key=seg.key, mode=mode, depth=depth):
                            text = render.render_segment(seg, mode, depth)
                            self.assertIn(seg.label, text)
                            self.assertRegex(text, r"[A-Za-z]{3,}")

    def test_clear_drops_a_counter(self):
        seg = segment(key="c", label="Decisions", count=1520, total=3747, count_label="allowed")
        self.assertIn("1520", render.render_segment(seg, render.PresentationMode.BALANCED))
        self.assertNotIn("1520", self.clear(seg))

    def test_clear_drops_confidence(self):
        seg = segment(
            key="e",
            label="Remaining work",
            confidence=contract.Confidence.HIGH,
            confidence_of=contract.ConfidenceSubject.PREFLIGHT_ESTIMATE,
        )
        self.assertNotEqual(detail_atoms(render.render_segment(seg, MODES[0])), set())
        self.assertEqual(detail_atoms(self.clear(seg)), set())

    def test_clear_drops_a_reason(self):
        seg = live_fornax().segments[0]
        self.assertIn("reason not recorded", render.render_segment(seg, MODES[0]))
        self.assertNotIn("reason not recorded", self.clear(seg))

    def test_clear_drops_an_age(self):
        seg = live_fornax().segments[0]
        self.assertIn("ago", render.render_segment(seg, MODES[0]))
        self.assertNotIn("ago", self.clear(seg))

    def test_clear_keeps_a_duration_because_the_span_is_the_reading(self):
        seg = live_libra().segments[1]
        text = self.clear(seg)
        self.assertIn("P90", text)
        # And only the duration: the confidence on the same segment is gone.
        self.assertEqual(detail_atoms(text), {render.format_duration(543530, "P90")})

    def test_clear_keeps_the_not_enforced_marker(self):
        # A would-have-blocked that loses its disclaimer is not a shortened
        # reading of shadow mode. It is a different and false one.
        seg = circinus_status().segments[0]
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn(render.HYPOTHETICAL_TEXT, self.clear(seg))

    def test_clear_supporting_material_is_always_a_subset_of_detail(self):
        # The privacy property, stated as a surface comparison: Clear cannot show
        # a field Detail does not, so it cannot be a wider surface than the one
        # already reviewed.
        for factory in ALL_FIXTURES:
            for seg in factory().segments:
                for mode in MODES:
                    with self.subTest(key=seg.key, mode=mode):
                        self.assertLessEqual(
                            detail_atoms(render.render_segment(seg, mode, CLEAR)),
                            detail_atoms(render.render_segment(seg, mode, DETAIL)),
                        )


class TestComposeDepth(unittest.TestCase):
    """Depth as it reaches the whole line, including under width pressure."""

    def all_live(self) -> tuple:
        return tuple(factory() for factory in LIVE_HOST)

    def test_clear_is_shorter_than_detail_on_the_live_host(self):
        clear = render.compose(UPSTREAM, self.all_live(), depth=CLEAR)
        detail = render.compose(UPSTREAM, self.all_live(), depth=DETAIL)
        self.assertLess(render.display_width(clear), render.display_width(detail))

    def test_every_product_is_named_at_every_depth(self):
        for depth in DEPTHS:
            for mode in MODES:
                text = render.compose(UPSTREAM, self.all_live(), mode=mode, depth=depth)
                for status_ in self.all_live():
                    with self.subTest(depth=depth, mode=mode, provider=status_.provider):
                        self.assertIn(render.provider_display_name(status_.provider), text)

    def test_the_upstream_line_is_untouched_at_every_depth(self):
        for depth in DEPTHS:
            for mode in MODES:
                with self.subTest(depth=depth, mode=mode):
                    self.assertTrue(
                        render.compose(
                            UPSTREAM, self.all_live(), mode=mode, depth=depth
                        ).startswith(UPSTREAM)
                    )

    def test_the_hidden_marker_counts_only_what_width_took(self):
        # Clear shows 4 of the live host's 5 readings. A depth-blind ladder would
        # start its count at 1 and report news the user never lost.
        statuses = self.all_live()
        wide = render.compose("", statuses, depth=CLEAR)
        self.assertNotIn("[+", wide)

    def test_the_hidden_count_is_of_readings_clear_actually_showed(self):
        # Libra reports three segments; Clear shows two. Squeezed by one column,
        # exactly one of those two goes, so the line must say `[+1 more]`.
        #
        # A ladder that counted the raw snapshot instead would say `[+2 more]` --
        # claiming the terminal cost the reader something Clear was never going to
        # show them. That is the difference this asserts, and it is why depth is
        # applied by projection before the ladder rather than at the point of
        # rendering text.
        libra = (live_libra(),)
        full = render.compose("", libra, depth=CLEAR)
        squeezed = render.compose(
            "", libra, depth=CLEAR, width_budget=render.display_width(full) - 1
        )
        self.assertIn(render._hidden_marker(1), squeezed)
        self.assertNotIn(render._hidden_marker(2), squeezed)

    def test_depth_does_not_change_which_rendering_modes_are_tried(self):
        # The axes are independent: asking for less information is not asking for
        # a different density or icon style.
        for mode in MODES:
            for depth in DEPTHS:
                with self.subTest(mode=mode, depth=depth):
                    self.assertEqual(render._mode_candidates(mode), render.MODE_LADDER[mode])

    def test_clear_under_pressure_still_admits_what_it_hid(self):
        statuses = self.all_live()
        full = render.compose("", statuses, depth=CLEAR)
        squeezed = render.compose(
            "", statuses, depth=CLEAR, width_budget=render.display_width(full) - 20
        )
        self.assertIn("[+", squeezed)

    def test_the_worst_state_survives_the_narrowest_rung_at_both_depths(self):
        # The label may be truncated at this width -- that is the documented
        # narrowest rung. The *state* may not be: a budget that quietly drops a
        # `critical` is indistinguishable from a provider reporting all clear.
        statuses = (libra_status(),)
        for depth in DEPTHS:
            with self.subTest(depth=depth):
                text = render.compose(
                    "", statuses, mode=render.PresentationMode.PLAIN, depth=depth, width_budget=40
                )
                self.assertIn(render.STATE_TEXT["critical"], text)
                self.assertIn("Awaiting", text)


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

    def test_the_ladder_never_reintroduces_a_glyph_a_text_request_declined(self):
        for mode in MODES:
            if mode.uses_glyphs:
                continue
            for candidate in render._mode_candidates(mode):
                with self.subTest(mode=mode, candidate=candidate):
                    self.assertFalse(candidate.uses_glyphs)

    def test_a_compact_request_is_never_re_expanded(self):
        # Including via a text fallback: falling from COMPACT to PLAIN would drop
        # the glyphs and hand the prose back, which is the trade COMPACT_PLAIN
        # exists to make unnecessary.
        for mode in MODES:
            if not mode.is_compact:
                continue
            for candidate in render._mode_candidates(mode):
                with self.subTest(mode=mode, candidate=candidate):
                    self.assertTrue(candidate.is_compact)

    def test_every_candidate_list_follows_the_declared_preference_order(self):
        for mode in MODES:
            candidates = render._mode_candidates(mode)
            with self.subTest(mode=mode):
                positions = [render.MODE_PREFERENCE.index(c) for c in candidates]
                self.assertEqual(positions, sorted(positions))
                self.assertEqual(len(set(candidates)), len(candidates))

    def test_a_text_request_can_still_be_tightened(self):
        # The ladder must not run out of rungs for a text reader: a plain request
        # on a narrow terminal has somewhere to go.
        self.assertIn(
            render.PresentationMode.COMPACT_PLAIN,
            render._mode_candidates(render.PresentationMode.PLAIN),
        )


class TestCompactTextMode(unittest.TestCase):
    """The axis combination that did not exist before HORO-1571.

    Both halves are asserted because either one alone would pass for a mode that
    merely aliased an existing member: `plain` is already glyph-free, and
    `compact` is already narrow.
    """

    ALL = (fornax_status(), circinus_status(), libra_status())
    GLYPHS = tuple(render.STATE_GLYPHS.values()) + tuple(render.SCOPE_GLYPHS.values())

    def test_it_is_narrower_than_the_balanced_text_mode(self):
        # The gap this member fills: a text reader on a narrow terminal had no
        # rung below `plain`, which is in fact the widest mode of the four.
        for status_ in self.ALL:
            with self.subTest(provider=status_.provider):
                self.assertLess(
                    render.display_width(
                        render.render_provider(status_, render.PresentationMode.COMPACT_PLAIN)
                    ),
                    render.display_width(
                        render.render_provider(status_, render.PresentationMode.PLAIN)
                    ),
                )

    def test_it_emits_no_host_glyph(self):
        for status_ in self.ALL:
            out = render.render_provider(status_, render.PresentationMode.COMPACT_PLAIN)
            for glyph in self.GLYPHS:
                with self.subTest(provider=status_.provider, glyph=glyph):
                    self.assertNotIn(glyph, out)


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

    def test_a_summary_block_is_divided_by_the_strongest_separator(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out = render.compose(UPSTREAM, (fornax_status(),), mode=mode, depth=CLEAR)
                self.assertTrue(out.startswith(UPSTREAM + render.UPSTREAM_SEPARATORS[mode]))

    def test_a_detail_block_begins_on_the_row_after_the_upstream_output(self):
        # The vertical layout's answer to the same question. A product line that
        # shared a row with the user's own output would not be a product line.
        for mode in MODES:
            with self.subTest(mode=mode):
                out = render.compose(UPSTREAM, (fornax_status(),), mode=mode, depth=DETAIL)
                self.assertTrue(out.startswith(UPSTREAM + render.ROW_SEPARATOR))
                self.assertNotIn(render.UPSTREAM_SEPARATORS[mode], out)

    def test_an_upstream_trailing_newline_is_used_rather_than_doubled(self):
        # Their command ended the row; ours does not need to end it again, and a
        # blank row between the two would read as a gap in the output.
        out = render.compose(UPSTREAM + "\n", (fornax_status(),), depth=DETAIL)
        self.assertTrue(out.startswith(UPSTREAM + "\n"))
        self.assertNotIn("\n\n", out)

    def test_every_upstream_row_is_preserved_exactly(self):
        # A command that printed three rows meant to print three rows. Ours are
        # appended after the last of them, not woven into them.
        upstream = "row one\n  row two indented\nrow three"
        out = render.compose(upstream, (fornax_status(), circinus_status()), depth=DETAIL)
        rows = out.split(render.ROW_SEPARATOR)
        self.assertEqual(rows[: len(upstream.split("\n"))], upstream.split("\n"))


class TestComposeDegradation(unittest.TestCase):
    """The horizontal ladder: what one shared line does as it runs out of room.

    Pinned to `CLEAR`, the depth that layout belongs to. `DETAIL` spends rows
    rather than shedding readings, and has its own sweep in
    `TestDetailLayoutUnderPressure` — including the narrow budgets at which it
    gives the problem back to this ladder.
    """

    ALL = (fornax_status(), circinus_status(), libra_status())
    HIDDEN = re.compile(r"\[\+(\d+) more\]")
    MAX_BUDGET = 240
    DEPTH = CLEAR
    _sweep = None

    @classmethod
    def setUpClass(cls):
        # Every test here walks the same budget sweep over every mode, so it is
        # rendered once rather than once per assertion.
        cls._sweep = []
        for mode in MODES:
            separator = render.UPSTREAM_SEPARATORS[mode]
            for budget in range(0, cls.MAX_BUDGET):
                out = render.compose(
                    UPSTREAM, cls.ALL, mode=mode, width_budget=budget, depth=cls.DEPTH
                )
                tail = out[len(UPSTREAM):]
                cls._sweep.append((mode, budget, out, tail[len(separator):] if tail else ""))

    def block_of(self, out, mode):
        tail = out[len(UPSTREAM):]
        if not tail:
            return ""
        separator = render.UPSTREAM_SEPARATORS[mode]
        self.assertTrue(tail.startswith(separator))
        return tail[len(separator):]

    def each_budget(self):
        return iter(self._sweep)

    def test_a_rendered_block_is_always_introduced_by_the_upstream_separator(self):
        # setUpClass strips the separator by length rather than by match, so this
        # is what makes the rest of the sweep's `block` values trustworthy.
        for mode, budget, out, _ in self.each_budget():
            tail = out[len(UPSTREAM):]
            if tail:
                self.assertTrue(
                    tail.startswith(render.UPSTREAM_SEPARATORS[mode]), f"{mode.value}/{budget}"
                )

    def test_the_block_never_exceeds_its_budget(self):
        for mode, budget, _, block in self.each_budget():
            if render.display_width(block) > budget:
                self.fail(f"mode={mode.value} budget={budget} width={render.display_width(block)}: {block}")

    def test_the_not_enforced_marker_is_never_rendered_partially(self):
        # A fragment like `Shadow mode [NOT` is a mangled form of the one marker
        # that stops a hypothetical reading as an enforced block.
        for mode, budget, _, block in self.each_budget():
            if "[NOT" in block:
                self.assertIn(f"[{render.HYPOTHETICAL_TEXT}]", block, f"{mode.value}/{budget}")

    def test_the_hidden_marker_is_never_rendered_partially(self):
        for mode, budget, _, block in self.each_budget():
            if "[+" in block:
                self.assertRegex(block, self.HIDDEN, f"{mode.value}/{budget}")

    def test_a_hidden_count_is_always_between_one_and_all_but_one(self):
        total = sum(len(s.segments) for s in self.ALL)
        for mode, budget, _, block in self.each_budget():
            match = self.HIDDEN.search(block)
            if match:
                self.assertTrue(1 <= int(match.group(1)) < total, f"{mode.value}/{budget}")

    def test_a_text_mode_stays_ascii_under_every_budget(self):
        for mode, budget, _, block in self.each_budget():
            if not mode.uses_glyphs:
                self.assertTrue(block.isascii(), f"{mode.value}/{budget}: {block}")

    def test_a_non_empty_block_always_carries_a_readable_word(self):
        for mode, budget, _, block in self.each_budget():
            if block:
                self.assertRegex(block, r"[A-Za-z]{3,}", f"{mode.value}/{budget}")

    def test_no_grapheme_cluster_is_ever_split(self):
        every_cluster = set()
        for status_ in self.ALL:
            every_cluster.update(render.grapheme_clusters(render.render_provider(status_, render.PresentationMode.BALANCED)))
        for mode, budget, _, block in self.each_budget():
            for cluster in render.grapheme_clusters(block):
                # A split cluster would produce a lone ZWJ, variation selector
                # or regional indicator that is not a cluster of any input.
                self.assertNotIn(
                    cluster, ("‍", "️"), f"{mode.value}/{budget}: {block!r}"
                )

    def test_a_wide_budget_shows_every_provider_with_nothing_hidden(self):
        out = render.compose(
            UPSTREAM,
            self.ALL,
            mode=render.PresentationMode.BALANCED,
            width_budget=400,
            depth=self.DEPTH,
        )
        block = self.block_of(out, render.PresentationMode.BALANCED)
        for name in ("Fornax", "Circinus", "Libra Governor"):
            self.assertIn(name, block)
        self.assertNotRegex(block, self.HIDDEN)

    def test_the_critical_state_is_the_last_thing_standing(self):
        # At the narrowest rung that still renders anything, what survives is the
        # worst news, not the first provider in the list. The label is truncated
        # there, so the claim is about the state and the label's beginning.
        narrowest = next(
            block
            for mode, _, _, block in self.each_budget()
            if block and mode is render.PresentationMode.BALANCED
        )
        # Asserted as the marker rather than the word: which candidate mode the
        # narrowest rung lands in is the ladder's business, and only the text modes
        # spell the state out. The claim is that the state survived, not how.
        self.assertIn(
            render.state_marker("critical", render.PresentationMode.BALANCED), narrowest
        )
        self.assertIn("Awa", narrowest)
        for other in ("Verified", "Shadow mode", "Preflight"):
            self.assertNotIn(other, narrowest)

    def test_every_rung_that_renders_at_all_renders_the_critical_state(self):
        # The degradation ladder must not be able to shed the one segment the
        # user has to act on while keeping a calmer one.
        for mode, budget, _, block in self.each_budget():
            if block:
                self.assertIn(
                    render.state_marker("critical", mode), block, f"{mode.value}/{budget}"
                )

    def test_no_budget_means_no_degradation(self):
        out = render.compose(
            UPSTREAM, self.ALL, mode=render.PresentationMode.BALANCED, depth=self.DEPTH
        )
        block = self.block_of(out, render.PresentationMode.BALANCED)
        self.assertNotRegex(block, self.HIDDEN)
        self.assertIn("Fornax", block)


class DetailLayoutCase(unittest.TestCase):
    """Shared reading of a vertical block: the user's rows first, then ours.

    The fixture is the three real products, whose names are deliberately of three
    different lengths — a layout that aligns a product column is a layout whose
    tests are vacuous if every name is the same width.
    """

    ALL = (fornax_status(), circinus_status(), libra_status())
    NAMES = ("Fornax", "Circinus", "Libra Governor")

    def compose(self, statuses=None, *, upstream="", mode=render.PresentationMode.BALANCED,
                budget=None) -> str:
        return render.compose(
            upstream,
            self.ALL if statuses is None else statuses,
            mode=mode,
            width_budget=budget,
            depth=DETAIL,
        )

    def block_rows(self, statuses=None, *, upstream="", **kwargs) -> list[str]:
        """The rows the Horonom block added, with the user's own output removed."""
        out = self.compose(statuses, upstream=upstream, **kwargs)
        self.assertTrue(out.startswith(upstream))
        tail = out[len(upstream):]
        if upstream and tail.startswith(render.ROW_SEPARATOR):
            tail = tail[len(render.ROW_SEPARATOR):]
        return tail.split(render.ROW_SEPARATOR) if tail else []

    def product_row(self, rows: list[str], name: str) -> str:
        """The single row this product opens, asserted to be single."""
        owned = [row for row in rows if row.startswith(name)]
        self.assertEqual(len(owned), 1, f"{name} does not open exactly one row: {rows}")
        return owned[0]


class TestDetailLayout(DetailLayoutCase):
    """One product, one row (HORO-1628).

    The requirement came out of Founder DogFooding: at the diagnostic depth the
    products with the most to say are the ones a reader most needs, and on one
    shared line they are exactly the ones that push it off the screen. Vertical
    space is the resource this depth has, so this is what it buys.
    """

    def test_every_product_gets_a_row_of_its_own(self):
        rows = self.block_rows()
        self.assertEqual(len(rows), len(self.ALL))
        for name in self.NAMES:
            self.assertTrue(self.product_row(rows, name))

    def test_the_rows_are_separated_by_a_real_newline(self):
        # Spelled literally rather than through `ROW_SEPARATOR`, because that
        # constant is what the rest of this class reads the block with: a
        # decorative divider standing in for the newline would satisfy every other
        # assertion here and still put the whole block back on one row, since the
        # compositor is what turns a newline into a row.
        out = self.compose(upstream=UPSTREAM)
        self.assertEqual(out.count("\n"), len(self.ALL))
        self.assertEqual(out.split("\n")[0], UPSTREAM)

    def test_a_row_names_one_product_and_only_one(self):
        # The failure this prevents is the one the horizontal layout had: a reader
        # scanning down the block must not have to work out where one product's
        # reading stopped and the next one's began.
        for row in self.block_rows():
            named = [name for name in self.NAMES if name in row]
            self.assertEqual(len(named), 1, row)

    def test_the_rows_are_in_the_contract_order_whatever_order_they_arrive_in(self):
        forward = self.compose(self.ALL)
        backward = self.compose(tuple(reversed(self.ALL)))
        self.assertEqual(forward, backward)
        rows = forward.split(render.ROW_SEPARATOR)
        self.assertEqual([row.split()[0] for row in rows], ["Fornax", "Circinus", "Libra"])

    def test_a_row_carries_its_own_scope_state_and_supporting_context(self):
        # "Each line must independently identify product, primary state and any
        # bounded detail shown" -- asserted on the row, not on the block.
        row = self.product_row(self.block_rows(), "Fornax")
        self.assertIn(render.scope_marker("project", render.PresentationMode.BALANCED), row)
        self.assertIn(render.state_marker("ok", render.PresentationMode.BALANCED), row)
        self.assertIn("Verified", row)
        self.assertIn("3 of 3 claims", row)
        self.assertIn("2m ago", row)

    def test_a_product_with_three_readings_keeps_them_on_its_own_row(self):
        row = self.product_row(self.block_rows(), "Libra Governor")
        for reading in ("Awaiting your approval", "Preflight", "Remaining work"):
            self.assertIn(reading, row)

    def test_nothing_is_hidden_when_nothing_had_to_be(self):
        # The vertical layout sheds nothing, so it must never claim it did. A
        # `[+2 more]` here would be the ladder's marker leaking into a layout that
        # spends rows instead of dropping readings.
        self.assertNotIn("[+", self.compose())

    def test_one_product_is_one_row(self):
        self.assertEqual(len(self.block_rows((fornax_status(),))), 1)

    def test_disabling_a_product_removes_exactly_that_row(self):
        remaining = self.block_rows((fornax_status(), libra_status()))
        self.assertEqual(len(remaining), len(self.ALL) - 1)
        self.assertNotIn("Circinus", render.ROW_SEPARATOR.join(remaining))
        self.assertNotIn("Shadow mode", render.ROW_SEPARATOR.join(remaining))
        for name in ("Fornax", "Libra Governor"):
            self.assertTrue(self.product_row(remaining, name))

    def test_re_enabling_a_product_restores_it_to_the_same_position(self):
        before = self.compose()
        without = self.compose((fornax_status(), libra_status()))
        # Re-registered last, which is the realistic case: a provider re-enabled
        # after the others is still ordered by the contract, not by arrival.
        after = self.compose((fornax_status(), libra_status(), circinus_status()))
        self.assertNotEqual(without, before)
        self.assertEqual(after, before)
        self.assertEqual(after.split(render.ROW_SEPARATOR)[1].split()[0], "Circinus")

    def test_a_product_that_reported_nothing_still_owns_a_row(self):
        # Silence reads as all-clear, which is why the host says it out loud --
        # and it says it on that product's own row rather than anywhere else.
        quiet = status(provider="quiet", segments=(), order_hint=400)
        rows = self.block_rows(self.ALL + (quiet,))
        self.assertEqual(len(rows), len(self.ALL) + 1)
        self.assertIn(render.NO_SEGMENTS_LABEL, self.product_row(rows, "Quiet"))

    def test_a_products_failure_is_contained_to_its_own_row(self):
        broken = status(
            provider="broken",
            availability=contract.Availability.ERROR,
            segments=(),
            fallback_text="Not answering",
            order_hint=400,
        )
        rows = self.block_rows(self.ALL + (broken,))
        self.assertIn("Not answering", self.product_row(rows, "Broken"))
        # The neighbours are untouched: same count, same rows, same readings.
        self.assertEqual(rows[:-1], self.block_rows())

    def test_prose_with_punctuation_and_parentheses_stays_on_one_row(self):
        wordy = status(
            provider="wordy",
            order_hint=400,
            segments=(
                segment(
                    state=contract.SegmentState.ATTENTION,
                    label="Blocked (policy 'strict') - can't proceed",
                ),
            ),
        )
        rows = self.block_rows(self.ALL + (wordy,))
        self.assertEqual(len(rows), len(self.ALL) + 1)
        self.assertIn(
            "Blocked (policy 'strict') - can't proceed",
            self.product_row(rows, "Wordy"),
        )

    def test_the_product_column_is_measured_in_display_cells(self):
        # The alignment claim, and the one thing a multi-cell glyph could break:
        # every row's scope marker begins at the same display column. Measured in
        # cells rather than characters, so an emoji cannot shift a product's
        # identity out from under its own row.
        mode = render.PresentationMode.BALANCED
        rows = self.block_rows()
        self.assertGreater(
            len({render.display_width(name) for name in self.NAMES}),
            1,
            "the fixture no longer exercises names of differing widths",
        )
        starts = set()
        for status_ in self.ALL:
            marker = render.scope_marker(status_.scope.value, mode)
            self.assertEqual(render.cluster_width(marker), 2, "the fixture lost its wide glyphs")
            row = self.product_row(rows, render.provider_display_name(status_.provider))
            starts.add(render.display_width(row[: row.index(marker)]))
        self.assertEqual(len(starts), 1, rows)

    def test_no_row_contains_an_escape_sequence(self):
        # Nothing here can be truncated through an ANSI escape because nothing
        # here emits one: colour is the user's to add to their own output, and a
        # block that wrote its own would also have to measure it.
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertNotIn("\x1b", self.compose(mode=mode))

    def test_an_upstream_escape_sequence_is_passed_through_untouched(self):
        coloured = "\x1b[32mmain\x1b[0m ~/proj"
        out = self.compose(upstream=coloured)
        self.assertTrue(out.startswith(coloured + render.ROW_SEPARATOR))

    def test_no_upstream_output_means_no_leading_row_break(self):
        for upstream in (None, ""):
            with self.subTest(upstream=upstream):
                out = render.compose(upstream, self.ALL, depth=DETAIL)
                self.assertFalse(out.startswith(render.ROW_SEPARATOR))
                self.assertTrue(out.startswith("Fornax"))

    def test_one_product_per_row_holds_in_every_render_style(self):
        # Detail is an information depth, not a fourth rendering mode: the layout
        # is the same in balanced, compact and both text styles.
        for mode in MODES:
            with self.subTest(mode=mode):
                rows = self.block_rows(mode=mode)
                self.assertEqual(len(rows), len(self.ALL))
                for status_ in self.ALL:
                    self.product_row(rows, render.provider_display_name(status_.provider))

    def test_a_text_style_row_says_everything_in_words(self):
        # Read by someone whose terminal cannot show the glyphs, or who is reading
        # a pasted copy of the block in a bug report. Product, state and scope are
        # all words; nothing is carried by colour or icon alone.
        for mode in (render.PresentationMode.PLAIN, render.PresentationMode.COMPACT_PLAIN):
            for status_ in self.ALL:
                with self.subTest(mode=mode, provider=status_.provider):
                    row = self.product_row(
                        self.block_rows(mode=mode),
                        render.provider_display_name(status_.provider),
                    )
                    self.assertTrue(row.isascii(), row)
                    self.assertIn(render.SCOPE_TEXT[status_.scope.value], row)
                    self.assertRegex(row, r"[A-Z]{2,}")

    def test_the_rows_are_the_same_snapshot_the_shared_line_would_have_rendered(self):
        # One state engine, two layouts. Every reading the horizontal rendering
        # shows appears in the vertical one and vice versa, so the layout cannot
        # be a second place where what a product means gets decided.
        for mode in MODES:
            block = self.compose(mode=mode)
            for status_ in self.ALL:
                _, _, readings = render.provider_parts(status_, mode, DETAIL)
                for reading in readings:
                    with self.subTest(mode=mode, reading=reading):
                        self.assertIn(reading, block)

    def test_the_same_snapshot_always_gives_the_same_rows(self):
        # Built twice rather than rendered twice: a layout that ordered by
        # anything mutable, or that cached on object identity, produces a block
        # that depends on which snapshot it was handed rather than on what the
        # snapshot says. Two equal-but-distinct snapshots are what tell them apart.
        first = self.compose((fornax_status(), circinus_status(), libra_status()))
        second = self.compose((fornax_status(), circinus_status(), libra_status()))
        self.assertEqual(first, second)


class TestDetailLayoutUnderPressure(DetailLayoutCase):
    """What the vertical layout does as the terminal narrows.

    It wraps rather than sheds: rows are the resource this depth has, so losing a
    reading to save columns would be paying in the wrong currency. Below the width
    of a single reading it stops being able to lay out at all and hands the problem
    to the horizontal ladder, which is the one place that drops readings and says
    so. Both regimes are swept here, and both are asserted to be reached.
    """

    MAX_BUDGET = 200
    _sweep = None

    @classmethod
    def setUpClass(cls):
        cls._sweep = [
            (mode, budget, render.compose(
                UPSTREAM, cls.ALL, mode=mode, width_budget=budget, depth=DETAIL
            ))
            for mode in MODES
            for budget in range(0, cls.MAX_BUDGET)
        ]

    def rows_of(self, out: str) -> list[str]:
        self.assertTrue(out.startswith(UPSTREAM))
        tail = out[len(UPSTREAM):]
        if tail.startswith(render.ROW_SEPARATOR):
            return tail[len(render.ROW_SEPARATOR):].split(render.ROW_SEPARATOR)
        if tail:  # the horizontal ladder's block, still on the user's own row
            return [tail[len(render.UPSTREAM_SEPARATORS[render.PresentationMode.BALANCED]):]]
        return []

    def vertical(self):
        """Every sweep entry whose block is the vertical layout."""
        for mode, budget, out in self._sweep:
            tail = out[len(UPSTREAM):]
            if tail.startswith(render.ROW_SEPARATOR):
                yield mode, budget, tail[len(render.ROW_SEPARATOR):].split(render.ROW_SEPARATOR)

    def test_no_row_ever_exceeds_the_budget(self):
        # The invariant the horizontal ladder states per line, stated per row.
        for mode, budget, rows in self.vertical():
            for row in rows:
                if render.display_width(row) > budget:
                    self.fail(f"{mode.value}/{budget}: {render.display_width(row)} cols: {row}")

    def test_every_product_still_opens_exactly_one_row(self):
        for mode, budget, rows in self.vertical():
            for name in self.NAMES:
                opened = [row for row in rows if row.startswith(name)]
                self.assertEqual(len(opened), 1, f"{mode.value}/{budget}: {rows}")

    def test_a_continuation_row_is_indented_and_never_looks_like_a_product(self):
        # This is what keeps a wrapped reading attached to the product above it.
        # Without it a narrow terminal produces rows whose owner is a guess, which
        # is the horizontal layout's failure reintroduced one row at a time.
        for mode, budget, rows in self.vertical():
            for row in rows:
                if any(row.startswith(name) for name in self.NAMES):
                    continue
                # Leading whitespace asserted literally: reading the indent out of
                # the constant would make an empty indent pass this test, and an
                # unindented continuation row is exactly the failure being excluded.
                self.assertTrue(row.startswith(" "), f"{mode.value}/{budget}: {row!r}")
                self.assertEqual(row[: len(render.READING_INDENT)], render.READING_INDENT)
                self.assertFalse(row.strip().startswith(tuple(self.NAMES)))

    def test_a_wrapped_product_never_hides_a_reading(self):
        for mode, budget, rows in self.vertical():
            self.assertNotIn("[+", render.ROW_SEPARATOR.join(rows), f"{mode.value}/{budget}")

    def test_the_hypothetical_marker_is_never_wrapped_away_from_its_label(self):
        # A reading is laid out whole or moved whole, so the marker cannot end up
        # on a different row from the label it qualifies -- which would leave a row
        # saying a block happened.
        for mode, budget, rows in self.vertical():
            for row in rows:
                if "Shadow mode" in row:
                    self.assertIn(
                        f"[{render.HYPOTHETICAL_TEXT}]", row, f"{mode.value}/{budget}: {row}"
                    )

    def test_a_text_style_stays_ascii_at_every_width(self):
        for mode, budget, rows in self.vertical():
            if not mode.uses_glyphs:
                for row in rows:
                    self.assertTrue(row.isascii(), f"{mode.value}/{budget}: {row}")

    def test_no_grapheme_cluster_is_ever_split(self):
        for mode, budget, rows in self.vertical():
            for cluster in render.grapheme_clusters(render.ROW_SEPARATOR.join(rows)):
                self.assertNotIn(cluster, ("‍", "️"), f"{mode.value}/{budget}")

    def test_the_users_own_row_survives_every_width(self):
        for _, _, out in self._sweep:
            self.assertTrue(out.startswith(UPSTREAM))

    def test_a_wide_terminal_needs_no_wrapping_at_all(self):
        widest = max(budget for _, budget, _ in self._sweep)
        rows = [rows for mode, budget, rows in self.vertical() if budget == widest]
        self.assertTrue(rows)
        for laid_out in rows:
            self.assertEqual(len(laid_out), len(self.ALL))

    def test_wrapping_is_actually_reached_by_this_sweep(self):
        # Non-vacuity for the four assertions above: a sweep that never wrapped
        # would pass all of them by never taking the path.
        self.assertTrue(
            [budget for _, budget, rows in self.vertical() if len(rows) > len(self.ALL)],
            "no budget in the sweep wrapped a product",
        )

    def test_a_style_that_cannot_be_laid_out_is_narrowed_before_anything_drops(self):
        # Between the width that wraps and the width that gives up there is a band
        # where the rows only survive by spending the icons instead. Asserted as a
        # property of the sweep rather than at a pinned width, so a reading gaining
        # a word moves the band without falsifying the claim: somewhere, a glyph
        # style produced a row-per-product block with no glyph in it.
        narrowed = [
            (mode.value, budget)
            for mode, budget, rows in self.vertical()
            if mode.uses_glyphs and all(row.isascii() for row in rows)
        ]
        self.assertTrue(narrowed, "no width in the sweep traded the icons to keep the rows")

    def test_the_fallback_to_the_shared_line_is_actually_reached(self):
        # And non-vacuity for the regime split itself: some budget is too narrow
        # for any vertical layout, and there the ladder takes over.
        fallbacks = [
            out
            for _, _, out in self._sweep
            if out != UPSTREAM and render.ROW_SEPARATOR not in out
        ]
        self.assertTrue(fallbacks, "no budget fell back to the horizontal ladder")
        self.assertTrue(any("[+" in out for out in fallbacks))

    def test_the_narrowest_widths_hand_the_line_back_untouched(self):
        self.assertEqual([out for _, budget, out in self._sweep if budget == 0], [UPSTREAM] * len(MODES))


class TestHypotheticalUnderPressure(unittest.TestCase):
    """A would-have keeps its disclaimer down to the narrowest rung.

    Its own fixture rather than `TestComposeDegradation.ALL`, deliberately. In
    that fixture Libra reports `critical`, so the narrowest rung -- which shows
    the single worst reading -- always picks Libra and never renders the
    hypothetical segment at all. An assertion about hypotheticals placed there
    passes because the path is never taken, which is the shape of a guard that
    reports a safety it cannot see. Here Circinus's `warn` is the worst thing on
    the line, so the rung has to render it.
    """

    # Two segments, so the rung must also fit the hidden-segment marker. With
    # one it would have columns to spare and the interesting case would not arise.
    STATUSES = (circinus_status(), fornax_status())
    MAX_BUDGET = 120

    def hypothetical_forms(self) -> set[str]:
        """Every way the renderer can spell the hypothetical label.

        Taken from the renderer's own truncation helper rather than guessed,
        because the rung that broke is the one that shortens the label: a check
        for the full label alone would have missed the defect exactly as the
        never-rendered-partially check above did.
        """
        labels = [s.label for st in self.STATUSES for s in st.segments if s.hypothetical]
        self.assertTrue(labels, "the fixture no longer exercises a hypothetical segment")
        forms = set(labels)
        for label in labels:
            for columns in range(render.MIN_LABEL_COLUMNS, render.display_width(label) + 1):
                shortened = render.truncate_to_width(label, columns)
                if shortened:
                    forms.add(shortened)
        return forms

    def test_a_hypothetical_label_never_appears_without_its_marker(self):
        # The stronger form of `test_the_not_enforced_marker_is_never_rendered_
        # partially`, and the form that was missing. An absent marker is worse
        # than a mangled one: `Shadow mode [NOT` is visibly broken and a reader
        # distrusts it, while `Shadow mo...` with nothing after it is a complete
        # sentence that happens to be false.
        forms = self.hypothetical_forms()
        for mode in MODES:
            for budget in range(0, self.MAX_BUDGET):
                block = render.compose(None, self.STATUSES, mode=mode, width_budget=budget)
                if any(form in block for form in forms):
                    self.assertIn(
                        f"[{render.HYPOTHETICAL_TEXT}]",
                        block,
                        f"{mode.value}/{budget}: {block}",
                    )

    def test_the_narrow_rung_is_actually_reached_by_this_fixture(self):
        # What makes the test above non-vacuous, asserted rather than assumed.
        # The rung is identified by the hidden marker with a single label, which
        # only `_fit_minimal` produces for a two-segment fixture.
        reached = [
            render.compose(None, self.STATUSES, mode=mode, width_budget=budget)
            for mode in MODES
            for budget in range(0, self.MAX_BUDGET)
        ]
        forms = self.hypothetical_forms()
        narrow = [
            block
            for block in reached
            if render._hidden_marker(1) in block
            and any(form in block for form in forms)
            and "Verified" not in block
        ]
        self.assertTrue(narrow, "no budget in the sweep reaches the single-worst-reading rung")

    def test_the_rung_hands_the_line_back_where_the_marker_cannot_be_afforded(self):
        # The other half of the fix, and the cost of it. Charging the marker in
        # full means some budgets can no longer say anything, and those budgets
        # return nothing rather than a cheaper sentence that is not true. This
        # asserts the give-up path is real and not just a branch nobody reaches.
        for mode in MODES:
            widths = [
                render.display_width(
                    render.compose(None, self.STATUSES, mode=mode, width_budget=budget)
                )
                for budget in range(0, self.MAX_BUDGET)
            ]
            with self.subTest(mode=mode):
                self.assertIn(0, widths, "every budget rendered something")
                self.assertTrue(any(width > 0 for width in widths), "no budget rendered anything")


class TestComposeDeterminism(unittest.TestCase):
    ALL = (fornax_status(), circinus_status(), libra_status())

    def test_repeated_renders_of_the_same_input_are_identical(self):
        for mode in MODES:
            for budget in (None, 200, 100, 50):
                with self.subTest(mode=mode, budget=budget):
                    first = render.compose(UPSTREAM, self.ALL, mode=mode, width_budget=budget)
                    for _ in range(3):
                        self.assertEqual(
                            first,
                            render.compose(UPSTREAM, self.ALL, mode=mode, width_budget=budget),
                        )

    def test_input_order_does_not_change_the_output(self):
        # The statusline re-renders on a timer, so a registry that yields
        # providers in a different order must not shuffle the line.
        forward = render.compose(UPSTREAM, self.ALL)
        backward = render.compose(UPSTREAM, tuple(reversed(self.ALL)))
        self.assertEqual(forward, backward)

    def test_providers_render_in_contract_order_hint_order(self):
        block = render.compose(None, self.ALL)
        self.assertLess(block.index("Fornax"), block.index("Circinus"))
        self.assertLess(block.index("Circinus"), block.index("Libra Governor"))

    def test_a_duplicate_provider_is_refused_rather_than_silently_deduplicated(self):
        duplicated = (fornax_status(), fornax_status())
        with self.assertRaises(contract.ContractViolation):
            render.compose(UPSTREAM, duplicated)


class TestNoUnknownFieldIsRendered(unittest.TestCase):
    """Permanent guard: nothing outside the validated contract reaches the line.

    Decoy attributes are attached to otherwise valid contract objects. The
    renderer reads only contract fields, so none of these canaries can appear.
    If a future change teaches it to read an unvalidated attribute, this fails.
    """

    DECOY_ATTRIBUTES = (
        "api_key",
        "token",
        "secret",
        "credential",
        "authorization",
        "prompt",
        "tool_payload",
        "rationale",
        "claim_text",
        "log",
        "path",
        "file_path",
        "url",
        "endpoint",
        "command",
        "argv",
        "env",
        "user_data",
    )

    def canary(self, name):
        # Assembled at runtime so this file contains no secret-shaped literal.
        return "CANARY" + "-" + name.upper().replace("_", "") + "-" + ("Z" * 8)

    def bug(self, obj):
        for name in self.DECOY_ATTRIBUTES:
            object.__setattr__(obj, name, self.canary(name))
        return obj

    def test_no_decoy_attribute_reaches_a_rendered_segment(self):
        for mode in MODES:
            seg = self.bug(segment(reason_code="a_reason", age_seconds=30, count=1, count_label="things"))
            text = render.render_segment(seg, mode)
            with self.subTest(mode=mode):
                self.assertNotIn("CANARY", text)

    def test_no_decoy_attribute_reaches_a_rendered_provider(self):
        for mode in MODES:
            provider = self.bug(status(segments=(self.bug(segment()),)))
            text = render.render_provider(provider, mode)
            with self.subTest(mode=mode):
                self.assertNotIn("CANARY", text)

    def test_no_decoy_attribute_reaches_the_composed_line(self):
        for mode in MODES:
            for budget in (None, 200, 80, 30):
                provider = self.bug(status(segments=(self.bug(segment()),)))
                text = render.compose(UPSTREAM, (provider,), mode=mode, width_budget=budget)
                with self.subTest(mode=mode, budget=budget):
                    self.assertNotIn("CANARY", text)

    def test_the_decoys_would_be_visible_if_they_were_rendered(self):
        # Guards the guard: a canary that cannot appear in any output makes the
        # three tests above vacuous.
        seg = self.bug(segment())
        self.assertIn("CANARY", seg.api_key)
        self.assertNotIn("CANARY", render.render_segment(seg, render.PresentationMode.BALANCED))

    def test_a_secret_shaped_label_cannot_be_constructed_in_the_first_place(self):
        # Defence in depth: the renderer's safety rests on the contract having
        # already refused these, so assert that it does.
        shaped = "sk" + "-" + "live" + "".join("abcdefghijklmnopqrstuvwxyz012345")
        with self.assertRaises(contract.PrivacyViolation):
            segment(label=shaped)

    def test_a_path_shaped_label_cannot_be_constructed_in_the_first_place(self):
        with self.assertRaises((contract.PrivacyViolation, contract.ContractViolation)):
            segment(label="/Users/someone/secrets/config.json")


class TestLegendCompleteness(unittest.TestCase):
    """Every token the line can show has a meaning, and vice versa.

    Both directions matter and they fail differently. A token with no meaning is
    a reader looking up the one symbol that is not in the key; a meaning with no
    token is dead prose that will eventually describe something the line stopped
    doing.
    """

    def test_state_meanings_cover_exactly_the_contract_states(self):
        self.assertEqual(
            set(render.STATE_MEANINGS), {member.value for member in contract.SegmentState}
        )

    def test_scope_meanings_cover_exactly_the_contract_scopes(self):
        self.assertEqual(set(render.SCOPE_MEANINGS), {member.value for member in contract.Scope})

    def test_confidence_meanings_cover_exactly_the_contract_subjects(self):
        self.assertEqual(
            set(render.CONFIDENCE_SUBJECT_MEANINGS),
            {member.value for member in contract.ConfidenceSubject},
        )

    def test_confidence_meanings_match_the_phrasings_that_are_printed(self):
        # The subject tables decide what the line says; a meaning keyed to a
        # subject the renderer has no phrasing for would explain a token nobody
        # ever sees.
        self.assertEqual(
            set(render.CONFIDENCE_SUBJECT_MEANINGS), set(render.CONFIDENCE_SUBJECT_TEXT)
        )

    def test_no_meaning_merely_restates_its_own_token(self):
        # The defect this whole surface exists to fix is a token that means
        # nothing to a reader. Glossing `attention` as "attention" reproduces it.
        for table, tokens in (
            (render.STATE_MEANINGS, render.STATE_TEXT),
            (render.SCOPE_MEANINGS, render.SCOPE_TEXT),
        ):
            for key, meaning in table.items():
                with self.subTest(key=key):
                    self.assertGreater(len(meaning.split()), 3)
                    self.assertNotEqual(meaning.strip().lower(), tokens[key].strip().lower())
                    self.assertNotEqual(meaning.strip().lower(), key)

    def test_unknown_is_explained_as_not_being_all_clear(self):
        # The one meaning with a specific job. `unknown` is the state a reader is
        # most likely to take for good news, and the contract is explicit that it
        # is never a stand-in for `ok`.
        self.assertIn("all clear", render.STATE_MEANINGS["unknown"])

    def test_host_scope_is_explained_as_reaching_other_sessions(self):
        # Host-wide state read as session-scoped is the concrete misreading
        # `scope_marker` exists to prevent, so the key has to say so in words.
        self.assertIn("other", render.SCOPE_MEANINGS["host"])

    def test_a_preflight_confidence_is_explained_as_not_a_risk_level(self):
        self.assertIn("Not a risk level", render.CONFIDENCE_SUBJECT_MEANINGS["preflight_estimate"])


class TestLegendTokens(unittest.TestCase):
    """Each row's token is one the renderer actually emits, in the same mode.

    This is the part that cannot be asserted against the legend alone. The rows
    are built from the rendering functions, so comparing a row to those functions
    would restate the implementation; comparing it to *rendered output* is what
    proves the key describes the line.
    """

    def rows(self, mode, title_contains):
        section = next(s for s in render.legend(mode) if title_contains in s.title)
        return section.entries

    def test_every_state_token_appears_in_a_segment_of_that_state(self):
        for mode in MODES:
            for row in self.rows(mode, "state"):
                with self.subTest(mode=mode, state=row.name):
                    state = contract.SegmentState(row.name.lower())
                    rendered = render.render_segment(segment(state=state), mode)
                    self.assertIn(row.token, rendered)

    def test_every_scope_token_appears_in_a_provider_of_that_scope(self):
        for mode in MODES:
            for row in self.rows(mode, "scope"):
                with self.subTest(mode=mode, scope=row.name):
                    scope = contract.Scope(row.name.strip("[]"))
                    rendered = render.render_provider(status(scope=scope), mode)
                    self.assertIn(row.token, rendered)

    def test_every_confidence_token_appears_in_a_segment_with_that_subject(self):
        for mode in MODES:
            for row in self.rows(mode, "confidence"):
                with self.subTest(mode=mode, subject=row.name):
                    rendered = render.render_segment(
                        segment(
                            confidence=contract.Confidence.HIGH,
                            confidence_of=contract.ConfidenceSubject(row.name),
                        ),
                        mode,
                    )
                    self.assertIn(row.token, rendered)

    def test_the_hypothetical_token_appears_in_a_hypothetical_segment(self):
        for mode in MODES:
            row = next(r for r in self.rows(mode, "markers") if r.name == "hypothetical")
            with self.subTest(mode=mode):
                rendered = render.render_segment(segment(hypothetical=True), mode)
                self.assertIn(row.token, rendered)

    def test_the_divider_token_appears_between_an_upstream_line_and_the_block(self):
        # At `CLEAR`, where the block shares the user's row and therefore needs a
        # token to divide it. The key is a vocabulary — "if you see this, it means
        # that" — so a `DETAIL` reader whose block is divided by a row break
        # instead is not being told something false; they are being told what the
        # divider would mean. The row-break form is asserted in
        # `TestComposeUpstreamPreservation`.
        for mode in MODES:
            row = next(r for r in self.rows(mode, "markers") if r.name == "divider")
            with self.subTest(mode=mode):
                composed = render.compose("mine", (status(),), mode=mode, depth=CLEAR)
                self.assertIn(f"mine{render.UPSTREAM_SEPARATORS[mode]}", composed)
                self.assertIn(row.token, composed)

    def test_the_freshness_token_is_an_age_the_renderer_would_print(self):
        for mode in MODES:
            row = next(r for r in self.rows(mode, "markers") if r.name == "freshness")
            with self.subTest(mode=mode):
                rendered = render.render_segment(
                    segment(age_seconds=render._LEGEND_AGE_SECONDS), mode
                )
                self.assertIn(row.token, rendered)

    def test_the_freshness_example_demonstrates_rounding_down(self):
        # A row that rounded to an exact `2m` would teach the reader the opposite
        # of what `format_age` does, and freshness is the reading they are most
        # likely to act on.
        self.assertNotEqual(render._LEGEND_AGE_SECONDS % 60, 0)

    def test_a_text_mode_legend_contains_no_glyph_at_all(self):
        # The load-bearing one. These modes exist for terminals whose font or
        # width handling makes emoji unreliable; a key rendered in glyphs would
        # fail exactly the reader who needed it.
        glyphs = set(render.STATE_GLYPHS.values()) | set(render.SCOPE_GLYPHS.values())
        for mode in MODES:
            if mode.uses_glyphs:
                continue
            # Titles included, not just rows. They are the part most easily
            # forgotten, being prose rather than a token, and a heading that
            # renders as a replacement character is no more readable than a glyph
            # that does.
            printed = "\n".join(
                "\n".join(
                    [section.title]
                    + [f"{row.token} {row.name} {row.meaning}" for row in section.entries]
                )
                for section in render.legend(mode)
            )
            with self.subTest(mode=mode):
                self.assertTrue(printed.isascii(), printed)
                for glyph in glyphs:
                    self.assertNotIn(glyph, printed)

    def test_a_glyph_mode_legend_does_show_the_glyphs_it_explains(self):
        # Guards the guard above: if `legend` returned text tokens in every mode,
        # that test would pass for the wrong reason.
        printed = " ".join(
            row.token
            for section in render.legend(render.PresentationMode.BALANCED)
            for row in section.entries
        )
        for glyph in set(render.STATE_GLYPHS.values()) | set(render.SCOPE_GLYPHS.values()):
            with self.subTest(glyph=glyph):
                self.assertIn(glyph, printed)

    def test_row_names_are_unique_within_a_section(self):
        for mode in MODES:
            for section in render.legend(mode):
                with self.subTest(mode=mode, section=section.title):
                    names = [row.name for row in section.entries]
                    self.assertEqual(len(set(names)), len(names))

    def test_no_row_is_missing_a_meaning(self):
        for mode in MODES:
            for section in render.legend(mode):
                for row in section.entries:
                    with self.subTest(mode=mode, name=row.name):
                        self.assertTrue(row.token)
                        self.assertTrue(row.meaning.strip())


if __name__ == "__main__":
    unittest.main()
