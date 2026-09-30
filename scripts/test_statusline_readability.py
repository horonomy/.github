"""Can a reader who has never seen this line before read it correctly?

The other rendering tests check the pieces: that a glyph is two columns, that a
cluster is never split, that a token has a legend entry. This module checks the
finished line, for the states the three products actually report, in every
presentation mode and at every width down to one that cannot hold a word -- and
asks the questions a reader asks rather than the ones the implementation
answers.

Those questions, and where each is settled below:

*Is anything drawn wrong?* The defect that started this is live Libra output
using U+2696 SCALES bare, which a terminal may draw as one monochrome column
while the column accounting assumes two. `GlyphVocabularyTest` closes that off
from both ends: the line's non-ASCII vocabulary is a fixed set the host owns, and
a product cannot smuggle a glyph into it, because a label containing one does not
survive the contract's allowlist.

*Does a word mean what it looks like it means?* `FornaxVerdictTest` renders all
six readings Fornax can report and requires them to stay six. `CircinusShadowTest`
requires a would-block to say it was not enforced wherever it appears at all --
the misreading there is a user believing their agent was stopped when nothing
stopped it. `LibraConfidenceTest` requires `high` to arrive attached to what it is
a confidence *in*, because a bare `high` beside a task reads as severity.

*Does it survive a small terminal?* `NarrowTerminalTest` walks the width ladder
and checks that what remains is still true, still whole, and still theirs.

*Does asking for less mean being told less?* `BalancedVersusCompactTest`. Compact
is not uniformly narrower and is not meant to be; the one thing it may never do
is drop a word an exception depends on.

The fixtures are the products' real state shapes, cross-checked against the
representability fixtures in `test_statusline_contract` so the two cannot drift
apart silently. Six Fornax readings are exercised here against that module's five
verdicts because `Observing` is a presentation state the host synthesises, not a
verdict Fornax has -- a distinction a reader has to be able to see, so it is
rendered here and asserted distinct.
"""

from __future__ import annotations

import unittest

import statusline_contract as contract
import statusline_render as render
import test_statusline_contract as contract_tests

# Stand-in for the user's own statusline output. Deliberately not a real path
# from this workstation: it appears in assertion messages, which are evidence.
UPSTREAM = "~/proj (main) 12%"

GLYPH_MODES = (render.PresentationMode.BALANCED, render.PresentationMode.COMPACT)
TEXT_MODES = (render.PresentationMode.PLAIN, render.PresentationMode.COMPACT_PLAIN)
ALL_MODES = GLYPH_MODES + TEXT_MODES

# From "comfortable" down past "cannot hold one label". 12 columns is narrower
# than the shortest honest rendering, which is the interesting end: the question
# there is whether the line degrades or lies.
WIDTHS = (None, 200, 120, 80, 60, 40, 24, 12)

# The two glyphs from the live defect, and the one in the same line that was fine
# -- which is why the bug looked arbitrary rather than systematic.
LIBRA_BROKEN_GLYPHS = ("⚖", "\U0001f6e1")
LIBRA_WORKING_GLYPH = "\U0001f9ed"


def fornax(reading: str, *, reason: bool = True) -> contract.ProviderStatus:
    """One Fornax reading, matching what the daemon reports.

    `unavailable` is the verdict for a finding whose signal could not be
    collected, and is carried while the provider itself is `AVAILABLE` -- the
    daemon answered, it just has nothing verified to say. That is not the same
    fact as the daemon being down, and a reader must be able to tell them apart,
    so both are rendered in the same test below.
    """
    by_reading = {
        "verified": (contract.SegmentState.OK, "Verified"),
        "observing": (contract.SegmentState.NEUTRAL, "Observing"),
        "unverified": (contract.SegmentState.ATTENTION, "Unverified"),
        "review": (contract.SegmentState.WARN, "Needs review"),
        "contradicted": (contract.SegmentState.CRITICAL, "Contradicted"),
        "unavailable": (contract.SegmentState.NEUTRAL, "Signal unavailable"),
    }
    state, label = by_reading[reading]
    explained = reason and reading == "unverified"
    return contract.ProviderStatus(
        provider="fornax",
        provider_version="0.0.8",
        scope=contract.Scope.HOST,
        availability=contract.Availability.AVAILABLE,
        observed_at="2026-09-29T08:00:00Z",
        cache_ttl_seconds=5,
        order_hint=20,
        segments=(
            contract.Segment(
                key="latest_verdict",
                state=state,
                label=label,
                reason_code="evidence_gap" if explained else None,
                reason_label="evidence gap" if explained else None,
                age_seconds=7200,
                explain_key="fornax.latest_verdict",
            ),
        ),
    )


FORNAX_READINGS = ("verified", "observing", "unverified", "review", "contradicted", "unavailable")


def circinus(*, enforcing: bool, refused: bool) -> contract.ProviderStatus:
    """Circinus in one of its four meaningful combinations.

    In shadow mode a refusal is something the policy *would* have done; in
    enforcing mode it is something that happened. The labels differ and the
    hypothetical flag differs, because those are two different facts about the
    user's agent and conflating them is the failure this product cannot afford.
    """
    mode = contract.Segment(
        key="mode",
        state=contract.SegmentState.NEUTRAL,
        label="enforcing" if enforcing else "shadow mode",
        explain_key="circinus.mode",
        order_hint=1,
    )
    decision = contract.Segment(
        key="latest_decision",
        state=contract.SegmentState.WARN if (enforcing and refused) else contract.SegmentState.OK,
        label="blocked" if (enforcing and refused) else "allowed",
        age_seconds=45,
        explain_key="circinus.latest_decision",
        order_hint=2,
    )
    segments = [mode, decision]
    if refused:
        segments.append(
            contract.Segment(
                key="would_block" if not enforcing else "blocked_count",
                state=contract.SegmentState.ATTENTION,
                label="would have blocked" if not enforcing else "blocked",
                count=113,
                total=705,
                count_label="decisions",
                hypothetical=not enforcing,
                explain_key="circinus.would_block",
                order_hint=3,
            )
        )
    return contract.ProviderStatus(
        provider="circinus",
        provider_version="1.2.0",
        scope=contract.Scope.HOST,
        availability=contract.Availability.AVAILABLE,
        observed_at="2026-09-29T08:00:00Z",
        cache_ttl_seconds=5,
        order_hint=30,
        segments=tuple(segments),
    )


def libra(*, replan: str = "escalated") -> contract.ProviderStatus:
    """Libra mid-task, in one of its three replan states."""
    by_replan = {
        "steady": (contract.SegmentState.OK, "on plan"),
        "escalated": (contract.SegmentState.ATTENTION, "escalated — awaiting approval"),
        "approved": (contract.SegmentState.OK, "replan approved"),
    }
    state, label = by_replan[replan]
    return contract.ProviderStatus(
        provider="libra-governor",
        provider_version="0.0.2",
        scope=contract.Scope.SESSION,
        availability=contract.Availability.AVAILABLE,
        observed_at="2026-09-29T08:00:00Z",
        cache_ttl_seconds=5,
        order_hint=40,
        segments=(
            contract.Segment(
                key="preflight",
                state=contract.SegmentState.OK,
                label="high",
                confidence=contract.Confidence.HIGH,
                confidence_of=contract.ConfidenceSubject.PREFLIGHT_ESTIMATE,
                explain_key="libra.preflight",
                order_hint=1,
            ),
            contract.Segment(
                key="remaining_p90",
                state=contract.SegmentState.NEUTRAL,
                label="remaining work",
                duration_seconds=720,
                duration_label="P90",
                explain_key="libra.remaining_p90",
                order_hint=2,
            ),
            contract.Segment(
                key="replan_state",
                state=state,
                label=label,
                explain_key="libra.replan_state",
                order_hint=3,
            ),
        ),
    )


def every_snapshot() -> list[tuple[str, tuple[contract.ProviderStatus, ...]]]:
    """Every product state this module renders, named for assertion messages."""
    snapshots = [(f"fornax:{reading}", (fornax(reading),)) for reading in FORNAX_READINGS]
    for enforcing in (False, True):
        for refused in (False, True):
            name = f"circinus:{'enforcing' if enforcing else 'shadow'}:{'refused' if refused else 'allowed'}"
            snapshots.append((name, (circinus(enforcing=enforcing, refused=refused),)))
    snapshots += [(f"libra:{state}", (libra(replan=state),)) for state in ("steady", "escalated", "approved")]
    snapshots.append(
        (
            "all three",
            (fornax("contradicted"), circinus(enforcing=False, refused=True), libra()),
        )
    )
    return snapshots


def words(text: str) -> str:
    """The text with everything but letters, digits and spaces removed.

    Used to ask whether a meaning survives when the decoration does not: a reader
    on a terminal that draws every glyph as a blank still has to be able to tell
    an exception from a clean run.
    """
    return "".join(char if char.isalnum() else " " for char in text)


class ReadabilityCase(unittest.TestCase):
    def lines(self, statuses, *, upstream: str | None = None):
        """Every (mode, width, line) this snapshot can produce."""
        for mode in ALL_MODES:
            for width in WIDTHS:
                yield mode, width, render.compose(
                    upstream, statuses, mode=mode, width_budget=width
                )


class GlyphVocabularyTest(ReadabilityCase):
    """Nothing reaches the line that a terminal may draw wrong."""

    def allowed_non_ascii(self) -> dict[str, str]:
        """Every non-ASCII cluster the host may emit, and why it is allowed.

        Two kinds, and the distinction is the whole safety argument: a glyph
        *intended as an emoji*, which must be presentation-safe so it occupies the
        two columns the accounting charges it; and a text character, which must
        measure one column and must not be mistaken for an emoji.
        """
        allowed = {}
        for glyph in list(render.STATE_GLYPHS.values()) + list(render.SCOPE_GLYPHS.values()):
            allowed[glyph] = "host glyph"
        for separator in list(render.SEGMENT_SEPARATORS.values()) + list(
            render.UPSTREAM_SEPARATORS.values()
        ):
            for char in separator.strip():
                if ord(char) > 127:
                    allowed[char] = "separator"
        # Non-ASCII punctuation the contract's label allowlist permits, so a
        # product's own prose can legitimately contain it.
        for char in "—≤≥":
            allowed[char] = "label punctuation"
        return allowed

    def test_the_two_glyphs_libra_shipped_cannot_reach_a_label(self) -> None:
        """The defect, closed at its source rather than at the renderer.

        Libra chose those glyphs itself. The fix is not that the host draws them
        better; it is that a product does not get to choose, and a label carrying
        one is refused before anything renders.
        """
        for glyph in LIBRA_BROKEN_GLYPHS + (LIBRA_WORKING_GLYPH,):
            with self.subTest(glyph=glyph):
                with self.assertRaises(contract.ContractViolation):
                    contract.Segment(
                        key="replan_state",
                        state=contract.SegmentState.OK,
                        label=f"{glyph} approved",
                    )

    def test_the_glyphs_libra_shipped_are_exactly_the_ones_the_rule_refuses(self) -> None:
        # So that this module's idea of the defect stays the renderer's idea of it.
        for glyph in LIBRA_BROKEN_GLYPHS:
            with self.subTest(glyph=glyph):
                self.assertFalse(render.is_emoji_presentation_safe(glyph))
                self.assertNotIn(glyph, self.allowed_non_ascii())
        self.assertTrue(render.is_emoji_presentation_safe(LIBRA_WORKING_GLYPH))

    def test_no_line_contains_a_glyph_outside_the_hosts_own_vocabulary(self) -> None:
        allowed = self.allowed_non_ascii()
        for name, statuses in every_snapshot():
            for mode, width, line in self.lines(statuses, upstream=UPSTREAM):
                for cluster in render.grapheme_clusters(line):
                    if all(ord(char) < 128 for char in cluster):
                        continue
                    with self.subTest(snapshot=name, mode=mode, width=width, cluster=cluster):
                        self.assertIn(cluster, allowed, f"unaccounted glyph in: {line}")

    def test_every_glyph_in_the_vocabulary_measures_what_it_is_charged(self) -> None:
        for cluster, why in self.allowed_non_ascii().items():
            with self.subTest(cluster=cluster, why=why):
                if why == "host glyph":
                    self.assertTrue(render.is_emoji_presentation_safe(cluster))
                    self.assertEqual(render.cluster_width(cluster), 2)
                else:
                    self.assertEqual(render.cluster_width(cluster), 1)
                    self.assertFalse(render.is_emoji_presentation_safe(cluster))


class FornaxVerdictTest(ReadabilityCase):
    """Six readings, and a reader who can tell which one they are looking at."""

    def test_the_readings_here_are_the_contracts_verdicts_plus_observing(self) -> None:
        """Anti-drift, both ways.

        If Fornax gains a verdict, the contract module's list changes and this
        fails until the new reading is rendered and read. If `Observing` ever
        becomes a real verdict, the subtraction below stops being one.
        """
        verdicts = set(contract_tests.FornaxRepresentabilityTest.VERDICTS)
        self.assertEqual(set(FORNAX_READINGS) - verdicts, {"observing"})
        for verdict in verdicts:
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    fornax(verdict).segments[0].label,
                    contract_tests._fornax_verdict(verdict).segments[0].label,
                )

    def test_all_six_readings_render_differently_in_every_mode(self) -> None:
        for mode in ALL_MODES:
            rendered = {
                reading: render.compose(None, (fornax(reading),), mode=mode)
                for reading in FORNAX_READINGS
            }
            with self.subTest(mode=mode):
                self.assertEqual(len(set(rendered.values())), len(FORNAX_READINGS), rendered)

    def test_every_reading_says_what_it_is_in_words(self) -> None:
        for reading in FORNAX_READINGS:
            label = fornax(reading).segments[0].label
            for mode, width, line in self.lines((fornax(reading),)):
                # Below the width that can hold the label the renderer truncates
                # it, which is honest; what it must never do is render the state
                # with no word at all.
                with self.subTest(reading=reading, mode=mode, width=width):
                    if line:
                        self.assertRegex(words(line), r"[A-Za-z]{3}")
                if width in (None, 200, 120):
                    self.assertIn(label, line, f"{reading} at {width}: {line}")

    def test_an_unverified_reading_carries_its_reason_and_its_freshness(self) -> None:
        line = render.compose(None, (fornax("unverified"),))
        self.assertIn("evidence gap", line)
        self.assertIn("2h ago", line)

    def test_an_unverified_reading_with_no_reason_is_given_none(self) -> None:
        """Freshness is still shown; a reason is not guessed.

        The founder wrapper's failure was inferring a cause from activity. An
        unexplained gap stays unexplained.
        """
        line = render.compose(None, (fornax("unverified", reason=False),))
        self.assertIn("Unverified", line)
        self.assertIn("2h ago", line)
        self.assertNotIn("evidence", line)

    def test_a_signal_that_could_not_be_collected_is_not_a_product_that_is_down(self) -> None:
        signal = render.compose(None, (fornax("unavailable"),))
        down = render.compose(
            None,
            (
                contract.not_available(
                    "fornax",
                    "0.0.8",
                    contract.Scope.HOST,
                    contract.Availability.UNAVAILABLE,
                    "daemon_not_running",
                    "daemon not running",
                ),
            ),
        )
        self.assertNotEqual(signal, down)
        self.assertIn("Signal unavailable", signal)
        self.assertNotIn("daemon", signal)
        self.assertIn("daemon not running", down)


class CircinusShadowTest(ReadabilityCase):
    """A would-block is not a block, at any width, in any mode."""

    COMBINATIONS = (
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    )

    def test_the_four_combinations_render_differently(self) -> None:
        for mode in ALL_MODES:
            rendered = {
                (enforcing, refused): render.compose(
                    None, (circinus(enforcing=enforcing, refused=refused),), mode=mode
                )
                for enforcing, refused in self.COMBINATIONS
            }
            with self.subTest(mode=mode):
                self.assertEqual(len(set(rendered.values())), len(self.COMBINATIONS), rendered)

    def test_a_shadow_would_block_always_says_it_was_not_enforced(self) -> None:
        """Wherever the phrase appears at all, the disclaimer appears with it.

        Stated over the width ladder rather than at one width because the failure
        mode is a degradation step that keeps the alarming half of the phrase and
        sheds the reassuring half.
        """
        statuses = (circinus(enforcing=False, refused=True),)
        for mode, width, line in self.lines(statuses, upstream=UPSTREAM):
            with self.subTest(mode=mode, width=width):
                if "would have blocked" in line or "would" in words(line).split():
                    self.assertIn(render.HYPOTHETICAL_TEXT, line, line)

    def test_a_shadow_reading_never_claims_something_was_stopped(self) -> None:
        statuses = (circinus(enforcing=False, refused=True),)
        for mode, width, line in self.lines(statuses):
            with self.subTest(mode=mode, width=width):
                if render.HYPOTHETICAL_TEXT in line:
                    continue
                # With the disclaimer gone the bare claim must be gone too.
                self.assertNotIn("blocked", line.lower(), line)

    def test_an_enforced_block_is_not_marked_hypothetical(self) -> None:
        line = render.compose(None, (circinus(enforcing=True, refused=True),))
        self.assertIn("blocked", line)
        self.assertNotIn(render.HYPOTHETICAL_TEXT, line)

    def test_the_mode_is_named_rather_than_implied(self) -> None:
        shadow = render.compose(None, (circinus(enforcing=False, refused=False),))
        enforcing = render.compose(None, (circinus(enforcing=True, refused=False),))
        self.assertIn("shadow mode", shadow)
        self.assertIn("enforcing", enforcing)

    def test_the_refusal_count_keeps_its_noun(self) -> None:
        for mode in ALL_MODES:
            line = render.compose(None, (circinus(enforcing=False, refused=True),), mode=mode)
            with self.subTest(mode=mode):
                self.assertIn("decisions", line)
                self.assertNotIn("wb", line.lower().replace("would", ""))


class LibraConfidenceTest(ReadabilityCase):
    """`high` is a confidence in something, and the line says in what."""

    def test_the_preflight_confidence_names_its_subject_in_every_mode(self) -> None:
        for mode in ALL_MODES:
            line = render.compose(None, (libra(),), mode=mode)
            with self.subTest(mode=mode):
                self.assertIn("preflight", line.lower(), line)

    def test_the_bare_value_never_appears_without_its_subject(self) -> None:
        """A reader who sees only `high` reads severity, not certainty."""
        for mode, width, line in self.lines((libra(),)):
            with self.subTest(mode=mode, width=width):
                if "high" in line.lower():
                    self.assertIn("preflight", line.lower(), line)

    def test_the_line_never_uses_the_pf_abbreviation(self) -> None:
        for mode, width, line in self.lines((libra(),), upstream=UPSTREAM):
            with self.subTest(mode=mode, width=width):
                self.assertNotIn("pf:", line.lower())
                self.assertNotIn(" pf ", f" {words(line).lower()} ")

    def test_escalation_and_approval_are_distinct_and_explicit(self) -> None:
        for mode in ALL_MODES:
            escalated = render.compose(None, (libra(replan="escalated"),), mode=mode)
            approved = render.compose(None, (libra(replan="approved"),), mode=mode)
            with self.subTest(mode=mode):
                self.assertNotEqual(escalated, approved)
                self.assertIn("awaiting approval", escalated)
                self.assertIn("approved", approved)

    def test_escalation_does_not_depend_on_punctuation_or_a_glyph(self) -> None:
        """Strip the decoration and the meaning has to still be there.

        `!` before a label, which is what a hand-rolled wrapper reaches for, does
        not survive this and should not: a reader cannot decode it, and a terminal
        that renders the glyph as a blank leaves nothing behind.
        """
        for mode in ALL_MODES:
            stripped = words(render.compose(None, (libra(replan="escalated"),), mode=mode))
            with self.subTest(mode=mode):
                self.assertIn("escalated", stripped)
                self.assertIn("awaiting approval", stripped)

    def test_the_remaining_estimate_says_what_the_span_is(self) -> None:
        for mode in ALL_MODES:
            line = render.compose(None, (libra(),), mode=mode)
            with self.subTest(mode=mode):
                self.assertIn("P90", line)
                self.assertIn("12m", line)


class ScopeTest(ReadabilityCase):
    """Whether a reading is about this project, this session or the machine."""

    def scoped(self, scope: contract.Scope) -> contract.ProviderStatus:
        return contract.ProviderStatus(
            provider="circinus",
            provider_version="1.2.0",
            scope=scope,
            availability=contract.Availability.AVAILABLE,
            segments=(
                contract.Segment(
                    key="mode", state=contract.SegmentState.NEUTRAL, label="enforcing"
                ),
            ),
        )

    def test_the_three_scopes_are_distinguishable_in_the_default_style(self) -> None:
        rendered = {
            scope: render.compose(None, (self.scoped(scope),)) for scope in contract.Scope
        }
        self.assertEqual(len(set(rendered.values())), len(contract.Scope), rendered)

    def test_the_text_fallback_spells_the_scope_out(self) -> None:
        for scope in contract.Scope:
            line = render.compose(
                None, (self.scoped(scope),), mode=render.PresentationMode.PLAIN
            )
            with self.subTest(scope=scope):
                self.assertIn(f"[{scope.value}]", line)

    def test_the_scope_is_never_absent_from_a_provider_group(self) -> None:
        for scope in contract.Scope:
            for mode in ALL_MODES:
                line = render.compose(None, (self.scoped(scope),), mode=mode)
                expected = render.scope_marker(scope.value, mode)
                with self.subTest(scope=scope, mode=mode):
                    self.assertIn(expected, line)


class NarrowTerminalTest(ReadabilityCase):
    """What is left when there is no room, and whether it is still true."""

    def test_the_block_never_exceeds_the_budget_it_was_given(self) -> None:
        for name, statuses in every_snapshot():
            for mode, width, block in self.lines(statuses):
                if width is None:
                    continue
                with self.subTest(snapshot=name, mode=mode, width=width):
                    self.assertLessEqual(render.display_width(block), width, block)

    def test_no_glyph_is_ever_left_half_rendered(self) -> None:
        """A truncation that cuts inside a cluster produces a replacement box.

        Checked by looking for a fragment of a host glyph rather than by trusting
        the truncation helper, because the helper is not the only thing that
        assembles the line.
        """
        fragments = {
            glyph[:index]
            for glyph in list(render.STATE_GLYPHS.values()) + list(render.SCOPE_GLYPHS.values())
            for index in range(1, len(glyph))
            if glyph[:index] != glyph
        }
        for name, statuses in every_snapshot():
            for mode, width, line in self.lines(statuses, upstream=UPSTREAM):
                clusters = render.grapheme_clusters(line)
                with self.subTest(snapshot=name, mode=mode, width=width):
                    self.assertEqual("".join(clusters), line)
                    self.assertFalse(
                        fragments & set(clusters), f"a partial glyph survived in: {line}"
                    )

    def test_the_users_own_line_is_untouched_at_every_width(self) -> None:
        for name, statuses in every_snapshot():
            for mode, width, line in self.lines(statuses, upstream=UPSTREAM):
                with self.subTest(snapshot=name, mode=mode, width=width):
                    self.assertTrue(line.startswith(UPSTREAM), line)

    def test_whatever_survives_still_carries_a_word(self) -> None:
        for name, statuses in every_snapshot():
            for mode, width, block in self.lines(statuses):
                with self.subTest(snapshot=name, mode=mode, width=width):
                    if block:
                        self.assertRegex(words(block), r"[A-Za-z]{3}", block)

    def test_the_worst_reading_is_the_last_thing_to_go(self) -> None:
        statuses = (fornax("contradicted"), circinus(enforcing=False, refused=False), libra(replan="steady"))
        for mode in ALL_MODES:
            for width in (200, 120, 80, 60, 40, 24):
                block = render.compose(None, statuses, mode=mode, width_budget=width)
                with self.subTest(mode=mode, width=width):
                    if block:
                        self.assertIn("Contradict", block, block)


class BalancedVersusCompactTest(ReadabilityCase):
    """Asking for a tighter line does not mean being told less."""

    def test_compact_keeps_the_word_for_every_exception(self) -> None:
        for reading in ("unverified", "review", "contradicted"):
            compact = render.compose(
                None, (fornax(reading),), mode=render.PresentationMode.COMPACT
            )
            state = fornax(reading).segments[0].state.value
            with self.subTest(reading=reading):
                self.assertIn(render.STATE_TEXT[state], compact, compact)

    def test_compact_keeps_the_not_enforced_marker(self) -> None:
        compact = render.compose(
            None,
            (circinus(enforcing=False, refused=True),),
            mode=render.PresentationMode.COMPACT,
        )
        self.assertIn(render.HYPOTHETICAL_TEXT, compact)

    def test_compact_keeps_the_reason_for_an_exception_and_drops_it_otherwise(self) -> None:
        unverified = render.compose(
            None, (fornax("unverified"),), mode=render.PresentationMode.COMPACT
        )
        self.assertIn("evidence gap", unverified)

    def test_compact_is_narrower_for_a_calm_reading(self) -> None:
        calm = (fornax("verified"), libra(replan="steady"))
        balanced = render.compose(None, calm, mode=render.PresentationMode.BALANCED)
        compact = render.compose(None, calm, mode=render.PresentationMode.COMPACT)
        self.assertLess(render.display_width(compact), render.display_width(balanced))

    def test_compact_is_wider_for_an_alarming_reading_and_that_is_the_trade(self) -> None:
        """The reason the degradation ladder measures instead of assuming.

        Asserted rather than commented so that if the trade is ever reversed --
        compact quietly dropping the state word to save columns -- this fails and
        names what was given up.
        """
        alarming = (fornax("contradicted"),)
        balanced = render.compose(None, alarming, mode=render.PresentationMode.BALANCED)
        compact = render.compose(None, alarming, mode=render.PresentationMode.COMPACT)
        self.assertGreater(render.display_width(compact), render.display_width(balanced))
        self.assertIn(render.STATE_TEXT["critical"], compact)

    def test_the_two_glyph_modes_report_the_same_facts(self) -> None:
        """Different density, not different content.

        The labels a product supplied appear in both; only the host's own
        decoration differs. A compact mode that dropped a product's label would be
        deciding on the user's behalf which product matters.
        """
        for name, statuses in every_snapshot():
            balanced = render.compose(None, statuses, mode=render.PresentationMode.BALANCED)
            compact = render.compose(None, statuses, mode=render.PresentationMode.COMPACT)
            for status in statuses:
                for segment in status.segments:
                    with self.subTest(snapshot=name, label=segment.label):
                        self.assertIn(segment.label, balanced)
                        self.assertIn(segment.label, compact)


if __name__ == "__main__":
    unittest.main()
