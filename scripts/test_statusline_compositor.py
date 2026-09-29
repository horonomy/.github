"""Tests for the statusline compositor.

Two groups here are load-bearing rather than merely thorough, and deleting a
test from either weakens a guarantee the product makes to the user:

`TestUpstreamIsNeverOurs` proves the user's own statusline survives everything
we can do to ourselves — its command string reaches the shell unmodified, it
receives the same stdin bytes we were given, its output is a verbatim prefix of
ours, and no failure of ours suppresses it.

`TestNothingLeaksAndNothingLeaksOut` proves the two containment promises: a
timed-out provider leaves no process behind (this command re-runs every few
seconds for the life of a session, so a per-refresh leak accumulates), and
neither a provider's output nor our own error paths put a secret, a path or an
exception message onto the rendered line.

The subprocess tests use real children because every guarantee being checked is
a guarantee about real process behaviour; a mocked `Popen` would prove only that
the mock matches my belief about it. Fixtures are warmed once before use: on
macOS a freshly written executable pays a large one-time evaluation cost that
would otherwise show up as a spurious timeout.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_render as render

# Generous, because these must pass even on a loaded machine: where a test
# asserts success, a timeout is a false failure.
GENEROUS_MS = 2000
# Short, because these are the cases where a timeout is the expected result and
# the child is asleep for far longer than any scheduling jitter.
IMPATIENT_MS = 200
# Long enough that a leaked child is still running when we look for it, short
# enough that one surviving a test run cleans itself up.
LEAK_SLEEP = "27"
# An upper bound on the first-execution cost of a newly written file, not an
# expected duration: warming normally finishes in a fraction of this.
WARM_LIMIT_SECONDS = 10.0


def wire(**overrides) -> dict:
    """A minimal valid provider answer, as a provider would print it."""
    payload = {
        "contract_version": 1,
        "provider": "fornax",
        "provider_version": "0.4.1",
        "scope": "project",
        "availability": "available",
        "segments": [{"key": "latest_verdict", "state": "ok", "label": "Verified"}],
    }
    payload.update(overrides)
    return payload


def registry_document(**overrides) -> dict:
    """A minimal valid registry document."""
    payload = {"registry_version": 1, "providers": []}
    payload.update(overrides)
    return payload


def provider_document(provider: str, command: list[str], **overrides) -> dict:
    payload = {"provider": provider, "command": command, "scope": "project"}
    payload.update(overrides)
    return payload


def without_state_home(**environment) -> unittest.mock.patch:
    """Patch the environment so the default state home is what gets resolved."""
    patcher = unittest.mock.patch.dict(os.environ, environment)
    patcher.start()
    os.environ.pop(compositor.STATE_HOME_ENV, None)
    return patcher


class FixtureCase(unittest.TestCase):
    """A case with a private state home and a directory of executable fixtures."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="horonom-test-")
        self.addCleanup(temporary.cleanup)
        self.home = pathlib.Path(temporary.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        patcher = unittest.mock.patch.dict(
            os.environ, {compositor.STATE_HOME_ENV: str(self.home)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def script(self, name: str, body: str, *, warm: bool = True) -> str:
        """Write an executable fixture and return its path.

        Warmed by running it once, because the first execution of a newly
        written file on this platform can cost hundreds of milliseconds, and a
        test that then measures a timeout would be measuring the wrong thing.

        The warm-up closes the fixture's stdin immediately. Inheriting the
        runner's stdin would make the warm-up hang for any fixture that reads
        it -- and hang only where the suite is run with a stdin that stays open,
        so it would pass locally and time out under a CI runner.
        """
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        if warm:
            subprocess.run(
                [str(path)], input=b"", capture_output=True, timeout=WARM_LIMIT_SECONDS
            )
        return str(path)

    def hanging_script(self, name: str, body: str) -> str:
        """Write a fixture that never exits, with its first-execution cost paid.

        A fixture that hangs cannot be warmed by running it to completion, and
        guessing a warm-up duration would make these tests depend on a number
        nobody can defend. It instead signals that it has reached its own first
        line, at which point the execution cost has been paid and it is killed.
        """
        ready = self.home / f"{name}.started"
        path = self.script(name, f": > '{ready}'\n{body}", warm=False)
        child = subprocess.Popen(
            [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        deadline = time.monotonic() + WARM_LIMIT_SECONDS
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        child.wait()
        ready.unlink(missing_ok=True)
        return path

    def answering(self, name: str, payload: dict) -> str:
        """A fixture that prints one JSON document and exits 0."""
        return self.script(name, "cat <<'HORONOM_EOF'\n" + json.dumps(payload) + "\nHORONOM_EOF\n")

    def entry(self, provider: str, command: str | list[str], **overrides):
        argv = [command] if isinstance(command, str) else list(command)
        return compositor.ProviderEntry(
            provider=provider,
            argv=tuple(argv),
            scope=overrides.get("scope", contract.Scope.PROJECT),
            timeout_ms=overrides.get("timeout_ms", GENEROUS_MS),
        )

    def write_registry(self, **overrides) -> None:
        (self.home / compositor.REGISTRY_FILENAME).write_text(
            json.dumps(registry_document(**overrides))
        )

    def run_main(self, payload: bytes = b"{}") -> tuple[int, str]:
        """Run the entry point, returning its exit code and rendered line.

        Diagnostics are captured rather than allowed through, both to keep the
        suite's own output readable and because the separation matters: the
        rendered line is the user's terminal and the diagnostic stream is not, so
        a test that conflated them could not notice a detail crossing over.
        """
        writer = io.StringIO()
        diagnostics = io.StringIO()
        with unittest.mock.patch.object(sys, "stderr", diagnostics):
            code = compositor.main(stdin=io.BytesIO(payload), stdout=writer)
        self.diagnostics = diagnostics.getvalue()
        return code, writer.getvalue()


class TestStatePaths(unittest.TestCase):
    def test_the_state_home_honours_the_environment_override(self):
        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: "/tmp/elsewhere"}):
            self.assertEqual(compositor.state_home(), pathlib.Path("/tmp/elsewhere"))

    def test_the_default_state_home_is_expanded(self):
        patcher = without_state_home(HOME="/tmp/fake-home")
        self.addCleanup(patcher.stop)
        home = compositor.state_home()
        self.assertTrue(home.is_absolute())
        self.assertNotIn("~", str(home))

    def test_the_registry_and_cache_live_under_the_state_home(self):
        home = pathlib.Path("/tmp/anywhere")
        self.assertEqual(compositor.registry_path(home).parent, home)
        self.assertEqual(compositor.cache_dir(home).parent, home)

    def test_nothing_is_written_under_the_hosts_own_configuration_directory(self):
        # The state home is ours; the host's config directory is not. Keeping
        # these separate is what lets an uninstall remove our state without
        # going anywhere near theirs.
        patcher = without_state_home(HOME="/tmp/fake-home")
        self.addCleanup(patcher.stop)
        self.assertNotIn("/.claude", str(compositor.state_home()))


class TestBoundedMilliseconds(unittest.TestCase):
    def test_an_absent_value_takes_the_default(self):
        self.assertEqual(compositor._bounded_ms({}, "t", 250, 2000), 250)

    def test_a_value_over_the_maximum_is_clamped_not_refused(self):
        self.assertEqual(compositor._bounded_ms({"t": 10**9}, "t", 250, 2000), 2000)

    def test_a_non_integer_is_refused(self):
        for value in ("250", 250.0, None, [250]):
            with self.subTest(value=value):
                with self.assertRaises(compositor.RegistryError):
                    compositor._bounded_ms({"t": value}, "t", 250, 2000)

    def test_a_boolean_is_refused_despite_being_an_integer(self):
        with self.assertRaises(compositor.RegistryError):
            compositor._bounded_ms({"t": True}, "t", 250, 2000)

    def test_zero_and_negative_budgets_are_refused(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(compositor.RegistryError):
                    compositor._bounded_ms({"t": value}, "t", 250, 2000)


class TestProviderArgvValidation(unittest.TestCase):
    def test_a_string_command_is_refused(self):
        # A string would have to be split to be executed, and splitting is
        # parsing. Provider commands are ours, so they are argv from the start.
        with self.assertRaises(compositor.RegistryError):
            compositor._require_argv("/usr/bin/fornax statusline", "fornax")

    def test_an_empty_list_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor._require_argv([], "fornax")

    def test_a_non_string_argument_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor._require_argv(["/bin/echo", 7], "fornax")

    def test_an_empty_argument_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor._require_argv(["/bin/echo", ""], "fornax")

    def test_an_over_long_argv_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor._require_argv(["x"] * (compositor.MAX_ARGV_LENGTH + 1), "fornax")

    def test_a_valid_argv_is_returned_as_a_tuple(self):
        self.assertEqual(
            compositor._require_argv(["/bin/echo", "a b"], "fornax"), ("/bin/echo", "a b")
        )


class TestScopeValidation(unittest.TestCase):
    def test_every_contract_scope_is_accepted(self):
        for scope in contract.Scope:
            with self.subTest(scope=scope):
                self.assertIs(compositor._require_scope(scope.value, "p"), scope)

    def test_an_unknown_scope_is_refused_rather_than_defaulted(self):
        # Rendering host-wide state as session-scoped would tell the user
        # something false about which session a provider speaks for.
        for value in ("global", "", None, "HOST", 1):
            with self.subTest(value=value):
                with self.assertRaises(compositor.RegistryError):
                    compositor._require_scope(value, "p")


class TestRegistryParsing(unittest.TestCase):
    def test_a_minimal_document_parses(self):
        registry = compositor.parse_registry(registry_document())
        self.assertIsNone(registry.upstream_command)
        self.assertEqual(registry.providers, ())
        self.assertEqual(registry.deadline_ms, compositor.DEFAULT_DEADLINE_MS)

    def test_a_non_object_document_is_refused(self):
        for payload in ([], "x", 1, None):
            with self.subTest(payload=payload):
                with self.assertRaises(compositor.RegistryError):
                    compositor.parse_registry(payload)

    def test_an_unsupported_registry_version_is_refused(self):
        # Refused, not best-efforted: a future writer may have moved the very
        # field we would go on to read.
        for version in (0, 2, "1", None, 1.0):
            with self.subTest(version=version):
                with self.assertRaises(compositor.RegistryError):
                    compositor.parse_registry(registry_document(registry_version=version))

    def test_the_supported_version_set_matches_the_declared_version(self):
        self.assertIn(compositor.REGISTRY_VERSION, compositor.SUPPORTED_REGISTRY_VERSIONS)

    def test_an_upstream_command_is_preserved_verbatim(self):
        command = """'/Users/someone/my scripts/statusline.sh' --flag "a b" | tr -d x"""
        registry = compositor.parse_registry(registry_document(upstream={"command": command}))
        self.assertEqual(registry.upstream_command, command)

    def test_an_absent_upstream_is_a_valid_state(self):
        for payload in (registry_document(), registry_document(upstream={})):
            with self.subTest(payload=payload):
                self.assertIsNone(compositor.parse_registry(payload).upstream_command)

    def test_a_blank_upstream_command_is_refused(self):
        for command in ("", "   ", "\n"):
            with self.subTest(command=command):
                with self.assertRaises(compositor.RegistryError):
                    compositor.parse_registry(registry_document(upstream={"command": command}))

    def test_a_non_object_upstream_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(upstream="/bin/true"))

    def test_providers_must_be_a_list(self):
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(providers={"fornax": []}))

    def test_too_many_providers_are_refused(self):
        entries = [
            provider_document(f"p{index}", ["/bin/true"])
            for index in range(compositor.MAX_PROVIDERS + 1)
        ]
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(providers=entries))

    def test_duplicate_provider_ids_are_refused(self):
        # The cache is keyed by provider id, so two entries sharing one would
        # overwrite each other's answers.
        entries = [provider_document("fornax", ["/bin/true"])] * 2
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(providers=entries))

    def test_a_disabled_provider_is_dropped_without_failing_the_document(self):
        entries = [
            provider_document("fornax", ["/bin/true"], enabled=False),
            provider_document("circinus", ["/bin/true"]),
        ]
        registry = compositor.parse_registry(registry_document(providers=entries))
        self.assertEqual([entry.provider for entry in registry.providers], ["circinus"])

    def test_only_a_literal_true_counts_as_enabled(self):
        for value in (1, "true", "yes", None):
            with self.subTest(value=value):
                registry = compositor.parse_registry(
                    registry_document(
                        providers=[provider_document("fornax", ["/bin/true"], enabled=value)]
                    )
                )
                self.assertEqual(registry.providers, ())

    def test_a_disabled_entry_with_an_invalid_id_is_still_refused(self):
        # The id is validated before the enabled check, so a malformed entry
        # cannot hide behind being switched off.
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(
                registry_document(
                    providers=[{"provider": "Not An Id", "command": ["/bin/true"], "enabled": False}]
                )
            )

    def test_an_invalid_provider_id_does_not_escape_as_a_contract_error(self):
        # `main` recognises a bad registry by type. A contract exception
        # escaping from here would reach the host as a traceback.
        for identifier in ("Not An Id", "", None, 7, "x" * 64):
            with self.subTest(identifier=identifier):
                with self.assertRaises(compositor.RegistryError):
                    compositor.parse_registry(
                        registry_document(
                            providers=[{"provider": identifier, "command": ["/bin/true"]}]
                        )
                    )

    def test_a_non_object_provider_entry_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(providers=["fornax"]))

    def test_provider_timeouts_are_clamped(self):
        registry = compositor.parse_registry(
            registry_document(
                providers=[provider_document("fornax", ["/bin/true"], timeout_ms=10**6)]
            )
        )
        self.assertEqual(registry.providers[0].timeout_ms, compositor.MAX_PROVIDER_TIMEOUT_MS)

    def test_the_deadline_and_upstream_timeout_are_clamped(self):
        registry = compositor.parse_registry(
            registry_document(deadline_ms=10**6, upstream_timeout_ms=10**6)
        )
        self.assertEqual(registry.deadline_ms, compositor.MAX_DEADLINE_MS)
        self.assertEqual(registry.upstream_timeout_ms, compositor.MAX_UPSTREAM_TIMEOUT_MS)

    def test_a_width_budget_must_be_a_positive_integer(self):
        for width in (0, -10, "80", 80.5, True):
            with self.subTest(width=width):
                with self.assertRaises(compositor.RegistryError):
                    compositor.parse_registry(
                        registry_document(presentation={"width_budget": width})
                    )

    def test_an_absent_width_budget_means_unbounded(self):
        self.assertIsNone(compositor.parse_registry(registry_document()).width_budget)

    def test_a_non_object_presentation_is_refused(self):
        with self.assertRaises(compositor.RegistryError):
            compositor.parse_registry(registry_document(presentation="balanced"))

    def test_an_unknown_presentation_mode_falls_back_rather_than_refusing(self):
        # The one lenient field. It decides how the line looks, not what we
        # execute, and losing the whole statusline over a cosmetic typo is
        # worse than rendering it in the default style.
        registry = compositor.parse_registry(
            registry_document(presentation={"mode": "fancy-nonsense"})
        )
        self.assertIsInstance(registry.mode, render.PresentationMode)

    def test_every_presentation_mode_can_be_selected(self):
        for mode in render.PresentationMode:
            with self.subTest(mode=mode):
                registry = compositor.parse_registry(
                    registry_document(presentation={"mode": mode.value})
                )
                self.assertIs(registry.mode, mode)


class TestRegistryLoading(FixtureCase):
    def test_a_missing_registry_is_a_registry_error(self):
        with self.assertRaises(compositor.RegistryError):
            compositor.load_registry(self.home / "absent.json")

    def test_invalid_json_is_a_registry_error_naming_the_line(self):
        path = self.home / compositor.REGISTRY_FILENAME
        path.write_text('{"registry_version": 1,\n  "providers": [oops]}')
        with self.assertRaises(compositor.RegistryError) as raised:
            compositor.load_registry(path)
        self.assertIn("line 2", str(raised.exception))

    def test_a_valid_registry_loads_from_disk(self):
        self.write_registry(upstream={"command": "/bin/echo hi"})
        self.assertEqual(
            compositor.load_registry(self.home / compositor.REGISTRY_FILENAME).upstream_command,
            "/bin/echo hi",
        )

    def test_an_unreadable_registry_reports_an_errno_and_not_a_message(self):
        # OSError messages on some platforms carry the full path of every
        # parent directory, and this string is printed to the user's terminal.
        path = self.home / compositor.REGISTRY_FILENAME
        path.mkdir()
        with self.assertRaises(compositor.RegistryError) as raised:
            compositor.load_registry(path)
        message = str(raised.exception)
        self.assertRegex(message, r"\(\d+\)$")
        self.assertNotIn("Is a directory", message)


class TestRecursionDepth(unittest.TestCase):
    def test_an_absent_marker_is_depth_zero(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(compositor.depth(), 0)

    def test_the_marker_is_read_as_an_integer(self):
        with unittest.mock.patch.dict(os.environ, {compositor.DEPTH_ENV: "3"}):
            self.assertEqual(compositor.depth(), 3)

    def test_an_unparseable_marker_is_treated_as_already_recursing(self):
        # The failure being prevented is unbounded recursion, so an
        # uninterpretable marker resolves towards refusing to descend.
        for value in ("deep", "", "1.5", "0x1"):
            with self.subTest(value=value):
                with unittest.mock.patch.dict(os.environ, {compositor.DEPTH_ENV: value}):
                    self.assertGreaterEqual(compositor.depth(), compositor.MAX_DEPTH)

    def test_a_negative_marker_cannot_buy_extra_depth(self):
        with unittest.mock.patch.dict(os.environ, {compositor.DEPTH_ENV: "-5"}):
            self.assertEqual(compositor.depth(), 0)

    def test_the_child_environment_increments_the_marker(self):
        with unittest.mock.patch.dict(os.environ, {compositor.DEPTH_ENV: "0"}):
            self.assertEqual(compositor.child_env()[compositor.DEPTH_ENV], "1")

    def test_the_child_environment_otherwise_inherits_the_parent(self):
        with unittest.mock.patch.dict(os.environ, {"HORONOM_TEST_MARKER": "kept"}):
            self.assertEqual(compositor.child_env()["HORONOM_TEST_MARKER"], "kept")


class TestSelfReferenceDetection(unittest.TestCase):
    def test_this_very_file_is_recognised(self):
        self.assertTrue(compositor.names_this_command(compositor.__file__))

    def test_a_self_reference_with_arguments_is_recognised(self):
        self.assertTrue(compositor.names_this_command(f"  {compositor.__file__} --anything  "))

    def test_an_ordinary_command_is_not_recognised(self):
        for command in ("/bin/echo hi", "python3 /some/other.py", "statusline.sh"):
            with self.subTest(command=command):
                self.assertFalse(compositor.names_this_command(command))

    def test_an_empty_command_is_not_recognised(self):
        self.assertFalse(compositor.names_this_command("   "))

    def test_a_nonexistent_first_token_is_not_recognised(self):
        self.assertFalse(compositor.names_this_command("/no/such/binary --flag"))

    def test_the_check_only_inspects_and_never_rewrites(self):
        # It takes a string and returns a bool; there is no path by which it
        # could alter the command the registry recorded.
        command = "/bin/echo 'a  b'"
        compositor.names_this_command(command)
        self.assertEqual(command, "/bin/echo 'a  b'")


class TestBoundedExecution(FixtureCase):
    def test_a_clean_child_returns_its_code_and_output(self):
        path = self.script("clean", "printf 'hello'\n")
        self.assertEqual(
            compositor._run_bounded((path,), b"", GENEROUS_MS, shell=False), (0, b"hello")
        )

    def test_a_non_zero_child_returns_its_code_and_output(self):
        path = self.script("noisy", "printf 'partial'\nexit 7\n")
        self.assertEqual(
            compositor._run_bounded((path,), b"", GENEROUS_MS, shell=False), (7, b"partial")
        )

    def test_a_timed_out_child_is_distinguishable_from_a_non_zero_exit(self):
        # None, not a code: a kill and a clean failure mean different things and
        # earn different not-available reasons.
        path = self.script("sleeper", f"sleep {LEAK_SLEEP}\n", warm=False)
        code, _ = compositor._run_bounded((path,), b"", IMPATIENT_MS, shell=False)
        self.assertIsNone(code)

    def test_stdin_reaches_the_child_byte_for_byte(self):
        path = self.script("echoing", "cat\n")
        payload = b'{"a": "\xc3\xa9  b", "n": 1}\n\x00tail'
        self.assertEqual(
            compositor._run_bounded((path,), payload, GENEROUS_MS, shell=False)[1], payload
        )

    def test_the_time_bound_is_real(self):
        path = self.script("sleeper2", f"sleep {LEAK_SLEEP}\n", warm=False)
        started = time.monotonic()
        compositor._run_bounded((path,), b"", IMPATIENT_MS, shell=False)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_shell_mode_runs_the_command_string_through_a_shell(self):
        self.assertEqual(
            compositor._run_bounded("printf 'a b' | tr ' ' '-'", b"", GENEROUS_MS, shell=True),
            (0, b"a-b"),
        )

    def test_a_missing_executable_raises_rather_than_returning_a_code(self):
        with self.assertRaises(FileNotFoundError):
            compositor._run_bounded(("/no/such/binary",), b"", GENEROUS_MS, shell=False)


class TestHostSynthesisedStatuses(unittest.TestCase):
    def entry(self, scope=contract.Scope.HOST):
        return compositor.ProviderEntry(
            provider="fornax", argv=("/bin/true",), scope=scope, timeout_ms=250
        )

    def test_a_synthesised_status_carries_the_declared_scope(self):
        status = compositor._host_not_available(
            self.entry(), contract.Availability.ERROR, "probe_failed", "Could not be started"
        )
        self.assertIs(status.scope, contract.Scope.HOST)

    def test_the_version_is_literally_unknown_rather_than_invented(self):
        # A provider that failed to answer did not tell us its version, and
        # inventing one would be a claim about which build is installed.
        status = compositor._host_not_available(
            self.entry(), contract.Availability.ERROR, "probe_failed", "Could not be started"
        )
        self.assertEqual(status.provider_version, "unknown")

    def test_a_synthesised_status_never_claims_to_be_available(self):
        for availability in contract.Availability:
            with self.subTest(availability=availability):
                if availability.has_live_readings:
                    with self.assertRaises(contract.ContractViolation):
                        compositor._host_not_available(
                            self.entry(), availability, "probe_failed", "Broke"
                        )
                else:
                    status = compositor._host_not_available(
                        self.entry(), availability, "probe_failed", "Broke"
                    )
                    self.assertFalse(status.availability.has_live_readings)

    def test_a_synthesised_status_renders_as_something_the_user_can_read(self):
        status = compositor._host_not_available(
            self.entry(), contract.Availability.ERROR, "probe_timeout", "Did not answer in time"
        )
        rendered = render.render_provider(status, render.PresentationMode.BALANCED)
        self.assertIn("Did not answer in time", rendered)


class TestSourceFingerprint(unittest.TestCase):
    def test_the_same_argv_fingerprints_the_same(self):
        argv = ("/usr/bin/fornax", "statusline")
        self.assertEqual(compositor.source_fingerprint(argv), compositor.source_fingerprint(argv))

    def test_a_different_argv_fingerprints_differently(self):
        self.assertNotEqual(
            compositor.source_fingerprint(("/usr/bin/fornax",)),
            compositor.source_fingerprint(("/usr/local/bin/fornax",)),
        )

    def test_argument_boundaries_are_not_collapsible(self):
        # Joining on a separator that could appear inside an argument would let
        # two different commands share a fingerprint.
        self.assertNotEqual(
            compositor.source_fingerprint(("a", "b")), compositor.source_fingerprint(("ab",))
        )

    def test_the_fingerprint_does_not_contain_the_path_it_covers(self):
        # The cache file is something a user may reasonably open; there is no
        # reason for it to restate their local paths.
        fingerprint = compositor.source_fingerprint(("/Users/someone/private/tool",))
        self.assertNotIn("someone", fingerprint)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")


class TestCache(FixtureCase):
    def status(self, **overrides) -> contract.ProviderStatus:
        return contract.provider_status_from_wire(wire(**overrides))

    def cache_file(self) -> pathlib.Path:
        return compositor.cache_dir(self.home) / "fornax.json"

    def test_a_fresh_entry_round_trips(self):
        entry = self.entry("fornax", "/bin/true")
        status = self.status(cache_ttl_seconds=30)
        compositor.write_cache(entry, status, self.home)
        self.assertEqual(compositor.read_cache(entry, self.home).to_wire(), status.to_wire())

    def test_nothing_is_written_for_a_zero_ttl(self):
        entry = self.entry("fornax", "/bin/true")
        compositor.write_cache(entry, self.status(cache_ttl_seconds=0), self.home)
        self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_an_expired_entry_is_not_served(self):
        # A stale reading is not a substitute for a failed probe: rendering last
        # minute's healthy state while the daemon is down is exactly the lie the
        # not-available states exist to prevent.
        entry = self.entry("fornax", "/bin/true")
        compositor.write_cache(entry, self.status(cache_ttl_seconds=30), self.home)
        payload = json.loads(self.cache_file().read_text())
        payload["expires_at"] = time.time() - 1
        self.cache_file().write_text(json.dumps(payload))
        self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_an_entry_written_for_another_command_is_not_served(self):
        compositor.write_cache(
            self.entry("fornax", "/bin/true"), self.status(cache_ttl_seconds=30), self.home
        )
        self.assertIsNone(compositor.read_cache(self.entry("fornax", "/bin/false"), self.home))

    def test_an_entry_with_no_source_recorded_is_not_served(self):
        entry = self.entry("fornax", "/bin/true")
        compositor.write_cache(entry, self.status(cache_ttl_seconds=30), self.home)
        payload = json.loads(self.cache_file().read_text())
        del payload["source"]
        self.cache_file().write_text(json.dumps(payload))
        self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_a_corrupt_or_absent_entry_is_not_served(self):
        entry = self.entry("fornax", "/bin/true")
        self.assertIsNone(compositor.read_cache(entry, self.home))
        compositor.cache_dir(self.home).mkdir(parents=True, exist_ok=True)
        for content in ("not json", "[]", '{"expires_at": "soon"}', '{"expires_at": 99999999999}'):
            with self.subTest(content=content):
                self.cache_file().write_text(content)
                self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_an_entry_whose_payload_violates_the_contract_is_not_served(self):
        entry = self.entry("fornax", "/bin/true")
        compositor.write_cache(entry, self.status(cache_ttl_seconds=30), self.home)
        payload = json.loads(self.cache_file().read_text())
        payload["wire"]["availability"] = "definitely-fine"
        self.cache_file().write_text(json.dumps(payload))
        self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_the_cache_directory_and_files_are_not_world_readable(self):
        compositor.write_cache(
            self.entry("fornax", "/bin/true"), self.status(cache_ttl_seconds=30), self.home
        )
        self.assertEqual(compositor.cache_dir(self.home).stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.cache_file().stat().st_mode & 0o777, 0o600)

    def test_a_write_leaves_no_temporary_file_behind(self):
        compositor.write_cache(
            self.entry("fornax", "/bin/true"), self.status(cache_ttl_seconds=30), self.home
        )
        names = sorted(path.name for path in compositor.cache_dir(self.home).iterdir())
        self.assertEqual(names, ["fornax.json"])

    def test_a_ttl_beyond_the_contract_maximum_is_clamped(self):
        before = time.time()
        compositor.write_cache(
            self.entry("fornax", "/bin/true"),
            self.status(cache_ttl_seconds=contract.MAX_CACHE_TTL_SECONDS),
            self.home,
        )
        payload = json.loads(self.cache_file().read_text())
        self.assertLessEqual(
            payload["expires_at"] - before, contract.MAX_CACHE_TTL_SECONDS + 1
        )

    def test_an_unwritable_cache_directory_is_not_fatal(self):
        # A cache is an optimisation. Failing to write one must not change what
        # the user sees this refresh or any other.
        entry = self.entry("fornax", "/bin/true")
        (self.home / compositor.CACHE_DIRNAME).write_text("not a directory")
        compositor.write_cache(entry, self.status(cache_ttl_seconds=30), self.home)
        self.assertIsNone(compositor.read_cache(entry, self.home))


class TestRunProvider(FixtureCase):
    def reasons(self, status: contract.ProviderStatus) -> list:
        return [segment.reason_code for segment in status.segments]

    def test_a_healthy_provider_is_returned_as_it_answered(self):
        path = self.answering("good", wire())
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        self.assertIs(status.availability, contract.Availability.AVAILABLE)
        self.assertEqual(status.segments[0].label, "Verified")

    def test_a_missing_executable_is_reported_as_not_installed(self):
        status = compositor.run_provider(
            self.entry("fornax", "/no/such/binary"), GENEROUS_MS, self.home
        )
        self.assertIs(status.availability, contract.Availability.UNSUPPORTED)
        self.assertIn("not_installed", self.reasons(status))

    def test_a_non_zero_exit_is_reported_as_an_error(self):
        path = self.script("failing", "exit 3\n")
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        self.assertIs(status.availability, contract.Availability.ERROR)
        self.assertIn("probe_failed", self.reasons(status))

    def test_a_hanging_provider_is_reported_as_a_timeout(self):
        path = self.script("hanging", f"sleep {LEAK_SLEEP}\n", warm=False)
        status = compositor.run_provider(self.entry("fornax", path), IMPATIENT_MS, self.home)
        self.assertIn("probe_timeout", self.reasons(status))

    def test_unreadable_output_is_reported_as_malformed(self):
        for name, body in (("garbage", "printf 'not json'\n"), ("binary", "printf '\\377\\376'\n")):
            with self.subTest(name=name):
                path = self.script(name, body)
                status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
                self.assertIn("malformed_output", self.reasons(status))

    def test_output_that_violates_the_contract_is_reported_as_such(self):
        path = self.answering("invalid", wire(availability="definitely-fine"))
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        self.assertIn("contract_violation", self.reasons(status))

    def test_a_provider_answering_under_another_id_is_refused(self):
        # Otherwise a provider could attribute its own state to another product,
        # or hide its own behind one.
        path = self.answering("liar", wire(provider="circinus"))
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        self.assertIn("identity_mismatch", self.reasons(status))
        self.assertEqual(status.provider, "fornax")

    def test_a_refused_answer_is_never_cached(self):
        path = self.answering("liar2", wire(provider="circinus", cache_ttl_seconds=30))
        entry = self.entry("fornax", path)
        compositor.run_provider(entry, GENEROUS_MS, self.home)
        self.assertIsNone(compositor.read_cache(entry, self.home))

    def test_no_remaining_time_is_reported_rather_than_probed(self):
        marker = self.home / "was-run"
        path = self.script("counting", f"touch {marker}\nexit 1\n", warm=False)
        status = compositor.run_provider(self.entry("fornax", path), 0, self.home)
        self.assertIn("deadline_exhausted", self.reasons(status))
        self.assertFalse(marker.exists())

    def test_a_fresh_cache_answers_without_running_the_provider(self):
        counter = self.home / "calls"
        path = self.script(
            "counted",
            f"printf x >> {counter}\ncat <<'HORONOM_EOF'\n"
            + json.dumps(wire(cache_ttl_seconds=30))
            + "\nHORONOM_EOF\n",
        )
        entry = self.entry("fornax", path)
        counter.write_text("")
        for _ in range(3):
            compositor.run_provider(entry, GENEROUS_MS, self.home)
        self.assertEqual(counter.read_text(), "x")

    def test_a_failure_never_raises_and_never_returns_none(self):
        for name, body in (("boom", "exit 9\n"), ("junk", "printf '{'\n")):
            with self.subTest(name=name):
                path = self.script(name, body)
                status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
                self.assertIsInstance(status, contract.ProviderStatus)

    def test_a_failure_reason_never_carries_the_exception_text(self):
        # A subprocess error message routinely contains a path, a command line
        # or an environment value, and this string is rendered into a terminal.
        path = self.script("failing2", "exit 3\n")
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        rendered = render.render_provider(status, render.PresentationMode.BALANCED)
        self.assertNotIn(str(self.home), rendered)
        self.assertNotIn("Traceback", rendered)

    def test_an_oversized_answer_does_not_become_a_status(self):
        path = self.script(
            "flood",
            f"head -c {compositor.MAX_PROVIDER_OUTPUT_BYTES * 2} /dev/zero | tr '\\0' 'a'\n",
        )
        status = compositor.run_provider(self.entry("fornax", path), GENEROUS_MS, self.home)
        self.assertIn("malformed_output", self.reasons(status))


class TestCollect(FixtureCase):
    def registry(self, providers, **overrides):
        payload = {"registry_version": 1, "providers": providers}
        payload.update(overrides)
        return compositor.parse_registry(payload)

    def test_independent_providers_run_concurrently(self):
        # Serialising them would make the worst case grow with every product the
        # user enables, which is the cost this whole design exists to avoid.
        slow = self.script("slow", "sleep 0.4\nexit 1\n")
        providers = [provider_document(f"p{index}", [slow], timeout_ms=1500) for index in range(4)]
        registry = self.registry(providers, deadline_ms=3000)
        started = time.monotonic()
        _, statuses = compositor.collect(registry, b"{}", self.home)
        elapsed = time.monotonic() - started
        self.assertEqual(len(statuses), 4)
        self.assertLess(elapsed, 1.2, "four 0.4s providers took about as long as the sum")

    def test_the_overall_deadline_bounds_the_total(self):
        hanging = self.script("hanging3", f"sleep {LEAK_SLEEP}\n", warm=False)
        providers = [
            provider_document(f"p{index}", [hanging], timeout_ms=2000) for index in range(3)
        ]
        registry = self.registry(providers, deadline_ms=400)
        started = time.monotonic()
        _, statuses = compositor.collect(registry, b"{}", self.home)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(len(statuses), 3)

    def test_every_registered_provider_produces_exactly_one_status(self):
        good = self.answering("good2", wire())
        bad = self.script("bad", "exit 1\n")
        registry = self.registry(
            [provider_document("fornax", [good]), provider_document("circinus", [bad])]
        )
        _, statuses = compositor.collect(registry, b"{}", self.home)
        self.assertEqual(sorted(status.provider for status in statuses), ["circinus", "fornax"])

    def test_the_upstream_command_receives_the_payload_we_were_given(self):
        sink = self.home / "stdin.bin"
        upstream = self.script("capture", f"cat > {sink}\nprintf 'line'\n")
        registry = self.registry([], upstream={"command": f"'{upstream}'"})
        payload = b'{"model": {"id": "x"}, "n": 1}\n'
        text, _ = compositor.collect(registry, payload, self.home)
        self.assertEqual(sink.read_bytes(), payload)
        self.assertEqual(text, "line")

    def test_there_is_no_upstream_text_when_there_is_no_upstream_command(self):
        self.assertEqual(compositor.collect(self.registry([]), b"{}", self.home)[0], "")


class TestUpstreamIsNeverOurs(FixtureCase):
    """The user's statusline survives everything we can do to ourselves."""

    def test_a_command_with_spaces_and_quoting_runs_as_configured(self):
        directory = self.home / "my scripts"
        directory.mkdir()
        path = directory / "status line.sh"
        path.write_text('#!/bin/sh\nprintf \'%s|%s\' "$1" "$2"\n')
        path.chmod(0o755)
        command = f"'{path}' 'a b' \"c d\""
        self.assertEqual(compositor.run_upstream(command, b"", GENEROUS_MS), "a b|c d")

    def test_a_pipeline_is_preserved_because_a_shell_runs_it(self):
        # Splitting the command into an argv would mean parsing it, which is the
        # one thing this module must never do to the user's command.
        self.assertEqual(
            compositor.run_upstream("printf 'a b' | tr ' ' '-'", b"", GENEROUS_MS), "a-b"
        )

    def test_stdin_is_forwarded_byte_for_byte(self):
        sink = self.home / "seen.bin"
        path = self.script("capture2", f"cat > {sink}\n")
        payload = b'{"workspace": {"current_dir": "/x"}}\n\xc3\xa9\x00'
        compositor.run_upstream(f"'{path}'", payload, GENEROUS_MS)
        self.assertEqual(sink.read_bytes(), payload)

    def test_output_is_returned_verbatim_apart_from_the_trailing_newline(self):
        # Leading and internal whitespace is theirs, including the indentation a
        # padded statusline relies on.
        path = self.script("padded", "printf '  a  b \\n'\n")
        self.assertEqual(compositor.run_upstream(f"'{path}'", b"", GENEROUS_MS), "  a  b ")

    def test_a_non_zero_exit_does_not_discard_their_line(self):
        # A script that prints its line and then exits non-zero still printed
        # their line; deciding it is invalid on their behalf is not ours to do.
        path = self.script("grumpy", "printf 'their line'\nexit 1\n")
        self.assertEqual(compositor.run_upstream(f"'{path}'", b"", GENEROUS_MS), "their line")

    def test_a_missing_command_returns_empty_rather_than_raising(self):
        self.assertEqual(compositor.run_upstream("/no/such/statusline --flag", b"", GENEROUS_MS), "")

    def test_a_hanging_command_is_bounded(self):
        path = self.script("hangup", f"sleep {LEAK_SLEEP}\n", warm=False)
        started = time.monotonic()
        compositor.run_upstream(f"'{path}'", b"", IMPATIENT_MS)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_output_already_produced_survives_a_timeout(self):
        path = self.hanging_script("partial", f"printf 'got this far'\nsleep {LEAK_SLEEP}\n")
        self.assertEqual(compositor.run_upstream(f"'{path}'", b"", IMPATIENT_MS), "got this far")

    def test_their_line_is_a_verbatim_prefix_when_every_provider_fails(self):
        upstream = self.script("theirs", "printf '~/proj  main*  $0.42'\n")
        hanging = self.script("hanging4", f"sleep {LEAK_SLEEP}\n", warm=False)
        broken = self.script("broken", "printf 'not json'\n")
        self.write_registry(
            upstream={"command": f"'{upstream}'"},
            providers=[
                provider_document("fornax", [hanging], timeout_ms=200),
                provider_document("circinus", [broken]),
                provider_document("libra-governor", ["/no/such/binary"]),
            ],
            deadline_ms=1000,
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("~/proj  main*  $0.42"), out)

    def test_their_line_survives_a_provider_that_floods_stdout(self):
        upstream = self.script("theirs2", "printf 'THEIRS'\n")
        flood = self.script(
            "flood2",
            f"head -c {compositor.MAX_PROVIDER_OUTPUT_BYTES * 2} /dev/zero | tr '\\0' 'a'\n",
        )
        self.write_registry(
            upstream={"command": f"'{upstream}'"},
            providers=[provider_document("fornax", [flood])],
        )
        self.assertTrue(self.run_main()[1].startswith("THEIRS"))

    def test_their_line_survives_a_defect_in_our_own_rendering(self):
        # The one broad catch in the module. They lose our block, which is ours
        # to lose, and keep their line, which is not.
        upstream = self.script("theirs5", "printf 'THEIRS'\n")
        self.write_registry(upstream={"command": f"'{upstream}'"})
        with unittest.mock.patch.object(
            render, "compose", side_effect=RuntimeError("a bug of ours")
        ):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "THEIRS\n")

    def test_we_never_run_a_command_that_names_this_script(self):
        self.write_registry(upstream={"command": compositor.__file__})
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", out)

    def test_our_own_broken_state_does_not_execute_anything(self):
        (self.home / compositor.REGISTRY_FILENAME).write_text("{ not json")
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("registry unreadable", out)


class TestMain(FixtureCase):
    def test_the_exit_code_is_always_zero(self):
        cases = {
            "no registry": None,
            "malformed registry": "{ nope",
            "unsupported version": '{"registry_version": 99}',
            "invalid provider id": '{"registry_version": 1, "providers": [{"provider": "N O"}]}',
            "valid": json.dumps(registry_document()),
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                path = self.home / compositor.REGISTRY_FILENAME
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(content)
                self.assertEqual(self.run_main()[0], 0)

    def test_a_broken_registry_says_so_rather_than_printing_a_blank_line(self):
        # A blank line reads as "no status", which is indistinguishable from
        # working correctly.
        (self.home / compositor.REGISTRY_FILENAME).write_text("{ nope")
        out = self.run_main()[1]
        self.assertTrue(out.strip())
        self.assertIn("unreadable", out)

    def test_an_empty_registry_renders_nothing_at_all(self):
        self.write_registry()
        self.assertEqual(self.run_main()[1].strip(), "")

    def test_a_healthy_provider_renders_into_the_line(self):
        path = self.answering("good3", wire())
        self.write_registry(providers=[provider_document("fornax", [path])])
        self.assertIn("Verified", self.run_main()[1])

    def test_the_depth_marker_stops_us_printing_a_second_copy(self):
        path = self.answering("good4", wire())
        self.write_registry(providers=[provider_document("fornax", [path])])
        with unittest.mock.patch.dict(
            os.environ, {compositor.DEPTH_ENV: str(compositor.MAX_DEPTH)}
        ):
            code, out = self.run_main()
        self.assertEqual((code, out), (0, ""))

    def test_the_depth_marker_is_passed_to_children(self):
        sink = self.home / "depth.txt"
        # Warmed, and the warm-up's own write to the sink is why that is safe to
        # do here: it records the ambient (unset) marker, which the run under
        # test then overwrites. Unwarmed, the fixture's first execution can cost
        # more than the provider timeout and the sink is never written at all.
        path = self.script("reporting", f'printf "%s" "${compositor.DEPTH_ENV}" > {sink}\nexit 1\n')
        self.write_registry(providers=[provider_document("fornax", [path])])
        self.run_main()
        self.assertEqual(sink.read_text(), "1")

    def test_stdin_is_read_even_when_there_is_nothing_to_forward_it_to(self):
        self.write_registry()
        self.assertEqual(self.run_main(b'{"a": 1}')[0], 0)

    def test_an_empty_stdin_is_not_an_error(self):
        path = self.answering("good5", wire())
        self.write_registry(providers=[provider_document("fornax", [path])])
        self.assertIn("Verified", self.run_main(b"")[1])

    def test_exactly_one_line_is_printed(self):
        good = self.answering("good6", wire())
        upstream = self.script("theirs3", "printf 'A'\n")
        self.write_registry(
            upstream={"command": f"'{upstream}'"},
            providers=[provider_document("fornax", [good])],
        )
        out = self.run_main()[1]
        self.assertEqual(out.count("\n"), 1)
        self.assertTrue(out.endswith("\n"))

    def test_a_width_budget_is_honoured(self):
        good = self.answering("good7", wire())
        self.write_registry(
            providers=[provider_document("fornax", [good])],
            presentation={"mode": "plain", "width_budget": 30},
        )
        out = self.run_main()[1].rstrip("\n")
        self.assertLessEqual(render.display_width(out), 30)

    def test_repeated_renders_produce_the_same_line(self):
        # This command runs every few seconds for the life of a session, so a
        # render that is not a pure function of the state it read would make the
        # line flicker between two truths without either of them changing.
        good = self.answering("good8", wire())
        upstream = self.script("theirs4", "printf 'A'\n")
        self.write_registry(
            upstream={"command": f"'{upstream}'"},
            providers=[provider_document("fornax", [good])],
        )
        first = self.run_main()[1]
        self.assertEqual(self.run_main()[1], first)

    def test_a_render_writes_nowhere_but_the_cache(self):
        # Asserted over the whole tree rather than against a named file, because
        # the risk is a write nobody thought to look for -- a receipt, a log, a
        # backup, a lock. Anything this render creates outside the cache appears
        # here as a new path, whatever it is called.
        good = self.answering("good9", wire())
        self.write_registry(providers=[provider_document("fornax", [good])])
        before = {
            path: path.stat().st_mtime_ns
            for path in self.home.rglob("*")
            if path.is_file()
        }
        self.run_main()
        after = {
            path: path.stat().st_mtime_ns
            for path in self.home.rglob("*")
            if path.is_file()
        }
        cache = compositor.cache_dir()
        self.assertEqual(
            {p for p in after if p not in before or after[p] != before[p]},
            {p for p in after if cache in p.parents},
        )

    def test_the_recursion_guard_holds_in_a_real_child_process(self):
        # The in-process test covers the marker; this one proves a real child
        # inherits it, which is the case the guard exists for.
        self.write_registry()
        result = subprocess.run(
            [sys.executable, compositor.__file__],
            input=b"{}",
            capture_output=True,
            env=dict(
                os.environ,
                **{
                    compositor.STATE_HOME_ENV: str(self.home),
                    compositor.DEPTH_ENV: str(compositor.MAX_DEPTH),
                },
            ),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"refusing to recurse", result.stderr)


class TestNothingLeaksAndNothingLeaksOut(FixtureCase):
    """Process containment, and output containment."""

    CANARY = "CANARY" + "-" + "S" * 8

    def running(self, marker: str) -> int:
        """Count live processes whose command line contains the marker.

        Counted from `ps` rather than asked of `pgrep -c`, and with `check=True`,
        because a count is what these tests treat as evidence of containment. A
        `pgrep` that rejects its own arguments prints nothing to stdout, which is
        indistinguishable from finding no process — a leak canary that reports
        all-clear because it never ran.
        """
        listing = subprocess.run(
            ["ps", "-A", "-o", "command="], capture_output=True, text=True, check=True
        ).stdout
        return sum(1 for line in listing.splitlines() if marker in line)

    def settled(self, marker: str, *, limit: float = 5.0) -> int:
        """The marker's process count once it has reached zero, else its count.

        Polled rather than sampled after a fixed sleep. A `SIGKILL` to a process
        group is delivered promptly but the kernel's teardown is not
        instantaneous, and on a loaded machine a single sample taken just after
        the kill catches a process that is already dying — a flake that reads as
        a containment failure. This cannot hide a real leak: a leaked worker
        sleeps for `LEAK_SLEEP` seconds, far longer than the limit here, so it is
        still counted.
        """
        deadline = time.monotonic() + limit
        while True:
            count = self.running(marker)
            if count == 0 or time.monotonic() >= deadline:
                return count
            time.sleep(0.05)

    def forking_fixture(self, name: str) -> tuple[str, str]:
        """A provider that forks a worker and then hangs; the worker is the canary.

        The worker's own path is the marker, because it is unique to this test
        run. It sleeps in a loop rather than in a single call so that the shell
        does not replace itself with `sleep` — that optimisation would leave the
        surviving process named `sleep`, with the marker nowhere `pgrep -f` can
        see it, and the canary would report no leak however badly we leaked.
        """
        worker = self.hanging_script(
            f"{name}-worker", f"n=0\nwhile [ $n -lt {LEAK_SLEEP} ]; do sleep 1; n=$((n+1)); done\n"
        )
        parent = self.hanging_script(name, f"'{worker}' &\nsleep {LEAK_SLEEP}\n")
        self.addCleanup(subprocess.run, ["pkill", "-f", worker], capture_output=True)
        return parent, worker

    def test_a_timed_out_provider_leaves_no_process_behind(self):
        # This command re-runs every few seconds for the life of a session, so a
        # worker orphaned per refresh accumulates into a real process count.
        parent, worker = self.forking_fixture("orphan-probe")
        self.assertEqual(self.running(worker), 0)
        compositor.run_provider(self.entry("fornax", parent), IMPATIENT_MS, self.home)
        self.assertEqual(self.settled(worker), 0)

    def test_a_timed_out_upstream_leaves_no_process_behind(self):
        parent, worker = self.forking_fixture("orphan-upstream")
        compositor.run_upstream(f"'{parent}'", b"", IMPATIENT_MS)
        self.assertEqual(self.settled(worker), 0)

    def test_a_forked_worker_would_survive_if_only_the_child_were_killed(self):
        # Guards the guard: the two tests above only mean something if this
        # fixture does in fact leave a process behind when the process group is
        # not killed, which is what `subprocess.run(timeout=)` would do.
        parent, worker = self.forking_fixture("orphan-control")
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run([parent], capture_output=True, timeout=IMPATIENT_MS / 1000)
        time.sleep(0.5)
        self.assertGreaterEqual(self.running(worker), 1)

    def test_a_hanging_provider_cannot_stall_the_render_by_holding_our_pipe(self):
        # An orphan holding the write end of our stdout pipe can delay EOF for
        # as long as it likes; the wait after the kill is bounded for that.
        parent, _ = self.forking_fixture("pipe-holder")
        started = time.monotonic()
        compositor.run_provider(self.entry("fornax", parent), IMPATIENT_MS, self.home)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_a_secret_shaped_field_never_reaches_the_line(self):
        path = self.answering(
            "leaky",
            wire(
                segments=[
                    {
                        "key": "latest_verdict",
                        "state": "ok",
                        "label": "Verified",
                        "api_key": self.CANARY,
                        "token": self.CANARY,
                        "prompt": self.CANARY,
                    }
                ]
            ),
        )
        self.write_registry(providers=[provider_document("fornax", [path])])
        out = self.run_main()[1]
        self.assertNotIn(self.CANARY, out)
        self.assertIn("Verified", out, "the decoys were dropped but so was the real answer")

    def test_a_secret_shaped_label_is_refused_outright(self):
        path = self.answering(
            "leaky2",
            wire(
                segments=[
                    {
                        "key": "latest_verdict",
                        "state": "ok",
                        "label": f"token=sk-live-{self.CANARY}",
                    }
                ]
            ),
        )
        self.write_registry(providers=[provider_document("fornax", [path])])
        out = self.run_main()[1]
        self.assertNotIn(self.CANARY, out)
        self.assertIn("invalid status", out)

    def test_our_own_state_paths_do_not_reach_the_line(self):
        path = self.home / compositor.REGISTRY_FILENAME
        for label, content in (
            ("malformed", "{ nope"),
            ("unsupported version", '{"registry_version": 99}'),
            ("invalid provider id", '{"registry_version": 1, "providers": [{"provider": "N O"}]}'),
        ):
            with self.subTest(label=label):
                path.write_text(content)
                self.assertNotIn(str(self.home), self.run_main()[1])

    def test_the_detailed_diagnostic_goes_to_the_other_stream(self):
        # The detail names our state path, which is useful to whoever is
        # debugging and has no business on the user's statusline.
        (self.home / compositor.REGISTRY_FILENAME).write_text("{ nope")
        out = self.run_main()[1]
        self.assertIn(str(self.home), self.diagnostics)
        self.assertNotIn(str(self.home), out)

    def test_an_unreadable_registry_path_does_not_reach_the_line(self):
        path = self.home / compositor.REGISTRY_FILENAME
        path.mkdir()
        self.addCleanup(path.rmdir)
        self.assertNotIn(str(self.home), self.run_main()[1])

    def test_the_canary_would_be_visible_if_it_were_rendered(self):
        # Guards the guard: the assertions above only mean something if a value
        # of this shape would in fact show up in the output.
        upstream = self.script("shouting", f"printf '{self.CANARY}'\n")
        self.write_registry(upstream={"command": f"'{upstream}'"})
        self.assertIn(self.CANARY, self.run_main()[1])


class TestHotPathContainment(FixtureCase):
    """What this module is structurally incapable of doing on a render."""

    SOURCE = pathlib.Path(compositor.__file__).read_text()

    def test_no_network_client_is_imported(self):
        for module in ("urllib", "http", "socket", "requests", "ssl", "smtplib", "ftplib"):
            with self.subTest(module=module):
                self.assertNotRegex(self.SOURCE, rf"(?m)^import {module}\b")
                self.assertNotRegex(self.SOURCE, rf"(?m)^from {module}\b")

    def test_no_recursive_directory_walk_is_used(self):
        for call in ("os.walk", "rglob", "glob.glob", "iterdir"):
            with self.subTest(call=call):
                self.assertNotIn(call, self.SOURCE)

    def test_only_the_cache_directory_is_ever_created(self):
        # Two creation sites would be two things to reason about at uninstall.
        self.assertEqual(self.SOURCE.count(".mkdir("), 1)

    def test_there_is_exactly_one_way_to_start_a_child(self):
        # One execution helper, so there is no second path that could forget the
        # time bound or the process group.
        self.assertEqual(self.SOURCE.count("subprocess.Popen("), 1)
        self.assertEqual(self.SOURCE.count("subprocess.run("), 0)

    def test_a_shell_is_used_for_exactly_one_thing(self):
        # The user's own command. Everything else is an argv, with no shell in
        # the middle of it.
        self.assertEqual(len(re.findall(r"shell=True", self.SOURCE)), 1)

    def test_the_worst_case_render_is_bounded_by_the_declared_maximums(self):
        self.assertLessEqual(compositor.MAX_DEADLINE_MS, 3000)
        self.assertLessEqual(compositor.MAX_PROVIDER_TIMEOUT_MS, compositor.MAX_DEADLINE_MS)
        self.assertLessEqual(compositor.DEFAULT_DEADLINE_MS, 1000)

    def test_a_full_render_with_three_providers_is_prompt(self):
        providers = []
        for product in ("fornax", "circinus", "libra-governor"):
            path = self.answering(f"prompt-{product}", wire(provider=product))
            providers.append(provider_document(product, [path]))
        upstream = self.script("theirs4", "printf 'THEIRS'\n")
        self.write_registry(upstream={"command": f"'{upstream}'"}, providers=providers)
        self.run_main()  # warm whatever this platform wants to warm
        started = time.monotonic()
        self.run_main()
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
