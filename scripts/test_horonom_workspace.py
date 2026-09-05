#!/usr/bin/env python3
"""Tests for the Horonom workspace bootstrap script (HORO-506).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import horonom_workspace as hw


class ResolveWorkspaceRootTest(unittest.TestCase):
    def test_explicit_root_wins(self) -> None:
        with mock.patch.dict("os.environ", {"HORONOM_WORKSPACE_ROOT": "/env/path"}):
            root = hw.resolve_workspace_root("/explicit/path")
        self.assertEqual(root, Path("/explicit/path").resolve())

    def test_env_var_used_when_no_explicit_root(self) -> None:
        with mock.patch.dict("os.environ", {"HORONOM_WORKSPACE_ROOT": "/env/path"}):
            root = hw.resolve_workspace_root(None)
        self.assertEqual(root, Path("/env/path").resolve())

    def test_missing_both_raises_no_hardcoded_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(hw.WorkspaceError):
                hw.resolve_workspace_root(None)


class ManifestValidationTest(unittest.TestCase):
    def test_real_manifest_loads(self) -> None:
        repos = hw.load_manifest()
        names = {r["name"] for r in repos}
        self.assertIn("governance", names)
        self.assertIn("circinus", names)
        for entry in repos:
            self.assertIn(entry["category"], ("company", "product"))

    def _load_text(self, text: str) -> list[dict]:
        with mock.patch.object(hw, "MANIFEST_PATH", _write_tmp(text)):
            return hw.load_manifest()

    def test_rejects_unsafe_name(self) -> None:
        with self.assertRaises(hw.WorkspaceError):
            self._load_text(
                "repos:\n  - name: \"../etc\"\n    org: horonomy\n    repo: x\n    category: product\n"
            )

    def test_rejects_duplicate_name(self) -> None:
        with self.assertRaises(hw.WorkspaceError):
            self._load_text(
                "repos:\n"
                "  - name: a\n    org: horonomy\n    repo: a\n    category: product\n"
                "  - name: a\n    org: horonomy\n    repo: b\n    category: product\n"
            )

    def test_rejects_unknown_category(self) -> None:
        with self.assertRaises(hw.WorkspaceError):
            self._load_text(
                "repos:\n  - name: a\n    org: horonomy\n    repo: a\n    category: nope\n"
            )

    def test_rejects_empty_repo_list(self) -> None:
        with self.assertRaises(hw.WorkspaceError):
            self._load_text("repos: []\n")


def _write_tmp(text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "manifest.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class GovernanceVersionTest(unittest.TestCase):
    def test_real_governance_version_loads_as_int(self) -> None:
        version = hw.load_governance_version()
        self.assertIsInstance(version, int)
        self.assertGreaterEqual(version, 1)


class BootstrapIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bootstrap_then_sync_is_idempotent(self) -> None:
        first = hw.do_sync(self.root, force=False, dry_run=False)
        self.assertEqual(first["claude_md"], "written")
        self.assertEqual(first["agents_md"], "written")

        second = hw.do_sync(self.root, force=False, dry_run=False)
        self.assertEqual(second["claude_md"], "unchanged")
        self.assertEqual(second["agents_md"], "unchanged")

    def test_bootstrap_creates_company_and_products_dirs(self) -> None:
        hw.do_sync(self.root, force=False, dry_run=False)
        self.assertTrue((self.root / "company").is_dir())
        self.assertTrue((self.root / "products").is_dir())

    def test_bootstrap_writes_state_json_with_all_manifest_repos(self) -> None:
        hw.do_sync(self.root, force=False, dry_run=False)
        state = json.loads((self.root / ".horonom" / "state.json").read_text())
        manifest_names = {e["name"] for e in hw.load_manifest()}
        self.assertEqual(set(state["repos"]), manifest_names)
        for info in state["repos"].values():
            self.assertFalse(info["present"])  # nothing actually cloned in the temp dir

    def test_never_overwrites_user_authored_file(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "CLAUDE.md").write_text("hand-written, no marker\n", encoding="utf-8")
        result = hw.do_sync(self.root, force=False, dry_run=False)
        self.assertEqual(result["claude_md"], "skipped-conflict")
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "hand-written, no marker\n")

    def test_force_overwrites_user_authored_file(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "CLAUDE.md").write_text("hand-written, no marker\n", encoding="utf-8")
        result = hw.do_sync(self.root, force=True, dry_run=False)
        self.assertEqual(result["claude_md"], "written")
        self.assertTrue((self.root / "CLAUDE.md").read_text().startswith(hw.GENERATED_MARKER))

    def test_status_reports_not_bootstrapped_before_first_run(self) -> None:
        status = hw.do_status(self.root)
        self.assertFalse(status["bootstrapped"])

    def test_status_reports_no_drift_immediately_after_sync(self) -> None:
        hw.do_sync(self.root, force=False, dry_run=False)
        status = hw.do_status(self.root)
        self.assertTrue(status["bootstrapped"])
        self.assertFalse(status["manifest_drift"])
        self.assertEqual(status["files"]["CLAUDE.md"], "current")
        self.assertEqual(status["files"]["AGENTS.md"], "current")

    def test_status_detects_stale_file_after_manual_edit(self) -> None:
        hw.do_sync(self.root, force=False, dry_run=False)
        content = (self.root / "CLAUDE.md").read_text()
        (self.root / "CLAUDE.md").write_text(content + "\nstray manual edit\n", encoding="utf-8")
        status = hw.do_status(self.root)
        self.assertEqual(status["files"]["CLAUDE.md"], "stale")

    def test_dry_run_writes_nothing(self) -> None:
        result = hw.do_sync(self.root, force=False, dry_run=True)
        self.assertEqual(result["claude_md"], "written")
        self.assertFalse(self.root.exists())


class RepoPathEscapeGuardTest(unittest.TestCase):
    def test_repo_path_stays_under_root(self) -> None:
        root = Path("/tmp/some-workspace")
        entry = {"name": "circinus", "category": "product"}
        path = hw._repo_path(root, entry)
        self.assertEqual(path, (root / "products" / "circinus").resolve())


if __name__ == "__main__":
    unittest.main()
