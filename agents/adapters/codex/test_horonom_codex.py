#!/usr/bin/env python3
"""Tests for the Codex adapter (HORO-508).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s agents/adapters/codex
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import horonom_codex as hc


class ResolveWorkspaceRootTest(unittest.TestCase):
    def test_delegates_to_horonom_workspace_resolver(self) -> None:
        with mock.patch.dict("os.environ", {"HORONOM_WORKSPACE_ROOT": "/env/path"}):
            root = hc.resolve_workspace_root(None)
        self.assertEqual(root, Path("/env/path").resolve())

    def test_missing_root_raises_codex_adapter_error(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(hc.CodexAdapterError):
                hc.resolve_workspace_root(None)


class CodexHomeGuardTest(unittest.TestCase):
    def test_refuses_to_alias_real_global_codex_home(self) -> None:
        with mock.patch.object(Path, "home", return_value=Path("/Users/fakehome")):
            with self.assertRaises(hc.CodexAdapterError):
                hc._codex_home(Path("/Users/fakehome"))

    def test_normal_workspace_root_is_fine(self) -> None:
        with mock.patch.object(Path, "home", return_value=Path("/Users/fakehome")):
            codex_home = hc._codex_home(Path("/Users/fakehome/workspace"))
        self.assertEqual(codex_home, Path("/Users/fakehome/workspace/.codex").resolve())


class SyncAndStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir(parents=True)
        self._patch = mock.patch.object(hc.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def test_sync_then_status_ready(self) -> None:
        outcome = hc.do_sync(self.root, force=False)
        self.assertEqual(outcome, "written")
        status = hc.do_status(self.root)
        self.assertTrue(status["ready"])

    def test_sync_idempotent(self) -> None:
        hc.do_sync(self.root, force=False)
        second = hc.do_sync(self.root, force=False)
        self.assertEqual(second, "unchanged")

    def test_status_before_sync_reports_not_ready(self) -> None:
        status = hc.do_status(self.root)
        self.assertFalse(status["ready"])
        self.assertIn("missing", status["reason"])

    def test_status_detects_stale_config_after_governance_bump(self) -> None:
        hc.do_sync(self.root, force=False)
        with mock.patch.object(hc.hw, "load_governance_version", return_value=2):
            status = hc.do_status(self.root)
        self.assertFalse(status["ready"])

    def test_never_overwrites_hand_authored_config(self) -> None:
        codex_home = self.root / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "config.toml").write_text("hand-written\n", encoding="utf-8")
        outcome = hc.do_sync(self.root, force=False)
        self.assertEqual(outcome, "skipped-conflict")
        self.assertEqual((codex_home / "config.toml").read_text(), "hand-written\n")

    def test_verify_ready_raises_when_not_synced(self) -> None:
        with self.assertRaises(hc.CodexAdapterError):
            hc.verify_ready(self.root)

    def test_verify_ready_succeeds_after_sync(self) -> None:
        hc.do_sync(self.root, force=False)
        codex_home = hc.verify_ready(self.root)
        self.assertEqual(codex_home, (self.root / ".codex").resolve())


class StripLeadingSeparatorTest(unittest.TestCase):
    def test_strips_leading_double_dash(self) -> None:
        """Regression: `launch -- --version` previously passed a literal
        '--' through to codex, which then read '--version' as a positional
        prompt instead of the flag — a real bug found via manual end-to-end
        testing against the installed codex binary (0.147.0), not caught by
        any mocked unit test."""
        self.assertEqual(hc._strip_leading_separator(["--", "--version"]), ["--version"])

    def test_no_separator_passes_through_unchanged(self) -> None:
        self.assertEqual(hc._strip_leading_separator(["--version"]), ["--version"])

    def test_empty_list_unchanged(self) -> None:
        self.assertEqual(hc._strip_leading_separator([]), [])


class BuildLaunchEnvTest(unittest.TestCase):
    def test_sets_codex_home_without_mutating_caller_dict(self) -> None:
        base = {"PATH": "/usr/bin", "OTHER": "x"}
        env = hc.build_launch_env(Path("/tmp/ws/.codex"), base)
        self.assertEqual(env["CODEX_HOME"], "/tmp/ws/.codex")
        self.assertEqual(base, {"PATH": "/usr/bin", "OTHER": "x"})  # unmutated
        self.assertEqual(env["PATH"], "/usr/bin")


class FindRealCodexTest(unittest.TestCase):
    def test_raises_when_not_on_path(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(hc.CodexAdapterError):
                hc.find_real_codex()

    def test_refuses_self_exec(self) -> None:
        with mock.patch("shutil.which", return_value=str(Path(hc.__file__).resolve())):
            with self.assertRaises(hc.CodexAdapterError):
                hc.find_real_codex()

    def test_returns_real_binary_path(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/local/bin/codex"):
            result = hc.find_real_codex()
        self.assertEqual(result, "/usr/local/bin/codex")


if __name__ == "__main__":
    unittest.main()
