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

Then the thirteen the information-depth gate is required to demonstrate
(HORO-1627), which are a different kind of defect: nothing crashes, nothing is
destroyed, and the line still looks like a statusline. Clear and detail
disagreeing about a product's state; detail costing an extra provider request;
detail showing a field no depth is allowed to show; clear losing the product's
name, its unavailability, its enforcement marker, its reason, or its confidence
semantics; clear choosing a routine reading over an escalation; and a mode
switch writing the host settings file, editing the reader's own statusline
script, restarting a daemon, or having an unrelated operation reset the stored
preference. Each of those renders something a reader would accept, which is why
they need a test that does not.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib
import shlex
import subprocess
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_lifecycle as lifecycle
import statusline_render as render
import test_statusline_compositor as compositor_tests
import test_statusline_contract as contract_tests
import test_statusline_depth_gate as depth_gate
import test_statusline_lifecycle as lifecycle_tests
import test_statusline_performance as performance_tests
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

    A lambda rather than `return_value=`, deliberately: this reads the environment
    when the mutated function is called, as the real one does, where a
    `return_value` would snapshot it when the patch was installed.
    """
    return unittest.mock.patch.object(compositor, "child_env", lambda: dict(os.environ))


def a_self_reference_check_that_never_matches() -> object:
    """The second recursion guard, answering no to everything.

    A lambda rather than `return_value=False` so the substitute still takes
    exactly one argument. A mock would accept any call at all, and a defect that
    changed how this guard is called would then go unnoticed here.
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
# The information-depth mutations (HORO-1627).
#
# "Clear and detail are two readings of one snapshot" is a property with four
# owners: the renderer decides what a depth says about a reading, the clear
# projection decides which readings survive, the compositor decides what a render
# costs, and the lifecycle decides what switching is allowed to touch. Every
# mutation below breaks it in exactly one of those four places, because a defect
# that broke two would be caught by whichever guard fired first and would prove
# nothing about the other.
# --------------------------------------------------------------------------


def summarising_the_state_in_clears_own_words() -> object:
    """Clear states the state in its own vocabulary instead of the provider's.

    The shape a shorter summary invites: the state ladder is already a vocabulary
    a reader understands, so say the state and skip the prose. It reads perfectly
    well -- and it is a second state engine. The provider said `Verified`, the
    summary says `Ok`, and the two depths are now describing the same snapshot
    differently.
    """
    real = render.render_segment

    def mutated(segment, mode, depth=render.InformationDepth.DETAIL):
        if depth.shows_supporting_detail:
            return real(segment, mode, depth)
        state = render._enum_value(segment.state)
        return f"{render.state_marker(state, mode)} {render.STATE_TEXT[state].title()}"

    return unittest.mock.patch.object(render, "render_segment", mutated)


def a_summary_that_does_not_name_the_product() -> object:
    """The product's name dropped from the summary, where columns are scarce.

    The most tempting width saving there is, and the reason the founder's own
    screenshot prompted this ticket: on a line with three products, `🛡 Verified`
    is a sentence with the subject removed.
    """
    real = render.provider_parts

    def mutated(status, mode, depth=render.InformationDepth.DETAIL):
        name, scope, readings = real(status, mode, depth)
        if depth.shows_supporting_detail:
            return name, scope, readings
        return "", scope, readings

    return unittest.mock.patch.object(render, "provider_parts", mutated)


def treating_availability_as_one_reading_among_the_rest() -> object:
    """The unavailability rule removed, leaving severity to choose.

    Stated as a simplification rather than as a break, which is how it would
    arrive: a provider that cannot be read reports that as a segment like any
    other, so let the ladder rank it. What the ladder then does is prefer the
    cached `warn` posture over the `unknown` availability -- and the line tells
    the reader their tool calls are being evaluated by a daemon that is not
    answering.
    """
    return unittest.mock.patch.object(render, "_has_live_readings", lambda status: True)


def dropping_the_hypothetical_marker_from_the_summary() -> object:
    """`[NOT ENFORCED]` treated as a detail-depth aside.

    Eleven columns and a bracketed shout, on the line with the least room for it.
    The defect is that removing it does not make the reading shorter, it makes it
    a different reading: `Would have blocked` without the marker is an executed
    block.
    """
    real = render.render_segment
    aside = f" [{render.HYPOTHETICAL_TEXT}]"

    def mutated(segment, mode, depth=render.InformationDepth.DETAIL):
        text = real(segment, mode, depth)
        if depth.shows_supporting_detail:
            return text
        return text.replace(aside, "")

    return unittest.mock.patch.object(render, "render_segment", mutated)


def explaining_an_unrecorded_reason_helpfully() -> object:
    """`reason_not_recorded` expanded into prose about the evidence.

    The honest phrase is kept, which is what makes this the realistic version of
    the fabrication: nobody deletes the truth, they annotate it. "Not recorded" is
    a fact about Fornax's bookkeeping; "insufficient evidence" is a finding about
    the claim, and the provider made no such finding.
    """
    real = render.format_reason
    prose = {
        "reason_not_recorded": "Evidence not recorded, so there is insufficient evidence to verify",
    }

    def mutated(reason_code, reason_label):
        if not reason_label and reason_code in prose:
            return prose[reason_code]
        return real(reason_code, reason_label)

    return unittest.mock.patch.object(render, "format_reason", mutated)


def a_confidence_without_its_subject() -> object:
    """The confidence value rendered bare, with the noun it qualifies dropped.

    `preflight confidence high` costs twenty columns to say one word, so the word
    goes in alone -- next to a state marker, where `high` reads as severity or
    priority. The ticket names this one directly: high, medium and low must remain
    attributable to preflight confidence and not to risk.
    """
    return unittest.mock.patch.object(
        render, "format_confidence", lambda confidence, confidence_of, mode: confidence
    )


def keeping_the_confidence_in_the_summary() -> object:
    """The same word, arriving at the depth that has no room to qualify it.

    The other half of the confidence defect, and independent of it: this one keeps
    the subject and moves the phrase into clear. Worth proving separately because
    `render_segment` gates the confidence field and the projection gates the
    reading, so a guard on one says nothing about the other.
    """
    real = render.render_segment

    def mutated(segment, mode, depth=render.InformationDepth.DETAIL):
        text = real(segment, mode, depth)
        confidence = render._enum_value(getattr(segment, "confidence", None))
        if depth.shows_supporting_detail or confidence is None:
            return text
        phrase = render.format_confidence(
            confidence, render._enum_value(getattr(segment, "confidence_of", None)), mode
        )
        if text.endswith(")"):
            return f"{text[:-1]}{render.DETAIL_SEPARATOR}{phrase})"
        return f"{text} ({phrase})"

    return unittest.mock.patch.object(render, "render_segment", mutated)


def summarising_the_declared_posture_and_ignoring_the_escalation() -> object:
    """The provider's declared posture taken as the summary, always.

    The most defensible-sounding of these: the provider knows its own product, it
    declared which reading is its posture, so show that one and stop inferring.
    What it discards is the rung above -- a segment whose state says the operator
    is being waited on, which the ladder infers precisely because a provider
    cannot declare it in advance. Libra then reports a delivery estimate for work
    that is not moving, and the approval nobody has given goes unmentioned.

    This is the mutation HORO-1627 asks for by name: the clear projection choosing
    a less important field over an available action-required signal.
    """
    real = render.clear_readings

    def mutated(status):
        declared = tuple(
            part
            for part in contract.order_segments(status)
            if getattr(part, "clear_role", None) is contract.ClearRole.POSTURE
        )
        return declared[:1] or real(status)

    return unittest.mock.patch.object(render, "clear_readings", mutated)


def telling_the_reader_which_explain_topic_to_open() -> object:
    """The explain key appended at detail, as a pointer to the deeper surface.

    With the justification the ticket anticipates and refuses: detail is the
    developer's depth, so a machine identifier is arguably useful there. It is a
    field no reader is meant to see, and "this is developer mode" does not make an
    unsafe field safe -- detail is a deeper reading of the reviewed surface, not a
    wider one.
    """
    real = render.render_segment

    def mutated(segment, mode, depth=render.InformationDepth.DETAIL):
        text = real(segment, mode, depth)
        key = getattr(segment, "explain_key", None)
        if not depth.shows_supporting_detail or not key:
            return text
        return f"{text} [{key}]"

    return unittest.mock.patch.object(render, "render_segment", mutated)


def polls_twice_at_detail() -> object:
    """A second round of provider probes, because more is being shown.

    The defect the ticket's performance rule exists for, in the form it would
    actually take: not a deliberate extra call, but a depth that re-collects
    because it wants the fuller snapshot and does not trust the one it has. It is
    nearly invisible on a clock -- the probes run underneath the user's own
    command -- which is why the fixtures count executions instead.
    """
    real = compositor.collect

    def mutated(registry, payload, home=None):
        upstream, statuses = real(registry, payload, home)
        if registry.depth.shows_supporting_detail:
            _, statuses = real(registry, payload, home)
        return upstream, statuses

    return unittest.mock.patch.object(compositor, "collect", mutated)


def recording_the_depth_in_the_host_settings_file() -> object:
    """The chosen depth written into the host's settings as an environment entry.

    The reasoning that makes it feel right: the compositor is launched by Claude
    Code, so the place to tell it something is the file that launches it. It
    reaches into a file this operation has no business opening -- and all three of
    the plan's settings fields have to be supplied together, because the planner
    refuses a half-formed one, which is itself a guard worth exercising.
    """
    real = lifecycle.plan_presentation

    def mutated(registry, **kwargs):
        plan = real(registry, **kwargs)
        recorded = (registry.data.get("lifecycle") or {}).get("settings_path")
        if plan.registry_after is None or not recorded:
            return plan
        document = lifecycle.read_settings(pathlib.Path(recorded))
        depth = plan.registry_after[lifecycle.PRESENTATION_KEY]["depth"]
        env = dict(document.data.get("env") or {}) | {"HORONOM_STATUSLINE_DEPTH": depth}
        return dataclasses.replace(
            plan,
            settings_path=document.path,
            ownership=lifecycle.classify(document),
            fingerprint=document.fingerprint,
            settings_after=dict(document.data) | {"env": env},
        )

    return unittest.mock.patch.object(lifecycle, "plan_presentation", mutated)


@contextlib.contextmanager
def teaching_the_readers_own_script_the_new_depth():
    """The depth exported from the reader's own statusline script.

    Which is the one file in this whole integration that is not ours to write at
    all. It is an easy defect to arrive at honestly: the registry records the
    command we wrapped, so the path is right there, and a single exported variable
    looks harmless next to a JSON rewrite.
    """
    real = lifecycle.apply

    def mutated(plan):
        result = real(plan)
        if plan.operation != "presentation" or plan.registry_after is None:
            return result
        recorded = (plan.registry_after.get("upstream") or {}).get("command")
        depth = (plan.registry_after.get(lifecycle.PRESENTATION_KEY) or {}).get("depth")
        if recorded and depth:
            with pathlib.Path(shlex.split(recorded)[0]).open("a", encoding="utf-8") as script:
                script.write(f"export HORONOM_DEPTH={depth}\n")
        return result

    with unittest.mock.patch.object(lifecycle, "apply", mutated):
        yield


def reloading_the_daemon_after_a_switch() -> object:
    """A process started so the new depth takes effect.

    The assumption underneath it is the interesting part: that a presentation
    preference is something a running product has to be told, rather than
    something the next render reads. `/bin/sh -c :` stands in for the reload --
    what is being proven is that a switch starts no process at all, and the
    cheapest possible process makes that claim without the fixture depending on
    any product being installed.
    """
    real = lifecycle.apply

    def mutated(plan):
        result = real(plan)
        if plan.operation == "presentation":
            subprocess.run(["/bin/sh", "-c", ":"], check=False)
        return result

    return unittest.mock.patch.object(lifecycle, "apply", mutated)


def filling_in_the_presentation_defaults_on_every_enable() -> object:
    """Every enable restates the presentation block, defaults included.

    The upgrade defect, in the place it would really live. Nobody writes "reset
    the user's preference"; they write "make sure the registry has a complete
    presentation block", and the completion runs on an operation that had no
    business touching it. A reader who chose detail months ago gets clear back the
    next time they adopt a product.
    """
    real = lifecycle.plan_enable

    def mutated(document, registry, registration, **kwargs):
        plan = real(document, registry, registration, **kwargs)
        if plan.registry_after is None:
            return plan
        presentation = dict(plan.registry_after.get(lifecycle.PRESENTATION_KEY) or {})
        presentation["depth"] = render.DEFAULT_INFORMATION_DEPTH.value
        return dataclasses.replace(
            plan,
            registry_after=dict(plan.registry_after)
            | {lifecycle.PRESENTATION_KEY: presentation},
        )

    return unittest.mock.patch.object(lifecycle, "plan_enable", mutated)


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


# --------------------------------------------------------------------------
# The thirteen depth defects.
# --------------------------------------------------------------------------


class ClearDetailAgreementTest(MutationCase):
    """The invariant that makes the two depths one product rather than two."""

    def test_a_summary_that_writes_its_own_verdict_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.CrossDepthInvariantTest,
            "test_the_primary_state_reads_identically_at_both_depths",
            summarising_the_state_in_clears_own_words(),
            expect="Verified' not found in",
        )

    def test_a_summary_that_does_not_name_its_product_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.FornaxDepthTest,
            "test_the_product_is_named_at_both_depths",
            a_summary_that_does_not_name_the_product(),
            expect="'Fornax' not found in",
        )


class ClearPriorityTest(MutationCase):
    """Which reading earns the one phrase, in the two rows where it matters most.

    Both mutations here are simplifications of the priority ladder rather than
    breaks of it, and both produce a line that is true about something while being
    wrong about the thing the reader needed.
    """

    def test_a_cached_posture_beside_an_unreachable_daemon_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.CircinusDepthTest,
            "test_an_unreachable_daemon_overrides_every_cached_posture",
            treating_availability_as_one_reading_among_the_rest(),
            expect="!= ['availability']",
        )

    def test_a_routine_posture_chosen_over_an_escalation_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.LibraDepthTest,
            "test_someone_being_waited_on_takes_the_line_from_an_estimate",
            summarising_the_declared_posture_and_ignoring_the_escalation(),
            expect="['remaining'] != ['approval']",
        )

    def test_the_matrix_row_catches_the_same_choice_from_the_other_side(self) -> None:
        """The same defect, seen as a reading that should not be there.

        Proven twice deliberately: the case above asserts which reading was chosen,
        this one asserts what the chosen reading says. A row whose expectations were
        weakened to whatever the implementation produces would still satisfy the
        first and not the second.
        """
        self.assert_guard_catches(
            depth_gate.LibraDepthTest,
            "test_the_summary_leaves_out_every_reading_its_row_forbids",
            summarising_the_declared_posture_and_ignoring_the_escalation(),
            expect="'Remaining work' unexpectedly found in",
        )


class EnforcementWordingTest(MutationCase):
    def test_a_shadow_outcome_stripped_of_its_marker_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.CircinusDepthTest,
            "test_a_shadow_outcome_and_an_executed_one_never_read_the_same",
            dropping_the_hypothetical_marker_from_the_summary(),
            expect="'NOT ENFORCED' not found in",
        )


class InventedReasonTest(MutationCase):
    def test_explaining_an_unrecorded_reason_into_a_finding_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.FornaxDepthTest,
            "test_an_unrecorded_reason_is_reported_as_unrecorded_and_not_invented",
            explaining_an_unrecorded_reason_helpfully(),
            expect="'insufficient evidence' unexpectedly found in",
        )


class ConfidenceSemanticsTest(MutationCase):
    """Two ways to lose the same distinction, at opposite depths."""

    def test_a_confidence_without_its_subject_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.LibraDepthTest,
            "test_a_confidence_that_is_shown_always_names_its_subject",
            a_confidence_without_its_subject(),
            expect="'preflight confidence' not found in",
        )

    def test_a_confidence_value_reaching_the_summary_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.LibraDepthTest,
            "test_no_confidence_value_reaches_the_summary",
            keeping_the_confidence_in_the_summary(),
            expect="'low' unexpectedly found in",
        )


class DepthDisclosureTest(MutationCase):
    """One leak, caught two ways, because the two ways catch different leaks.

    The subtractive check has no list of bad values and so catches fields nobody
    anticipated; the token check knows what an identifier looks like and so catches
    the ones that read like prose. This mutation is visible to both, which is the
    only kind of mutation that can demonstrate either.
    """

    def test_a_machine_identifier_shown_at_detail_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.DisclosureBoundaryTest,
            "test_no_machine_token_reaches_a_reader_at_either_depth",
            telling_the_reader_which_explain_topic_to_open(),
            expect="'fornax.verdict.latest' unexpectedly found in",
        )

    def test_the_subtractive_check_sees_it_without_being_told_what_to_look_for(
        self,
    ) -> None:
        self.assert_guard_catches(
            depth_gate.DisclosureBoundaryTest,
            "test_nothing_on_the_line_came_from_outside_the_reviewed_surface",
            telling_the_reader_which_explain_topic_to_open(),
            expect="fornax.verdict.latest",
        )


class DepthCostTest(MutationCase):
    def test_polling_again_because_more_is_shown_is_caught(self) -> None:
        """Caught by the count, which is the only half that can catch it.

        The timing assertions in the same class pass with this mutation in place --
        the extra probes run concurrently underneath the user's own command and
        land inside the slack every timing bound has to allow. That is not a
        weakness in those tests; it is why the class counts executions as well.
        """
        self.assert_guard_catches(
            performance_tests.InformationDepthCostTest,
            "test_showing_more_of_a_snapshot_does_not_ask_for_a_new_one",
            polls_twice_at_detail(),
            expect="6 != 3",
        )


class DepthSwitchSafetyTest(MutationCase):
    """The three things a switch must not touch, broken one at a time.

    All three are caught by one guard, which is deliberate on that guard's part:
    the interesting failure is a switch that gets two of these right and takes the
    third with it in the same step, and six separate tests over six separate
    switches could not see that. What the three cases here add is that the one
    guard is not passing by accident on any of the three.
    """

    GUARD = (depth_gate.DepthSwitchTest, "test_a_switch_takes_nothing_else_with_it")

    def test_recording_the_depth_in_the_host_settings_file_is_caught(self) -> None:
        self.assert_guard_catches(
            *self.GUARD,
            recording_the_depth_in_the_host_settings_file(),
            expect="a depth switch wrote the host settings file",
        )

    def test_editing_the_readers_own_statusline_script_is_caught(self) -> None:
        self.assert_guard_catches(
            *self.GUARD,
            teaching_the_readers_own_script_the_new_depth(),
            expect="HORONOM_DEPTH",
        )

    def test_restarting_a_daemon_to_apply_a_switch_is_caught(self) -> None:
        self.assert_guard_catches(
            *self.GUARD,
            reloading_the_daemon_after_a_switch(),
            expect="a depth switch started a process",
        )


class PreferenceMigrationTest(MutationCase):
    def test_an_unrelated_operation_resetting_an_explicit_preference_is_caught(self) -> None:
        self.assert_guard_catches(
            lifecycle_tests.PresentationDepthTest,
            "test_enabling_another_provider_preserves_the_depth",
            filling_in_the_presentation_defaults_on_every_enable(),
            expect="is not <InformationDepth.DETAIL",
        )


if __name__ == "__main__":
    unittest.main()
