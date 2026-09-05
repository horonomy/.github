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


class ResolveRepoNameTest(unittest.TestCase):
    """Regression: adopting from a worktree (named e.g.
    `official-website-wt-HORO-507` per this campaign's own worktree
    convention) must embed the canonical repo name in generated content,
    never the worktree directory's basename — found via manual testing
    against a real worktree, not a mocked unit test."""

    def test_resolves_name_from_github_remote_https(self) -> None:
        fake = lambda args: mock.Mock(  # noqa: E731
            returncode=0, stdout="origin\thttps://github.com/horonomy/official-website.git (fetch)\n"
        )
        name = rb.resolve_repo_name(Path("/some/worktree/official-website-wt-HORO-507"), run_git=fake)
        self.assertEqual(name, "official-website")

    def test_resolves_name_from_github_remote_ssh(self) -> None:
        fake = lambda args: mock.Mock(returncode=0, stdout="origin\tgit@github.com:horonomy/circinus.git (fetch)\n")  # noqa: E731
        name = rb.resolve_repo_name(Path("/some/worktree/circinus-wt-X"), run_git=fake)
        self.assertEqual(name, "circinus")

    def test_falls_back_to_directory_name_when_no_remote(self) -> None:
        fake = lambda args: mock.Mock(returncode=0, stdout="")  # noqa: E731
        name = rb.resolve_repo_name(Path("/some/fresh-repo"), run_git=fake)
        self.assertEqual(name, "fresh-repo")

    def test_falls_back_when_git_command_fails(self) -> None:
        fake = lambda args: mock.Mock(returncode=1, stdout="")  # noqa: E731
        name = rb.resolve_repo_name(Path("/some/dir"), run_git=fake)
        self.assertEqual(name, "dir")

    def test_shell_metacharacters_in_remote_url_do_not_leak_into_name(self) -> None:
        """HORO-533: a `git remote -v` value is attacker-influenced (whoever
        controls the repo's remote config), and the resolved name lands
        unescaped in generated content — including a copy-pasteable
        `adopt <name> --org ...` command line. The original regex excluded
        only `/`, whitespace, and `.`, so a remote crafted with a
        `;$(touch ...)` suffix produced a generated regenerate-command
        containing that exact injection payload — confirmed live before
        this fix. The positive allowlist must reject it and fall back to
        the safe directory-name default instead of capturing a truncated,
        still-dangerous fragment."""
        fake = lambda args: mock.Mock(  # noqa: E731
            returncode=0,
            stdout="origin\thttps://github.com/horonomy/foo;$(touch /tmp/pwn) (fetch)\n",
        )
        name = rb.resolve_repo_name(Path("/some/dir/safe-fallback-name"), run_git=fake)
        self.assertEqual(name, "safe-fallback-name")
        self.assertNotIn(";", name)
        self.assertNotIn("$", name)


class ValidateTargetRepoTest(unittest.TestCase):
    """SonarQube python:S2083 flagged scripts/repo_bootstrap.py's write path
    as "constructed from user-controlled data" (the `repo` CLI argument).
    This is a real CLI tool whose destination directory is always an
    intentional operator argument, not network input — but the explicit
    resolve+validate step this test locks in is still a genuine hardening:
    it rejects the filesystem root, a non-existent path, and a path that
    isn't actually a git repo, before any write is attempted."""

    def test_rejects_nonexistent_path(self) -> None:
        with self.assertRaises(rb.AdoptionError):
            rb._validate_target_repo(Path("/definitely/does/not/exist/anywhere"))

    def test_rejects_non_git_directory(self) -> None:
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(rb.AdoptionError):
            rb._validate_target_repo(d)

    def test_rejects_filesystem_root(self) -> None:
        with self.assertRaises(rb.AdoptionError):
            rb._validate_target_repo(Path("/"))

    def test_accepts_real_git_repo(self) -> None:
        repo = _tmp_git_repo()
        result = rb._validate_target_repo(repo)
        self.assertEqual(result, repo.resolve())


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


class AdoptSymlinkGuardTest(unittest.TestCase):
    """HORO-533: `adopt()` writes at `.horonom-adoption.yaml`, `AGENTS.md`,
    the CLAUDE.md entry point, and each projected skill file all used
    `Path.exists()` (which follows symlinks and returns False for a
    dangling one) as the "is this real content?" test — so a dangling
    symlink at any of those paths fell into the "doesn't exist, create it"
    branch and wrote straight through the link to wherever it pointed.
    Confirmed live against a real scratch repo before this fix (a real
    file appeared outside the repo at the dangling link's target)."""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_dangling_marker_symlink_is_refused_not_written_through(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "escape.txt"
        (self.repo / rb.ADOPTION_MARKER_FILENAME).symlink_to(outside)
        with self.assertRaises(rb.AdoptionError):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertFalse(outside.exists())

    def test_dangling_agents_md_symlink_is_refused_not_written_through(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "escape_agents.md"
        (self.repo / "AGENTS.md").symlink_to(outside)
        with self.assertRaises(rb.AdoptionError):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertFalse(outside.exists())

    def test_symlink_escaping_repo_to_a_real_file_is_refused(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "real_outside_file.yaml"
        outside.write_text("pre-existing content\n", encoding="utf-8")
        (self.repo / rb.ADOPTION_MARKER_FILENAME).symlink_to(outside)
        with self.assertRaises(rb.AdoptionError):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outside.read_text(encoding="utf-8"), "pre-existing content\n")

    def test_symlinked_ancestor_directory_is_refused_not_written_through(self) -> None:
        """Independent review (HORO-533) found the first version of this
        guard only checked `path.is_symlink()` on the leaf file — fully
        bypassable by making an ANCESTOR directory (e.g. `.claude`) the
        symlink instead, since the leaf itself is then never a symlink and
        the check short-circuited before ever resolving the real write
        location. Confirmed live: this wrote CLAUDE.md straight into the
        symlinked-to directory before the fix."""
        outside = Path(tempfile.mkdtemp())
        (self.repo / ".claude").symlink_to(outside)
        with self.assertRaises(rb.AdoptionError):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_pointing_inside_repo_is_treated_as_hand_authored(self) -> None:
        """The Eridanus case (ADR-0001): AGENTS.md is a real symlink to
        .claude/CLAUDE.md, entirely within the repo — this must keep working
        via the ordinary skipped-conflict path, not be rejected as unsafe."""
        (self.repo / ".claude").mkdir(parents=True)
        (self.repo / ".claude" / "CLAUDE.md").write_text("# hand-authored\n", encoding="utf-8")
        (self.repo / "AGENTS.md").symlink_to(Path(".claude") / "CLAUDE.md")
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["agents_md"], "skipped-conflict")
        self.assertTrue((self.repo / "AGENTS.md").is_symlink())


class CrossRepoSkillProjectionTest(unittest.TestCase):
    """HORO-507: an adopted repo gets the same canonical skill content
    Claude/Codex read from horonomy/.github itself — never a second
    hand-copied implementation (ADR-0005 decision #6)."""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_adopt_projects_all_canonical_skills_into_target_repo(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        claude_skills = sorted(p.parent.name for p in (self.repo / ".claude" / "skills").glob("*/SKILL.md"))
        codex_skills = sorted(p.stem for p in (self.repo / ".codex" / "skills").glob("*.md"))
        canonical = sorted(rb.project_skills.discover_skills())
        self.assertEqual(claude_skills, canonical)
        self.assertEqual(codex_skills, canonical)

    def test_projected_skill_matches_canonical_content(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        name = rb.project_skills.discover_skills()[0]
        canonical = (rb.project_skills.SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        projected = (self.repo / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(canonical, projected)
        self.assertTrue(projected.startswith(rb.project_skills.GENERATED_MARKER))

    def test_reports_skills_outcome(self) -> None:
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertIn("written", outcomes["skills"])

    def test_never_overwrites_a_hand_edited_projected_skill(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        name = rb.project_skills.discover_skills()[0]
        target = self.repo / ".claude" / "skills" / name / "SKILL.md"
        target.write_text("hand-edited, no marker\n", encoding="utf-8")
        outcomes = rb.adopt(self.repo, org="horonomy", force=True, now="2026-01-01T00:00:01+00:00")
        self.assertIn("skipped-conflict", outcomes["skills"])
        self.assertEqual(target.read_text(), "hand-edited, no marker\n")


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
