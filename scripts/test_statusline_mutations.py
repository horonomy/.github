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

Then the fourteen the information-depth gate is required to demonstrate
(HORO-1627), which are a different kind of defect: nothing crashes, nothing is
destroyed, and the line still looks like a statusline. Clear and detail
disagreeing about a product's state; detail costing an extra provider request;
detail showing a field no depth is allowed to show; clear losing the product's
name, its unavailability, its enforcement marker, its reason, or its confidence
semantics; clear choosing a routine reading over an escalation; a mode switch
writing the host settings file, editing the reader's own statusline script or
restarting a daemon; and an unrelated operation -- adopting a product, or
dropping one -- resetting the stored preference. Each of those renders something
a reader would accept, which is why they need a test that does not.

Then the sixteen the product-authority gate is required to demonstrate
(HORO-1635), which are narrower still: the three shipped products now declare
which reading plays which part in Clear, and these are the ways a host could take
that decision back without appearing to. The host infers over a declaration by
promoting an action-required state, inventing a posture, or defaulting the parts
the product assigned; one of the four rules that make a declaration trustworthy is
deleted, so a half-declared payload is accepted and the rest inferred; a freshness
window stops closing; an unreachable product is read by severity; an exception
loses the reading the product nominated beside it, or gains one the product did
not; a percentage loses the word that says what it measures; Clear re-ranks its
readings by severity and stops leading with Detail's lead; the width ladder sheds
a declared primary; and -- the two that are not host defects at all -- a product
stops declaring, or starts reporting a schedule for work it is not doing. These
are proven against the real payload fixtures rather than hand-written stand-ins,
because a mutation that only breaks an approximation of a product has only proven
something about the approximation.
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
import test_statusline_authority_gate as authority_gate
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


def normalising_the_presentation_block_when_a_product_leaves() -> object:
    """And the same completion on the way out.

    The disable side of the defect above, and the one more likely to survive
    review: a disable already rewrites the provider list, so normalising the rest
    of the registry while the file is open looks like tidying rather than like
    resetting somebody's preference. The reader loses the depth they chose by
    taking a product out -- an operation with no presentation in it at all.
    """
    real = lifecycle.plan_remove

    def mutated(document, registry, **kwargs):
        plan = real(document, registry, **kwargs)
        if plan.registry_after is None:
            return plan
        presentation = dict(plan.registry_after.get(lifecycle.PRESENTATION_KEY) or {})
        presentation["depth"] = render.DEFAULT_INFORMATION_DEPTH.value
        return dataclasses.replace(
            plan,
            registry_after=dict(plan.registry_after)
            | {lifecycle.PRESENTATION_KEY: presentation},
        )

    return unittest.mock.patch.object(lifecycle, "plan_remove", mutated)


# --------------------------------------------------------------------------
# The seven defects.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The product-authority mutations (HORO-1635). Every one of these had to be
# chosen against the real payloads, because the obvious shape of the mutation
# does not work here: under `ClearAuthority.PROVIDER` every segment already
# carries a role, and every rung of `render.clear_roles` fires only on a role
# that is still `None`, so "ignore the declaration and use the ladder" is a
# no-op. What the host can still do is *infer on top of* a declaration, so each
# mutation below promotes, invents or defaults a part the product already
# assigned -- or deletes one of the four rules that make the declaration
# trustworthy, at which point the host accepts a half-declared projection and
# fills the rest in by inference, which is the merge the mode exists to prevent.
#
# Rejected as vacuous, recorded so the next person does not spend the afternoon
# rediscovering it: keying the exception's companion on `_has_quantified_reading`
# (no shipped payload carries both an exception and a quantified segment --
# Libra's budget percentage lives in its label, not in `count`); returning Clear's
# readings in role order instead of contract order (`order_segments` re-sorts them
# downstream); and any change confined to rungs 2 and 3 of the role ladder (no
# segment of any shipped payload reaches them).
# --------------------------------------------------------------------------


def _indices_of(segments: tuple, chosen: tuple) -> set:
    """Which positions in `segments` the objects in `chosen` occupy.

    By identity rather than equality: two readings of a provider can be equal
    without being the same reading, and a position set built on equality would
    silently merge them.
    """
    return {
        index
        for index, segment in enumerate(segments)
        if any(segment is one for one in chosen)
    }


def promoting_an_action_required_state_over_the_declared_part() -> object:
    """The top rung of the role ladder run unconditionally.

    The plausible reasoning, and it is a good one: a state that stops work must
    never be demoted, so make the rule absolute rather than a fallback. It reads
    as defence in depth. What it actually does is overrule the product on the one
    reading the product is best placed to rank -- Circinus declares its mode the
    posture and its decision a vital, and this makes the decision an exception, at
    which point `clear_readings` shows the exception alone and the mode is gone.
    The line then says a block happened without saying whether enforcement is on.
    """
    real = render.clear_roles

    def mutated(status):
        roles = list(real(status))
        for index, segment in enumerate(contract.order_segments(status)):
            if render._enum_value(segment.state) in render.ACTION_REQUIRED_STATES:
                roles[index] = contract.ClearRole.EXCEPTION
        return tuple(roles)

    return unittest.mock.patch.object(render, "clear_roles", mutated)


def inventing_a_posture_beside_the_declared_one() -> object:
    """The posture rung run unconditionally, so a measurement claims the part.

    `_has_quantified_reading` exists because a measurement is usually the more
    useful reading, and running it always looks like applying that insight
    consistently. Circinus's block counter is the measurement in its snapshot, so
    the counter becomes a second posture -- and a raw tally reaches Clear, which
    is the one thing the product's Detail surface is for.
    """
    real = render.clear_roles

    def mutated(status):
        roles = list(real(status))
        for index, segment in enumerate(contract.order_segments(status)):
            if render._has_quantified_reading(segment):
                roles[index] = contract.ClearRole.POSTURE
                break
        return tuple(roles)

    return unittest.mock.patch.object(render, "clear_roles", mutated)


def defaulting_the_roles_the_product_already_assigned() -> object:
    """The whole ladder run over a declared projection, as if nothing were declared.

    The form this takes in practice is not a decision to ignore the declaration
    but a refactor that forgets to read it -- a helper that rebuilds the role list
    from the segments it can see. Libra is where it shows: the host infers the
    estimate as the posture and then has no vital left to put beside it, because
    the budget posture it would have shown is a percentage inside a label rather
    than a counted quantity. The reading the founder asked for by name goes.
    """
    real = render.clear_roles

    def mutated(status):
        undeclared = dataclasses.replace(
            status,
            clear_authority=contract.ClearAuthority.HOST,
            segments=tuple(
                dataclasses.replace(segment, clear_role=None)
                for segment in status.segments
            ),
        )
        return real(undeclared)

    return unittest.mock.patch.object(render, "clear_roles", mutated)


def a_declaration_check_with_one_rule_deleted(rule: str) -> object:
    """One of the four provider-authority rules removed, the others kept.

    Deliberately a reimplementation rather than a swallowed exception, because
    that is the shape the defect takes: a rule gets deleted by someone who has met
    a payload it refused and reads it as too strict. Deleting rule `every-role` is
    the one the ticket names -- a payload that declares some of its segments is
    accepted, and the host infers the rest, which is the silent merge of product
    semantics and host guesswork that `ClearAuthority.PROVIDER` exists to stop.

    The messages here are abbreviated copies of the real ones. They are only
    reached by the rules this mutation keeps, and the tests that assert on message
    text assert against the real implementation.
    """

    def mutated(self) -> None:
        if not isinstance(self.clear_authority, contract.ClearAuthority):
            raise contract.ContractViolation("clear_authority must be a ClearAuthority")
        if self.clear_authority is not contract.ClearAuthority.PROVIDER:
            return
        if rule != "at-least-one-segment" and not self.segments:
            raise contract.ContractViolation(
                "clear_authority 'provider' requires at least one segment"
            )
        if rule != "every-role":
            undeclared = [s.key for s in self.segments if s.clear_role is None]
            if undeclared:
                raise contract.ContractViolation(
                    "clear_authority 'provider' requires a clear_role on every segment"
                )
        postures = [s.key for s in self.segments if s.clear_role is contract.ClearRole.POSTURE]
        if rule != "one-posture" and len(postures) > 1:
            raise contract.ContractViolation(
                "clear_authority 'provider' allows at most one 'posture' segment"
            )
        if (
            rule != "a-primary"
            and not postures
            and not any(s.clear_role is contract.ClearRole.EXCEPTION for s in self.segments)
        ):
            raise contract.ContractViolation(
                "clear_authority 'provider' requires a 'posture' or 'exception' segment"
            )

    return unittest.mock.patch.object(
        contract.ProviderStatus, "_validate_clear_authority", mutated
    )


def a_freshness_window_that_never_closes() -> object:
    """Clear's freshness test always true, so an old reading reads as current.

    Not written as "show stale things" -- written as a freshness computation that
    has lost its comparison, which is how it would arrive. Circinus's own
    `fresh_within_seconds` is then decoration: an hour-old block decision sits
    beside the mode in Clear, and the reader is told about an evaluation that
    happened sometime, in the one place that has no room to say when.

    Patches the host's reading of staleness (`render._is_current`) rather than
    `Segment.is_stale` itself, which was the first attempt and was wrong: falsifying
    the property also falsifies the gate's own precondition that the fixture is
    stale, so the gate failed at `the fixture is only a proof while it is stale`
    instead of at the Clear semantics. That is a guard tripping on its own setup,
    not on the defect -- the payload must stay honestly stale and the host must be
    the thing that stops noticing.
    """
    return unittest.mock.patch.object(render, "_is_current", lambda segment: True)


def reading_an_unreachable_product_by_severity() -> object:
    """The unavailability rule's first form, restored: the worst reading wins.

    Documented in `_unavailability_index` as the version that was wrong, which
    makes it the version worth keeping a mutation for -- a rule that was once
    written this way can be written this way again, and the comment explaining why
    not is not a test. A wedged Circinus then leads with the cached decision it
    cannot currently make.
    """

    def mutated(segments):
        return render._worst_index(segments, list(range(len(segments))))

    return unittest.mock.patch.object(render, "_unavailability_index", mutated)


def showing_an_exception_with_nothing_beside_it() -> object:
    """An exception takes the whole line, as the simpler reading of the rule.

    The rule it simplifies is subtle enough to invite this: nothing may be shown
    beside an exception *except* a vital the product itself nominated. Dropping the
    exception turns `Approval · 12% budget left` into `Approval`, which is the
    reading the founder rejected by name -- an approval whose cost is invisible.
    """
    real = render.clear_readings

    def mutated(status):
        chosen = real(status)
        if not render._has_live_readings(status):
            return chosen
        segments = contract.order_segments(status)
        roles = render.clear_roles(status)
        exceptions = [
            segment
            for index, segment in enumerate(segments)
            if roles[index] is contract.ClearRole.EXCEPTION
        ]
        return (exceptions[0],) if exceptions else chosen

    return unittest.mock.patch.object(render, "clear_readings", mutated)


def accompanying_an_exception_with_any_fresh_reading() -> object:
    """The exception's companion chosen by the host rather than by the product.

    The mirror of the mutation above and the likelier of the two, because it is
    generous rather than austere: if a product may nominate a reading to sit beside
    its exception, surely a fresh reading is better than an empty space. It is not.
    Libra's exhausted budget gains a task identifier, and the line spends half its
    width on an id while saying work has stopped.
    """
    real = render.clear_readings

    def mutated(status):
        chosen = real(status)
        if len(chosen) != 1 or not render._has_live_readings(status):
            return chosen
        segments = contract.order_segments(status)
        roles = render.clear_roles(status)
        kept = _indices_of(segments, chosen)
        if not all(roles[index] is contract.ClearRole.EXCEPTION for index in kept):
            return chosen
        for index, segment in enumerate(segments):
            if index not in kept and not segment.is_stale:
                kept.add(index)
                break
        return tuple(segment for index, segment in enumerate(segments) if index in kept)

    return unittest.mock.patch.object(render, "clear_readings", mutated)


def accepting_a_percentage_with_no_axis() -> object:
    """The axis check reduced to a length check, as one validation too many.

    It looks redundant next to the label validation it sits beside, and a provider
    that reports percentages presumably knows what they measure. Libra's budget
    posture is where it bites, and it bites in the direction that cannot be
    recovered: the host has no way to restore the missing word, so `53%` beside a
    budget reads as spent to one reader and as left to the next.
    """
    return unittest.mock.patch.object(
        contract, "require_percentage_axis", lambda value, field: value
    )


def ranking_clears_readings_by_severity() -> object:
    """Clear's chosen readings re-ordered worst-first, as the summary's own order.

    The most sympathetic mutation in the file: a one-line summary surely leads with
    the most severe thing in it. What it breaks is the property that makes the two
    depths one product -- Clear and Detail lead with the same reading -- so a
    reader switching depth sees the subject of the line change, and cannot tell
    whether the product's state changed with it.
    """
    real = render.clear_readings

    def mutated(status):
        return tuple(
            sorted(
                real(status),
                key=lambda segment: -render.STATE_SEVERITY.get(
                    render._enum_value(segment.state), 0
                ),
            )
        )

    return unittest.mock.patch.object(render, "clear_readings", mutated)


def shedding_a_declared_primary_by_severity_alone() -> object:
    """The width ladder's shed order before this ticket fixed it.

    The regression this gate found, kept as a mutation because the fix is an
    exception to a rule the rest of the file states plainly -- lowest severity
    first -- and an exception is exactly what a later simplification removes.
    Restoring it does not produce a worse line; it produces no line at all, because
    the remainder is a declared projection with no primary and the contract refuses
    to build one.
    """

    def mutated(statuses, order, dropped):
        for p_index, s_index, _ in order:
            if (p_index, s_index) not in dropped:
                return (p_index, s_index)
        return None

    return unittest.mock.patch.object(render, "_next_droppable", mutated)


def _replacing_the_payload_set(rebuilt: tuple) -> object:
    """Swap the gate's loaded fixtures for edited copies, consistently.

    `PAYLOADS`, `BY_NAME` and `LINES` are three views of one set, and patching one
    of them would leave the gate proving things about two different fixture sets at
    once -- which would make a passing mutation case meaningless rather than wrong.
    """
    by_name = {item.name: item for item in rebuilt}
    lines = tuple(
        tuple(by_name[item.name] for item in group) for group in authority_gate.LINES
    )
    return unittest.mock.patch.multiple(
        authority_gate, PAYLOADS=rebuilt, BY_NAME=by_name, LINES=lines
    )


def _edited_payloads(name: str, edit) -> tuple:
    return tuple(
        dataclasses.replace(item, status=edit(item.status)) if item.name == name else item
        for item in authority_gate.PAYLOADS
    )


def a_product_that_stopped_declaring_its_own_projection() -> object:
    """Fornax ships a build that no longer declares, and the host infers again.

    The regression this whole ticket is insurance against, and the only one that is
    not a host defect: nothing in the host changes, a product simply stops saying
    which reading is which, and the inference ladder resumes without a word. The
    rendered line stays plausible, which is why the gate has to assert the
    declaration itself rather than only its consequences.
    """

    def undeclare(status):
        return dataclasses.replace(
            status,
            clear_authority=contract.ClearAuthority.HOST,
            segments=tuple(
                dataclasses.replace(segment, clear_role=None)
                for segment in status.segments
            ),
        )

    rebuilt = tuple(
        dataclasses.replace(item, status=undeclare(item.status))
        if item.product == "fornax"
        else item
        for item in authority_gate.PAYLOADS
    )
    return _replacing_the_payload_set(rebuilt)


def a_product_that_reports_a_schedule_while_idle() -> object:
    """Libra ships an estimate for work it is not doing.

    A plausible product regression rather than a malicious one: the estimator keeps
    answering after the task ends and the last span is still in the snapshot. The
    founder's rule is about the reading, not about the mechanism -- a P90 beside an
    idle governor is a delivery promise for nothing.
    """

    def with_an_estimate(status):
        return dataclasses.replace(
            status,
            segments=tuple(
                dataclasses.replace(segment, duration_seconds=450000, duration_label="P90")
                for segment in status.segments
            ),
        )

    return _replacing_the_payload_set(_edited_payloads("libra/idle", with_an_estimate))


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
# The fourteen depth defects.
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

    def test_a_product_leaving_and_resetting_the_preference_is_caught(self) -> None:
        self.assert_guard_catches(
            depth_gate.ProviderChurnTest,
            "test_disabling_a_product_at_detail_takes_nothing_else_with_it",
            normalising_the_presentation_block_when_a_product_leaves(),
            expect="is not <InformationDepth.DETAIL",
        )


class HostInferringOverADeclarationTest(MutationCase):
    """Three ways to overrule a product that already decided (HORO-1635).

    All three leave a line a reader would accept, and none of them is reachable by
    turning the declaration off -- the host has no authority branch to disable.
    They are reached by inferring *in addition*, which is the only move left.
    """

    def test_promoting_an_action_required_state_over_the_declaration_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.CircinusClearTest,
            "test_clear_is_the_mode_and_the_fresh_enforcement_outcome",
            promoting_an_action_required_state_over_the_declared_part(),
            expect="['latest_decision'] != ['mode', 'latest_decision']",
        )

    def test_inventing_a_posture_beside_the_declared_one_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.CircinusClearTest,
            "test_a_raw_decision_tally_never_reaches_clear",
            inventing_a_posture_beside_the_declared_one(),
            expect="block_window",
        )

    def test_defaulting_the_parts_the_product_assigned_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.LibraClearTest,
            "test_a_normal_active_task_is_schedule_plus_budget_posture",
            defaulting_the_roles_the_product_already_assigned(),
            expect="['estimate'] != ['estimate', 'budget']",
        )

    def test_the_role_the_host_computes_is_watched_for_every_payload(self) -> None:
        # The same defect seen from the other side: whichever of the three shapes
        # it takes, it shows up as the computed role list diverging from the
        # declared one. Asserted separately because this is the guard that covers
        # the payloads whose rendered Clear line happens not to change.
        self.assert_guard_catches(
            authority_gate.DeclaredAuthorityTest,
            "test_the_role_the_host_computes_is_the_role_the_product_declared",
            promoting_an_action_required_state_over_the_declared_part(),
            expect="ClearRole.EXCEPTION",
        )


class DeclarationRuleTest(MutationCase):
    """Each of the four rules that make a declaration worth trusting (HORO-1635).

    The rules only ever fire on a payload the host should refuse, so none of them
    is exercised by the shipped fixtures passing. Deleting one has to be shown to
    turn a refusal into an acceptance, which is what these four do.
    """

    def test_accepting_a_payload_that_declared_only_some_of_its_parts_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.RefusalTest,
            "test_a_declaring_payload_that_omits_one_role_is_refused",
            a_declaration_check_with_one_rule_deleted("every-role"),
            expect="ContractViolation not raised",
        )

    def test_accepting_a_payload_that_names_no_primary_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.RefusalTest,
            "test_a_declaring_payload_that_names_no_primary_is_refused",
            a_declaration_check_with_one_rule_deleted("a-primary"),
            expect="ContractViolation not raised",
        )

    def test_accepting_two_declared_postures_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.RefusalTest,
            "test_a_declaring_payload_with_two_postures_is_refused",
            a_declaration_check_with_one_rule_deleted("one-posture"),
            expect="ContractViolation not raised",
        )

    def test_accepting_authority_over_no_segments_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.RefusalTest,
            "test_a_declaring_payload_with_no_segments_is_refused",
            a_declaration_check_with_one_rule_deleted("at-least-one-segment"),
            # Not "not raised": rule 4 subsumes rule 1 for refusal purposes, since a
            # payload with no segments has no posture either. So deleting rule 1 keeps
            # the refusal and changes only its reason -- which the gate asserts on,
            # because a reader handed "requires a posture or exception" for an empty
            # payload is told to add a role to a segment that does not exist. Recorded
            # rather than smoothed over: rule 1 earns its place as the diagnosis, not
            # as the gate.
            expect="at least one segment",
        )

    def test_accepting_a_percentage_with_no_axis_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.RefusalTest,
            "test_a_percentage_that_loses_its_axis_is_refused",
            accepting_a_percentage_with_no_axis(),
            expect="ContractViolation not raised",
        )


class ClearPrecedenceTest(MutationCase):
    """The three-rule precedence, mutated one rule at a time (HORO-1635)."""

    def test_a_stale_outcome_surviving_into_clear_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.CircinusClearTest,
            "test_a_stale_outcome_leaves_clear_and_the_mode_remains",
            a_freshness_window_that_never_closes(),
            expect="['mode', 'latest_decision'] != ['mode']",
        )

    def test_a_cached_posture_surviving_an_unreachable_runtime_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.CircinusClearTest,
            "test_an_unreachable_runtime_overrides_a_cached_mode_if_one_ever_arrives",
            reading_an_unreachable_product_by_severity(),
            expect="latest_decision",
        )

    def test_a_shadow_would_block_losing_its_marking_in_clear_is_caught(self) -> None:
        # The one mutation this set shares with HORO-1627's. Pointed at the real
        # Circinus payload as well as at the depth gate's matrix, because the
        # product's own wording is what a reader sees and the two differ.
        self.assert_guard_catches(
            authority_gate.CircinusClearTest,
            "test_a_would_block_in_shadow_keeps_its_hypothetical_marking_in_clear",
            dropping_the_hypothetical_marker_from_the_summary(),
            expect="NOT ENFORCED",
        )

    def test_an_exception_shown_without_its_declared_companion_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.LibraClearTest,
            "test_waiting_on_a_human_leads_and_keeps_the_budget_beside_it",
            showing_an_exception_with_nothing_beside_it(),
            expect="['task'] != ['task', 'budget']",
        )

    def test_an_exception_given_a_companion_the_product_did_not_name_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.LibraClearTest,
            "test_an_exhausted_budget_takes_the_line_from_every_routine_metric",
            accompanying_an_exception_with_any_fresh_reading(),
            expect="['task', 'budget'] != ['budget']",
        )

    def test_clear_re_ranking_its_readings_by_severity_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.CrossDepthTest,
            "test_clear_leads_with_the_same_reading_detail_leads_with",
            ranking_clears_readings_by_severity(),
            expect="Segment(",
        )


class WidthLadderTest(MutationCase):
    """The regression this gate found, and the shape of its fix (HORO-1635)."""

    def test_shedding_a_declared_primary_under_width_pressure_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.WidthPressureTest,
            "test_every_budget_renders_a_declared_projection_instead_of_refusing_it",
            shedding_a_declared_primary_by_severity_alone(),
            expect="requires a 'posture' or 'exception' segment",
        )

    def test_the_invariant_is_watched_where_the_remainder_is_built(self) -> None:
        # The crash is the loud symptom; the invariant is the actual claim. Pointed
        # at the test that inspects every remainder the ladder builds, so a future
        # fix that stopped the crash without keeping the primary -- by relaxing the
        # contract for host-built snapshots, say -- would still be caught here.
        self.assert_guard_catches(
            authority_gate.WidthPressureTest,
            "test_the_ladder_never_hands_the_contract_a_projection_with_no_primary",
            shedding_a_declared_primary_by_severity_alone(),
            expect="requires a 'posture' or 'exception' segment",
        )


class ProductRegressionTest(MutationCase):
    """The two defects no host change can cause, and no host rule can fix (HORO-1635).

    Both are edits to the loaded payloads rather than to the host, which is the
    point: this gate's subject is the products as much as the renderer, and a
    product that stops holding up its end has to fail something.
    """

    def test_a_product_that_stops_declaring_authority_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.DeclaredAuthorityTest,
            "test_every_product_declares_authority_over_its_own_projection",
            a_product_that_stopped_declaring_its_own_projection(),
            expect="ClearAuthority.HOST",
        )

    def test_a_product_that_stops_declaring_its_parts_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.DeclaredAuthorityTest,
            "test_every_segment_of_every_payload_declares_its_part",
            a_product_that_stopped_declaring_its_own_projection(),
            expect="unexpectedly None",
        )

    def test_a_product_reporting_a_schedule_while_idle_is_caught(self) -> None:
        self.assert_guard_catches(
            authority_gate.LibraClearTest,
            "test_an_idle_libra_shows_no_schedule_expectation",
            a_product_that_reports_a_schedule_while_idle(),
            expect="P90",
        )


if __name__ == "__main__":
    unittest.main()
