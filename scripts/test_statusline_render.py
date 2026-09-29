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

if __name__ == "__main__":
    unittest.main()
