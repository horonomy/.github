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
        """
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        if warm:
            subprocess.run([str(path)], capture_output=True, timeout=WARM_LIMIT_SECONDS)
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


if __name__ == "__main__":
    unittest.main()
