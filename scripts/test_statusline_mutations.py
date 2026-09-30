"""Deliberate destructive implementations, and the guards that must catch them.

Every other test in this directory asserts that the statusline behaves. That is
not the same as asserting the tests would notice if it stopped. A guard can pass
because the code is right, or because the path it guards never runs -- and the
second kind is worse than no guard at all, because it reports a safety it cannot
actually see.

So each case here breaks the implementation on purpose, in one specific way the
product could plausibly have been written, and proves a *permanent* test fails.
The proof has three parts, all of which `assert_guard_catches` insists on:

1. the guard passes with the implementation intact, so a later failure is the
   mutation's doing and not the fixture's;
2. the guard fails with the mutation in place;
3. the failure text contains the fragment the case names, so the guard failed at
   the assertion meant to catch *this* defect and not somewhere incidental.

Part 3 is what makes the rest worth anything. It is also why one case below
asserts that a guard does *not* catch a mutation: the obvious-looking test for
the self-reference defect passes with that defect in place, and the next person
to reach for it deserves to find that written down rather than rediscover it.

The mutations patch the real modules rather than editing a copy. A fixture built
around a mutated copy of the source would prove something about the copy.

The defects covered are the seven the release gate is required to demonstrate:
whole-object `statusLine` replacement dropping unknown keys; whole-file settings
restore deleting a key the user added later; removing one provider taking the
others with it; wrapper recursion; a provider's timeout blocking the user's own
line; a missing product reported as healthy; and a secret-shaped field reaching
the rendered line.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_lifecycle as lifecycle
import statusline_render as render
import test_statusline_compositor as compositor_tests
import test_statusline_contract as contract_tests
import test_statusline_release_gate as gate

# Guards proven clean once per run. The clean run exists to establish that a
# failure under mutation is the mutation's doing; re-establishing that for every
# case that shares a guard would only cost wall clock.
_PROVEN_CLEAN: set[str] = set()


def _identify(case: type[unittest.TestCase], name: str) -> str:
    return f"{case.__module__}.{case.__qualname__}.{name}"


def _run(case: type[unittest.TestCase], name: str) -> unittest.TestResult:
    """Run exactly one existing test and report what happened to it.

    `maxDiff` is lifted for the duration because the reason a guard failed is the
    evidence this module collects, and a truncated dict diff can elide the very
    key whose disappearance is the defect.
    """
    suite = unittest.TestLoader().loadTestsFromName(name, case)
    result = unittest.TestResult()
    with unittest.mock.patch.object(unittest.TestCase, "maxDiff", None):
        suite.run(result)
    return result


def _diagnosis(result: unittest.TestResult) -> str:
    """Everything the run said about why it failed.

    Errors count as failures here. A mutation that makes a guard raise a
    `KeyError` naming the key it deleted has been caught by that guard just as
    surely as one that trips an assertion.
    """
    return "\n".join(text for _, text in result.failures + result.errors)


class MutationCase(unittest.TestCase):
    """The three-part proof, and the one case that inverts it."""

    def assert_guard_catches(
        self,
        case: type[unittest.TestCase],
        name: str,
        mutation: object,
        *,
        expect: str,
    ) -> None:
        identity = _identify(case, name)
        if identity not in _PROVEN_CLEAN:
            clean = _run(case, name)
            self.assertTrue(
                clean.wasSuccessful(),
                f"{identity} does not pass with the implementation intact, so it cannot "
                f"prove anything about a mutation:\n{_diagnosis(clean)}",
            )
            _PROVEN_CLEAN.add(identity)

        with mutation:
            mutated = _run(case, name)

        self.assertFalse(
            mutated.wasSuccessful(),
            f"{identity} passed with the defect in place. Either the guard never reaches "
            "the mutated path, or it does not assert what it appears to.",
        )
        diagnosis = _diagnosis(mutated)
        self.assertIn(
            expect,
            diagnosis,
            f"{identity} failed, but not at the assertion this defect is supposed to "
            f"trip:\n{diagnosis}",
        )

    def assert_guard_misses(
        self,
        case: type[unittest.TestCase],
        name: str,
        mutation: object,
        *,
        because: str,
    ) -> None:
        """Record that a plausible-looking guard does not catch this defect.

        Asserted rather than left as a comment so that it stays true. If someone
        later strengthens the named test until it does catch the defect, this
        case fails and points at `because` -- at which point the right move is to
        promote it to `assert_guard_catches` and delete this one.
        """
        with mutation:
            mutated = _run(case, name)
        self.assertTrue(
            mutated.wasSuccessful(),
            f"{_identify(case, name)} now catches this defect, which it did not before. "
            f"It was excluded because {because}\n{_diagnosis(mutated)}",
        )


# --------------------------------------------------------------------------
# The mutations. Each is written the way the defect would plausibly have been
# implemented, not as a contrived break, because a contrived break can be caught
# by a guard that would miss the real thing.
# --------------------------------------------------------------------------


def rebuilding_the_status_line_object() -> object:
    """`statusLine` written from what this version knows, instead of patched.

    The tempting shape: we know a statusline needs a type and a command, so build
    one. Everything else in the object -- the user's padding, the field a later
    Claude Code release added -- is not in our schema and so is not written.
    """
    real = lifecycle._taking_the_slot

    def mutated(document, ownership, registry):
        _, upstream, created, changes = real(document, ownership, registry)
        return {"type": lifecycle.SUPPORTED_STATUS_LINE_TYPE}, upstream, created, changes

    return unittest.mock.patch.object(lifecycle, "_taking_the_slot", mutated)


@contextlib.contextmanager
def restoring_the_file_we_saw_at_install_time():
    """Uninstall puts back the settings document install remembered.

    The shape a backup invites: keep what was there, write it back at the end.
    It is wrong because the user kept using the tool in between, so "what was
    there" is a stale copy of a file that has moved on.
    """
    snapshot: dict = {}
    real_plan = lifecycle.plan_enable
    real_release = lifecycle._giving_the_slot_back

    def remembering(document, registry, registration, **kwargs):
        snapshot.setdefault("data", dict(document.data))
        return real_plan(document, registry, registration, **kwargs)

    def restoring(document, ownership, registry):
        settings_after, state, changes, notes = real_release(document, ownership, registry)
        if settings_after is None or "data" not in snapshot:
            return settings_after, state, changes, notes
        restored = dict(snapshot["data"])
        if lifecycle.STATUS_LINE_KEY in settings_after:
            restored[lifecycle.STATUS_LINE_KEY] = settings_after[lifecycle.STATUS_LINE_KEY]
        else:
            restored.pop(lifecycle.STATUS_LINE_KEY, None)
        return restored, state, changes, notes

    with unittest.mock.patch.object(lifecycle, "plan_enable", remembering):
        with unittest.mock.patch.object(lifecycle, "_giving_the_slot_back", restoring):
            yield


def normalising_away_keys_we_do_not_know() -> object:
    """Uninstall writes back only the keys this version's schema lists.

    Distinct from the snapshot defect above and worse in one way: it needs no
    stale copy to lose data, so it loses the same data on a fresh install.
    """
    known = frozenset(
        {
            "model",
            "theme",
            "cleanupPeriodDays",
            "skipWorkflowUsageWarning",
            "env",
            "permissions",
            "hooks",
            "managedSettingsSource",
            lifecycle.STATUS_LINE_KEY,
        }
    )
    real = lifecycle._giving_the_slot_back

    def mutated(document, ownership, registry):
        settings_after, state, changes, notes = real(document, ownership, registry)
        if settings_after is None:
            return settings_after, state, changes, notes
        return (
            {key: value for key, value in settings_after.items() if key in known},
            state,
            changes,
            notes,
        )

    return unittest.mock.patch.object(lifecycle, "_giving_the_slot_back", mutated)


def emptying_the_registry_on_any_removal() -> object:
    """Removing a provider rewrites the provider list as empty.

    The off-by-one-concept defect: "remove the providers" read as the list rather
    than the named entries. Every other product the user enabled goes with it.
    """
    real = lifecycle.plan_remove

    def mutated(document, registry, *, providers, operation="disable"):
        plan = real(document, registry, providers=providers, operation=operation)
        if plan.registry_after is None:
            return plan
        return dataclasses.replace(
            plan, registry_after=dict(plan.registry_after) | {"providers": []}
        )

    return unittest.mock.patch.object(lifecycle, "plan_remove", mutated)


def a_child_environment_that_does_not_count() -> object:
    """Children inherit our environment unchanged, marker and all.

    Which is to say the marker never increments, so a compositor invoked below a
    compositor cannot tell it is the second one.
    """
    return unittest.mock.patch.object(compositor, "child_env", lambda: dict(os.environ))


def a_self_reference_check_that_never_matches() -> object:
    """The second recursion guard, answering no to everything.
    """
    return unittest.mock.patch.object(compositor, "names_this_command", lambda command: False)


def abandoning_the_line_when_a_provider_is_late() -> object:
    """A provider that misses its bound takes the whole render down with it.

    The defensible-sounding version of the defect: we are out of time, so print
    nothing rather than something half-formed. It discards the user's own line,
    which was never ours to be late with.
    """
    real = compositor.collect
    late = {"probe_timeout", "deadline_exceeded"}

    def mutated(registry, payload, home=None):
        upstream, statuses = real(registry, payload, home)
        if any(
            segment.reason_code in late for status in statuses for segment in status.segments
        ):
            return "", statuses
        return upstream, statuses

    return unittest.mock.patch.object(compositor, "collect", mutated)


def running_the_providers_one_after_another() -> object:
    """Providers probed in sequence, each with its own full timeout.

    No overall deadline either, because there is nothing left for one to bound:
    the render now costs the sum of every enabled product's worst case, so it
    gets slower every time the user adopts another one.
    """

    def mutated(registry, payload, home=None):
        upstream = (
            compositor.run_upstream(
                registry.upstream_command, payload, registry.upstream_timeout_ms
            )
            if registry.upstream_command
            else ""
        )
        statuses = tuple(
            compositor.run_provider(entry, entry.timeout_ms, home)
            for entry in registry.providers
        )
        return upstream, statuses

    return unittest.mock.patch.object(compositor, "collect", mutated)


def waiting_for_a_provider_however_long_it_takes() -> object:
    """The time bound removed from provider probes, and only those.

    The user's own command keeps its bound, so this isolates the claim being
    tested: that the *provider* timeout is what stops a wedged product from
    holding the line, rather than something else happening to be prompt.
    """
    real = compositor._run_bounded

    def mutated(command, payload, timeout_ms, *, shell):
        return real(command, payload, timeout_ms if shell else 10**9, shell=shell)

    return unittest.mock.patch.object(compositor, "_run_bounded", mutated)


def reporting_a_product_that_did_not_answer_as_healthy() -> object:
    """A probe that failed is written up as an available, all-clear reading.

    Built directly rather than through `contract.not_available`, because that
    constructor refuses to build an available status -- which is the guard this
    mutation exists to point at.
    """

    def mutated(entry, availability, reason_code, reason_label):
        return contract.ProviderStatus(
            provider=entry.provider,
            provider_version="unknown",
            scope=entry.scope,
            availability=contract.Availability.AVAILABLE,
            segments=(
                contract.Segment(
                    key="latest_verdict", state=contract.SegmentState.OK, label="Verified"
                ),
            ),
        )

    return unittest.mock.patch.object(compositor, "_host_not_available", mutated)


def trusting_that_prose_is_prose() -> object:
    """The credential-shape check removed, leaving the character allowlist.

    The plausible reasoning: the allowlist already rejects punctuation, and a
    credential has punctuation in it. Opaque tokens do not.
    """
    return unittest.mock.patch.object(
        contract, "assert_no_secret_shape", lambda value, field: value
    )


def trusting_our_own_products_labels() -> object:
    """Labels from a Horonom provider accepted as they arrive.

    The reasoning that makes it feel safe -- we wrote the products, so their
    labels are fine -- is exactly what makes it dangerous: the label is whatever
    the product found, and what it found came from the user's repository.
    """
    return unittest.mock.patch.object(
        contract, "require_safe_label", lambda value, field, max_chars=None: value
    )


# --------------------------------------------------------------------------
# The seven defects.
# --------------------------------------------------------------------------


class HarnessTest(MutationCase):
    """The harness is the thing everything below trusts, so it is checked too.

    Every case in this module amounts to an argument that a guard is not vacuous.
    That argument is only as good as the machinery making it, and a harness that
    reported success unconditionally would produce a module of green tests
    asserting nothing at all.
    """

    GUARD = (compositor_tests.TestSelfReferenceDetection, "test_this_very_file_is_recognised")

    def test_a_mutation_that_changes_nothing_is_reported_as_vacuous(self) -> None:
        # Built outside the block, so the failure it catches can only have come
        # from the assertion under test and not from the setup.
        no_mutation = contextlib.nullcontext()
        with self.assertRaises(AssertionError) as caught:
            self.assert_guard_catches(*self.GUARD, no_mutation, expect="False is not true")
        self.assertIn("passed with the defect in place", str(caught.exception))

    def test_a_guard_failing_for_the_wrong_reason_is_not_accepted(self) -> None:
        mutation = a_self_reference_check_that_never_matches()
        with self.assertRaises(AssertionError) as caught:
            self.assert_guard_catches(
                *self.GUARD, mutation, expect="a reason this failure does not have"
            )
        self.assertIn("not at the assertion this defect is supposed to trip", str(caught.exception))


class WholeObjectReplacementTest(MutationCase):
    def test_dropping_unknown_status_line_keys_is_caught(self) -> None:
        self.assert_guard_catches(
            gate.CaseTUnknownStatusLineFieldsTest,
            "test_fields_this_version_has_never_heard_of_survive_everything",
            rebuilding_the_status_line_object(),
            expect="aScalarWeDoNotKnow",
        )


class WholeFileRestoreTest(MutationCase):
    GUARD = "test_a_user_editing_unrelated_settings_while_installed"

    def test_restoring_a_stale_copy_of_the_file_is_caught(self) -> None:
        # The guard trips on the resurrected value rather than the lost key,
        # which is the more alarming half: a stale restore does not merely forget
        # a later edit, it reverts one.
        self.assert_guard_catches(
            gate.CaseHUnrelatedSettingsChangeTest,
            self.GUARD,
            restoring_the_file_we_saw_at_install_time(),
            expect="'claude-opus-4' != 'claude-sonnet-5'",
        )

    def test_deleting_a_key_the_user_added_later_is_caught(self) -> None:
        self.assert_guard_catches(
            gate.CaseHUnrelatedSettingsChangeTest,
            self.GUARD,
            normalising_away_keys_we_do_not_know(),
            expect="aBrandNewKeyClaudeAddedLater",
        )


class ProviderRemovalTest(MutationCase):
    def test_taking_the_other_products_with_it_is_caught(self) -> None:
        self.assert_guard_catches(
            gate.CaseFDisableMiddleProviderTest,
            "test_disabling_the_middle_provider_leaves_the_outer_two",
            emptying_the_registry_on_any_removal(),
            expect="() != ('fornax', 'libra')",
        )


class WrapperRecursionTest(MutationCase):
    """Two independent guards, because there are two independent mechanisms.

    The depth marker bounds recursion that has already started; the
    self-reference check declines to start it. Each is proven separately, and
    deliberately not together: with both removed the compositor would spawn
    itself without limit, and a fork bomb is not a test.
    """

    def test_a_marker_that_never_increments_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestMain,
            "test_the_depth_marker_is_passed_to_children",
            a_child_environment_that_does_not_count(),
            expect="!= '1'",
        )

    def test_a_self_reference_check_that_never_fires_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestSelfReferenceDetection,
            "test_this_very_file_is_recognised",
            a_self_reference_check_that_never_matches(),
            expect="False is not true",
        )

    def test_the_end_to_end_test_is_not_what_catches_a_broken_self_reference_check(
        self,
    ) -> None:
        # Named for what it guards against: reaching for the test whose *name*
        # matches the defect. It asserts that nothing of ours crashes onto the
        # line, and nothing does -- the compositor is not directly executable, so
        # a shell handed its path produces no output either way, and a child that
        # did start would find the depth marker already set. Neither fact has
        # anything to do with the check being mutated here.
        self.assert_guard_misses(
            compositor_tests.TestUpstreamIsNeverOurs,
            "test_we_never_run_a_command_that_names_this_script",
            a_self_reference_check_that_never_matches(),
            because=(
                "it asserts the absence of a traceback, which the depth marker and the "
                "file's own mode already ensure, so it cannot speak for the "
                "self-reference check."
            ),
        )


class ProviderTimeoutTest(MutationCase):
    """Three ways a slow product can reach the user's line, one at a time."""

    def test_discarding_their_line_when_we_are_late_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestUpstreamIsNeverOurs,
            "test_their_line_is_a_verbatim_prefix_when_every_provider_fails",
            abandoning_the_line_when_a_provider_is_late(),
            expect='out.startswith("~/proj',
        )

    def test_serialising_independent_providers_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestCollect,
            "test_independent_providers_run_concurrently",
            running_the_providers_one_after_another(),
            expect="took about as long as the sum",
        )

    def test_not_bounding_a_provider_at_all_is_caught(self) -> None:
        """The bound itself, which the two cases above take for granted.

        The fixture's hang is shortened for this case alone. Unmutated the bound
        fires in a fifth of a second and the hang's length is irrelevant; mutated
        the run waits the hang out, and waiting out the fixture's usual half
        minute would buy nothing -- three seconds is still fifteen times the
        bound being tested, so the assertion discriminates exactly as well. Both
        runs use the shortened fixture, so the comparison is like for like.
        """
        with unittest.mock.patch.object(compositor_tests, "LEAK_SLEEP", "3"):
            self.assert_guard_catches(
                compositor_tests.TestRunProvider,
                "test_a_hanging_provider_is_reported_as_a_timeout",
                waiting_for_a_provider_however_long_it_takes(),
                expect="'probe_timeout' not found in",
            )


class MissingProductTest(MutationCase):
    def test_an_absent_product_reported_as_installed_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestRunProvider,
            "test_a_missing_executable_is_reported_as_not_installed",
            reporting_a_product_that_did_not_answer_as_healthy(),
            expect="is not <Availability.UNSUPPORTED",
        )

    def test_the_constructor_refusing_to_claim_health_is_load_bearing(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestHostSynthesisedStatuses,
            "test_a_synthesised_status_never_claims_to_be_available",
            reporting_a_product_that_did_not_answer_as_healthy(),
            expect="ContractViolation not raised",
        )


class SecretShapedFieldTest(MutationCase):
    # Assembled at runtime rather than written as a literal, so this file cannot
    # trip a secret scanner or a push-protection rule. Pure alphanumerics mixing
    # case and digits: the shape the character allowlist has no opinion about.
    OPAQUE = "aB3dE5fG7hJ9kL1" + "mN3pQ5"

    def test_removing_the_credential_shape_check_is_caught(self) -> None:
        self.assert_guard_catches(
            contract_tests.SegmentTest,
            "test_a_secret_shaped_label_cannot_enter_a_segment",
            trusting_that_prose_is_prose(),
            expect="PrivacyViolation not raised",
        )

    def test_the_value_that_check_stops_would_reach_the_terminal(self) -> None:
        """What the guard above is actually protecting, stated once.

        Without it the value does not merely pass validation -- it renders. Worth
        asserting because "a contract check stopped raising" and "a credential
        appeared on the founder's statusline" are the same finding, and only the
        second one reads as serious.
        """
        with trusting_that_prose_is_prose():
            status = contract.ProviderStatus(
                provider="fornax",
                provider_version="0.4.1",
                scope=contract.Scope.HOST,
                availability=contract.Availability.AVAILABLE,
                segments=(
                    contract.Segment(
                        key="latest_verdict",
                        state=contract.SegmentState.OK,
                        label=self.OPAQUE,
                    ),
                ),
            )
            rendered = render.render_provider(status, render.PresentationMode.BALANCED)
        self.assertIn(self.OPAQUE, rendered)

        # And with the check in place it never gets as far as a status to render.
        with self.assertRaises(contract.PrivacyViolation):
            contract.Segment(
                key="latest_verdict", state=contract.SegmentState.OK, label=self.OPAQUE
            )

    def test_skipping_label_validation_for_our_own_products_is_caught(self) -> None:
        self.assert_guard_catches(
            compositor_tests.TestNothingLeaksAndNothingLeaksOut,
            "test_a_secret_shaped_label_is_refused_outright",
            trusting_our_own_products_labels(),
            expect=compositor_tests.TestNothingLeaksAndNothingLeaksOut.CANARY,
        )


if __name__ == "__main__":
    unittest.main()
