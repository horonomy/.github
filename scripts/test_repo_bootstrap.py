#!/usr/bin/env python3
"""Tests for scripts/repo_bootstrap.py (HORO-511).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import repo_bootstrap as rb


def _tmp_git_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    return d


def _clean_git(repo: Path):
    return lambda args: subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)


def _fake_clean_git(clean: bool):
    return lambda args: mock.Mock(returncode=0, stdout="" if clean else " M file\n")


class BoundedBlockInsertionTest(unittest.TestCase):
    def test_inserts_block_into_empty_file(self) -> None:
        block = rb.render_adoption_block(org="horonomy", repo="widget", governance_version=1)
        result = rb.apply_bounded_block("", block)
        self.assertEqual(result.strip(), block.strip())

    def test_inserts_block_after_first_line_preserving_rest(self) -> None:
        existing = "# CLAUDE.md — widget\n\nSome real repo-specific content.\n"
        block = rb.render_adoption_block(org="horonomy", repo="widget", governance_version=1)
        result = rb.apply_bounded_block(existing, block)
        self.assertIn("Some real repo-specific content.", result)
        self.assertIn(block, result)
        self.assertTrue(result.startswith("# CLAUDE.md — widget\n"))

    def test_idempotent_when_block_already_present(self) -> None:
        block = rb.render_adoption_block(org="horonomy", repo="widget", governance_version=1)
        existing = "# Title\n\n" + block + "\n\nRepo content below.\n"
        result = rb.apply_bounded_block(existing, block)
        self.assertEqual(result, existing)

    def test_refreshes_stale_block_without_touching_surrounding_content(self) -> None:
        old_block = rb.render_adoption_block(org="horonomy", repo="widget", governance_version=1)
        new_block = rb.render_adoption_block(org="horonomy", repo="widget", governance_version=2)
        existing = "# Title\n\n" + old_block + "\n\nRepo content below.\n"
        result = rb.apply_bounded_block(existing, new_block)
        self.assertIn(new_block, result)
        self.assertNotIn(old_block, result)
        self.assertIn("Repo content below.", result)


class AdoptFreshRepoFixtureTest(unittest.TestCase):
    """AC: 'A new fixture repo can be bootstrapped reproducibly.'"""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_adopt_creates_all_three_artifacts(self) -> None:
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["claude_md"], "created")
        self.assertEqual(outcomes["agents_md"], "created")
        self.assertEqual(outcomes["adoption_marker"], "written")
        self.assertTrue((self.repo / ".claude" / "CLAUDE.md").is_file())
        self.assertTrue((self.repo / "AGENTS.md").is_file())
        self.assertTrue((self.repo / rb.ADOPTION_MARKER_FILENAME).is_file())

    def test_adopt_is_reproducible_idempotent(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:01+00:00")
        self.assertEqual(outcomes["claude_md"], "unchanged")
        self.assertEqual(outcomes["agents_md"], "unchanged")

    def test_dry_run_writes_nothing(self) -> None:
        rb.adopt(self.repo, org="horonomy", dry_run=True, now="2026-01-01T00:00:00+00:00")
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((self.repo / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_check_passes_after_fresh_adoption(self) -> None:
        """AC: drift check works on a healthy fixture too — PASS, not just FAIL detection."""
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        results = rb.check(self.repo, expected_governance_version=1)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["adoption_marker"], "PASS")
        self.assertEqual(statuses["claude_md_block"], "PASS")
        self.assertEqual(statuses["agents_md"], "PASS")


class AdoptPreservesRepoSpecificContentTest(unittest.TestCase):
    """AC: 'Repo-level instructions contain only repository-specific facts/
    rules plus canonical pointers' and 'Preserve repo-local stricter rules
    ... rather than silently deleting them.'"""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()
        claude_dir = self.repo / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\nThis repo requires two reviewers, stricter than the company floor.\n",
            encoding="utf-8",
        )
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_existing_repo_specific_rule_survives_adoption(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        content = (self.repo / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("requires two reviewers, stricter than the company floor", content)
        self.assertIn(rb.BLOCK_BEGIN, content)

    def test_hand_authored_agents_md_is_reported_as_conflict_not_overwritten(self) -> None:
        (self.repo / "AGENTS.md").write_text("Hand-written, repo-specific AGENTS.md.\n", encoding="utf-8")
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["agents_md"], "skipped-conflict")
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "Hand-written, repo-specific AGENTS.md.\n")


class UncommittedChangesSafetyTest(unittest.TestCase):
    """AC: 'Adoption tooling ... is safe around existing uncommitted work.'"""

    def test_refuses_to_adopt_dirty_repo(self) -> None:
        repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            with self.assertRaises(rb.AdoptionError):
                rb.adopt(repo, org="horonomy", run_git=_fake_clean_git(False))

    def test_force_bypasses_the_dirty_check(self) -> None:
        repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            outcomes = rb.adopt(repo, org="horonomy", force=True, run_git=_fake_clean_git(False), now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["adoption_marker"], "written")


class CheckDriftDetectionTest(unittest.TestCase):
    """AC: 'Drift check fails when a generated adapter is stale or canonical
    governance reference is invalid.'"""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")

    def test_check_needs_no_workspace_root(self) -> None:
        # No $HORONOM_WORKSPACE_ROOT patched or set anywhere in this test —
        # check() takes an explicit expected_governance_version instead.
        results = rb.check(self.repo, expected_governance_version=1)
        self.assertTrue(results)

    def test_detects_missing_adoption_marker(self) -> None:
        (self.repo / rb.ADOPTION_MARKER_FILENAME).unlink()
        results = rb.check(self.repo, expected_governance_version=1)
        self.assertEqual(results[0][1], "FAIL")

    def test_detects_stale_governance_version(self) -> None:
        results = rb.check(self.repo, expected_governance_version=2)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["adoption_marker"], "WARN")

    def test_detects_hand_edited_claude_md_block(self) -> None:
        claude_path = self.repo / ".claude" / "CLAUDE.md"
        content = claude_path.read_text(encoding="utf-8")
        corrupted = content.replace("governance_version: 1", "governance_version: 999")
        claude_path.write_text(corrupted, encoding="utf-8")
        results = rb.check(self.repo, expected_governance_version=1)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["claude_md_block"], "FAIL")

    def test_detects_missing_generated_block(self) -> None:
        claude_path = self.repo / ".claude" / "CLAUDE.md"
        claude_path.write_text("# CLAUDE.md\n\nno adoption block at all\n", encoding="utf-8")
        results = rb.check(self.repo, expected_governance_version=1)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["claude_md_block"], "FAIL")


class MainCLIIntegrationTest(unittest.TestCase):
    def test_adopt_then_check_end_to_end(self) -> None:
        repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            exit_code = rb.main(["adopt", str(repo), "--org", "horonomy"])
            self.assertEqual(exit_code, 0)
            exit_code = rb.main(["check", str(repo)])
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
