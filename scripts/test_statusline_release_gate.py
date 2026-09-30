"""The statusline release gate: the coexistence matrix (HORO-1572).

The other statusline test modules each prove their own component. This one is the
release gate for the integration as a whole, and it asks a different question:
walk the lifecycle the way a real user would, in twenty different starting
positions, and after *every single step* check the four things whose loss is a
release-blocking defect under ADR-0009.

Those four, checked by `GateCase.invariants` and never by inspection:

1. unrelated configuration survives -- every key outside `statusLine`, and every
   key inside it that is not the command or our marker;
2. the user's own statusline script is byte-identical, contents and mode;
3. every other registered provider survives, in order;
4. Claude Code remains usable -- the configured command is run, through a shell,
   with a Claude-shaped payload on stdin, and its output is read.

The fourth is the one that cannot be replaced by reading the file back. A
settings file that parses, validates and diffs clean can still leave the host
tool with no statusline, and the ownership test contract is explicit that "a
preservation test that 'reads back clean' but breaks the tool doesn't count".

The rendering fixture is imported from `test_statusline_lifecycle` rather than
rebuilt here: a second render harness that drifted from the first would let this
gate pass against a rendering path no user has.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import time
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_lifecycle as lifecycle
import statusline_render as render
from test_statusline_lifecycle import RenderingCase, rich_settings, unowned

# The user's own line, printed by the upstream script the fixture installs. Every
# case that keeps an upstream asserts the composed line begins with it, because
# "the user's output comes first" is an ordering promise and not a preference.
MINE = "MY OWN LINE"

# Values that live in the fixture settings file and must never reach the rendered
# line. Real shapes rather than the literal word "secret": a token-ish PATH and an
# internal URL are what an `env` block on this workstation actually holds.
NEVER_RENDERED = (
    "/opt/homebrew/bin:/usr/local/bin",
    "https://api.example.invalid/v1?trailing=true",
    "/Library/Application Support/ClaudeCode/managed-settings.json",
)


class GateCase(RenderingCase):
    """A rich starting position plus the four-invariant check run after each step."""

    def setUp(self) -> None:
        super().setUp()
        self.rebaseline()
        # Files this integration must never write to. Recorded with their mode as
        # well as their bytes: a script that survives with its execute bit cleared
        # has been broken just as thoroughly as one that was edited.
        self.untouchable: dict[pathlib.Path, tuple[bytes, int]] = {}
        self.guard(self.upstream)

    def rebaseline(self) -> None:
        """Adopt the current settings file as the state later steps must preserve.

        Called by the cases where the *user* changes something: after that, the
        thing to preserve is what they chose, not what the fixture started with.
        """
        self.baseline = self._read()

    def guard(self, path: pathlib.Path) -> None:
        self.untouchable[path] = (path.read_bytes(), path.stat().st_mode & 0o7777)

    @staticmethod
    def _metadata(status_line: dict) -> dict:
        """Everything in a `statusLine` object that is not ours to change."""
        return {
            key: value
            for key, value in status_line.items()
            if key not in ("command", lifecycle.MARKER_KEY)
        }

    def invariants(self, *, providers: tuple[str, ...], labels: tuple[str, ...] = (),
                   upstream: bool = True) -> str:
        """The four checks the release gate owes every step of every case."""
        current = self._read()

        # (1) unrelated configuration, outside and inside `statusLine`.
        self.assertEqual(unowned(current), unowned(self.baseline))
        if lifecycle.STATUS_LINE_KEY in self.baseline:
            self.assertEqual(
                self._metadata(current[lifecycle.STATUS_LINE_KEY]),
                self._metadata(self.baseline[lifecycle.STATUS_LINE_KEY]),
            )

        # (2) the user's own script, byte-for-byte and mode-for-mode.
        for path, (payload, mode) in self.untouchable.items():
            self.assertEqual(path.read_bytes(), payload, path)
            self.assertEqual(path.stat().st_mode & 0o7777, mode, path)

        # (3) every other provider, in order. Order is part of it: a rewrite that
        # preserved the set but reshuffled it would move products around the line.
        self.assertEqual(
            lifecycle._provider_ids(lifecycle.read_registry(self.home)), providers
        )

        # (4) the host tool can still use what is configured.
        configured = current[lifecycle.STATUS_LINE_KEY]["command"]
        self.assertIsInstance(configured, str)
        self.assertTrue(configured.strip())
        line = self.render()
        if upstream:
            self.assertTrue(line.startswith(MINE), line)
        for label in labels:
            self.assertIn(label, line)
        for value in NEVER_RENDERED:
            self.assertNotIn(value, line)
        return line

    def fornax(self, label: str = "VERIFIED") -> pathlib.Path:
        return self.provider_script("fornax-provider.sh", "fornax", label)

    def circinus(self, label: str = "SHADOW") -> pathlib.Path:
        return self.provider_script("circinus-provider.sh", "circinus", label)

    def libra(self, label: str = "APPROVED") -> pathlib.Path:
        return self.provider_script("libra-provider.sh", "libra", label)

    def enable_all(self) -> None:
        """The three real products, in the order a user would meet them."""
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))
        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))
        self.enable("libra", argv=(str(self.libra()),), timeout_ms=2000)
        self.invariants(
            providers=("fornax", "circinus", "libra"),
            labels=("VERIFIED", "SHADOW", "APPROVED"),
        )


class CaseANoExistingStatuslineTest(GateCase):
    def test_enabling_the_first_provider_where_there_was_no_statusline(self) -> None:
        data = rich_settings()
        del data[lifecycle.STATUS_LINE_KEY]
        self.write(data)
        self.rebaseline()
        document, _ = self.documents()
        self.assertIs(lifecycle.classify(document), lifecycle.Ownership.ABSENT)

        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        line = self.invariants(providers=("fornax",), labels=("VERIFIED",), upstream=False)
        # There was no user line to put first, and none is invented.
        self.assertNotIn(MINE, line)

        # And the slot goes back to not existing, rather than to an empty shell.
        self._uninstall()
        self.assertNotIn(lifecycle.STATUS_LINE_KEY, self._read())
        self.assertEqual(unowned(self._read()), unowned(self.baseline))


class CaseBRichCustomStatuslineTest(GateCase):
    def test_enabling_the_first_provider_over_a_rich_custom_statusline(self) -> None:
        document, _ = self.documents()
        self.assertIs(lifecycle.classify(document), lifecycle.Ownership.USER_OWNED)
        self.invariants(providers=())

        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))
        # The original command is recorded, not replaced: the registry holds the
        # only copy, and it holds it verbatim.
        self.assertEqual(self._registry()["upstream"]["command"], str(self.upstream))


class CaseCThreeProductsInOrderTest(GateCase):
    def test_fornax_then_circinus_then_libra(self) -> None:
        self.enable_all()
        # All three are on the line together, each attributed to itself. The
        # per-step invariants inside `enable_all` are what prove the second and
        # third arrivals cost the first nothing.
        line = self.render()
        for name in ("Fornax", "Circinus", "Libra"):
            self.assertIn(name, line)
        self.assertEqual(unowned(self._read()), unowned(self.baseline))


class CaseDReverseOrderTest(GateCase):
    def test_libra_then_circinus_then_fornax(self) -> None:
        self.enable("libra", argv=(str(self.libra()),), timeout_ms=2000)
        self.invariants(providers=("libra",), labels=("APPROVED",))
        settings_after_first = self.settings.read_bytes()

        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)
        self.invariants(providers=("libra", "circinus"), labels=("APPROVED", "SHADOW"))
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(
            providers=("libra", "circinus", "fornax"),
            labels=("APPROVED", "SHADOW", "VERIFIED"),
        )
        # Byte-identical, not merely equivalent: nothing rewrote the settings file
        # to say the same thing a different way.
        self.assertEqual(self.settings.read_bytes(), settings_after_first)


class CaseERepeatedEnableTest(GateCase):
    def test_enabling_the_same_provider_again_changes_nothing(self) -> None:
        argv = (str(self.fornax()),)
        self.enable("fornax", argv=argv, timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))
        settings_before = self.settings.read_bytes()
        registry_before = lifecycle.read_registry(self.home).raw

        for _ in range(3):
            plan = self.plan_enable("fornax", argv=argv, timeout_ms=2000)
            # Structurally idempotent rather than idempotent by comparison: the
            # plan has nothing to do, so there is no second write to get wrong.
            self.assertFalse(plan.mutates, plan.to_json())
            result = lifecycle.apply(plan)
            self.assertFalse(result.settings_written)
            self.assertFalse(result.registry_written)
            self.invariants(providers=("fornax",), labels=("VERIFIED",))

        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(lifecycle.read_registry(self.home).raw, registry_before)


class CaseFDisableMiddleProviderTest(GateCase):
    def test_disabling_the_middle_provider_leaves_the_outer_two(self) -> None:
        self.enable_all()
        settings_before = self.settings.read_bytes()

        self.remove("circinus")
        line = self.invariants(providers=("fornax", "libra"), labels=("VERIFIED", "APPROVED"))
        self.assertNotIn("SHADOW", line)
        # Removing a product is a registry-only operation while others remain, so
        # the shared file the host tool reads is not rewritten at all.
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(self._registry()["upstream"]["command"], str(self.upstream))


class CaseGReinstallOneProviderTest(GateCase):
    def test_upgrading_one_provider_in_place_disturbs_nothing_else(self) -> None:
        self.enable_all()
        settings_before = self.settings.read_bytes()
        chosen = render.PresentationMode.for_axes(compact=False, glyphs=False)
        applied = lifecycle.apply(
            lifecycle.plan_presentation(lifecycle.read_registry(self.home), glyphs=False)
        )
        self.assertTrue(applied.registry_written)

        upgraded = self.provider_script("fornax-v2.sh", "fornax", "VERIFIED v2")
        self.enable("fornax", argv=(str(upgraded),), timeout_ms=1500)
        line = self.invariants(
            providers=("fornax", "circinus", "libra"),
            labels=("VERIFIED v2", "SHADOW", "APPROVED"),
        )
        self.assertIn("VERIFIED v2", line)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        # An upgrade re-states one registration. It must not reset the reader's
        # display preference, nor forget what their statusline used to be.
        registry = lifecycle.read_registry(self.home)
        self.assertEqual(lifecycle._presentation_of(registry).get("mode"), chosen.value)
        self.assertEqual(lifecycle._upstream_of(registry), str(self.upstream))


class CaseHUnrelatedSettingsChangeTest(GateCase):
    def test_a_user_editing_unrelated_settings_while_installed(self) -> None:
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))

        data = self._read()
        data["model"] = "claude-sonnet-5"
        data["permissions"]["allow"].append("Bash(git log:*)")
        data["aBrandNewKeyClaudeAddedLater"] = {"nested": ["values", 1, None]}
        del data["theme"]
        self.write(data)
        self.rebaseline()

        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))
        self.remove("circinus")
        self.invariants(providers=("fornax",), labels=("VERIFIED",))
        self._uninstall()
        after = self._read()
        self.assertEqual(after["model"], "claude-sonnet-5")
        self.assertEqual(after["aBrandNewKeyClaudeAddedLater"], {"nested": ["values", 1, None]})
        self.assertNotIn("theme", after)
        self.assertEqual(after[lifecycle.STATUS_LINE_KEY]["command"], str(self.upstream))
        self.assertEqual(self.render(), MINE)


class CaseIStatusLineEditedWhileInstalledTest(GateCase):
    def test_metadata_changed_after_install_survives_the_restore(self) -> None:
        """The case that separates a delta from a snapshot.

        A restore that wrote back a remembered `statusLine` object would undo the
        `padding` the user chose after installing. Restoration puts the recorded
        command into the object that is on disk now, and nothing else.
        """
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))

        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["padding"] = 0
        data[lifecycle.STATUS_LINE_KEY]["refreshInterval"] = 9
        data[lifecycle.STATUS_LINE_KEY]["evenNewerKey"] = ["a", "b"]
        self.write(data)
        self.rebaseline()

        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))

        self._uninstall()
        status_line = self._read()[lifecycle.STATUS_LINE_KEY]
        self.assertEqual(status_line["padding"], 0)
        self.assertEqual(status_line["refreshInterval"], 9)
        self.assertEqual(status_line["evenNewerKey"], ["a", "b"])
        self.assertEqual(status_line["command"], str(self.upstream))
        self.assertNotIn(lifecycle.MARKER_KEY, status_line)
        self.assertEqual(self.render(), MINE)


class CaseJRepairAfterDriftTest(GateCase):
    def test_drift_is_reported_refused_and_then_repaired_explicitly(self) -> None:
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))

        # The user takes the slot back by hand, leaving our marker behind.
        theirs = self.script("their-new-line.sh", '#!/bin/sh\ncat >/dev/null\nprintf "THEIRS"\n')
        self.guard(theirs)
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = str(theirs)
        self.write(data)
        self.rebaseline()
        drifted = self.settings.read_bytes()

        document, registry = self.documents()
        self.assertIs(lifecycle.classify(document), lifecycle.Ownership.DRIFTED)

        # Reported without touching anything...
        report = lifecycle.doctor(self.settings, self.home)
        self.assertTrue(report["drift"]["detected"])
        self.assertEqual(self.settings.read_bytes(), drifted)

        # ...refused rather than silently retaken...
        plan = self.plan_enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.assertIn("CONFIG_DRIFT", plan.refusal)
        with self.assertRaises(lifecycle.OwnershipError):
            lifecycle.apply(plan)
        self.assertEqual(self.settings.read_bytes(), drifted)
        self.assertEqual(self.render(), "THEIRS")

        # ...and repaired only when the operator says so. `--adopt` is the user
        # accepting that the recorded original is abandoned in favour of what they
        # chose themselves, which is why it cannot be inferred.
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000, adopt=True)
        line = self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"),
                               upstream=False)
        self.assertTrue(line.startswith("THEIRS"), line)
        self.assertEqual(self._registry()["upstream"]["command"], str(theirs))


class CaseKFinalProviderRemovalTest(GateCase):
    def test_only_the_last_provider_leaving_gives_the_slot_back(self) -> None:
        before = self.settings.read_bytes()
        self.enable_all()

        self.remove("libra")
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))
        self.remove("fornax")
        self.invariants(providers=("circinus",), labels=("SHADOW",))
        self.assertTrue(lifecycle.read_registry(self.home).present)

        self.remove("circinus")
        # Byte-identical to the file that existed before any of this happened.
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.render(), MINE)
        # And our own state is gone rather than left as litter.
        self.assertFalse(lifecycle.read_registry(self.home).present)


class CaseLMalformedSettingsTest(GateCase):
    def test_every_unfamiliar_shape_fails_closed_with_zero_mutation(self) -> None:
        shapes = {
            "not json at all": b"{ this is not json,,",
            "statusLine as a string": b'{"model":"x","statusLine":"/bin/true"}',
            "statusLine as null": b'{"model":"x","statusLine":null}',
            "an unrecognised type": b'{"statusLine":{"type":"widget","command":"/bin/true"}}',
            "a foreign ownership marker": json.dumps(
                {"statusLine": {"type": "command", "command": "/bin/true",
                                lifecycle.MARKER_KEY: {"owner": "someone-else"}}}
            ).encode(),
            "a command of the wrong type": b'{"statusLine":{"type":"command","command":42}}',
            "a blank command": b'{"statusLine":{"type":"command","command":"   "}}',
        }
        for name, raw in shapes.items():
            with self.subTest(shape=name):
                self.settings.write_bytes(raw)
                registry_before = lifecycle.read_registry(self.home).present

                if name == "not json at all":
                    # Unreadable is its own refusal: there is no document to
                    # classify, so nothing can be planned against it.
                    with self.assertRaises(lifecycle.SettingsParseError):
                        self.documents()
                else:
                    document, _ = self.documents()
                    self.assertIs(
                        lifecycle.classify(document), lifecycle.Ownership.UNSUPPORTED_SHAPE
                    )
                    for plan in (
                        self.plan_enable("fornax", argv=(str(self.fornax()),)),
                        self.plan_remove("fornax"),
                        self.plan_remove("fornax", operation="uninstall"),
                    ):
                        self.assertIsNotNone(plan.refusal)
                        self.assertEqual(
                            [change.kind for change in plan.changes],
                            [lifecycle.ChangeKind.BLOCKED_UNKNOWN],
                        )
                        self.assertTrue(plan.remediation)
                        with self.assertRaises(lifecycle.OwnershipError):
                            lifecycle.apply(plan)

                # Zero mutation, in both files, on every one of those paths.
                self.assertEqual(self.settings.read_bytes(), raw)
                self.assertEqual(lifecycle.read_registry(self.home).present, registry_before)


class CaseMConcurrentEditTest(GateCase):
    def test_an_edit_between_plan_and_apply_is_refused_not_clobbered(self) -> None:
        argv = (str(self.fornax()),)
        plan = self.plan_enable("fornax", argv=argv, timeout_ms=2000)

        # The user edits the file in the window the plan was formed against.
        data = self._read()
        data["aKeyAddedInTheWindow"] = True
        self.write(data)
        self.rebaseline()
        current = self.settings.read_bytes()

        with self.assertRaises(lifecycle.ConcurrentModificationError):
            lifecycle.apply(plan)
        self.assertEqual(self.settings.read_bytes(), current)
        self.assertFalse(lifecycle.read_registry(self.home).present)
        self.invariants(providers=())

        # Re-planning against the new state succeeds and keeps their edit.
        self.enable("fornax", argv=argv, timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))
        self.assertTrue(self._read()["aKeyAddedInTheWindow"])

    def test_a_registry_edit_between_plan_and_apply_is_refused_too(self) -> None:
        """The second file needs its own guard, and it needs a distinct proof.

        If only the settings fingerprint were checked, a concurrent `enable` of a
        different product would be overwritten by this plan's idea of the registry
        -- losing a provider rather than a user key, but losing it just as surely.
        """
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        plan = self.plan_enable("libra", argv=(str(self.libra()),), timeout_ms=2000)
        self.enable("circinus", argv=(str(self.circinus()),), timeout_ms=2000)

        with self.assertRaises(lifecycle.ConcurrentModificationError):
            lifecycle.apply(plan)
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))


class CaseNCrashDuringAtomicUpdateTest(GateCase):
    """Interruption is modelled as a failing `os.replace`, which is the exact
    instant a crash matters: before it the target is untouched, after it the new
    contents are complete. A test that killed a subprocess instead would sample a
    random instant and pass without ever landing on the one that counts.
    """

    def failing_replace(self, after: int):
        """Let the first `after` publications through, then fail the next."""
        real = os.replace
        seen = {"n": 0}

        def fake(source, target, *args, **kwargs):
            if seen["n"] >= after:
                raise OSError(28, "No space left on device")
            seen["n"] += 1
            return real(source, target, *args, **kwargs)

        return unittest.mock.patch.object(lifecycle.os, "replace", fake)

    def test_a_crash_before_the_first_publication_changes_nothing(self) -> None:
        before = self.settings.read_bytes()
        with self.failing_replace(0):
            with self.assertRaises(lifecycle.LifecycleError):
                self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(lifecycle.read_registry(self.home).present)
        self.invariants(providers=())
        # No temporary left in the way of the next attempt, either.
        self.assertEqual(sorted(p.name for p in self.settings.parent.iterdir()),
                         [self.settings.name])

    def test_a_crash_between_the_two_writes_leaves_the_users_line_in_charge(self) -> None:
        """The whole reason the two writes are ordered.

        `enable` writes the registry first. Crashing before the settings file is
        published leaves a registry nobody reads and a statusline that still
        belongs to the user -- never a compositor in the slot with no registry
        behind it, which is the state that renders nothing.
        """
        before = self.settings.read_bytes()
        with self.failing_replace(1):
            with self.assertRaises(lifecycle.LifecycleError):
                self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)

        self.assertEqual(self.settings.read_bytes(), before)
        self.assertTrue(lifecycle.read_registry(self.home).present)
        self.assertEqual(self.render(), MINE)

        # And the half-done state is recoverable by re-running, not by hand.
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))

    def test_a_crash_while_giving_the_slot_back_leaves_it_given_back(self) -> None:
        """Removal writes in the opposite order, for the same reason.

        The last provider leaving publishes the settings file and then *deletes*
        the registry rather than rewriting it, so the interruption to model here is
        a failing removal rather than a failing publication. Dying in that window
        has to leave the user's own command in charge with nothing but stale state
        of ours behind it -- never the reverse.
        """
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))

        # Exactly one path, for the reason `deny_reads` takes the same care: a
        # blanket failure would break the temporary-file cleanup inside the atomic
        # write and the test would pass without ever reaching the window it names.
        registry_path = lifecycle.read_registry(self.home).path
        real = pathlib.Path.unlink

        def refuse(this: pathlib.Path, *args, **kwargs):
            if this == registry_path:
                raise OSError(5, "Input/output error")
            return real(this, *args, **kwargs)

        with unittest.mock.patch.object(pathlib.Path, "unlink", refuse):
            with self.assertRaises(OSError):
                self.remove("fornax", operation="uninstall")

        self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY]["command"], str(self.upstream))
        self.assertEqual(self.render(), MINE)
        # Re-running finishes the job rather than needing the file edited by hand.
        self.remove("fornax", operation="uninstall")
        self.assertFalse(lifecycle.read_registry(self.home).present)
        self.assertEqual(self.render(), MINE)

    def test_the_destination_is_never_observed_half_written(self) -> None:
        observed: list[bytes] = []
        real = os.replace

        def spy(source, target, *args, **kwargs):
            result = real(source, target, *args, **kwargs)
            observed.append(pathlib.Path(target).read_bytes())
            return result

        with unittest.mock.patch.object(lifecycle.os, "replace", spy):
            self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)

        # Two publications, one per file, and each one complete at the instant it
        # became visible -- never an empty or truncated file under the real name.
        self.assertEqual(len(observed), 2)
        for payload in observed:
            self.assertIsInstance(json.loads(payload.decode("utf-8")), dict)
        self.invariants(providers=("fornax",), labels=("VERIFIED",))


class CaseOProviderTimeoutTest(GateCase):
    def test_a_hanging_provider_cannot_delay_or_suppress_the_line(self) -> None:
        # Deliberately not warmed: warming a script that never finishes would hang
        # the fixture instead of the provider.
        hanging = self.script(
            "hanging-provider.sh", "#!/bin/sh\ncat >/dev/null\nsleep 30\n", warm=False
        )
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.enable("slow", argv=(str(hanging),))

        started = time.monotonic()
        line = self.invariants(providers=("fornax", "slow"), labels=("VERIFIED",))
        elapsed = time.monotonic() - started

        # The budget the host actually promises: one upstream timeout plus the
        # overall deadline, with room for process spawn. Nothing like 30 seconds.
        budget = (
            compositor.DEFAULT_UPSTREAM_TIMEOUT_MS + compositor.DEFAULT_DEADLINE_MS
        ) / 1000 + 3.0
        self.assertLess(elapsed, budget, f"render took {elapsed:.2f}s")
        # The hanging provider is reported, not omitted: a provider that vanishes
        # silently reads as "nothing to report", which is a different claim.
        self.assertIn("slow", line.lower())


class CasePProviderMalformedOutputTest(GateCase):
    def test_nonsense_from_one_provider_is_contained(self) -> None:
        for name, body in (
            ("garbage", "#!/bin/sh\ncat >/dev/null\nprintf 'not json at all'\n"),
            ("wrong-shape", "#!/bin/sh\ncat >/dev/null\nprintf '{\"provider\":42}'\n"),
            ("empty", "#!/bin/sh\ncat >/dev/null\n"),
            ("flood", "#!/bin/sh\ncat >/dev/null\nhead -c 400000 /dev/zero | tr '\\0' 'x'\n"),
        ):
            with self.subTest(provider=name):
                broken = self.script(f"{name}-provider.sh", body)
                self.enable(name, argv=(str(broken),), timeout_ms=2000)
                self.invariants(providers=(name,), labels=())
                self.remove(name)

    def test_a_broken_provider_does_not_hide_a_healthy_one(self) -> None:
        broken = self.script("garbage.sh", "#!/bin/sh\ncat >/dev/null\nprintf 'nonsense'\n")
        self.enable("broken", argv=(str(broken),), timeout_ms=2000)
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.invariants(providers=("broken", "fornax"), labels=("VERIFIED",))


class CaseQProviderDaemonDownTest(GateCase):
    def test_a_product_whose_service_is_down_reports_it_without_leaking_why(self) -> None:
        down = self.script(
            "circinus-down.sh",
            "#!/bin/sh\ncat >/dev/null\n"
            "echo 'error: connection refused /Users/founder/.circinus/run/sock' >&2\nexit 7\n",
        )
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.enable("circinus", argv=(str(down),), timeout_ms=2000)
        line = self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED",))
        self.assertIn("circinus", line.lower())
        # The product's crash output is not the line's content. That is where a
        # filesystem path shows up, and the line is what gets pasted into a ticket.
        self.assertNotIn("/Users/founder", line)
        self.assertNotIn("connection refused", line)

    def test_a_product_that_is_not_installed_is_not_reported_as_healthy(self) -> None:
        missing = self.root / "definitely-not-here" / "fornax"
        self.enable("fornax", argv=(str(missing),), timeout_ms=2000)
        line = self.invariants(providers=("fornax",))
        self.assertIn("fornax", line.lower())
        # A missing product reading as all-clear is the specific confusion the
        # whole contract was shaped to prevent.
        self.assertNotIn("VERIFIED", line)


class CaseROriginalStatuslineFailsTest(GateCase):
    def test_the_users_own_command_failing_does_not_break_the_providers(self) -> None:
        broken = self.script(
            "their-broken-line.sh",
            "#!/bin/sh\ncat >/dev/null\necho 'Traceback: /Users/founder/x.py' >&2\nexit 1\n",
        )
        self.guard(broken)
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = str(broken)
        self.write(data)
        self.rebaseline()

        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        line = self.invariants(providers=("fornax",), labels=("VERIFIED",), upstream=False)
        self.assertNotIn("Traceback", line)

        # And their broken command is what comes back, unaltered. A failing
        # upstream is not a licence to replace it with something that works.
        self._uninstall()
        self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY]["command"], str(broken))


class CaseSAwkwardUpstreamCommandTest(GateCase):
    def test_a_path_with_spaces_and_a_quoted_argument_through_the_whole_matrix(self) -> None:
        (self.root / "my statusline dir").mkdir()
        script = self.script(
            "my statusline dir/print args.sh",
            '#!/bin/sh\ncat >/dev/null\nprintf "%s" "$1"\n',
        )
        self.guard(script)
        configured = f'{shlex.quote(str(script))} "MY OWN LINE"'
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = configured
        self.write(data)
        self.rebaseline()
        self.assertEqual(self.render(), MINE)

        self.enable_all()
        # Stored as the single string it is. Anything that split and rejoined it
        # would work here and fail on the first user whose quoting differs.
        self.assertEqual(self._registry()["upstream"]["command"], configured)

        self.remove("circinus")
        self.invariants(providers=("fornax", "libra"), labels=("VERIFIED", "APPROVED"))
        self._uninstall()
        self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY]["command"], configured)
        self.assertEqual(self.render(), MINE)


class CaseTUnknownStatusLineFieldsTest(GateCase):
    def test_fields_this_version_has_never_heard_of_survive_everything(self) -> None:
        exotic = {
            "aScalarWeDoNotKnow": 7,
            "aNestedObject": {"deep": {"deeper": [1, {"deepest": None}]}},
            "aList": ["keep", "every", "one"],
            "aFalsyOne": False,
            "anEmptyOne": {},
            "aUnicodeOne": "ぜんぶ残す ⚠️",
        }
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY].update(exotic)
        self.write(data)
        self.rebaseline()

        self.enable_all()
        self.remove("libra")
        self.invariants(providers=("fornax", "circinus"), labels=("VERIFIED", "SHADOW"))
        self._uninstall()

        status_line = self._read()[lifecycle.STATUS_LINE_KEY]
        for key, value in exotic.items():
            self.assertEqual(status_line[key], value, key)
        self.assertEqual(status_line["command"], str(self.upstream))
        self.assertEqual(self.render(), MINE)


if __name__ == "__main__":
    unittest.main()
