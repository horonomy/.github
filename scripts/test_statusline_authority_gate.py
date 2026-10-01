"""Product-declared Clear semantics, proven against the payloads the products emit.

Every other test in this directory builds its own provider snapshots.
`test_statusline_depth_gate.py` says so in its own docstring: its matrices are
*contract fixtures*, not installed providers. That is the right tool for proving
what the host does with a shape, and the wrong one for proving what the host does
with Fornax, Circinus and Libra — because a hand-written approximation of a
payload proves a property of the approximation.

So this module asserts over 27 payloads captured from the three shipped provider
binaries reading real staged product state, committed under
`scripts/fixtures/statusline_payloads/` with a per-fixture provenance record
naming the command, the product repo and the commit it was built from.
`scripts/statusline_capture.py` is the driver that produced them; it needs the
three products installed and so is deliberately not named `test_*`, because CI
runs `python3 -m unittest discover -s scripts` on a runner that has none of them.
The fixtures are the only thing CI needs.

What is being proven is narrow and specific. All three products now declare
`clear_authority: provider` and a `clear_role` on every segment, which switches
the host's inference ladder off: under `ClearAuthority.PROVIDER` every segment
already carries a role, so every rung of `statusline_render.clear_roles` — each
of which only fires on a role that is still `None` — has nothing to do. The
product's own semantics reach the line because the arithmetic leaves them alone,
not because a flag is checked somewhere.

That is a real guarantee and an uncommonly fragile one. Nothing crashes if it
breaks. A rung that stopped respecting the declaration would still produce a
plausible statusline, assembled out of generic severity heuristics, and the only
visible symptom would be a product's primary reading quietly changing into the
host's opinion of it. Hence the mutations in `test_statusline_mutations.py`
(`AuthorityMutation*`), which aim at those three rungs, at the two staleness
filters, at the unavailability rule and at the contract's four refusals — and not
at an authority branch, because there is deliberately no authority branch to
aim at.

Two of the ten proofs are narrower than their wording suggests, and both say so
where they are asserted rather than here: Fornax emits exactly one segment per
payload, so no fornax fixture can demonstrate a *choice* between readings, and no
shipped Circinus payload carries a cached posture beside an unreachable runtime
because the product refuses to emit one. Both are properties of the products
worth pinning, and both are backed elsewhere — the first by the role assignment
the host actually computes, the second by an explicitly-labelled probe payload.
A gate that let its wording outrun its evidence would be the same defect it
exists to catch.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
import subprocess
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_render as render
from test_statusline_depth_gate import primary_of
from test_statusline_render import detail_atoms

CLEAR = render.InformationDepth.CLEAR
DETAIL = render.InformationDepth.DETAIL
DEPTHS = (CLEAR, DETAIL)
MODES = tuple(render.PresentationMode)
BALANCED = render.PresentationMode.BALANCED

FIXTURE_ROOT = pathlib.Path(__file__).resolve().parent / "fixtures" / "statusline_payloads"
PROVENANCE_PATH = FIXTURE_ROOT / "provenance.json"

# How a payload was obtained. `live-cli` drove the product's own installer, hooks
# and daemon and read the provider's stdout; `staged-store` wrote the product's
# own on-disk state directly and then read the provider's stdout, for the states
# whose live route is gated on wall clock the test cannot spend (Circinus's 300s
# freshness window, Libra's 3x300s replan hysteresis, a multi-day estimate);
# `stub-peer` stood a minimal HTTP peer in for a daemon that cannot be asked to
# answer slowly or unintelligibly on demand. In all three the payload is the
# shipped provider binary's verbatim stdout.
ROUTES = frozenset({"live-cli", "staged-store", "stub-peer"})

# The roster, pinned. Without it, deleting a fixture would make this module prove
# less while still passing — which is the failure mode the whole file is about.
EXPECTED_CASES = {
    "fornax": frozenset(
        {
            "empty_store",
            "verdict_verified",
            "verdict_review",
            "verdict_contradicted",
            "verdict_unverified",
            "no_reading_daemon_unreachable",
            "no_reading_daemon_too_slow",
            "no_reading_daemon_identity_mismatch",
            "no_reading_daemon_identity_not_reported",
            "no_reading_response_not_understood",
            "no_reading_store_read_failed",
        }
    ),
    "circinus": frozenset(
        {
            "installed_shadow_no_decisions",
            "shadow_would_allow_fresh",
            "shadow_would_block_fresh",
            "enforce_blocked",
            "mode_restart_required",
            "stale_decision",
            "hooks_disconnected",
            "not_running",
            "unreachable",
        }
    ),
    "libra": frozenset(
        {
            "idle",
            "active_no_estimate",
            "active_with_estimate",
            "active_long_estimate",
            "budget_exhausted",
            "escalated_awaiting_approval",
            "no_reading_daemon_unreachable",
        }
    ),
}

# The scope each product actually declares. Recorded because it is not what the
# depth gate's hand-written matrix assumes — that matrix scopes Fornax to the
# project and Libra to the session, on the reasoning that verification is
# per-project and scheduling per-session, while all three shipped providers
# report `host`. Both are defensible readings of the contract and scope is the
# product's call, so this is pinned rather than corrected: a product changing its
# mind should fail a test, not pass one quietly.
DECLARED_SCOPE = {
    "fornax": contract.Scope.HOST,
    "circinus": contract.Scope.HOST,
    "libra": contract.Scope.HOST,
}


@dataclasses.dataclass(frozen=True)
class Payload:
    """One captured provider payload, as wire JSON and as a parsed snapshot."""

    product: str
    case: str
    path: pathlib.Path
    wire: dict
    status: contract.ProviderStatus
    provenance: dict

    @property
    def name(self) -> str:
        return f"{self.product}/{self.case}"


def _load() -> tuple[tuple[Payload, ...], dict]:
    """Every committed payload, parsed through the host's own wire parser.

    `provider_status_from_wire` rather than a local reader, so a payload that the
    host would refuse cannot be asserted over here as though the host accepted
    it. A parse failure at import time is the correct outcome: it means a
    committed fixture is one the product should never have emitted.
    """
    index = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    payloads = []
    for path in sorted(FIXTURE_ROOT.glob("*/*.json")):
        wire = json.loads(path.read_text(encoding="utf-8"))
        name = f"{path.parent.name}/{path.stem}"
        payloads.append(
            Payload(
                product=path.parent.name,
                case=path.stem,
                path=path,
                wire=wire,
                status=contract.provider_status_from_wire(wire),
                provenance=index["fixtures"].get(name, {}),
            )
        )
    return tuple(payloads), index


PAYLOADS, PROVENANCE = _load()
BY_NAME = {item.name: item for item in PAYLOADS}


def payload(name: str) -> Payload:
    """One fixture by `product/case`, failing loudly if it has been renamed."""
    if name not in BY_NAME:
        raise KeyError(f"no captured payload {name!r}; have {sorted(BY_NAME)}")
    return BY_NAME[name]


def of(product: str) -> tuple[Payload, ...]:
    return tuple(item for item in PAYLOADS if item.product == product)


def _lines() -> tuple[tuple[Payload, ...], ...]:
    """Every payload placed on a three-product line at least once.

    `compose` takes one snapshot per provider — `order_providers` refuses a
    duplicate id, because two answers from one product means a duplicated
    registry entry — so the fixture set cannot be composed all at once. Walking
    the three products in step covers every case of each, in as many lines as the
    longest product needs, and every line is the configuration the workstation
    actually renders: Fornax, Circinus and Libra together.
    """
    groups = [of(name) for name in sorted(EXPECTED_CASES)]
    span = max(len(group) for group in groups)
    return tuple(
        tuple(group[index % len(group)] for group in groups) for index in range(span)
    )


LINES = _lines()

# The reader's own statusline. Present so that "nothing else changed" includes the
# part of the line this integration does not own.
UPSTREAM = "~/proj  main*  claude-opus-5  $0.42"


def clear_keys(status: contract.ProviderStatus) -> list[str]:
    return [part.key for part in render.clear_readings(status)]


def detail_keys(status: contract.ProviderStatus) -> list[str]:
    return [part.key for part in contract.order_segments(status)]


def roles(status: contract.ProviderStatus) -> dict:
    """The role the host assigns each segment, keyed by segment key."""
    ordered = contract.order_segments(status)
    return dict(zip((part.key for part in ordered), render.clear_roles(status)))


def declared(status: contract.ProviderStatus) -> dict:
    return {part.key: part.clear_role for part in contract.order_segments(status)}


def text(status: contract.ProviderStatus, depth, mode=BALANCED) -> str:
    return render.render_provider(status, mode, depth)


def rewire(item: Payload, mutate) -> dict:
    """A deep copy of a real payload with one deliberate edit applied.

    Used only where the proof is about something the host must *refuse*. The
    edited payload is never evidence of product behaviour — it is a probe, and
    every caller says in its own words which real payload it started from and
    what single thing was changed.
    """
    wire = copy.deepcopy(item.wire)
    mutate(wire)
    return wire


class FixtureIntegrityTest(unittest.TestCase):
    """The fixtures are the evidence, so their own shape is worth asserting.

    Each failure here is one that would quietly reduce what the rest of the
    module proves: a payload with no provenance (so nothing says which product
    build emitted it), a provenance entry with no payload (so coverage is
    claimed for a case that cannot be loaded), or a roster that shrank.
    """

    def test_every_expected_case_is_present_and_nothing_else_is(self) -> None:
        found = {product: {item.case for item in of(product)} for product in EXPECTED_CASES}
        self.assertEqual(found, {k: set(v) for k, v in EXPECTED_CASES.items()})

    def test_the_fixture_tree_holds_no_product_the_roster_does_not_name(self) -> None:
        self.assertEqual(
            sorted({item.product for item in PAYLOADS}), sorted(EXPECTED_CASES)
        )

    def test_every_payload_has_a_provenance_entry(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertTrue(
                    item.provenance,
                    f"{item.path} is committed with nothing recording which product "
                    "build produced it, so nothing asserted over it is attributable",
                )

    def test_every_provenance_entry_has_a_payload(self) -> None:
        # The direction that matters more: an index still vouching for a deleted
        # payload reports coverage of a case the gate cannot load.
        self.assertEqual(sorted(PROVENANCE["fixtures"]), sorted(BY_NAME))

    def test_every_provenance_entry_names_a_route_a_capture_can_take(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertIn(item.provenance["route"], ROUTES)

    def test_every_provenance_entry_names_the_product_build_it_came_from(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                entry = item.provenance
                self.assertEqual(entry["product"], item.product)
                self.assertEqual(entry["case"], item.case)
                self.assertTrue(entry["how"].strip(), "no account of how it was staged")
                self.assertTrue(entry["provider_command"].strip())
                self.assertRegex(entry["product_repo_commit"], r"^[0-9a-f]{40}$")
                self.assertEqual(entry["provider_version"], item.status.provider_version)

    def test_the_index_names_the_driver_that_produced_it(self) -> None:
        self.assertEqual(PROVENANCE["captured_by"], "scripts/statusline_capture.py")
        self.assertTrue((FIXTURE_ROOT.parent.parent / "statusline_capture.py").exists())

    def test_a_state_no_shipped_build_can_reach_is_recorded_rather_than_faked(self) -> None:
        # The alternative — a hand-written payload for Fornax's `unavailable`
        # verdict — would have read as coverage of a product behaviour that no
        # installed adapter can produce, which is worse than the gap.
        recorded = PROVENANCE["unreachable_states"]
        self.assertIn("fornax/verdict_unavailable", recorded)
        for state, why in recorded.items():
            with self.subTest(state=state):
                self.assertNotIn(state, BY_NAME, "recorded as unreachable yet captured")
                self.assertGreater(len(why.split()), 20, "no argument for why")

    def test_the_directory_a_payload_sits_in_is_the_provider_that_emitted_it(self) -> None:
        # `libra` the directory is `libra` the provider id, not `libra-governor`
        # the repo and binary name. Asserted so the two cannot drift into each
        # other silently.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertEqual(item.status.provider, item.product)

    def test_every_payload_declares_one_product_version_per_product(self) -> None:
        for product in EXPECTED_CASES:
            versions = {item.status.provider_version for item in of(product)}
            with self.subTest(product=product):
                self.assertEqual(
                    len(versions),
                    1,
                    f"{product} payloads came from more than one build ({sorted(versions)}), "
                    "so a difference between them could be a version difference",
                )

    def test_the_composed_lines_between_them_use_every_payload(self) -> None:
        # The whole-line assertions walk `LINES` rather than the fixture set, so a
        # payload that fell out of the rotation would be exercised alone and never
        # beside the other two products.
        self.assertEqual(
            sorted({item.name for group in LINES for item in group}), sorted(BY_NAME)
        )

    def test_every_payload_declares_the_scope_its_product_is_pinned_to(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertIs(item.status.scope, DECLARED_SCOPE[item.product])


class DeclaredAuthorityTest(unittest.TestCase):
    """That the products declare, and that the host uses what they declared.

    The first half is a claim about the products: all three ship a payload the
    contract recognises as authoritative. The second is the claim this whole
    campaign rests on — the role the host computes for every segment is the role
    the product asked for, with no inference mixed in.
    """

    def test_every_product_declares_authority_over_its_own_projection(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertIs(item.status.clear_authority, contract.ClearAuthority.PROVIDER)
                self.assertEqual(item.wire["clear_authority"], "provider")

    def test_every_segment_of_every_payload_declares_its_part(self) -> None:
        # The contract already refuses a payload that does not, so this is a
        # claim about the products rather than about the host: none of the three
        # is relying on the host to fill a role in.
        for item in PAYLOADS:
            for part in item.status.segments:
                with self.subTest(fixture=item.name, segment=part.key):
                    self.assertIsNotNone(part.clear_role)

    def test_the_role_the_host_computes_is_the_role_the_product_declared(self) -> None:
        # The load-bearing assertion of the file. Every rung of `clear_roles`
        # fires only on a role still unset, so a fully-declared projection comes
        # back unchanged — and if a rung ever stops checking, this is where it
        # shows, for every one of the 27 real payloads rather than for a shape.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertEqual(roles(item.status), declared(item.status))

    def test_the_declaration_is_not_merely_what_the_heuristics_would_have_said(self) -> None:
        """At least one real payload where inference and declaration disagree.

        Without this the test above could be passing because the products happen
        to declare exactly what the ladder would have guessed, in which case
        `clear_authority` is decoration and no mutation of the ladder could prove
        otherwise. The disagreement is the reason the field exists.
        """
        disagreements = {}
        for item in PAYLOADS:
            stripped = rewire(item, _strip_declaration)
            inferred = roles(contract.provider_status_from_wire(stripped))
            mine = {key: role for key, role in roles(item.status).items()}
            if inferred != mine:
                disagreements[item.name] = (mine, inferred)
        self.assertTrue(
            disagreements,
            "every product declares exactly what the host would have inferred, so "
            "the declaration cannot be shown to be doing anything",
        )
        # Named rather than counted: these are the payloads whose primary reading
        # is the product's judgement and not the host's, and a change to the list
        # is a change to how much the declaration is buying.
        self.assertIn("fornax/verdict_contradicted", disagreements)
        self.assertIn("libra/escalated_awaiting_approval", disagreements)

    def test_a_product_that_stopped_declaring_would_be_read_by_the_heuristics(self) -> None:
        # The regression this campaign exists to prevent, stated as a fact about
        # the host rather than as a hope: strip the declaration from Libra's
        # approval payload and the generic ladder takes the line back, promoting
        # by severity and losing the product's own pairing.
        item = payload("libra/escalated_awaiting_approval")
        self.assertEqual(clear_keys(item.status), ["task", "budget"])
        stripped = contract.provider_status_from_wire(rewire(item, _strip_declaration))
        self.assertIs(stripped.clear_authority, contract.ClearAuthority.HOST)
        self.assertEqual(
            clear_keys(stripped),
            ["task", "estimate"],
            "the heuristic ladder is expected to lose the budget reading the product "
            "nominated and promote an unknown estimate in its place; if it no longer "
            "does, the declaration's value has changed and this test should say what "
            "it is now",
        )
        # The concrete regression, in words: the reader is told an estimate could
        # not be made, and is no longer told what the approval they are being asked
        # for would cost. `warn` is not in the host's action-required set, so the
        # escalation is not even promoted — it becomes the inferred posture, which
        # is the host's guess at a routine reading.
        heuristic = text(stripped, CLEAR)
        self.assertIn("Remaining work not estimated", heuristic)
        self.assertNotIn("budget left", heuristic)


def _strip_declaration(wire: dict) -> None:
    """Make a declaring payload into a pre-declaration one, in place."""
    wire.pop("clear_authority", None)
    for part in wire.get("segments", ()):
        part.pop("clear_role", None)


class FornaxClearTest(unittest.TestCase):
    """Proof 1 — Fornax's Clear reading is its verification state.

    Narrower than it sounds, and deliberately so. Every fornax payload carries
    exactly one segment, so no fornax fixture can show a *choice* between
    readings; `test_fornax_speaks_in_single_segments` pins that, and the content
    of the proof is which segment the product declares as its primary and that
    the host's role assignment matches.
    """

    VERDICTS = {
        "empty_store": "No findings yet",
        "verdict_verified": "Verified",
        "verdict_review": "Needs review",
        "verdict_contradicted": "Contradicted",
        "verdict_unverified": "Unverified",
    }

    def test_fornax_speaks_in_single_segments(self) -> None:
        for item in of("fornax"):
            with self.subTest(fixture=item.name):
                self.assertEqual(len(item.status.segments), 1)

    def test_a_verdict_payload_leads_with_the_verdict_as_its_posture(self) -> None:
        for case, label in self.VERDICTS.items():
            item = payload(f"fornax/{case}")
            with self.subTest(fixture=item.name):
                self.assertEqual(clear_keys(item.status), ["latest_finding"])
                self.assertIs(
                    roles(item.status)["latest_finding"], contract.ClearRole.POSTURE
                )
                self.assertIn(label, text(item.status, CLEAR))

    def test_only_the_verified_verdict_ever_reads_as_verified(self) -> None:
        # The product's characteristic defect is upgrading a verdict on the
        # strength of activity. Asserted over the real five-state vocabulary
        # rather than over a representative row of it.
        for item in of("fornax"):
            if item.case == "verdict_verified":
                continue
            with self.subTest(fixture=item.name):
                for depth in DEPTHS:
                    self.assertNotIn("Verified", text(item.status, depth))

    def test_a_payload_that_could_not_be_read_says_so_instead_of_a_verdict(self) -> None:
        # Six of the eleven fornax payloads are refusals to report a verdict, and
        # each declares `exception` rather than dressing the absence up as a
        # neutral verdict. The absence of a verification state is the reading.
        for item in of("fornax"):
            if not item.case.startswith("no_reading_"):
                continue
            with self.subTest(fixture=item.name):
                self.assertEqual(clear_keys(item.status), ["availability"])
                self.assertIs(
                    roles(item.status)["availability"], contract.ClearRole.EXCEPTION
                )
                self.assertNotIn("latest_finding", detail_keys(item.status))

    def test_every_refusal_keeps_its_reason_out_of_clear_and_in_detail(self) -> None:
        # Reason codes are diagnosis. The ticket's rule is that Clear carries the
        # state and Detail carries why, and the six refusals are where a reason
        # is most tempting to promote.
        for item in of("fornax"):
            part = item.status.segments[0]
            if part.reason_code is None:
                continue
            words = part.reason_code.replace("_", " ")
            with self.subTest(fixture=item.name):
                self.assertNotIn(words, text(item.status, CLEAR))
                self.assertIn(words, text(item.status, DETAIL))


class CircinusClearTest(unittest.TestCase):
    """Proofs 2, 3 and 4 — mode, a fresh outcome, staleness and unreachability."""

    FRESH = ("shadow_would_allow_fresh", "shadow_would_block_fresh", "enforce_blocked")

    def test_clear_is_the_mode_and_the_fresh_enforcement_outcome(self) -> None:
        for case in self.FRESH:
            item = payload(f"circinus/{case}")
            with self.subTest(fixture=item.name):
                self.assertEqual(clear_keys(item.status), ["mode", "latest_decision"])
                assigned = roles(item.status)
                self.assertIs(assigned["mode"], contract.ClearRole.POSTURE)
                self.assertIs(assigned["latest_decision"], contract.ClearRole.VITAL)
                decision = {part.key: part for part in item.status.segments}["latest_decision"]
                self.assertFalse(decision.is_stale)

    def test_the_install_state_is_supporting_while_the_product_is_healthy(self) -> None:
        # Circinus's four segments compete for two slots. `install: Installed` is
        # the one a reader does not need while enforcement is running, and the
        # product says so rather than the host deciding it.
        for case in self.FRESH + ("installed_shadow_no_decisions",):
            item = payload(f"circinus/{case}")
            with self.subTest(fixture=item.name):
                self.assertIs(roles(item.status)["install"], contract.ClearRole.SUPPORTING)
                self.assertNotIn("install", clear_keys(item.status))
                self.assertIn("install", detail_keys(item.status))

    def test_a_raw_decision_tally_never_reaches_clear(self) -> None:
        # The counters are Detail-only by the Founder's rule, and they are the one
        # thing in the payload that looks like a headline — `1 of 1 recent` reads
        # as news and tells an operator nothing to act on.
        for item in of("circinus"):
            counted = [part for part in item.status.segments if part.count is not None]
            if not counted:
                continue
            with self.subTest(fixture=item.name):
                for part in counted:
                    self.assertNotIn(part.key, clear_keys(item.status))
                    self.assertNotIn(str(part.count), text(item.status, CLEAR))
                    self.assertIn(str(part.count), text(item.status, DETAIL))

    def test_a_stale_outcome_leaves_clear_and_the_mode_remains(self) -> None:
        item = payload("circinus/stale_decision")
        stale = {part.key: part for part in item.status.segments}["latest_decision"]
        self.assertTrue(stale.is_stale, "the fixture is only a proof while it is stale")
        self.assertIs(
            stale.clear_role,
            contract.ClearRole.VITAL,
            "the product still nominates it; the host drops it for being stale, which "
            "is the distinction this proof is about",
        )
        self.assertEqual(clear_keys(item.status), ["mode"])
        self.assertNotIn(stale.label, text(item.status, CLEAR))
        self.assertIn("Observing, not enforcing", text(item.status, CLEAR))
        # Not deleted, just not current: Detail still shows it, with its age.
        self.assertIn(stale.label, text(item.status, DETAIL))
        self.assertIn("1h ago", text(item.status, DETAIL))

    def test_a_would_block_in_shadow_keeps_its_hypothetical_marking_in_clear(self) -> None:
        # The one reading in the whole surface that must never be allowed to read
        # as a thing that happened. Clear drops fields; it may not drop this one.
        item = payload("circinus/shadow_would_block_fresh")
        decision = {part.key: part for part in item.status.segments}["latest_decision"]
        self.assertTrue(decision.hypothetical)
        for depth in DEPTHS:
            for mode in MODES:
                with self.subTest(depth=depth, mode=mode):
                    self.assertIn(
                        render.HYPOTHETICAL_TEXT, text(item.status, depth, mode)
                    )

    def test_an_executed_block_is_not_marked_hypothetical(self) -> None:
        item = payload("circinus/enforce_blocked")
        decision = {part.key: part for part in item.status.segments}["latest_decision"]
        self.assertFalse(decision.hypothetical)
        self.assertNotIn(render.HYPOTHETICAL_TEXT, text(item.status, CLEAR))

    def test_a_pending_restart_outranks_the_routine_decision_reading(self) -> None:
        # A mode the config has changed but the daemon has not adopted is the one
        # Circinus state where the posture is actively misleading, and the product
        # declares it an exception rather than leaving severity to carry it.
        item = payload("circinus/mode_restart_required")
        self.assertIs(roles(item.status)["mode"], contract.ClearRole.EXCEPTION)
        self.assertEqual(clear_keys(item.status), ["mode", "latest_decision"])
        self.assertIn("Restart required", text(item.status, CLEAR))
        self.assertNotIn("Config says enforce", text(item.status, CLEAR))

    def test_disconnected_hooks_take_the_line_and_demote_the_decision(self) -> None:
        # The product's own demotion: with hooks unregistered, nothing is being
        # decided, so `latest_decision` is declared supporting and disappears from
        # Clear rather than sitting beside the exception implying activity.
        item = payload("circinus/hooks_disconnected")
        assigned = roles(item.status)
        self.assertIs(assigned["install"], contract.ClearRole.EXCEPTION)
        self.assertIs(assigned["latest_decision"], contract.ClearRole.SUPPORTING)
        self.assertEqual(clear_keys(item.status), ["install"])
        self.assertIn("Hooks disconnected", text(item.status, CLEAR))
        self.assertNotIn("No decisions yet", text(item.status, CLEAR))

    def test_a_product_that_is_not_answering_shows_exactly_that_and_nothing_else(self) -> None:
        for case in ("not_running", "unreachable"):
            item = payload(f"circinus/{case}")
            with self.subTest(fixture=item.name):
                self.assertFalse(item.status.availability.has_live_readings)
                self.assertEqual(clear_keys(item.status), ["availability"])

    def test_the_product_emits_no_cached_posture_when_it_cannot_read_itself(self) -> None:
        """Proof 4, first half: the product does not offer a cached mode at all.

        This is the stronger of the two halves and the one that belongs to
        Circinus rather than to the host: an unreachable or stopped daemon
        produces a single availability segment, so there is no cached mode or
        decision in the payload for anything downstream to promote.
        """
        for case in ("not_running", "unreachable"):
            item = payload(f"circinus/{case}")
            with self.subTest(fixture=item.name):
                self.assertEqual(detail_keys(item.status), ["availability"])
                for forbidden in ("mode", "latest_decision", "block_window"):
                    self.assertNotIn(forbidden, detail_keys(item.status))

    def test_an_unreachable_runtime_overrides_a_cached_mode_if_one_ever_arrives(self) -> None:
        """Proof 4, second half: the host rule, probed rather than captured.

        No shipped Circinus build emits this payload — the test above is why — so
        this is explicitly a probe: the real `unreachable` availability segment
        spliced onto the real cached segments from `shadow_would_block_fresh`,
        which is what a future build that cached its last known state would send.
        The rule being proven is the host's: an unreachable provider shows one
        reading, and the one it shows is the unreachability, even though `warn`
        outranks `unknown` on severity and the cached decision would win a
        severity contest.
        """
        unreachable = payload("circinus/unreachable")
        cached = payload("circinus/shadow_would_block_fresh")
        probe = copy.deepcopy(unreachable.wire)
        probe["segments"] = list(probe["segments"]) + [
            copy.deepcopy(part)
            for part in cached.wire["segments"]
            if part["key"] in ("mode", "latest_decision")
        ]
        status = contract.provider_status_from_wire(probe)
        self.assertEqual(
            sorted(detail_keys(status)), ["availability", "latest_decision", "mode"]
        )
        self.assertEqual(clear_keys(status), ["availability"])
        rendered = text(status, CLEAR)
        self.assertIn("Not responding", rendered)
        self.assertNotIn("Would block", rendered)
        self.assertNotIn("Observing, not enforcing", rendered)


class LibraClearTest(unittest.TestCase):
    """Proofs 5, 6 and 7 — schedule plus budget, escalation first, nothing while idle."""

    ACTIVE = ("active_with_estimate", "active_long_estimate")

    def test_a_normal_active_task_is_schedule_plus_budget_posture(self) -> None:
        for case in self.ACTIVE + ("active_no_estimate",):
            item = payload(f"libra/{case}")
            with self.subTest(fixture=item.name):
                self.assertEqual(clear_keys(item.status), ["estimate", "budget"])
                assigned = roles(item.status)
                self.assertIs(assigned["estimate"], contract.ClearRole.POSTURE)
                self.assertIs(assigned["budget"], contract.ClearRole.VITAL)
                self.assertIs(assigned["task"], contract.ClearRole.SUPPORTING)

    def test_the_task_identifier_never_takes_the_line_from_the_two_that_matter(self) -> None:
        # `task` and `estimate` are both neutral and adjacent in order, so neither
        # severity nor position separates them; the product's declaration does.
        for case in self.ACTIVE + ("active_no_estimate", "budget_exhausted"):
            item = payload(f"libra/{case}")
            identifier = {part.key: part for part in item.status.segments}["task"].label
            with self.subTest(fixture=item.name):
                self.assertNotIn(identifier, text(item.status, CLEAR))
                self.assertIn(identifier, text(item.status, DETAIL))

    def test_the_schedule_expectation_reaches_clear_with_its_quantile_named(self) -> None:
        # `5d4h` alone would be a span with no claim attached. The Founder's shape
        # is `P90 5d4h`, and the quantile is the part that makes it a commitment.
        expected = {"active_with_estimate": "P90 6s", "active_long_estimate": "P90 5d4h"}
        for case, phrase in expected.items():
            item = payload(f"libra/{case}")
            with self.subTest(fixture=item.name):
                self.assertIn(phrase, text(item.status, CLEAR))
                self.assertIn(phrase, text(item.status, DETAIL))

    def test_the_budget_posture_keeps_the_axis_it_arrived_with(self) -> None:
        # `53%` could mean used or left, which are opposite readings of the same
        # glanced-at number. Every percentage the products emit carries its noun.
        for item in of("libra"):
            for part in item.status.segments:
                if "%" not in part.label:
                    continue
                with self.subTest(fixture=item.name, segment=part.key):
                    self.assertRegex(part.label, r"%\s*\w+")
                    for depth in DEPTHS:
                        self.assertIn(part.label, text(item.status, depth))

    def test_preflight_confidence_stays_in_detail(self) -> None:
        # The Founder's rule: confidence is Detail information unless no stronger
        # authoritative primary exists. Both estimate payloads carry it; neither
        # spends a Clear slot on it.
        for case in self.ACTIVE:
            item = payload(f"libra/{case}")
            estimate = {part.key: part for part in item.status.segments}["estimate"]
            with self.subTest(fixture=item.name):
                self.assertIsNotNone(estimate.confidence)
                self.assertNotIn(estimate.confidence.value, text(item.status, CLEAR))
                detail = text(item.status, DETAIL)
                self.assertIn(estimate.confidence.value, detail)
                # And never as a bare word: a confidence with no subject is a
                # confidence the reader attaches to whatever is nearest.
                self.assertIn("preflight confidence", detail)

    def test_an_exhausted_budget_takes_the_line_from_every_routine_metric(self) -> None:
        item = payload("libra/budget_exhausted")
        self.assertIs(roles(item.status)["budget"], contract.ClearRole.EXCEPTION)
        self.assertEqual(clear_keys(item.status), ["budget"])
        rendered = text(item.status, CLEAR)
        self.assertIn("Budget exhausted", rendered)
        self.assertNotIn("Remaining work", rendered)
        # Still a posture and not a blockage: nothing claims work has stopped.
        self.assertNotIn("Blocked", rendered)
        self.assertNotIn("budget hard limit reached", rendered)

    def test_waiting_on_a_human_leads_and_keeps_the_budget_beside_it(self) -> None:
        # The Founder's `Approval - 12% budget left` shape: an action-required
        # state first, and the one reading that says what the approval costs. The
        # pairing is the product's nomination, which is the whole reason
        # `clear_role: vital` exists beside an exception.
        item = payload("libra/escalated_awaiting_approval")
        assigned = roles(item.status)
        self.assertIs(assigned["task"], contract.ClearRole.EXCEPTION)
        self.assertIs(assigned["budget"], contract.ClearRole.VITAL)
        self.assertEqual(clear_keys(item.status), ["task", "budget"])
        rendered = text(item.status, CLEAR)
        self.assertIn("Replans now need approval", rendered)
        self.assertIn("budget left", rendered)
        self.assertNotIn("Remaining work", rendered)

    def test_an_idle_libra_shows_no_schedule_expectation(self) -> None:
        item = payload("libra/idle")
        self.assertEqual(clear_keys(item.status), ["task"])
        self.assertIn("No task being governed", text(item.status, CLEAR))
        for depth in DEPTHS:
            with self.subTest(depth=depth):
                self.assertNotIn("P90", text(item.status, depth))
                self.assertNotIn("Remaining work", text(item.status, depth))

    def test_an_idle_payload_carries_no_estimate_to_show(self) -> None:
        """The product's half of proof 7, and the one a host test cannot cover.

        A host rule could suppress a schedule expectation while idle; Libra does
        not emit one, which is the stronger property and the one worth pinning
        against the real payload. The bound is the whole payload, not just the
        segment the host happens to read.
        """
        item = payload("libra/idle")
        self.assertEqual(detail_keys(item.status), ["task"])
        for part in item.status.segments:
            self.assertIsNone(part.duration_seconds)
            self.assertIsNone(part.duration_label)
            self.assertIsNone(part.confidence)
        self.assertNotIn("duration_seconds", json.dumps(item.wire))


class CrossDepthTest(unittest.TestCase):
    """Proofs 8 and 10 — Clear is a subset of Detail, and Detail is untouched."""

    def test_detail_shows_every_segment_the_provider_reported(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                keys = [part["key"] for part in item.wire["segments"]]
                self.assertEqual(sorted(detail_keys(item.status)), sorted(keys))
                rendered = text(item.status, DETAIL)
                for part in item.status.segments:
                    self.assertIn(part.label, rendered)

    def test_detail_is_the_identity_projection(self) -> None:
        # Proof 10 at the only place it can be proven rather than asserted about:
        # the depth projection itself. Detail must not be Clear with extras.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                projected = render.project_to_depth((item.status,), DETAIL)
                self.assertEqual(projected, (item.status,))

    def test_every_clear_reading_is_a_segment_detail_shows(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                readings = render.clear_readings(item.status)
                self.assertTrue(readings)
                for part in readings:
                    self.assertIn(part, item.status.segments)
                detail = text(item.status, DETAIL)
                for part in readings:
                    self.assertIn(part.label, detail)

    def test_clear_leads_with_the_same_reading_detail_leads_with(self) -> None:
        # The invariant that makes the two depths one product: whatever else they
        # differ on, the primary state is the same segment, and Clear renders it
        # first rather than re-ranking by severity.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                primary = primary_of(item.status)
                self.assertIsNotNone(primary)
                self.assertEqual(render.clear_readings(item.status)[0], primary)
                name, _, readings = render.provider_parts(item.status, BALANCED, CLEAR)
                self.assertIn(primary.label, readings[0])

    def test_clear_never_says_anything_detail_does_not(self) -> None:
        # Asserted per reading on the supporting phrases rather than on the whole
        # string, because the two depths legitimately render the same reading
        # differently: Clear's `Remaining work (P90 5d4h)` is not a substring of
        # Detail's `Remaining work (P90 5d4h; preflight confidence medium)`. What
        # must hold is that no *phrase* appears in Clear that Detail omits, which
        # is the privacy statement in surface form — switching to Clear can never
        # widen the reviewed surface. `detail_atoms` is the render suite's own
        # reader, imported rather than re-implemented.
        for item in PAYLOADS:
            for part in item.status.segments:
                for mode in MODES:
                    with self.subTest(fixture=item.name, segment=part.key, mode=mode):
                        self.assertLessEqual(
                            detail_atoms(render.render_segment(part, mode, CLEAR)),
                            detail_atoms(render.render_segment(part, mode, DETAIL)),
                        )

    def test_clear_is_never_wider_than_the_detail_it_summarises(self) -> None:
        # The one arithmetic relation between the depths: Clear selects segments
        # and drops fields, with no path that adds anything, so a payload where it
        # came out wider means something was rendered differently rather than less.
        for item in PAYLOADS:
            for mode in MODES:
                with self.subTest(fixture=item.name, mode=mode):
                    self.assertLessEqual(
                        render.display_width(text(item.status, CLEAR, mode)),
                        render.display_width(text(item.status, DETAIL, mode)),
                    )

    def test_clear_holds_at_most_two_readings(self) -> None:
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertLessEqual(
                    len(render.clear_readings(item.status)), render.MAX_CLEAR_READINGS
                )

    def test_summarising_a_summary_changes_nothing(self) -> None:
        # `compose` projects for its width accounting and `render_provider`
        # projects again for its output. The two must not be able to disagree.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                once = render.project_to_depth((item.status,), CLEAR)
                twice = render.project_to_depth(once, CLEAR)
                self.assertEqual(once, twice)

    def test_the_whole_line_carries_every_reading_each_depth_selected(self) -> None:
        # Three real products on one line, which is the only configuration the
        # workstation actually renders. Asserted on labels rather than on the
        # single-product rendering, because `compose` pads provider names into
        # columns in its vertical layout and so is not a substring of the parts.
        for group in LINES:
            statuses = tuple(item.status for item in group)
            for depth in DEPTHS:
                line = render.compose(UPSTREAM, statuses, depth=depth)
                with self.subTest(line=[item.name for item in group], depth=depth):
                    self.assertIn(UPSTREAM, line)
                    for item in group:
                        self.assertIn(
                            render.provider_display_name(item.status.provider), line
                        )
                        shown = (
                            contract.order_segments(item.status)
                            if depth.shows_supporting_detail
                            else render.clear_readings(item.status)
                        )
                        for part in shown:
                            self.assertIn(part.label, line)

    def test_no_reading_on_the_clear_line_is_missing_from_the_detail_line(self) -> None:
        for group in LINES:
            statuses = tuple(item.status for item in group)
            for mode in MODES:
                detail = render.compose(UPSTREAM, statuses, mode=mode, depth=DETAIL)
                for item in group:
                    for part in render.clear_readings(item.status):
                        with self.subTest(fixture=item.name, mode=mode, key=part.key):
                            self.assertIn(part.label, detail)


class NoExtraQueryTest(unittest.TestCase):
    """Proof 9 — neither depth asks a product anything.

    Clear is computed from the snapshot Detail was computed from. A depth that
    cost a provider request would make the cheaper-looking line the expensive
    one, and the user would be paying a subprocess per prompt for a shorter
    reading.
    """

    def no_processes(self):
        def refuse(*args, **kwargs):
            raise AssertionError(
                "rendering asked the system to run something; Clear and Detail are "
                f"both projections of one snapshot: {args!r} {kwargs!r}"
            )

        return unittest.mock.patch.multiple(
            subprocess,
            run=refuse,
            Popen=refuse,
            check_output=refuse,
            call=refuse,
            check_call=refuse,
        )

    def no_provider_calls(self):
        """The compositor's own four doors to a product, all nailed shut.

        Patching `subprocess` alone would leave a cache read or a bounded run
        that had been stubbed elsewhere looking like no call at all, so the
        compositor's entry points are named explicitly: a depth change must not
        re-run a provider, re-run the reader's line, or go back to the cache.
        """

        def refuse(name):
            def inner(*args, **kwargs):
                raise AssertionError(
                    f"rendering called compositor.{name}; a depth is a projection of a "
                    "snapshot already in hand, not a reason to ask a product again"
                )

            return inner

        return unittest.mock.patch.multiple(
            compositor,
            _run_bounded=refuse("_run_bounded"),
            run_provider=refuse("run_provider"),
            run_upstream=refuse("run_upstream"),
            collect=refuse("collect"),
            read_cache=refuse("read_cache"),
        )

    def test_rendering_every_payload_at_every_depth_runs_nothing(self) -> None:
        with self.no_processes():
            with self.no_provider_calls():
                for item in PAYLOADS:
                    for depth in DEPTHS:
                        for mode in MODES:
                            with self.subTest(fixture=item.name, depth=depth, mode=mode):
                                self.assertTrue(text(item.status, depth, mode))

    def test_composing_the_whole_line_at_either_depth_runs_nothing(self) -> None:
        with self.no_processes():
            with self.no_provider_calls():
                for group in LINES:
                    statuses = tuple(item.status for item in group)
                    for depth in DEPTHS:
                        for budget in (None, 120, 40):
                            with self.subTest(depth=depth, budget=budget):
                                self.assertTrue(
                                    render.compose(
                                        UPSTREAM,
                                        statuses,
                                        depth=depth,
                                        width_budget=budget,
                                    )
                                )

    def test_switching_depth_re_reads_nothing(self) -> None:
        # The same snapshot, projected both ways, with no read of anything in
        # between: the switch is arithmetic on data already in hand. This is the
        # whole reason a depth preference can be a stored preference rather than a
        # re-interrogation of every installed product.
        for group in LINES:
            statuses = tuple(item.status for item in group)
            with self.no_processes():
                clear = render.project_to_depth(statuses, CLEAR)
                detail = render.project_to_depth(statuses, DETAIL)
            with self.subTest(line=[item.name for item in group]):
                self.assertEqual(detail, contract.order_providers(statuses))
                self.assertEqual(len(clear), len(detail))


class WidthPressureTest(unittest.TestCase):
    """A declared projection survives a narrow terminal — found by this gate.

    Writing the proofs above surfaced a live defect the hand-written matrices could
    not reach. Under width pressure the host sheds readings and rebuilds each
    shortened provider with `dataclasses.replace`, which re-runs
    `_validate_clear_authority` — and the shed order was lowest severity first, so
    Circinus's `ok` "Enforcing" mode went before the `warn` block counter beside
    it. The remainder was a declared projection with no primary, which the contract
    refuses, so every budget narrow enough to shed the mode raised
    `ContractViolation` from inside the renderer instead of producing a short line.
    It reproduced at every budget at or below 60 columns, at both depths, from the
    real three-product payload set — a regression that arrived the moment all three
    products started declaring authority, which is to say in the state this whole
    campaign was shipping.

    The fix is an exception to the shed order rather than a relaxation of the
    contract: a declared primary is shed only once the rest of that provider's
    readings are gone. That keeps all four validation rules enforced on every
    rebuilt snapshot, and it is the better line anyway — a surviving product shows
    its mode rather than a counter whose mode has been hidden.
    """

    # Chosen to straddle the layouts rather than to sample evenly: 200 and 120 are
    # roomy, 80 and 60 are where the vertical Detail layout gives way, and
    # everything below 50 is the shedding ladder. 36 is pinned because it is the
    # exact width at which Circinus reduces to a single reading, which is the
    # defect's own width.
    BUDGETS = (None, 200, 120, 80, 60, 50, 40, 36, 30, 24, 20)

    def test_every_budget_renders_a_declared_projection_instead_of_refusing_it(
        self,
    ) -> None:
        for group in LINES:
            statuses = tuple(item.status for item in group)
            names = [item.name for item in group]
            for depth in DEPTHS:
                for mode in MODES:
                    for budget in self.BUDGETS:
                        with self.subTest(line=names, depth=depth, mode=mode, width=budget):
                            # Not `assertTrue`: below about 20 columns every rung
                            # declines and the empty block is the correct answer.
                            # The claim here is that it renders at all.
                            self.assertIsInstance(
                                render.compose(
                                    UPSTREAM,
                                    statuses,
                                    mode=mode,
                                    width_budget=budget,
                                    depth=depth,
                                ),
                                str,
                            )

    def test_the_block_stays_inside_the_budget_it_was_given(self) -> None:
        # With no upstream text the whole line is the Horonom block, so the
        # documented budget — ours to spend, never the reader's — is measurable
        # directly. A rung that overflowed would be hiding readings *and* wrapping.
        for group in LINES:
            statuses = tuple(item.status for item in group)
            for depth in DEPTHS:
                for budget in self.BUDGETS:
                    if budget is None:
                        continue
                    block = render.compose(
                        "", statuses, width_budget=budget, depth=depth
                    )
                    for row in block.split("\n"):
                        with self.subTest(depth=depth, width=budget, row=row):
                            self.assertLessEqual(render.display_width(row), budget)

    def test_a_shed_product_keeps_the_mode_it_declared_not_its_counter(self) -> None:
        # The defect's own case, pinned as text at the width where it bit. Severity
        # order would keep `Blocked` — the critical reading — and drop the mode,
        # which is both the crash and the wrong line: "Circinus blocked something"
        # without "Circinus is enforcing" reads as a product that may or may not be
        # running. One reading is the mode.
        item = payload("circinus/enforce_blocked")
        line = render.compose("", (item.status,), width_budget=36, depth=CLEAR)
        self.assertEqual(line, "Circinus 💻 ✅ Enforcing · [+1 more]")
        self.assertNotIn("Blocked", line)

    def test_the_ladder_never_hands_the_contract_a_projection_with_no_primary(
        self,
    ) -> None:
        # The invariant itself, watched where it is produced. Every intermediate
        # the shedding ladder builds is recorded, and each surviving provider that
        # declared a primary must still have one — which is exactly the condition
        # `_validate_clear_authority` rule 4 checks, so a remainder that failed it
        # would have raised on construction rather than reached this assertion.
        seen: list = []
        real = render._without_dropped

        def record(statuses, dropped):
            remaining = real(statuses, dropped)
            seen.append(remaining)
            return remaining

        with unittest.mock.patch.object(render, "_without_dropped", record):
            for group in LINES:
                statuses = tuple(item.status for item in group)
                for depth in DEPTHS:
                    for budget in (60, 50, 40, 36, 30):
                        render.compose("", statuses, width_budget=budget, depth=depth)

        self.assertTrue(seen, "the shedding ladder never ran, so this proved nothing")
        for remaining in seen:
            for status in remaining:
                with self.subTest(provider=status.provider, keys=detail_keys(status)):
                    self.assertTrue(status.segments)
                    if status.clear_authority is contract.ClearAuthority.PROVIDER:
                        self.assertTrue(
                            [
                                segment
                                for segment in status.segments
                                if segment.clear_role
                                in (contract.ClearRole.EXCEPTION, contract.ClearRole.POSTURE)
                            ],
                            "a shed remainder kept only supporting readings",
                        )

    def test_a_product_that_declares_nothing_is_shed_in_the_order_handed_in(
        self,
    ) -> None:
        # The other half of the fix: it is a no-op for providers that declared no
        # primary, because there is none to strand. Stated as the shed order rather
        # than as rendered text, so it cannot drift into agreeing by coincidence —
        # with no declarations the ladder takes candidates straight down the order
        # it was handed, skipping nothing, exactly as it did before the exception
        # existed. The order given here is the contract's rather than the caller's
        # severity sort precisely to show that this function does not re-rank: it
        # defers, or it takes the next one.
        statuses = tuple(
            contract.provider_status_from_wire(rewire(item, _strip_declaration))
            for item in LINES[0]
        )
        order = [
            (p_index, s_index, segment)
            for p_index, status in enumerate(statuses)
            for s_index, segment in enumerate(status.segments)
        ]
        dropped: set = set()
        for expected in order:
            candidate = render._next_droppable(statuses, order, dropped)
            self.assertEqual(candidate, (expected[0], expected[1]))
            dropped.add(candidate)


class RefusalTest(unittest.TestCase):
    """What the host must refuse rather than quietly re-infer.

    Each probe starts from a real payload and changes one thing, because the
    interesting failure is a product that regresses slightly — a role dropped
    from one segment of four — rather than one that sends nonsense. The contract
    raises instead of falling back, so the symptom is *this provider* reading as
    unreadable, which is visible and fixable. A silent re-inference looks exactly
    like success.

    Each probe builds its regressed payload *before* entering `assertRaises`, so
    the refusal provably comes from parsing it rather than from an accident in the
    edit that produced it. Inside the block, a `rewire` that raised would read as
    the proof.
    """

    SUBJECT = "circinus/shadow_would_block_fresh"

    def test_a_declaring_payload_that_omits_one_role_is_refused(self) -> None:
        item = payload(self.SUBJECT)

        def drop_one(wire: dict) -> None:
            for part in wire["segments"]:
                if part["key"] == "latest_decision":
                    del part["clear_role"]

        regressed = rewire(item, drop_one)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        self.assertIn("clear_role on every segment", str(caught.exception))
        self.assertIn("latest_decision", str(caught.exception))

    def test_a_declaring_payload_with_two_postures_is_refused(self) -> None:
        item = payload(self.SUBJECT)

        def two_postures(wire: dict) -> None:
            for part in wire["segments"]:
                if part["key"] in ("mode", "latest_decision"):
                    part["clear_role"] = "posture"

        regressed = rewire(item, two_postures)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        self.assertIn("at most one 'posture'", str(caught.exception))

    def test_a_declaring_payload_that_names_no_primary_is_refused(self) -> None:
        item = payload(self.SUBJECT)

        def all_supporting(wire: dict) -> None:
            for part in wire["segments"]:
                part["clear_role"] = "supporting"

        regressed = rewire(item, all_supporting)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        self.assertIn("requires a 'posture' or 'exception'", str(caught.exception))

    def test_a_declaring_payload_with_no_segments_is_refused(self) -> None:
        item = payload(self.SUBJECT)

        def empty(wire: dict) -> None:
            wire["segments"] = []

        regressed = rewire(item, empty)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        self.assertIn("at least one segment", str(caught.exception))

    def test_a_percentage_that_loses_its_axis_is_refused(self) -> None:
        # Libra's budget posture carries its noun inside the label, so the host
        # cannot restore it once a product drops it — which is why the contract
        # refuses the payload instead of rendering the ambiguity.
        item = payload("libra/active_with_estimate")

        def bare_percentage(wire: dict) -> None:
            for part in wire["segments"]:
                if part["key"] == "budget":
                    part["label"] = "53%"

        regressed = rewire(item, bare_percentage)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        self.assertIn("percentage with no word", str(caught.exception))

    def test_an_unknown_role_is_refused_rather_than_degraded(self) -> None:
        # `clear_role` is the one enum where degrading to a default would be
        # indistinguishable from the product not having declared at all.
        item = payload(self.SUBJECT)

        def invented(wire: dict) -> None:
            wire["segments"][0]["clear_role"] = "headline"

        regressed = rewire(item, invented)
        with self.assertRaises(contract.ContractViolation) as caught:
            contract.provider_status_from_wire(regressed)
        # The value is named, not just refused. Before this was asserted the
        # refusal came from rule 2 and read "missing on ['install']", which tells
        # a product author their payload omitted a field it plainly carries.
        self.assertIn("headline", str(caught.exception))
        self.assertIn("this host can read", str(caught.exception))

    def test_an_unchanged_payload_still_parses(self) -> None:
        # The control. Every probe above is one edit away from this, so if this
        # ever stops passing the probes are proving nothing about the edit.
        for item in PAYLOADS:
            with self.subTest(fixture=item.name):
                self.assertEqual(
                    contract.provider_status_from_wire(copy.deepcopy(item.wire)),
                    item.status,
                )


if __name__ == "__main__":
    unittest.main()
