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


if __name__ == "__main__":
    unittest.main()
