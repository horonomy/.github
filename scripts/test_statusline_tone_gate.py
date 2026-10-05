"""Colour as emphasis on meaning already spelled out, proven against real payloads.

HORO-1719 asks for semantic colour on a line that three products share, and the
hard part is not the palette. It is that colour is the one part of a statusline a
reader cannot verify: a glanced-at green is indistinguishable from a correct
green, so a mis-toned reading does not look like a bug, it looks like good news.
Every proof here is therefore built so that it fails when the *meaning* behind a
tone slips, not merely when a sequence changes.

Three properties carry the ticket, and each is asserted in the strongest form
available rather than the most convenient:

*Colour adds nothing.* `test_the_coloured_line_is_the_plain_line_with_emphasis`
strips the escapes back out of a toned line and requires the result to equal the
uncoloured line byte for byte -- at every mode, depth, width budget and colour
rung, over real captured payloads. That is one assertion covering the plain-mode,
`NO_COLOR`, non-TTY and accessibility requirements at once, because it says the
words on the line never depended on the colour being there.

*A tone means what the product said.* The tone derivation never reads a label, so
`38% budget left` cannot become green by arithmetic on the number a reader can
see. It is the *band* -- Libra's own classification of its own utilization --
that decides, and since the re-capture (`statusline_capture.py`) all four bands
are present in committed bytes, including that 38%-left reading declaring
`caution`. Before those captures existed this file could only have asserted over
a hand-written payload, which would have proven a property of the payload.

*What cannot be read is not reassuring.* The derivation's two host-owned caps --
a hypothetical critical renders as a warning, and an unreadable or stale reading
loses any reassuring tone -- are the ones a product cannot opt out of, because
they are the two ways colour could tell a reader something the line does not say.

The module imports its payload loader from `test_statusline_authority_gate` for
the reason that file gives in its own docstring: a hand-written approximation of
a payload proves a property of the approximation. The derivation tests that do
build their own segments are about shapes the products do not currently emit, and
each says so where it is asserted.

What is deliberately *not* proven here: that Libra's bands sit at the right
thresholds. That is arithmetic inside the product, tested in the product's own
suite against its own utilization inputs, and asserting a copy of those
thresholds here would mean two sources of truth for one rule. What this gate owns
is everything after the band reaches the wire.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import json
import re
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_render as render
from test_statusline_authority_gate import LINES, PAYLOADS, UPSTREAM, of, payload, rewire
from test_statusline_depth_gate import segment, status

CLEAR = render.InformationDepth.CLEAR
DETAIL = render.InformationDepth.DETAIL
DEPTHS = (CLEAR, DETAIL)
MODES = tuple(render.PresentationMode)
BALANCED = render.PresentationMode.BALANCED

NONE = render.ColorCapability.NONE
ANSI16 = render.ColorCapability.ANSI16
ANSI256 = render.ColorCapability.ANSI256
RUNGS = (NONE, ANSI16, ANSI256)
COLOURED = (ANSI16, ANSI256)

TONES = tuple(contract.SemanticState)
ESC = "\x1b"
SEQUENCE = re.compile(r"\x1b\[[0-9;]*m")

# Width budgets that reach every degradation rung: roomy, tight enough to drop
# readings, and tight enough to reach the single-reading floor. Colour must not
# change which rung is chosen, so the narrow ones are the interesting ones.
BUDGETS = (None, 200, 120, 80, 60, 40, 28)


def sequences(text: str) -> tuple[str, ...]:
    return tuple(SEQUENCE.findall(text))


def opening(text: str) -> str:
    """The escape sequence a rendered reading starts with, or `""`."""
    match = SEQUENCE.match(text)
    return match.group(0) if match else ""


def tone_of(item, key: str) -> contract.SemanticState:
    """The tone the host gives one segment of one captured payload."""
    live = render._has_live_readings(item.status)
    for part in contract.order_segments(item.status):
        if part.key == key:
            return render.semantic_tone(part, live=live)
    raise KeyError(f"{item.name} has no {key!r} segment")


def wire_segments(item) -> dict:
    return {part["key"]: part for part in item.wire["segments"]}


class TonePaletteTest(unittest.TestCase):
    """What the host is allowed to emit for each tone, at each rung."""

    def test_no_rung_but_a_coloured_one_emits_anything(self) -> None:
        # The renderer's default. Asserted first because every other guarantee in
        # this file rests on "off" meaning off rather than meaning dark grey.
        for tone in TONES:
            with self.subTest(tone=tone):
                self.assertEqual(render.tone_sgr(tone, NONE), "")

    def test_every_tone_but_neutral_is_coloured_where_colour_is_available(self) -> None:
        # `NEUTRAL` is silent on purpose: it is the tone for a reading that is
        # neither reassuring nor concerning, and the honest rendering of that is
        # the reader's own foreground colour. A palette entry for it would make
        # "nothing to report" into a statement.
        for tone in TONES:
            for rung in COLOURED:
                with self.subTest(tone=tone, rung=rung):
                    emitted = render.tone_sgr(tone, rung)
                    if tone is contract.SemanticState.NEUTRAL:
                        self.assertEqual(emitted, "")
                    else:
                        self.assertTrue(emitted.startswith(ESC), emitted)

    def test_every_coloured_tone_is_distinguishable_at_every_rung(self) -> None:
        # Six tones, six treatments, at both rungs -- the property Fornax's six
        # verdict outcomes need, and the one a sixteen-colour terminal is most
        # likely to break by collapsing two of them onto the same parameter.
        for rung in COLOURED:
            emitted = [
                render.tone_sgr(tone, rung)
                for tone in TONES
                if tone is not contract.SemanticState.NEUTRAL
            ]
            with self.subTest(rung=rung):
                self.assertEqual(len(set(emitted)), len(emitted), emitted)

    def test_caution_and_warning_stay_apart_without_an_amber(self) -> None:
        # The one palette problem that is not solved by having fewer colours:
        # caution and warning are adjacent in meaning and both live in the
        # yellow-to-orange part of the spectrum, which a sixteen-colour terminal
        # does not have. The fallback separates them by weight instead, so the
        # distinction survives where the hue cannot.
        caution = contract.SemanticState.CAUTION
        warning = contract.SemanticState.WARNING
        for rung in COLOURED:
            with self.subTest(rung=rung):
                self.assertNotEqual(render.tone_sgr(caution, rung), render.tone_sgr(warning, rung))
        self.assertEqual(render.tone_sgr(caution, ANSI16), render.tone_sgr(caution, ANSI256))
        self.assertNotEqual(render.tone_sgr(warning, ANSI16), render.tone_sgr(warning, ANSI256))

    def test_painting_changes_no_characters_and_no_columns(self) -> None:
        samples = ("38% budget left", "Would block", "", "⚠️ Review", "Budget exhausted")
        for text in samples:
            for tone in TONES:
                for rung in RUNGS:
                    with self.subTest(text=text, tone=tone, rung=rung):
                        painted = render.paint(text, tone, rung)
                        self.assertEqual(render.strip_sgr(painted), text)
                        self.assertEqual(render.display_width(painted), render.display_width(text))

    def test_every_sequence_opened_is_closed(self) -> None:
        # An unterminated sequence does not stay inside our block: it bleeds into
        # whatever the host prints next, which on a statusline is the user's own
        # prompt. Two sequences exactly -- one in, one out.
        for tone in TONES:
            for rung in COLOURED:
                painted = render.paint("Would block", tone, rung)
                with self.subTest(tone=tone, rung=rung):
                    if tone is contract.SemanticState.NEUTRAL:
                        self.assertEqual(sequences(painted), ())
                    else:
                        self.assertEqual(len(sequences(painted)), 2, painted)
                        self.assertTrue(painted.endswith("\x1b[0m"))

    def test_nothing_is_painted_around_nothing(self) -> None:
        # A reading that rendered to nothing must not become two escapes and no
        # text, which is a colour change with no glyph to justify it.
        for tone in TONES:
            for rung in RUNGS:
                with self.subTest(tone=tone, rung=rung):
                    self.assertEqual(render.paint("", tone, rung), "")


class ToneDerivationTest(unittest.TestCase):
    """How the host decides a tone, including what it refuses to be told.

    These cases build their own segments, because several of them are shapes no
    shipped provider currently emits -- a declared tone contradicting a state, a
    stale reassurance -- and the point of each is what the host does if one
    arrives. The captured payloads carry the cases the products do emit.
    """

    def test_a_product_s_own_declaration_is_not_second_guessed(self) -> None:
        # Libra's budget reading is `neutral` in every healthy posture, because
        # its `state` tracks whether anything needs doing and pressure does not.
        # Deriving from `state` alone would therefore throw the band away.
        for tone in TONES:
            part = segment(state=contract.SegmentState.NEUTRAL, semantic_state=tone)
            with self.subTest(tone=tone):
                self.assertIs(render.semantic_tone(part), tone)

    def test_a_posture_with_nothing_to_report_is_information(self) -> None:
        # `neutral` means two different things depending on what the reading is
        # for. A posture reading is the product saying what it is currently
        # doing -- observing, shadowing, idle -- which is information worth
        # distinguishing from the absence of a reading. Anywhere else, `neutral`
        # is the host's default styling.
        for role in contract.ClearRole:
            part = segment(state=contract.SegmentState.NEUTRAL, clear_role=role)
            expected = (
                contract.SemanticState.INFO
                if role is contract.ClearRole.POSTURE
                else contract.SemanticState.NEUTRAL
            )
            with self.subTest(role=role):
                self.assertIs(render.semantic_tone(part), expected)

    def test_each_contract_state_reaches_a_tone(self) -> None:
        expected = {
            contract.SegmentState.OK: contract.SemanticState.SAFE,
            contract.SegmentState.ATTENTION: contract.SemanticState.CAUTION,
            contract.SegmentState.WARN: contract.SemanticState.WARNING,
            contract.SegmentState.CRITICAL: contract.SemanticState.CRITICAL,
            contract.SegmentState.UNKNOWN: contract.SemanticState.UNAVAILABLE,
        }
        for state, tone in expected.items():
            with self.subTest(state=state):
                self.assertIs(render.semantic_tone(segment(state=state)), tone)

    def test_the_tone_never_reads_the_label(self) -> None:
        # The specific trap: `38% left` is 62% pressure, and a derivation that
        # reached for the visible number would read it as comfortable. Same
        # label, different band -> different tone; same band, labels that
        # disagree with it -> same tone.
        left = "38% budget left"
        caution = segment(label=left, state=contract.SegmentState.NEUTRAL,
                          semantic_state=contract.SemanticState.CAUTION)
        safe = segment(label=left, state=contract.SegmentState.NEUTRAL,
                       semantic_state=contract.SemanticState.SAFE)
        self.assertIsNot(render.semantic_tone(caution), render.semantic_tone(safe))
        for label in ("99% budget left", "1% budget left", "Budget exhausted", "All clear"):
            part = segment(label=label, state=contract.SegmentState.NEUTRAL,
                           semantic_state=contract.SemanticState.CRITICAL)
            with self.subTest(label=label):
                self.assertIs(render.semantic_tone(part), contract.SemanticState.CRITICAL)

    def test_a_hypothetical_critical_cannot_wear_an_enforced_one_s_tone(self) -> None:
        # Shadow mode's whole claim is that nothing was enforced. A would-block
        # rendered in the colour of a block is the one mis-tone that would make a
        # reader act on something that did not happen, so the host caps it even
        # if a product declares critical outright.
        part = segment(state=contract.SegmentState.CRITICAL, hypothetical=True)
        self.assertIs(render.semantic_tone(part), contract.SemanticState.WARNING)
        declared = segment(
            state=contract.SegmentState.CRITICAL,
            hypothetical=True,
            semantic_state=contract.SemanticState.CRITICAL,
        )
        self.assertIs(render.semantic_tone(declared), contract.SemanticState.WARNING)

    def test_a_reassurance_that_is_not_current_stops_reassuring(self) -> None:
        # Staleness is where colour is most dangerous: the reading is real, it
        # just stopped being now. Green for "was fine a while ago" is a claim
        # about the present that nothing supports.
        #
        # Both routes to "not current" are checked, because they come from
        # different places -- `live=False` is the provider having no live
        # readings at all, and `is_stale` is one reading having outlived the
        # horizon its own provider declared.
        for tone in TONES:
            demoted = (
                contract.SemanticState.UNAVAILABLE if tone.is_reassuring else tone
            )
            stale = segment(semantic_state=tone, age_seconds=600, fresh_for_seconds=60)
            with self.subTest(tone=tone):
                self.assertIs(render.semantic_tone(segment(semantic_state=tone), live=False),
                              demoted)
                self.assertIs(render.semantic_tone(stale), demoted)

    def test_an_alarm_that_is_not_current_keeps_its_tone(self) -> None:
        # The other half of the same rule, and the reason it is written as a
        # demotion of reassurance rather than a blanket dimming: an unrefreshed
        # alarm is still the worst thing known.
        for tone in (contract.SemanticState.CAUTION, contract.SemanticState.WARNING,
                     contract.SemanticState.CRITICAL):
            with self.subTest(tone=tone):
                self.assertIs(render.semantic_tone(segment(semantic_state=tone), live=False), tone)

    def test_a_tone_the_host_does_not_know_is_not_taken_as_reassurance(self) -> None:
        # A future product declaring a vocabulary this host predates must land on
        # "no declaration" and be derived from the state, never on the first
        # member of the enum.
        self.assertIsNone(contract._parse_semantic_state("sparkling"))
        derived = segment(state=contract.SegmentState.ATTENTION,
                          semantic_state=contract._parse_semantic_state("sparkling"))
        self.assertIs(render.semantic_tone(derived), contract.SemanticState.CAUTION)


class LibraPressureTest(unittest.TestCase):
    """The pressure axis, over the bands Libra actually puts on the wire."""

    BANDS = {
        "active_with_estimate": contract.SemanticState.SAFE,
        "budget_pressure_caution": contract.SemanticState.CAUTION,
        "budget_pressure_warning": contract.SemanticState.WARNING,
        "budget_exhausted": contract.SemanticState.CRITICAL,
    }

    def test_every_band_the_product_declares_reaches_its_tone(self) -> None:
        for case, tone in self.BANDS.items():
            with self.subTest(fixture=f"libra/{case}"):
                self.assertIs(tone_of(payload(f"libra/{case}"), "budget"), tone)

    def test_the_four_bands_do_not_collapse_into_each_other(self) -> None:
        # A mapping can be correct case by case and still be useless, if two
        # bands end up the same colour on the rung the reader has.
        for rung in COLOURED:
            emitted = [render.tone_sgr(tone, rung) for tone in self.BANDS.values()]
            with self.subTest(rung=rung):
                self.assertEqual(len(set(emitted)), len(emitted), emitted)

    def test_a_reading_below_half_left_is_not_green(self) -> None:
        # The ticket's example, now a captured payload: `38% budget left` is 61%
        # utilization by the product's own arithmetic, and the number a reader
        # sees is the one that would have made it green.
        item = payload("libra/budget_pressure_caution")
        budget = wire_segments(item)["budget"]
        self.assertEqual(budget["label"], "38% budget left")
        self.assertGreater(budget["budget_pressure_percent"], 50)
        self.assertIs(tone_of(item, "budget"), contract.SemanticState.CAUTION)

    def test_a_consumed_envelope_stays_critical_everywhere(self) -> None:
        item = payload("libra/budget_exhausted")
        for mode in MODES:
            for depth in DEPTHS:
                for rung in COLOURED:
                    line = render.render_provider(item.status, mode, depth, color=rung)
                    with self.subTest(mode=mode, depth=depth, rung=rung):
                        self.assertIn(
                            render.tone_sgr(contract.SemanticState.CRITICAL, rung), line
                        )

    def test_a_band_is_only_declared_for_an_observed_active_task_envelope(self) -> None:
        # Where AC 6 is actually enforced. The host cannot tell an active-task
        # envelope from a configured default or a host cap -- it never sees the
        # scope, because `budget_scope` is not a contract field. So the guarantee
        # is the product's: it declares a band only for the scope whose pressure
        # a tone is allowed to describe, and this pins that it keeps doing so.
        declaring = 0
        for item in of("libra"):
            for key, part in wire_segments(item).items():
                if "semantic_state" not in part:
                    continue
                declaring += 1
                with self.subTest(fixture=item.name, segment=key):
                    self.assertEqual(part.get("budget_scope"), "active_task")
                    self.assertTrue(part.get("budget_observed"))
        self.assertEqual(declaring, 7, "the declaring fixtures changed; re-read the bands")

    def test_an_idle_governor_declares_no_pressure_at_all(self) -> None:
        # Idle is the state AC 6 is about, and the product resolves it upstream
        # of colour: with no task under governance there is no active-task
        # envelope, so there is no budget reading on the line to tone. The
        # posture reading that remains is information, not reassurance.
        item = payload("libra/idle")
        self.assertEqual(sorted(wire_segments(item)), ["task"])
        self.assertNotIn("semantic_state", wire_segments(item)["task"])
        self.assertIs(tone_of(item, "task"), contract.SemanticState.INFO)


class FornaxVerdictTest(unittest.TestCase):
    """The verification vocabulary, which is a ladder and not a score."""

    VERDICTS = {
        "verdict_verified": contract.SemanticState.SAFE,
        "empty_store": contract.SemanticState.INFO,
        "verdict_unverified": contract.SemanticState.CAUTION,
        "verdict_review": contract.SemanticState.WARNING,
        "verdict_contradicted": contract.SemanticState.CRITICAL,
        "no_reading_daemon_unreachable": contract.SemanticState.UNAVAILABLE,
    }

    def test_the_six_outcomes_get_six_tones(self) -> None:
        for case, tone in self.VERDICTS.items():
            item = payload(f"fornax/{case}")
            key = "latest_finding" if "verdict" in case or case == "empty_store" else "availability"
            with self.subTest(fixture=item.name):
                self.assertIs(tone_of(item, key), tone)
        self.assertEqual(len(set(self.VERDICTS.values())), 6)

    def test_observing_is_not_verification(self) -> None:
        # The distinction the ticket is most explicit about, and the easiest to
        # lose: an empty store means nothing has contradicted anything yet, which
        # is not the same claim as having checked. Green would report the second.
        observing = tone_of(payload("fornax/empty_store"), "latest_finding")
        verified = tone_of(payload("fornax/verdict_verified"), "latest_finding")
        self.assertIsNot(observing, verified)
        for rung in COLOURED:
            with self.subTest(rung=rung):
                self.assertNotEqual(
                    render.tone_sgr(observing, rung), render.tone_sgr(verified, rung)
                )


class CircinusDecisionTest(unittest.TestCase):
    """Shadow mode's distinctions, which colour is the likeliest place to erase."""

    def test_a_would_block_is_neither_an_allow_nor_a_block(self) -> None:
        tones = {
            case: tone_of(payload(f"circinus/{case}"), "latest_decision")
            for case in ("shadow_would_allow_fresh", "shadow_would_block_fresh", "enforce_blocked")
        }
        self.assertEqual(len(set(tones.values())), 3, tones)
        self.assertIs(tones["shadow_would_block_fresh"], contract.SemanticState.WARNING)
        self.assertIs(tones["enforce_blocked"], contract.SemanticState.CRITICAL)

    def test_a_would_block_that_is_no_longer_fresh_is_still_not_an_allow(self) -> None:
        stale = tone_of(payload("circinus/stale_decision"), "latest_decision")
        allow = tone_of(payload("circinus/shadow_would_allow_fresh"), "latest_decision")
        self.assertIsNot(stale, allow)
        self.assertFalse(stale.is_reassuring)

    def test_shadow_mode_itself_is_reported_rather_than_endorsed(self) -> None:
        # Running in shadow is the product telling the reader what it is doing.
        # It is not a clean bill of health -- nothing has been enforced -- so the
        # posture reading is information rather than reassurance.
        for case in ("installed_shadow_no_decisions", "shadow_would_allow_fresh"):
            with self.subTest(fixture=f"circinus/{case}"):
                self.assertIs(tone_of(payload(f"circinus/{case}"), "mode"),
                              contract.SemanticState.INFO)


class UnreadableIsNotHealthyTest(unittest.TestCase):
    """The rule that spans all three products: absence is not safety."""

    def test_no_payload_without_live_readings_tones_anything_reassuringly(self) -> None:
        absent = [item for item in PAYLOADS if not render._has_live_readings(item.status)]
        self.assertEqual(len(absent), 9, "the unreadable fixtures changed; re-read them")
        for item in absent:
            for part in contract.order_segments(item.status):
                tone = render.semantic_tone(part, live=False)
                with self.subTest(fixture=item.name, segment=part.key):
                    self.assertFalse(tone.is_reassuring, tone)

    def test_a_provider_with_nothing_to_say_is_toned_as_unavailable(self) -> None:
        # The fallback reading the host writes when a payload carries no
        # segments at all. It is the one reading on the line that no product
        # authored, and it must not inherit the default styling of a healthy one.
        empty = status(segments=(), availability=contract.Availability.UNAVAILABLE,
                       fallback_text="Daemon not running")
        for rung in COLOURED:
            _, _, rendered = render.provider_parts(empty, BALANCED, CLEAR, color=rung)
            with self.subTest(rung=rung):
                self.assertEqual(len(rendered), 1)
                self.assertTrue(
                    rendered[0].startswith(
                        render.tone_sgr(contract.SemanticState.UNAVAILABLE, rung)
                    ),
                    rendered[0],
                )


class DepthAgreementTest(unittest.TestCase):
    """One snapshot, one set of meanings, whichever depth is showing it."""

    def test_the_same_reading_carries_the_same_tone_at_either_depth(self) -> None:
        for item in PAYLOADS:
            live = render._has_live_readings(item.status)
            for part in contract.order_segments(item.status):
                for mode in MODES:
                    clear = render.render_segment(part, mode, CLEAR, color=ANSI256, live=live)
                    detail = render.render_segment(part, mode, DETAIL, color=ANSI256, live=live)
                    with self.subTest(fixture=item.name, segment=part.key, mode=mode):
                        self.assertEqual(opening(clear), opening(detail))

    def test_the_clear_line_says_nothing_in_a_tone_the_detail_line_lacks(self) -> None:
        # Clear shows fewer readings than Detail, never different ones, so every
        # tone on the short line has to be accounted for on the long one. A
        # reader switching depth is looking at the same situation.
        for line in LINES:
            statuses = tuple(item.status for item in line)
            for mode in MODES:
                clear = render.compose(UPSTREAM, statuses, mode=mode, depth=CLEAR, color=ANSI256)
                detail = render.compose(UPSTREAM, statuses, mode=mode, depth=DETAIL, color=ANSI256)
                with self.subTest(line=[item.name for item in line], mode=mode):
                    self.assertLessEqual(set(sequences(clear)), set(sequences(detail)))


class SupplementalTest(unittest.TestCase):
    """Colour is emphasis. Removing it must cost nothing but the emphasis."""

    def test_the_renderer_says_nothing_in_colour_unless_asked(self) -> None:
        # The default, which is what makes every colour-free surface -- a pipe, a
        # plain mode, `NO_COLOR`, the JSON -- correct by construction rather than
        # by each caller remembering to opt out.
        for name in ("compose", "render_provider", "render_segment", "provider_parts"):
            with self.subTest(callable=name):
                default = inspect.signature(getattr(render, name)).parameters["color"].default
                self.assertIs(default, NONE)

    def test_the_coloured_line_is_the_plain_line_with_emphasis(self) -> None:
        # The whole of "text remains authoritative", in one assertion. Not a
        # subset check and not a width check: the same bytes, so a reader
        # comparing a terminal with a piped capture sees one line twice.
        for line in LINES:
            statuses = tuple(item.status for item in line)
            for mode in MODES:
                for depth in DEPTHS:
                    for budget in BUDGETS:
                        plain = render.compose(
                            UPSTREAM, statuses, mode=mode, depth=depth, width_budget=budget
                        )
                        for rung in COLOURED:
                            toned = render.compose(
                                UPSTREAM, statuses, mode=mode, depth=depth,
                                width_budget=budget, color=rung,
                            )
                            with self.subTest(
                                line=[item.name for item in line], mode=mode,
                                depth=depth, budget=budget, rung=rung,
                            ):
                                self.assertEqual(render.strip_sgr(toned), plain)

    def test_a_narrow_line_drops_the_same_readings_in_colour(self) -> None:
        # Explicit about the thing the byte-equality above already covers but
        # which is worth failing by name: colour costs no columns, so it cannot
        # push a reading off the line or change the hidden count. The budgets
        # here are tight enough to reach the single-reading floor.
        for line in LINES[:3]:
            statuses = tuple(item.status for item in line)
            for budget in (60, 40, 28):
                plain = render.compose(UPSTREAM, statuses, width_budget=budget)
                toned = render.compose(UPSTREAM, statuses, width_budget=budget, color=ANSI256)
                with self.subTest(line=[item.name for item in line], budget=budget):
                    self.assertEqual(render.display_width(toned), render.display_width(plain))

    def test_a_truncated_label_is_never_cut_mid_sequence(self) -> None:
        # What the byte-equality check cannot see: a line can strip back
        # correctly and still have been built by truncating *inside* an escape,
        # which leaves an opening colour with no reset to bleed into the prompt.
        for budget in range(20, 90):
            for line in LINES[:3]:
                statuses = tuple(item.status for item in line)
                toned = render.compose(UPSTREAM, statuses, width_budget=budget, color=ANSI256)
                with self.subTest(budget=budget, line=[item.name for item in line]):
                    self.assertEqual(toned.count(ESC) % 2, 0, toned)
                    self.assertEqual(
                        len(sequences(toned)), toned.count(ESC),
                        "an escape that is not a complete SGR sequence survived",
                    )


class NoEscapeInMachineOutputTest(unittest.TestCase):
    """The JSON surfaces, where a colour would be a lie about the content.

    Every assertion here checks the strings *before* serialisation as well as
    after. `json.dumps` turns an escape into the six characters `\\u001b`, so
    searching the serialised document for `ESC` can never fail -- a check written
    that way reports a guarantee it is not making, which is the one defect this
    directory's mutation suite exists to prevent.
    """

    def assert_plain(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn(ESC, text)
        encoded = json.dumps(list(texts))
        self.assertNotIn("u001b", encoded)
        self.assertNotIn(ESC, encoded)

    def test_no_captured_payload_carries_an_escape(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assert_plain(item.path.read_text(encoding="utf-8"))

    def test_a_label_carrying_an_escape_is_refused_at_the_boundary(self) -> None:
        # Not filtered, refused: a provider emitting its own ANSI has decided
        # something that is not its to decide, and silently stripping it would
        # leave that provider shipping a contract violation that works.
        broken = rewire(
            payload("fornax/verdict_verified"),
            lambda wire: wire["segments"][0].__setitem__("label", "Verified\x1b[31m"),
        )
        with self.assertRaises(ValueError):
            contract.provider_status_from_wire(broken)

    def test_a_reading_quoted_into_json_is_rendered_without_colour(self) -> None:
        # `explain` quotes each reading's rendered text so a reader can match the
        # line against the decode. It calls the renderer the way this does --
        # positionally, with no `color` -- so the quoted fragment is plain text.
        for item in PAYLOADS:
            for mode in MODES:
                for depth in DEPTHS:
                    rendered = [render.render_provider(item.status, mode, depth)]
                    rendered += [
                        render.render_segment(part, mode, depth)
                        for part in contract.order_segments(item.status)
                    ]
                    with self.subTest(fixture=item.name, mode=mode, depth=depth):
                        self.assert_plain(*rendered)

    def test_the_legend_is_plain_text_in_every_mode(self) -> None:
        for mode in MODES:
            entries = [
                dataclasses.asdict(entry)
                for section in render.legend(mode)
                for entry in section.entries
            ]
            with self.subTest(mode=mode):
                self.assert_plain(*(str(value) for entry in entries for value in entry.values()))


class CapabilityTest(unittest.TestCase):
    """What the host decides the destination can carry, and what silences it."""

    HOSTED = {"CLAUDECODE": "1", "TERM": "xterm-256color", "COLORTERM": "truecolor"}

    def capability(self, preference, env=None, stream=None):
        return compositor.color_capability(preference, dict(env or {}), stream)

    def test_no_color_silences_every_preference(self) -> None:
        # Checked before the preference, not after: `NO_COLOR` is the reader's
        # accessibility setting and a registry cannot overrule it.
        for preference in compositor.ColorPreference:
            env = dict(self.HOSTED, NO_COLOR="1")
            with self.subTest(preference=preference):
                self.assertIs(self.capability(preference, env), NONE)

    def test_a_terminal_that_says_it_is_dumb_is_believed(self) -> None:
        for preference in compositor.ColorPreference:
            env = dict(self.HOSTED, TERM="dumb")
            with self.subTest(preference=preference):
                self.assertIs(self.capability(preference, env), NONE)

    def test_a_pipe_is_not_coloured_on_its_own(self) -> None:
        # No terminal and no host marker: whatever is reading this is reading
        # text, and an escape would be corruption rather than emphasis.
        self.assertIs(
            self.capability(compositor.ColorPreference.AUTO, {"TERM": "xterm-256color"},
                            io.StringIO()),
            NONE,
        )

    def test_the_host_counts_even_though_it_captures_our_output(self) -> None:
        # Why `isatty` alone is not the test. Claude Code captures the statusline
        # command's stdout and then renders the result itself, so the stream is
        # never a terminal while the destination plainly is one. Treating the
        # pipe as authoritative would mean this feature never appears in the
        # environment it was built for.
        self.assertIs(
            self.capability(compositor.ColorPreference.AUTO, self.HOSTED, io.StringIO()),
            ANSI256,
        )

    def test_an_explicit_rung_does_not_need_a_terminal_to_believe_it(self) -> None:
        for preference, expected in (
            (compositor.ColorPreference.ANSI16, ANSI16),
            (compositor.ColorPreference.ANSI256, ANSI256),
            (compositor.ColorPreference.NEVER, NONE),
        ):
            with self.subTest(preference=preference):
                self.assertIs(self.capability(preference, {}, io.StringIO()), expected)

    def test_the_rung_follows_what_the_environment_claims(self) -> None:
        cases = (
            ({"CLAUDECODE": "1", "COLORTERM": "truecolor"}, ANSI256),
            ({"CLAUDECODE": "1", "TERM": "xterm-256color"}, ANSI256),
            ({"CLAUDECODE": "1", "TERM": "xterm-direct"}, ANSI256),
            ({"CLAUDECODE": "1", "TERM": "xterm"}, ANSI16),
            ({"CLAUDECODE": "1"}, ANSI16),
        )
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertIs(self.capability(compositor.ColorPreference.AUTO, env), expected)

    def test_a_stream_that_cannot_answer_is_not_a_terminal(self) -> None:
        class Awkward:
            def isatty(self):
                raise OSError("closed")

        self.assertIs(self.capability(compositor.ColorPreference.AUTO, {}, Awkward()), NONE)

    def test_a_preference_the_host_does_not_recognise_turns_colour_off(self) -> None:
        # The opposite of how `mode` and `depth` fall back, and deliberately so:
        # a misunderstood density setting still renders the line, while a
        # misunderstood colour setting would emit escapes to a destination
        # nobody established could take them.
        for value in ("rainbow", "", "16-colour", "maybe"):
            with self.subTest(value=value):
                self.assertIs(compositor.ColorPreference.parse(value),
                              compositor.ColorPreference.NEVER)
        self.assertIs(compositor.ColorPreference.parse(None), compositor.ColorPreference.AUTO)
        for value in ("no", "off", "false", "none"):
            with self.subTest(value=value):
                self.assertIs(compositor.ColorPreference.parse(value),
                              compositor.ColorPreference.NEVER)

    def test_a_registry_written_before_colour_existed_reads_as_auto(self) -> None:
        registry = compositor.parse_registry(
            {"registry_version": 1, "providers": [],
             "presentation": {"mode": "balanced", "depth": "clear"}}
        )
        self.assertIs(registry.color, compositor.ColorPreference.AUTO)

    def test_a_registry_can_turn_colour_off_for_everyone_on_this_machine(self) -> None:
        registry = compositor.parse_registry(
            {"registry_version": 1, "providers": [],
             "presentation": {"mode": "balanced", "color": "never"}}
        )
        self.assertIs(registry.color, compositor.ColorPreference.NEVER)
        self.assertIs(self.capability(registry.color, self.HOSTED), NONE)


class CostTest(unittest.TestCase):
    """Colour asks the products for nothing."""

    def test_the_providers_are_run_once_whether_or_not_the_line_is_coloured(self) -> None:
        # The latency class has to be unchanged, and the structural reason it is
        # unchanged is that collection happens before the capability is even
        # resolved. Asserted by counting, because "I did not add a call" is not
        # something a reader of the diff can check.
        item = payload("libra/budget_pressure_caution")
        registry = compositor.parse_registry({"registry_version": 1, "providers": []})
        calls = []

        def collect(reg, payload_bytes):
            calls.append(reg)
            return UPSTREAM, (item.status,)

        for environ in ({"NO_COLOR": "1"}, {"CLAUDECODE": "1", "COLORTERM": "truecolor"}):
            calls.clear()
            writer = io.StringIO()
            with unittest.mock.patch.object(compositor, "collect", collect), \
                 unittest.mock.patch.object(compositor, "load_registry", lambda: registry), \
                 unittest.mock.patch.dict("os.environ", environ, clear=True):
                compositor._render(io.BytesIO(b""), writer)
            with self.subTest(environ=sorted(environ)):
                self.assertEqual(len(calls), 1)
                self.assertEqual(ESC in writer.getvalue(), "NO_COLOR" not in environ)


if __name__ == "__main__":
    unittest.main()
