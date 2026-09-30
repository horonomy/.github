"""The information-depth release gate: clear and detail, product by product (HORO-1627).

`test_statusline_release_gate` is the gate for *coexistence* — whether installing
these products destroys anything the user owns. This is the gate for *depth*:
whether the two information depths say the same true thing about each product,
and whether moving between them is safe.

Those are different failure modes and they need different evidence. A depth can
be perfectly well engineered and still ship a defect, because the defect is
semantic: a summary that leaves out the one reading the operator needed, a
cached posture rendered beside an unreachable daemon, a would-have-blocked read
as a block, a bare `62%`. None of those break a test that asserts the line
renders.

So this module holds three things the other suites structurally cannot:

1. **A per-product state matrix asserted at both depths.** The readability suite
   renders at one depth. A state that reads correctly at `detail` and loses its
   meaning at `clear` — or gains a meaning it has no evidence for — is exactly
   the defect this ticket exists to catch, and it is invisible to any suite that
   only ever looks at one depth.

2. **The cross-depth invariant.** For one provider snapshot, the primary state
   `clear` shows must be a state `detail` also shows, unchanged. Only the
   supporting material may differ. This is what makes the two depths two views of
   one state engine rather than two implementations that agree today.

3. **The switching matrix.** Moving between depths is a configuration change, and
   the thing a configuration change must not do is take anything else with it:
   the host's settings file, the reader's own statusline script, the rest of the
   registry, a running daemon, or the set of enabled products.

The matrices are built from contract fixtures rather than from the three
providers installed on a developer machine. That is not a convenience: the
Circinus daemon on this workstation is wedged — its socket exists and does not
answer — so its enforcing and would-block states are unreachable live, and a gate
that can only assert whatever mood one local daemon happens to be in is not a
gate. Every state below is a state the product's own contract says it can report.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import pathlib
import shlex
import subprocess
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_lifecycle as lifecycle
import statusline_render as render
from test_statusline_lifecycle import PresentationCase, RenderingCase
from test_statusline_render import detail_atoms

CLEAR = render.InformationDepth.CLEAR
DETAIL = render.InformationDepth.DETAIL
DEPTHS = (CLEAR, DETAIL)
MODES = tuple(render.PresentationMode)

# The reader's own line. Present in the switching cases so that "nothing else
# changed" includes the part of the line this integration does not own.
UPSTREAM = "~/proj  main*  claude-opus-5  $0.42"


def segment(**overrides) -> contract.Segment:
    fields = {"key": "example", "state": contract.SegmentState.OK, "label": "Example"}
    fields.update(overrides)
    return contract.Segment(**fields)


def status(**overrides) -> contract.ProviderStatus:
    fields = {
        "provider": "example",
        "provider_version": "1.0.0",
        "scope": contract.Scope.SESSION,
        "availability": contract.Availability.AVAILABLE,
        "segments": (segment(),),
    }
    fields.update(overrides)
    return contract.ProviderStatus(**fields)


@dataclasses.dataclass(frozen=True)
class Case:
    """One product state, and what each depth owes a reader looking at it.

    `clear_says` / `detail_says` are fragments that must appear; `clear_omits` are
    fragments that must not. Writing the expectation beside the fixture rather
    than inside a test body is what keeps the matrix readable as a matrix — the
    interesting question about any row is what the two depths are allowed to
    differ on, and that is only visible when both columns are in one place.

    `primary` is the segment key the product's summary must lead with. It is the
    anchor for the cross-depth invariant: whatever else the two depths do, they
    must agree about this.
    """

    name: str
    snapshot: contract.ProviderStatus
    primary: str
    clear_says: tuple[str, ...] = ()
    clear_omits: tuple[str, ...] = ()
    detail_says: tuple[str, ...] = ()


# --------------------------------------------------------------------- Fornax
#
# The verification product. Its whole value is the verdict, so its summary is the
# verdict and nothing else — and its characteristic defect is upgrading the
# verdict on the strength of activity, which is why two rows below carry a large
# observation tally alongside a verdict that is *not* verified.


def fornax(*segments, availability=contract.Availability.AVAILABLE) -> contract.ProviderStatus:
    return status(
        provider="fornax",
        provider_version="0.4.1",
        scope=contract.Scope.PROJECT,
        availability=availability,
        order_hint=100,
        segments=segments,
    )


FORNAX = (
    Case(
        name="verified",
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.OK,
                label="Verified",
                count=3,
                total=3,
                count_label="claims",
                age_seconds=125,
                fresh_for_seconds=3600,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Verified",),
        # The tally is what the verdict was computed from, not the verdict.
        clear_omits=("3 of 3", "2m ago"),
        detail_says=("Verified", "3 of 3 claims", "2m ago"),
    ),
    Case(
        name="observing",
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.NEUTRAL,
                label="Observing",
                count=41,
                count_label="claims",
                age_seconds=30,
                fresh_for_seconds=3600,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Observing",),
        clear_omits=("41 claims",),
        detail_says=("Observing", "41 claims"),
    ),
    Case(
        name="unverified_with_reason",
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.ATTENTION,
                label="Unverified",
                reason_code="evidence_missing",
                reason_label="No evidence recorded for 2 claims",
                age_seconds=600,
                fresh_for_seconds=3600,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Unverified",),
        detail_says=("Unverified", "No evidence recorded for 2 claims"),
    ),
    Case(
        name="unverified_no_reason",
        # The live shape, with the tally the live provider also reports. A
        # verification product that has watched three thousand events and still
        # cannot say the claim holds is *unverified*, and the specific defect is
        # reading the activity as the verdict.
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.ATTENTION,
                label="Unverified",
                reason_code="reason_not_recorded",
                count=3747,
                count_label="observations",
                age_seconds=99883,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Unverified",),
        # "Verified" rather than "verified": the reading is `Unverified`, and the
        # fabrication this row guards against is the host supplying a reason the
        # provider did not record — "insufficient evidence" being the plausible one.
        clear_omits=("3747", "Verified", "insufficient"),
        detail_says=("Unverified", "3747 observations", "not recorded"),
    ),
    Case(
        name="review",
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.ATTENTION,
                label="Needs review",
                reason_code="awaiting_review",
                count=1,
                total=4,
                count_label="claims",
                age_seconds=90,
                fresh_for_seconds=3600,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Needs review",),
        clear_omits=("1 of 4",),
        detail_says=("Needs review", "1 of 4 claims"),
    ),
    Case(
        name="contradicted",
        snapshot=fornax(
            segment(
                key="latest_verdict",
                state=contract.SegmentState.CRITICAL,
                label="Contradicted",
                reason_code="claims_disagree",
                reason_label="Two claims disagree about the same fact",
                age_seconds=45,
                fresh_for_seconds=3600,
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="latest_verdict",
        clear_says=("Contradicted",),
        detail_says=("Contradicted", "Two claims disagree about the same fact"),
    ),
    Case(
        name="unavailable",
        snapshot=fornax(
            segment(
                key="availability",
                # No reason code: the label is the reason, and a fixture that
                # renders "Not installed (not installed)" would be asserting the
                # renderer's tolerance for redundancy rather than this product's
                # unavailable state.
                state=contract.SegmentState.UNKNOWN,
                label="Not installed",
            ),
            availability=contract.Availability.UNAVAILABLE,
        ),
        primary="availability",
        clear_says=("Not installed",),
        clear_omits=("Verified",),
        detail_says=("Not installed",),
    ),
)


# ------------------------------------------------------------------- Circinus
#
# The enforcement product, and the one whose readings are dangerous rather than
# merely unhelpful when summarised carelessly. Two distinct confusions have to be
# impossible: a hypothetical outcome read as an executed one, and a cached
# posture read as a running daemon.


def circinus(*segments, availability=contract.Availability.AVAILABLE, **overrides):
    fields = {
        "provider": "circinus",
        "provider_version": "1.2.0",
        "scope": contract.Scope.HOST,
        "availability": availability,
        "order_hint": 200,
        "segments": segments,
    }
    fields.update(overrides)
    return status(**fields)


def _enforcing(**overrides) -> contract.Segment:
    fields = {
        "key": "mode",
        "state": contract.SegmentState.OK,
        "label": "Enforcing",
        "age_seconds": 20,
        "fresh_for_seconds": 300,
        "clear_role": contract.ClearRole.POSTURE,
    }
    fields.update(overrides)
    return segment(**fields)


def _shadow(**overrides) -> contract.Segment:
    fields = {
        "key": "mode",
        "state": contract.SegmentState.WARN,
        "label": "Shadow mode",
        "hypothetical": True,
        "age_seconds": 20,
        "fresh_for_seconds": 300,
        "clear_role": contract.ClearRole.POSTURE,
    }
    fields.update(overrides)
    return segment(**fields)


CIRCINUS = (
    Case(
        name="healthy_shadow",
        snapshot=circinus(_shadow(count=2, total=14, count_label="tool calls")),
        primary="mode",
        clear_says=("Shadow mode", render.HYPOTHETICAL_TEXT),
        # The tally is the purest form of the counter defect: `2 of 14` tells an
        # operator nothing they can act on and costs the product its whole line.
        clear_omits=("2 of 14",),
        detail_says=("Shadow mode", render.HYPOTHETICAL_TEXT, "2 of 14 tool calls"),
    ),
    Case(
        name="healthy_enforce",
        snapshot=circinus(_enforcing(count=1520, total=3747, count_label="decisions")),
        primary="mode",
        clear_says=("Enforcing",),
        clear_omits=("1520", "3747", render.HYPOTHETICAL_TEXT),
        detail_says=("Enforcing", "1520 of 3747 decisions"),
    ),
    Case(
        name="unreachable_with_cached_posture",
        # The live wedged daemon, plus the cached posture a status store still
        # holds. Reporting the cached shadow here would tell the reader that
        # something evaluated their tool call. Nothing did.
        snapshot=circinus(
            segment(
                key="availability",
                state=contract.SegmentState.UNKNOWN,
                label="Not responding",
                reason_code="daemon_unreachable",
                reason_label="Socket exists but did not answer",
                order_hint=0,
            ),
            _shadow(order_hint=10, age_seconds=8400),
            availability=contract.Availability.UNKNOWN,
        ),
        primary="availability",
        clear_says=("Not responding",),
        clear_omits=("Shadow mode", render.HYPOTHETICAL_TEXT, "Enforcing"),
        detail_says=("Not responding", "Shadow mode"),
    ),
    Case(
        name="latest_allow",
        snapshot=circinus(
            _enforcing(),
            segment(
                key="latest_decision",
                state=contract.SegmentState.OK,
                label="Allowed",
                count=4,
                count_label="tool calls",
                age_seconds=8,
                fresh_for_seconds=300,
                order_hint=10,
                clear_role=contract.ClearRole.VITAL,
            ),
        ),
        primary="mode",
        clear_says=("Enforcing", "Allowed"),
        clear_omits=(render.HYPOTHETICAL_TEXT, "4 tool calls"),
        detail_says=("Enforcing", "Allowed", "4 tool calls"),
    ),
    Case(
        name="latest_would_block_in_shadow",
        snapshot=circinus(
            _shadow(state=contract.SegmentState.NEUTRAL, hypothetical=False),
            segment(
                key="latest_decision",
                state=contract.SegmentState.WARN,
                label="Would have blocked",
                hypothetical=True,
                reason_code="policy_denied_write",
                age_seconds=8,
                fresh_for_seconds=300,
                order_hint=10,
                clear_role=contract.ClearRole.VITAL,
            ),
        ),
        primary="mode",
        # If the would-have-blocked reaches the line at all, it reaches it welded
        # to the marker saying nothing was stopped.
        clear_says=("Shadow mode", "Would have blocked", render.HYPOTHETICAL_TEXT),
        detail_says=("Shadow mode", "Would have blocked", render.HYPOTHETICAL_TEXT),
    ),
    Case(
        name="executed_block_while_enforcing",
        snapshot=circinus(
            _enforcing(),
            segment(
                key="latest_decision",
                state=contract.SegmentState.CRITICAL,
                label="Blocked a tool call",
                reason_code="policy_denied_write",
                reason_label="Write outside the project root",
                age_seconds=3,
                fresh_for_seconds=300,
                order_hint=10,
            ),
        ),
        primary="latest_decision",
        clear_says=("Blocked a tool call",),
        # An executed block is never marked hypothetical, and it takes the line
        # from the routine posture rather than sharing it.
        clear_omits=(render.HYPOTHETICAL_TEXT, "Enforcing"),
        detail_says=("Enforcing", "Blocked a tool call", "Write outside the project root"),
    ),
    Case(
        name="stale_secondary",
        snapshot=circinus(
            _enforcing(),
            segment(
                key="latest_decision",
                state=contract.SegmentState.NEUTRAL,
                label="Allowed",
                age_seconds=7200,
                fresh_for_seconds=300,
                order_hint=10,
                clear_role=contract.ClearRole.VITAL,
            ),
        ),
        primary="mode",
        clear_says=("Enforcing",),
        # Two hours old against a five-minute horizon. Detail may date it; the
        # summary has no room to date anything, so it does not carry it.
        clear_omits=("Allowed",),
        detail_says=("Enforcing", "Allowed", "2h ago"),
    ),
    Case(
        name="malformed_answer",
        # The provider answered with something the contract refused, so the host
        # speaks for it. `fallback_text` is the only channel left, and the state
        # it is rendered with must not be a health claim.
        snapshot=circinus(availability=contract.Availability.ERROR, fallback_text="Unreadable answer"),
        primary="",
        clear_says=("Unreadable answer",),
        clear_omits=("Enforcing", "Shadow mode"),
        detail_says=("Unreadable answer",),
    ),
)


# -------------------------------------------------------------- Libra Governor
#
# The scheduling and budget product. Its characteristic defects are both about
# nouns: a confidence read as a severity, and a percentage with no axis.


def libra(*segments, availability=contract.Availability.AVAILABLE) -> contract.ProviderStatus:
    return status(
        provider="libra-governor",
        provider_version="0.9.0",
        scope=contract.Scope.SESSION,
        availability=availability,
        order_hint=300,
        segments=segments,
    )


def _estimate(confidence: contract.Confidence, **overrides) -> contract.Segment:
    fields = {
        "key": "remaining",
        "state": contract.SegmentState.NEUTRAL,
        "label": "Remaining work",
        "duration_seconds": 720,
        "duration_label": "P90",
        "confidence": confidence,
        "confidence_of": contract.ConfidenceSubject.PREFLIGHT_ESTIMATE,
        "age_seconds": 15,
        "fresh_for_seconds": 600,
        "order_hint": 20,
        "clear_role": contract.ClearRole.POSTURE,
    }
    fields.update(overrides)
    return segment(**fields)


def _confidence_case(name: str, confidence: contract.Confidence) -> Case:
    """One of the three preflight confidences, which `clear` must not surface.

    Written as a factory because the three rows differ in exactly one field and
    make exactly the same claim: the confidence is a question for someone
    deciding whether to trust the estimate, which is a `detail` question, and a
    bare `high` beside a state marker reads as severity or priority.
    """
    return Case(
        name=name,
        snapshot=libra(
            segment(
                key="task",
                state=contract.SegmentState.NEUTRAL,
                label="Task in flight",
                order_hint=10,
            ),
            _estimate(confidence),
        ),
        primary="remaining",
        clear_says=("Remaining work", "P90 12m"),
        clear_omits=("confidence", confidence.value),
        detail_says=("Remaining work", "P90 12m", "preflight confidence", confidence.value),
    )


LIBRA = (
    Case(
        name="idle",
        snapshot=libra(
            segment(
                key="task",
                state=contract.SegmentState.NEUTRAL,
                label="No task in flight",
                clear_role=contract.ClearRole.POSTURE,
            )
        ),
        primary="task",
        clear_says=("No task in flight",),
        detail_says=("No task in flight",),
    ),
    _confidence_case("active_low_confidence", contract.Confidence.LOW),
    _confidence_case("active_medium_confidence", contract.Confidence.MEDIUM),
    _confidence_case("active_high_confidence", contract.Confidence.HIGH),
    Case(
        name="stable_plan_with_budget",
        snapshot=libra(
            segment(
                key="plan",
                state=contract.SegmentState.OK,
                label="Plan stable",
                age_seconds=40,
                fresh_for_seconds=600,
                order_hint=10,
                clear_role=contract.ClearRole.POSTURE,
            ),
            segment(
                key="budget",
                state=contract.SegmentState.NEUTRAL,
                label="38% replan budget left",
                age_seconds=40,
                fresh_for_seconds=600,
                order_hint=20,
                clear_role=contract.ClearRole.VITAL,
            ),
        ),
        primary="plan",
        # The axis travels with the number, so the summary can carry the budget
        # without the reader having to guess which direction it runs.
        clear_says=("Plan stable", "38% replan budget left"),
        detail_says=("Plan stable", "38% replan budget left"),
    ),
    Case(
        name="replanned",
        snapshot=libra(
            segment(
                key="plan",
                state=contract.SegmentState.WARN,
                label="Replan budget spent",
                reason_code="next_replan_needs_human_approval",
                age_seconds=40,
                fresh_for_seconds=600,
                order_hint=10,
                clear_role=contract.ClearRole.POSTURE,
            ),
            # A vital rather than a second posture: the plan's state is the
            # posture, and the estimate qualifies how to read it.
            _estimate(contract.Confidence.LOW, clear_role=contract.ClearRole.VITAL),
        ),
        primary="plan",
        clear_says=("Replan budget spent",),
        # A budget that is spent is a posture, not a blockage. The line must not
        # promote it into either of the two words that mean work has stopped.
        clear_omits=("Awaiting", "Blocked"),
        detail_says=("Replan budget spent", "needs human approval"),
    ),
    Case(
        name="awaiting_approval",
        snapshot=libra(
            segment(
                key="approval",
                state=contract.SegmentState.CRITICAL,
                label="Awaiting your approval",
                reason_code="escalated_to_human",
                age_seconds=95,
                fresh_for_seconds=600,
                order_hint=0,
            ),
            _estimate(contract.Confidence.HIGH),
        ),
        primary="approval",
        clear_says=("Awaiting your approval",),
        # Someone is being waited on. An estimate about work that is not moving
        # is not an improvement on saying so.
        clear_omits=("Remaining work", "P90"),
        detail_says=("Awaiting your approval", "escalated to human", "Remaining work"),
    ),
    Case(
        name="unavailable",
        snapshot=libra(
            segment(
                key="availability",
                state=contract.SegmentState.UNKNOWN,
                label="Not running",
                reason_code="daemon_not_started",
            ),
            availability=contract.Availability.UNAVAILABLE,
        ),
        primary="availability",
        clear_says=("Not running",),
        clear_omits=("Plan stable", "Remaining work"),
        detail_says=("Not running",),
    ),
)


MATRIX = FORNAX + CIRCINUS + LIBRA


def primary_of(snapshot: contract.ProviderStatus) -> contract.Segment | None:
    """The one reading a product's summary is built around.

    Read out of the host's own role ladder rather than recomputed, so this cannot
    drift into a second opinion about what the primary state is — which is the
    very thing the cross-depth invariant exists to rule out.

    The exception outranks the posture, because the top rung of the ladder exists
    precisely so that it is not competing with routine readings. Where a provider
    is not answering at all, neither role survives the projection, and the single
    reading it is allowed to show *is* the primary.
    """
    segments = contract.order_segments(snapshot)
    readings = render.clear_readings(snapshot)
    if not segments or not readings:
        return None
    roles = dict(zip((part.key for part in segments), render.clear_roles(snapshot)))
    for role in (contract.ClearRole.EXCEPTION, contract.ClearRole.POSTURE):
        for part in readings:
            if roles[part.key] is role:
                return part
    return readings[0]


class MatrixIntegrityTest(unittest.TestCase):
    """The matrix is the evidence, so its own shape is worth asserting.

    Every failure mode here is one that would quietly reduce how much the rest of
    this module proves: a row that is really the same row twice, a `primary` key
    that names a segment the snapshot does not have, an expectation that is
    trivially true, or a state the products can report that no row covers.
    """

    def test_every_case_has_a_distinct_name(self) -> None:
        names = [f"{case.snapshot.provider}/{case.name}" for case in MATRIX]
        self.assertEqual(len(names), len(set(names)), sorted(names))

    def test_every_primary_names_a_segment_the_snapshot_reports(self) -> None:
        for case in MATRIX:
            with self.subTest(case=case.name):
                keys = [part.key for part in case.snapshot.segments]
                if not keys:
                    # The malformed row has no segments at all, which is the
                    # state it exists to cover; it declares no primary.
                    self.assertEqual(case.primary, "")
                    continue
                self.assertIn(case.primary, keys)

    def test_no_case_expects_the_same_fragment_both_ways(self) -> None:
        # A fragment in `clear_says` and `clear_omits` at once would make the row
        # unsatisfiable, and the failure would read as an implementation bug.
        for case in MATRIX:
            with self.subTest(case=case.name):
                self.assertEqual(set(case.clear_says) & set(case.clear_omits), set())

    def test_every_snapshot_carries_the_scope_its_product_is_scoped_to(self) -> None:
        # Enforcement is host-wide, verification is per project, scheduling is per
        # session. A row that got this wrong would assert the wrong marker and
        # still pass, because the renderer would faithfully print what it was given.
        expected = {
            "fornax": contract.Scope.PROJECT,
            "circinus": contract.Scope.HOST,
            "libra-governor": contract.Scope.SESSION,
        }
        for case in MATRIX:
            with self.subTest(case=case.name):
                self.assertIs(case.snapshot.scope, expected[case.snapshot.provider])

    def test_every_state_a_product_can_report_appears_somewhere(self) -> None:
        # Not "every state is covered for every product" — Fornax has no
        # hypothetical and Circinus has no confidence — but the six states must
        # all be exercised, or a whole rung of the severity ladder goes untested.
        seen = {
            part.state
            for case in MATRIX
            for part in case.snapshot.segments
        }
        self.assertEqual(seen, set(contract.SegmentState))

    def test_both_availability_answers_that_mean_no_live_reading_are_covered(self) -> None:
        seen = {case.snapshot.availability for case in MATRIX}
        self.assertEqual(
            {value for value in seen if not value.has_live_readings},
            {
                contract.Availability.UNAVAILABLE,
                contract.Availability.UNKNOWN,
                contract.Availability.ERROR,
            },
        )

    def test_the_matrix_covers_a_stale_reading_and_a_fresh_one(self) -> None:
        staleness = {
            part.is_stale
            for case in MATRIX
            for part in case.snapshot.segments
            if part.fresh_for_seconds is not None
        }
        self.assertEqual(staleness, {True, False})

    def test_the_matrix_covers_a_hypothetical_and_an_executed_outcome(self) -> None:
        marks = {
            part.hypothetical
            for case in MATRIX
            for part in case.snapshot.segments
            if part.key in ("mode", "latest_decision")
        }
        self.assertEqual(marks, {True, False})


class DepthMatrixChecks:
    """What every row of every product's matrix owes a reader at each depth.

    A mixin rather than a base test case: a `unittest.TestCase` with an empty
    matrix would be collected and would pass, reporting a coverage it does not
    have. Subclasses pair this with `TestCase` and supply `CASES`;
    `MatrixCoverageTest` below asserts the three of them account for every row.
    """

    CASES: tuple[Case, ...] = ()

    def read(self, case: Case, depth, mode=render.PresentationMode.BALANCED) -> str:
        """One product rendered as the reader sees it, at one depth.

        `render_provider` rather than `compose`: the claims in this class are about
        one product's reading, and going through the whole line would let a
        width-pressure decision about a *different* product change what is
        asserted here.
        """
        return render.render_provider(case.snapshot, mode, depth)

    def shown(self, case: Case, depth) -> tuple:
        """The segments this depth puts on the line for this provider."""
        if depth.shows_supporting_detail:
            return contract.order_segments(case.snapshot)
        return render.clear_readings(case.snapshot)

    def test_the_summary_is_built_around_the_reading_its_row_names(self) -> None:
        # The anchor for everything else: each row declares which of the
        # provider's segments is its primary state, and the host's role ladder has
        # to agree. A row whose expectations all hold while the summary is built
        # around a different reading is a row that proves nothing.
        for case in self.CASES:
            with self.subTest(case=case.name):
                primary = primary_of(case.snapshot)
                self.assertEqual("" if primary is None else primary.key, case.primary)

    def test_the_summary_carries_every_reading_its_row_requires(self) -> None:
        for case in self.CASES:
            line = self.read(case, CLEAR)
            for fragment in case.clear_says:
                with self.subTest(case=case.name, says=fragment):
                    self.assertIn(fragment, line, line)

    def test_the_summary_leaves_out_every_reading_its_row_forbids(self) -> None:
        for case in self.CASES:
            line = self.read(case, CLEAR)
            for fragment in case.clear_omits:
                with self.subTest(case=case.name, omits=fragment):
                    self.assertNotIn(fragment, line, line)

    def test_detail_carries_every_reading_its_row_requires(self) -> None:
        for case in self.CASES:
            line = self.read(case, DETAIL)
            for fragment in case.detail_says:
                with self.subTest(case=case.name, says=fragment):
                    self.assertIn(fragment, line, line)

    def test_the_product_is_named_at_both_depths(self) -> None:
        # Attribution is not a detail-only luxury. A summary that says `Verified`
        # without saying which product verified anything is a glyph with a mood.
        for case in self.CASES:
            name = render.provider_display_name(case.snapshot.provider)
            for depth in DEPTHS:
                for mode in MODES:
                    with self.subTest(case=case.name, depth=depth.value, mode=mode.value):
                        self.assertIn(name, self.read(case, depth, mode))

    def test_the_scope_is_marked_at_both_depths(self) -> None:
        for case in self.CASES:
            marker = {
                mode: render.scope_marker(case.snapshot.scope.value, mode) for mode in MODES
            }
            for depth in DEPTHS:
                for mode in MODES:
                    with self.subTest(case=case.name, depth=depth.value, mode=mode.value):
                        self.assertIn(marker[mode], self.read(case, depth, mode))

    def test_the_summary_is_never_wider_than_the_detail_it_summarises(self) -> None:
        # The one arithmetic relation between the depths. `clear` selects segments
        # and drops fields; it has no path that adds anything, so any row where it
        # came out wider means something was rendered differently rather than less.
        for case in self.CASES:
            for mode in MODES:
                with self.subTest(case=case.name, mode=mode.value):
                    self.assertLessEqual(
                        render.display_width(self.read(case, CLEAR, mode)),
                        render.display_width(self.read(case, DETAIL, mode)),
                    )

    def test_a_hypothetical_reading_is_welded_to_its_marker_at_both_depths(self) -> None:
        """No reading that did not happen may appear without saying so.

        Asserted structurally rather than by looking for the marker anywhere in
        the line: with two readings present, a marker attached to the wrong one is
        worse than a missing marker, because it labels the executed outcome as
        hypothetical and the hypothetical one as executed.
        """
        for case in self.CASES:
            for depth in DEPTHS:
                line = self.read(case, depth)
                for part in self.shown(case, depth):
                    welded = f"{part.label} [{render.HYPOTHETICAL_TEXT}]"
                    with self.subTest(case=case.name, depth=depth.value, key=part.key):
                        if part.hypothetical:
                            self.assertIn(welded, line, line)
                        else:
                            self.assertNotIn(welded, line, line)

    def test_no_reading_reaches_the_summary_that_detail_would_not_show(self) -> None:
        # The containment half of the cross-depth invariant, per product: `clear`
        # chooses among the provider's segments, so every segment it chooses is one
        # `detail` also has. A `clear` that synthesised its own reading would be a
        # second state engine, and this is where that shows up first.
        for case in self.CASES:
            with self.subTest(case=case.name):
                everything = contract.order_segments(case.snapshot)
                for part in render.clear_readings(case.snapshot):
                    self.assertIn(part, everything)


class FornaxDepthTest(DepthMatrixChecks, unittest.TestCase):
    """Verification: the verdict is the product, so the verdict is the summary."""

    CASES = FORNAX

    def test_only_the_verified_verdict_is_ever_summarised_as_verified(self) -> None:
        for case in self.CASES:
            verdict = case.snapshot.segments[0]
            verified = verdict.label == "Verified"
            for depth in DEPTHS:
                with self.subTest(case=case.name, depth=depth.value):
                    said = "Verified" in self.read(case, depth)
                    self.assertEqual(said, verified, self.read(case, depth))

    def test_an_unrecorded_reason_is_reported_as_unrecorded_and_not_invented(self) -> None:
        """The specific fabrication this product invites.

        A verification product that cannot say *why* a claim is unverified has one
        honest answer and several plausible-sounding dishonest ones. `detail` must
        give the honest one — the provider's own `reason_not_recorded` — and must
        not upgrade it into a finding about the evidence.
        """
        case = next(row for row in self.CASES if row.name == "unverified_no_reason")
        detail = self.read(case, DETAIL)
        self.assertIn("not recorded", detail)
        for invented in ("insufficient evidence", "no evidence", "evidence missing"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, detail.lower())

    def test_activity_never_becomes_a_verdict_at_either_depth(self) -> None:
        # Three thousand observations and an `attention` verdict is an unverified
        # claim that has been looked at a lot, and the tally must not read as the
        # verdict at either depth.
        case = next(row for row in self.CASES if row.name == "unverified_no_reason")
        for depth in DEPTHS:
            line = self.read(case, depth)
            with self.subTest(depth=depth.value):
                self.assertIn("Unverified", line)
                self.assertNotIn("Verified (", line)


class CircinusDepthTest(DepthMatrixChecks, unittest.TestCase):
    """Enforcement: two confusions have to be structurally impossible."""

    CASES = CIRCINUS

    def test_an_unreachable_daemon_overrides_every_cached_posture(self) -> None:
        """The rule that makes a wedged daemon safe to look at.

        The cached shadow posture in this row is `warn`, which outranks the
        `unknown` of the daemon that is not answering — so a summary built on
        severity alone picks the cached one and tells the reader their tool calls
        are being evaluated by a process that is not running.
        """
        case = next(row for row in self.CASES if row.name == "unreachable_with_cached_posture")
        readings = render.clear_readings(case.snapshot)
        self.assertEqual([part.key for part in readings], ["availability"])
        for mode in MODES:
            with self.subTest(mode=mode.value):
                summary = self.read(case, CLEAR, mode)
                self.assertIn("Not responding", summary)
                self.assertNotIn("Shadow mode", summary)

    def test_a_shadow_outcome_and_an_executed_one_never_read_the_same(self) -> None:
        shadow = next(row for row in self.CASES if row.name == "latest_would_block_in_shadow")
        executed = next(
            row for row in self.CASES if row.name == "executed_block_while_enforcing"
        )
        for depth in DEPTHS:
            with self.subTest(depth=depth.value):
                self.assertIn(render.HYPOTHETICAL_TEXT, self.read(shadow, depth))
                self.assertNotIn(render.HYPOTHETICAL_TEXT, self.read(executed, depth))

    def test_a_decision_tally_never_reaches_the_summary(self) -> None:
        # `1520 of 3747 decisions` is the counter defect in its purest form: it is
        # not actionable, and it costs the product its entire summary line.
        for case in self.CASES:
            for part in case.snapshot.segments:
                if part.count is None:
                    continue
                with self.subTest(case=case.name, key=part.key):
                    self.assertNotIn(str(part.count), self.read(case, CLEAR))
                    self.assertIn(str(part.count), self.read(case, DETAIL))

    def test_a_product_that_could_not_be_read_is_not_a_product_that_is_calm(self) -> None:
        malformed = next(row for row in self.CASES if row.name == "malformed_answer")
        for depth in DEPTHS:
            with self.subTest(depth=depth.value):
                line = self.read(malformed, depth, render.PresentationMode.PLAIN)
                self.assertIn("Unreadable answer", line)
                # The host is speaking for a provider it could not parse, so the
                # state it speaks with must be the one that makes no health claim.
                self.assertIn(render.STATE_TEXT[render.UNKNOWN_STATE], line)


class LibraDepthTest(DepthMatrixChecks, unittest.TestCase):
    """Scheduling and budget: both defects here are missing nouns."""

    CASES = LIBRA

    def test_no_confidence_value_reaches_the_summary(self) -> None:
        """A bare `high` beside a state marker reads as severity, not certainty.

        Checked against the confidence *values* rather than the rendered phrase,
        because the danger is the word arriving without its subject — which is
        exactly what dropping the subject and keeping the value would do.
        """
        for case in self.CASES:
            for part in case.snapshot.segments:
                if part.confidence is None:
                    continue
                with self.subTest(case=case.name, key=part.key):
                    self.assertNotIn(part.confidence.value, self.read(case, CLEAR))

    def test_a_confidence_that_is_shown_always_names_its_subject(self) -> None:
        for case in self.CASES:
            for part in case.snapshot.segments:
                if part.confidence is None:
                    continue
                detail = self.read(case, DETAIL)
                with self.subTest(case=case.name, key=part.key):
                    self.assertIn(part.confidence_of.label, detail)
                    self.assertIn(
                        f"{part.confidence_of.label} {part.confidence.value}", detail
                    )

    def test_every_percentage_keeps_the_axis_it_arrived_with(self) -> None:
        # The provider is required to name the axis inside the label; the renderer
        # must not be able to separate them, at either depth. `38%` alone could
        # mean spent or left, and those are opposite instructions to the reader.
        for case in self.CASES:
            for part in case.snapshot.segments:
                if "%" not in part.label:
                    continue
                for depth in DEPTHS:
                    with self.subTest(case=case.name, depth=depth.value):
                        self.assertIn(part.label, self.read(case, depth))

    def test_a_spent_budget_is_a_posture_and_not_a_blockage(self) -> None:
        """The state ladder's most consequential distinction, in Libra's words.

        `Replan budget spent` is `warn`: something is wrong and work continues.
        Promoting it to the rung above would tell the reader they are being waited
        on when nothing is waiting on them — and the product's own explain surface
        would then disagree with its statusline.
        """
        case = next(row for row in self.CASES if row.name == "replanned")
        self.assertNotIn(contract.ClearRole.EXCEPTION, render.clear_roles(case.snapshot))
        self.assertEqual(primary_of(case.snapshot).key, "plan")
        # The estimate riding along is legitimate — it changes how a spent budget
        # reads. What is not legitimate is the posture being displaced by it.
        self.assertIn("Replan budget spent", self.read(case, CLEAR))

    def test_someone_being_waited_on_takes_the_line_from_an_estimate(self) -> None:
        case = next(row for row in self.CASES if row.name == "awaiting_approval")
        readings = render.clear_readings(case.snapshot)
        self.assertEqual([part.key for part in readings], ["approval"])
        detail = self.read(case, DETAIL)
        # The estimate is not wrong and is not hidden — it is just not the thing
        # the reader needs in one phrase.
        self.assertIn("Remaining work", detail)


class MatrixCoverageTest(unittest.TestCase):
    """Every row of the matrix is actually read by one of the product classes.

    Without this, adding a row to `MATRIX` and forgetting to add it to a product
    tuple would grow the matrix's apparent coverage while asserting nothing new.
    """

    CLASSES = (FornaxDepthTest, CircinusDepthTest, LibraDepthTest)

    def test_the_product_classes_partition_the_matrix(self) -> None:
        read = [case for klass in self.CLASSES for case in klass.CASES]
        self.assertEqual(len(read), len(MATRIX))
        self.assertEqual({id(case) for case in read}, {id(case) for case in MATRIX})

    def test_each_product_class_reads_exactly_one_product(self) -> None:
        for klass in self.CLASSES:
            with self.subTest(klass=klass.__name__):
                providers = {case.snapshot.provider for case in klass.CASES}
                self.assertEqual(len(providers), 1, providers)


def row(cases: tuple[Case, ...], name: str) -> Case:
    return next(case for case in cases if case.name == name)


# Whole hosts, assembled from one row per product. The per-product classes above
# read one product in isolation, which is the right unit for "does this state read
# correctly"; it is the wrong unit for "do three products on one line still each
# say what they said", and the switching and performance cases need a host rather
# than a segment anyway.
HOSTS = (
    ("calm", ("verified", "healthy_enforce", "idle")),
    ("shadow", ("observing", "healthy_shadow", "active_high_confidence")),
    ("wedged", ("unverified_no_reason", "unreachable_with_cached_posture", "unavailable")),
    (
        "escalated",
        ("contradicted", "executed_block_while_enforcing", "awaiting_approval"),
    ),
)


def host(names: tuple[str, str, str]) -> tuple[contract.ProviderStatus, ...]:
    fornax_name, circinus_name, libra_name = names
    return (
        row(FORNAX, fornax_name).snapshot,
        row(CIRCINUS, circinus_name).snapshot,
        row(LIBRA, libra_name).snapshot,
    )


class CrossDepthInvariantTest(unittest.TestCase):
    """One snapshot, two depths, and the parts of the reading that may not differ.

    This is the claim that makes clear and detail two views of one state engine
    rather than two renderers that happen to agree on today's fixtures. Stated
    three ways, because each catches a different way of breaking it: the primary
    state is identical; the supporting material is a subset; and the projection is
    idempotent, so a second application somewhere in the pipeline cannot quietly
    summarise a summary.
    """

    def test_the_primary_state_reads_identically_at_both_depths(self) -> None:
        """The state marker and the label, adjacent, in both renderings.

        The marker comes from `render.state_marker` rather than from a table
        written here: a copy of the mapping in this file would keep agreeing with
        itself while the renderer moved underneath it, which is the failure mode
        this campaign has already hit twice.
        """
        for case in MATRIX:
            primary = primary_of(case.snapshot)
            if primary is None:
                continue
            for mode in MODES:
                marker = render.state_marker(primary.state.value, mode)
                pair = f"{marker} {primary.label}"
                for depth in DEPTHS:
                    with self.subTest(case=case.name, mode=mode.value, depth=depth.value):
                        text = render.render_provider(case.snapshot, mode, depth)
                        self.assertIn(pair, text, text)

    def test_the_summary_supporting_material_is_a_subset_of_detail(self) -> None:
        # The privacy statement in surface form, over this module's own states:
        # clear cannot carry a field detail does not, so switching to clear can
        # never widen the reviewed surface. `detail_atoms` is the render suite's
        # own reader, imported rather than re-implemented.
        for case in MATRIX:
            for part in case.snapshot.segments:
                for mode in MODES:
                    with self.subTest(case=case.name, key=part.key, mode=mode.value):
                        self.assertLessEqual(
                            detail_atoms(render.render_segment(part, mode, CLEAR)),
                            detail_atoms(render.render_segment(part, mode, DETAIL)),
                        )

    def test_the_summary_stays_within_its_editorial_bound(self) -> None:
        for case in MATRIX:
            with self.subTest(case=case.name):
                readings = render.clear_readings(case.snapshot)
                self.assertLessEqual(len(readings), render.MAX_CLEAR_READINGS)
                self.assertLessEqual(len(readings), len(case.snapshot.segments))

    def test_summarising_a_summary_changes_nothing(self) -> None:
        # `compose` projects to account for width and `render_provider` projects
        # to produce output. If the projection were not idempotent, those two would
        # disagree about what the line contains, and the disagreement would show up
        # as a `[+N more]` counting readings clear was never going to show.
        for name, names in HOSTS:
            once = render.project_to_depth(host(names), CLEAR)
            with self.subTest(host=name):
                self.assertEqual(render.project_to_depth(once, CLEAR), once)

    def test_every_product_on_the_summary_line_is_on_the_detail_line(self) -> None:
        for name, names in HOSTS:
            statuses = host(names)
            for mode in MODES:
                summary = render.compose(UPSTREAM, statuses, mode=mode, depth=CLEAR)
                detail = render.compose(UPSTREAM, statuses, mode=mode, depth=DETAIL)
                for status in statuses:
                    display = render.provider_display_name(status.provider)
                    with self.subTest(host=name, mode=mode.value, provider=display):
                        self.assertIn(display, summary)
                        self.assertIn(display, detail)

    def test_no_reading_on_the_summary_line_is_missing_from_the_detail_line(self) -> None:
        for name, names in HOSTS:
            statuses = host(names)
            for mode in MODES:
                detail = render.compose(UPSTREAM, statuses, mode=mode, depth=DETAIL)
                for status in statuses:
                    for part in render.clear_readings(status):
                        with self.subTest(host=name, mode=mode.value, key=part.key):
                            self.assertIn(part.label, detail, detail)

    def test_only_the_depth_changes_the_shape_of_the_line(self) -> None:
        """The HORO-1628 layout, restated as a property of the depth axis.

        Included here rather than left to the render suite because it is the
        no-regression half of this ticket: a depth change is the only thing that
        may turn one row into several, and it must do so for *every* rendering
        style rather than only the default one.
        """
        for name, names in HOSTS:
            statuses = host(names)
            for mode in MODES:
                with self.subTest(host=name, mode=mode.value):
                    summary = render.compose(UPSTREAM, statuses, mode=mode, depth=CLEAR)
                    detail = render.compose(UPSTREAM, statuses, mode=mode, depth=DETAIL)
                    self.assertEqual(summary.count("\n"), 0, summary)
                    self.assertEqual(detail.count("\n"), len(statuses), detail)
                    self.assertTrue(summary.startswith(UPSTREAM))
                    self.assertEqual(detail.split("\n")[0], UPSTREAM)

    def test_the_reader_line_is_untouched_by_either_depth(self) -> None:
        for name, names in HOSTS:
            statuses = host(names)
            for mode in MODES:
                for depth in DEPTHS:
                    with self.subTest(host=name, mode=mode.value, depth=depth.value):
                        line = render.compose(UPSTREAM, statuses, mode=mode, depth=depth)
                        self.assertTrue(line.startswith(UPSTREAM), line)


# --------------------------------------------------------------------------- #
# The switching matrix
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class SwitchCase:
    """One walk across the depth axis, in one installation shape.

    `products` are matrix rows from above, turned into real provider executables,
    so the states a switch is checked against are the same states the semantic
    matrices assert — rather than a second, friendlier set of fixtures that only
    the switching tests ever see.
    """

    name: str
    products: tuple[Case, ...]
    rendering: tuple[bool, bool] | None
    start: render.InformationDepth
    target: render.InformationDepth

    @property
    def is_noop(self) -> bool:
        return self.start is self.target


def _one() -> tuple[Case, ...]:
    return (row(FORNAX, "verified"),)


def _wedged() -> tuple[Case, ...]:
    """Three products, two of which cannot be read.

    The shape most likely to lose a product on the way across: a switch that
    rebuilt the enabled set from whatever answered would silently drop these two.
    """
    return tuple(row(cases, name) for cases, name in zip((FORNAX, CIRCINUS, LIBRA), HOSTS[2][1]))


def _escalated() -> tuple[Case, ...]:
    """Three products all demanding attention, rendered in text-only compact.

    Carries the rendering preference so that the orthogonality claim is checked
    under a switch rather than only in the planner: asking for supporting context
    must not quietly restore glyphs or loosen the density.
    """
    return tuple(row(cases, name) for cases, name in zip((FORNAX, CIRCINUS, LIBRA), HOSTS[3][1]))


# Four transitions -- both directions, and both no-ops, because "set the depth you
# are already at" is how a script re-asserts a preference and it must not write --
# in each of three installation shapes.
SWITCHES = tuple(
    SwitchCase(
        name=f"{shape}/{start.value}_to_{target.value}",
        products=products(),
        rendering=rendering,
        start=start,
        target=target,
    )
    for shape, products, rendering in (
        ("one_product", _one, None),
        ("three_products", _wedged, None),
        ("three_products_text_compact", _escalated, (True, False)),
    )
    for start, target in ((CLEAR, DETAIL), (DETAIL, CLEAR), (CLEAR, CLEAR), (DETAIL, DETAIL))
)


def switch(name: str) -> SwitchCase:
    return next(case for case in SWITCHES if case.name == name)


@dataclasses.dataclass(frozen=True)
class Untouched:
    """Everything a depth switch must leave exactly as it found it.

    Modes as well as bytes: a switch that rewrote the reader's script by copying
    it back without its executable bit would compare equal on content and leave
    the host unable to run it.
    """

    settings: bytes
    upstream: bytes
    upstream_mode: int
    registry: dict
    mode: render.PresentationMode
    providers: tuple[str, ...]
    files: dict[str, bytes]
    modes: dict[str, int]


class DepthSwitchTest(RenderingCase, PresentationCase):
    """Moving between depths, checked for everything it must not take with it.

    Composed from the two existing lifecycle fixtures rather than given a new one:
    `RenderingCase` runs the configured statusline the way Claude Code does, and
    `PresentationCase` drives the real planner. A third harness here would let
    this gate pass against a switching path and a rendering path no user has.
    """

    @property
    def counter(self) -> pathlib.Path:
        """Where the provider fixtures record that they were asked."""
        return self.root / "provider-runs"

    def asked(self) -> int:
        """How many times any provider has been executed so far."""
        return len(self.counter.read_bytes()) if self.counter.exists() else 0

    def provider_of(self, case: Case) -> pathlib.Path:
        """A real executable emitting one matrix row, as the wire document.

        It records each execution, so "no extra provider request" can be counted
        rather than inferred. The counter is read as a delta at each measurement
        point, never assumed to start at zero: writing an executable warms it by
        running it once, and a test that assumed otherwise would be measuring the
        fixture's own setup.
        """
        payload = json.dumps(case.snapshot.to_wire())
        return self.script(
            f"{case.snapshot.provider}-provider.sh",
            f"#!/bin/sh\ncat >/dev/null\nprintf 'x' >> {shlex.quote(str(self.counter))}\n"
            f"printf '%s' {shlex.quote(payload)}\n",
        )

    def rendered(self) -> str:
        """The line as the host receives it, without the terminating newline.

        The trailing newline is the compositor being a well-behaved command, not
        part of the line's shape -- and the shape is what the depth owns.
        """
        return self.render().rstrip("\n")

    def install(self, case: SwitchCase) -> None:
        for product in case.products:
            argv = (str(self.provider_of(product)),)
            self.enable(product.snapshot.provider, argv=argv, timeout_ms=2000)
        if case.rendering is not None:
            compact, glyphs = case.rendering
            self.presentation(compact=compact, glyphs=glyphs)
        self.presentation(depth=case.start)
        self.assertIs(self.effective_depth(), case.start)

    def capture(self) -> Untouched:
        registry_path = lifecycle.read_registry(self.home).path
        files = {
            str(path): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path != registry_path
        }
        return Untouched(
            settings=self.settings.read_bytes(),
            upstream=self.upstream.read_bytes(),
            upstream_mode=self.upstream.stat().st_mode,
            registry=self._registry(),
            mode=self.effective(),
            providers=tuple(entry["provider"] for entry in self._registry()["providers"]),
            files=files,
            modes={path: pathlib.Path(path).stat().st_mode for path in files},
        )

    def assert_untouched(self, before: Untouched) -> None:
        """The six things a depth switch must not disturb, checked together.

        Together rather than as six tests over one shared switch, because the
        interesting failure is a switch that gets one of them right and takes
        something else with it in the same step.
        """
        after = self.capture()

        self.assertEqual(after.settings, before.settings)
        self.assertEqual(after.upstream, before.upstream)
        self.assertEqual(after.upstream_mode, before.upstream_mode)
        # Path set as well as contents, so a lock file, a marker or a queued job
        # appearing counts as a change.
        self.assertEqual(sorted(after.files), sorted(before.files))
        self.assertEqual(after.files, before.files)
        self.assertEqual(after.modes, before.modes)
        self.assertEqual(after.providers, before.providers)
        self.assertEqual(after.mode, before.mode)

        outside = {key: value for key, value in after.registry.items() if key != lifecycle.PRESENTATION_KEY}
        self.assertEqual(
            outside,
            {key: value for key, value in before.registry.items() if key != lifecycle.PRESENTATION_KEY},
        )
        moved = {
            key
            for key in set(after.registry.get(lifecycle.PRESENTATION_KEY, {}))
            | set(before.registry.get(lifecycle.PRESENTATION_KEY, {}))
            if after.registry.get(lifecycle.PRESENTATION_KEY, {}).get(key)
            != before.registry.get(lifecycle.PRESENTATION_KEY, {}).get(key)
        }
        # `mode` is in the owned set because the planner resolves and restates it on
        # every write; what must not move is the mode actually in force, asserted
        # above through the compositor's own parser.
        self.assertLessEqual(moved, set(lifecycle._OWNED_PRESENTATION_KEYS), moved)

    @contextlib.contextmanager
    def no_processes(self):
        """No process may start while the depth is changing.

        One assertion covering four of the ticket's prohibitions at once — no
        provider re-run, no daemon start, no reinstall, no rebuild — because all
        four have to start a process to happen. Patched on `subprocess` itself
        rather than on the compositor, so a switch that grew its own spawn path
        somewhere else is caught too.
        """

        def refuse(*args, **kwargs):
            raise AssertionError(f"a depth switch started a process: {args[:1]}")

        with unittest.mock.patch.object(subprocess, "Popen", refuse):
            with unittest.mock.patch.object(subprocess, "run", refuse):
                yield

    def switch(self, case: SwitchCase) -> lifecycle.Plan:
        before = self.capture()
        plan = self.plan_presentation(depth=case.target)
        with self.no_processes():
            result = lifecycle.apply(plan)
        self.assertFalse(result.settings_written)
        self.assertEqual(result.registry_written, not case.is_noop)
        self.assert_untouched(before)
        self.assertIs(self.effective_depth(), case.target)
        return plan

    def test_every_case_is_a_distinct_walk(self) -> None:
        self.assertEqual(len({case.name for case in SWITCHES}), len(SWITCHES))
        self.assertEqual(len(SWITCHES), 12)

    def test_the_matrix_covers_both_directions_both_noops_and_a_stated_rendering(self) -> None:
        # Guards the matrix itself: a comprehension that quietly lost a transition
        # would still produce twelve names.
        walks = {(case.start, case.target) for case in SWITCHES}
        self.assertEqual(walks, {(CLEAR, DETAIL), (DETAIL, CLEAR), (CLEAR, CLEAR), (DETAIL, DETAIL)})
        self.assertEqual(len({case.rendering for case in SWITCHES}), 2)
        self.assertEqual({len(case.products) for case in SWITCHES}, {1, 3})

    def test_a_switch_takes_nothing_else_with_it(self) -> None:
        for case in SWITCHES:
            with self.subTest(case=case.name):
                self.setUp()
                self.install(case)
                self.switch(case)

    def test_a_depth_already_in_force_writes_nothing_at_all(self) -> None:
        for case in SWITCHES:
            if not case.is_noop:
                continue
            with self.subTest(case=case.name):
                self.setUp()
                self.install(case)
                registry_path = lifecycle.read_registry(self.home).path
                before = registry_path.read_bytes()
                plan = self.switch(case)
                # Byte-identical, not merely equivalent: re-serialising our own file
                # to record a change that is not happening is still a write, and a
                # user reading file timestamps to see what a command did deserves
                # the honest answer.
                self.assertFalse(plan.mutates)
                self.assertEqual(registry_path.read_bytes(), before)

    def test_the_products_still_render_after_every_switch(self) -> None:
        for case in SWITCHES:
            with self.subTest(case=case.name):
                self.setUp()
                self.install(case)
                self.switch(case)
                line = self.rendered()
                self.assertTrue(line.startswith("MY OWN LINE"), line)
                for product in case.products:
                    display = render.provider_display_name(product.snapshot.provider)
                    # Exactly once. A switch that left the previous rendering in
                    # place and appended the new one would still contain every
                    # product, and the reader would see each of them twice.
                    self.assertEqual(line.count(display), 1, line)

    def test_the_depth_switched_to_is_the_depth_rendered_at(self) -> None:
        # The claim the stored preference exists to make. Checked through the
        # rendered line rather than the registry, because a preference the
        # compositor does not honour is not a preference.
        for case in SWITCHES:
            with self.subTest(case=case.name):
                self.setUp()
                self.install(case)
                self.switch(case)
                line = self.rendered()
                rows = 0 if case.target is CLEAR else len(case.products)
                self.assertEqual(line.count("\n"), rows, line)
                if rows:
                    # The reader's own line keeps a row to itself rather than
                    # becoming the first product's prefix.
                    self.assertEqual(line.split("\n")[0], "MY OWN LINE")

    def test_no_product_changes_what_it_says_on_the_way_across(self) -> None:
        """The cross-depth invariant, through the real switch rather than the API.

        `CrossDepthInvariantTest` proves the renderer projects one snapshot two
        ways. This proves the thing a reader actually does — change the setting and
        look again — does not change the answer, with the states coming from real
        provider processes and the depth from the real registry.
        """
        for case in SWITCHES:
            with self.subTest(case=case.name):
                self.setUp()
                self.install(case)
                before = self.rendered()
                self.switch(case)
                after = self.rendered()
                for product in case.products:
                    primary = primary_of(product.snapshot)
                    if primary is None:
                        continue
                    marker = render.state_marker(primary.state.value, self.effective())
                    with self.subTest(provider=product.snapshot.provider):
                        for line in (before, after):
                            self.assertIn(f"{marker} {primary.label}", line, line)

    def test_a_switch_does_not_ask_the_products_anything(self) -> None:
        """No extra provider request, counted rather than inferred.

        `no_processes` already forbids starting one, but that is an assertion about
        mechanism. This is the assertion about observable effect, which is what the
        ticket asks for: the fixtures count their own executions, and the count
        does not move across a switch that is rendering three of them.
        """
        case = switch("three_products/clear_to_detail")
        self.install(case)
        warmed = self.asked()
        self.rendered()
        before = self.asked()
        # The control: a render does ask, once per product. Without it, a switch
        # asking nothing would be indistinguishable from a counter that never
        # records anything.
        self.assertEqual(before - warmed, len(case.products))

        self.switch(case)

        self.assertEqual(self.asked(), before)
