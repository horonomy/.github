#!/usr/bin/env python3
"""Tests for scripts/repo_bootstrap.py (HORO-511).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import argparse
import os
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


def _fake_remote_git(remote_stdout: str):
    """A run_git fake that reports a clean `git status` and the given
    `git remote -v` output — so a test can control what org resolve_org()
    sees without also (accidentally, since a single-response Mock answers
    every call identically) making _git_status_clean() see remote-shaped
    text as an uncommitted change."""

    def run(args):
        if args and args[0] == "status":
            return mock.Mock(returncode=0, stdout="")
        if args and args[0] == "remote":
            return mock.Mock(returncode=0, stdout=remote_stdout)
        return mock.Mock(returncode=0, stdout="")

    return run


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

    def test_dry_run_reports_would_write_not_written(self) -> None:
        """Independent review (PR #44): a dry run must never claim
        'written'/'created' — consume() was fixed for this earlier in the
        same ticket; adopt() needed the matching fix."""
        outcomes = rb.adopt(self.repo, org="horonomy", dry_run=True, now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["claude_md"], "would-create")
        self.assertEqual(outcomes["agents_md"], "would-create")
        self.assertEqual(outcomes["adoption_marker"], "would-write")
        self.assertIn("would-write", outcomes["skills"])
        self.assertNotIn(" written", outcomes["skills"])

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


class ResolveOrgTest(unittest.TestCase):
    def test_resolves_org_from_remote(self) -> None:
        fake = mock.Mock(returncode=0, stdout="origin\thttps://github.com/ai-agent-assembly/agent-assembly.git (fetch)\n")
        self.assertEqual(rb.resolve_org(Path("/x"), run_git=lambda args: fake), "ai-agent-assembly")

    def test_none_when_no_remote(self) -> None:
        fake = mock.Mock(returncode=0, stdout="")
        self.assertIsNone(rb.resolve_org(Path("/x"), run_git=lambda args: fake))

    def test_none_when_git_fails(self) -> None:
        fake = mock.Mock(returncode=128, stdout="")
        self.assertIsNone(rb.resolve_org(Path("/x"), run_git=lambda args: fake))


class ConsumeTest(unittest.TestCase):
    """HORO-982: `consume` is the narrower, non-Horonom-repo counterpart of
    `adopt` — applicable-filtered skill projection only, no CLAUDE.md/
    AGENTS.md governance block, no cross-contamination with `adopt`'s own
    marker."""

    def setUp(self) -> None:
        self.repo = _tmp_git_repo()  # real git repo, no remote configured
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_consume_writes_only_marker_and_skills_no_governance_files(self) -> None:
        outcomes = rb.consume(self.repo, now="2026-01-01T00:00:00+00:00")
        self.assertTrue((self.repo / rb.CONSUMPTION_MARKER_FILENAME).is_file())
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((self.repo / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((self.repo / rb.ADOPTION_MARKER_FILENAME).exists())
        self.assertIn("written", outcomes["consumption_marker"])

    def test_consume_projects_only_applicable_skills(self) -> None:
        rb.consume(self.repo, now="2026-01-01T00:00:00+00:00")
        applicable = set(rb.project_skills.resolve_applicable_skills(self.repo))
        projected = {p.parent.name for p in (self.repo / ".claude" / "skills").glob("*/SKILL.md")}
        self.assertEqual(projected, applicable)

    def test_consume_refuses_a_real_horonomy_repo_without_force(self) -> None:
        # The guard keys off the repo's actual remote (see
        # test_org_override_does_not_bypass_horonomy_guard), not the --org
        # value, so a real horonomy remote is required to exercise it here.
        run_git = _fake_remote_git("origin\thttps://github.com/horonomy/.github.git (fetch)\n")
        with self.assertRaises(rb.AdoptionError):
            rb.consume(self.repo, run_git=run_git, now="2026-01-01T00:00:00+00:00")
        self.assertFalse((self.repo / rb.CONSUMPTION_MARKER_FILENAME).exists())

    def test_consume_allows_horonomy_repo_with_force(self) -> None:
        run_git = _fake_remote_git("origin\thttps://github.com/horonomy/.github.git (fetch)\n")
        outcomes = rb.consume(self.repo, force=True, run_git=run_git, now="2026-01-01T00:00:00+00:00")
        self.assertIn("written", outcomes["consumption_marker"])

    def test_org_override_does_not_bypass_horonomy_guard(self) -> None:
        """Independent review finding: passing --org must not silently talk
        the safety check out of looking at the repo's real remote."""
        run_git = _fake_remote_git("origin\thttps://github.com/horonomy/.github.git (fetch)\n")
        with self.assertRaises(rb.AdoptionError):
            rb.consume(self.repo, org="ai-agent-assembly", run_git=run_git, now="2026-01-01T00:00:00+00:00")
        self.assertFalse((self.repo / rb.CONSUMPTION_MARKER_FILENAME).exists())

    def test_org_override_bypass_still_possible_with_force(self) -> None:
        run_git = _fake_remote_git("origin\thttps://github.com/horonomy/.github.git (fetch)\n")
        outcomes = rb.consume(
            self.repo, org="ai-agent-assembly", force=True, run_git=run_git, now="2026-01-01T00:00:00+00:00"
        )
        self.assertIn("written", outcomes["consumption_marker"])

    def test_dry_run_reports_would_write_not_written(self) -> None:
        outcomes = rb.consume(self.repo, org="ai-agent-assembly", dry_run=True, now="2026-01-01T00:00:00+00:00")
        self.assertEqual(outcomes["consumption_marker"], "would-write")
        self.assertIn("would-write", outcomes["skills"])
        self.assertNotIn(" written", outcomes["skills"])

    def test_consume_refuses_when_already_adopted(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        with self.assertRaises(rb.AdoptionError):
            rb.consume(self.repo, org="ai-agent-assembly", now="2026-01-01T00:00:01+00:00")

    def test_adopt_refuses_when_already_consumed(self) -> None:
        rb.consume(self.repo, org="ai-agent-assembly", now="2026-01-01T00:00:00+00:00")
        with self.assertRaises(rb.AdoptionError):
            rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:01+00:00")

    def test_dry_run_writes_nothing(self) -> None:
        rb.consume(self.repo, org="ai-agent-assembly", dry_run=True, now="2026-01-01T00:00:00+00:00")
        self.assertFalse((self.repo / rb.CONSUMPTION_MARKER_FILENAME).exists())
        self.assertEqual(list((self.repo).glob(".claude/skills/*")), [])

    def test_consume_is_idempotent(self) -> None:
        rb.consume(self.repo, org="ai-agent-assembly", now="2026-01-01T00:00:00+00:00")
        outcomes = rb.consume(self.repo, org="ai-agent-assembly", now="2026-01-01T00:00:01+00:00")
        self.assertIn("unchanged", outcomes["skills"])

    def test_check_consumption_passes_after_fresh_consume(self) -> None:
        rb.consume(self.repo, org="ai-agent-assembly", now="2026-01-01T00:00:00+00:00")
        results = rb.check_consumption(self.repo, expected_governance_version=1)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["consumption_marker"], "PASS")
        self.assertEqual(statuses["consumption_skills"], "PASS")

    def test_check_consumption_not_applicable_when_never_consumed(self) -> None:
        results = rb.check_consumption(self.repo)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses["consumption_marker"], "NOT_APPLICABLE")


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

    def test_adopt_projects_only_applicable_canonical_skills(self) -> None:
        """HORO-982: adopt() used to project the full unfiltered catalog
        into every repo regardless of stack — a bare repo with no stack
        evidence now correctly receives only the always-applicable
        (no-manifest, non-design-qa) subset, matching
        resolve_applicable_skills()'s own evidence-based decision."""
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        claude_skills = sorted(p.parent.name for p in (self.repo / ".claude" / "skills").glob("*/SKILL.md"))
        codex_skills = sorted(p.stem for p in (self.repo / ".codex" / "skills").glob("*.md"))
        applicable = sorted(rb.project_skills.resolve_applicable_skills(self.repo))
        self.assertEqual(claude_skills, applicable)
        self.assertEqual(codex_skills, applicable)
        self.assertLess(len(applicable), len(rb.project_skills.discover_skills()))  # a real, non-trivial filter

    def test_projected_skill_matches_canonical_content(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        name = rb.project_skills.resolve_applicable_skills(self.repo)[0]
        canonical = (rb.project_skills.SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        projected = (self.repo / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(canonical, projected)
        self.assertTrue(projected.startswith(rb.project_skills.GENERATED_MARKER))

    def test_reports_skills_outcome(self) -> None:
        outcomes = rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertIn("written", outcomes["skills"])
        self.assertIn("applicable", outcomes["skills"])

    def test_never_overwrites_a_hand_edited_projected_skill(self) -> None:
        rb.adopt(self.repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        name = rb.project_skills.resolve_applicable_skills(self.repo)[0]
        target = self.repo / ".claude" / "skills" / name / "SKILL.md"
        target.write_text("hand-edited, no marker\n", encoding="utf-8")
        outcomes = rb.adopt(self.repo, org="horonomy", force=True, now="2026-01-01T00:00:01+00:00")
        self.assertIn("skipped-conflict", outcomes["skills"])
        self.assertEqual(target.read_text(), "hand-edited, no marker\n")

    def test_adopt_also_projects_applicable_skill_assets(self) -> None:
        """HORO-982: adopt() previously projected SKILL.md only, never the
        references/examples/scripts/tests assets consume() already got."""
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")  # real stack evidence -> rust-development
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        canonical_assets = rb.project_skills.discover_skill_assets("rust-development")
        if not canonical_assets:
            self.skipTest("rust-development currently has no asset files to verify against")
        for rel_path in canonical_assets:
            self.assertTrue((repo / ".claude" / "skills" / "rust-development" / rel_path).is_file())

    def test_readopt_removes_now_inapplicable_skill_projection(self) -> None:
        """The counterpart to filtering: a repo whose applicable set
        shrinks (e.g. Cargo.toml removed) must lose that skill's projected
        files on the next adopt, never accumulate stale dead content."""
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        self.assertTrue((repo / ".claude" / "skills" / "rust-development").is_dir())
        (repo / "Cargo.toml").unlink()

        outcomes = rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:01+00:00")
        self.assertFalse((repo / ".claude" / "skills" / "rust-development").exists())
        self.assertFalse((repo / ".codex" / "skills" / "rust-development.md").exists())
        self.assertIn("removed", outcomes["skills"])

    def test_readopt_never_removes_a_hand_edited_now_inapplicable_skill(self) -> None:
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        skill_md = repo / ".claude" / "skills" / "rust-development" / "SKILL.md"
        skill_md.write_text("hand-edited, no marker\n", encoding="utf-8")
        (repo / "Cargo.toml").unlink()

        rb.adopt(repo, org="horonomy", force=True, now="2026-01-01T00:00:01+00:00")
        self.assertTrue(skill_md.is_file())
        self.assertEqual(skill_md.read_text(), "hand-edited, no marker\n")

    def test_dry_run_orphan_removal_removes_nothing(self) -> None:
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        (repo / "Cargo.toml").unlink()

        outcomes = rb.adopt(repo, org="horonomy", dry_run=True, now="2026-01-01T00:00:01+00:00")
        self.assertTrue((repo / ".claude" / "skills" / "rust-development").is_dir())
        self.assertIn("removed", outcomes["skills"])  # reports what WOULD be removed

    def test_orphan_removal_never_deletes_a_skill_dir_with_no_skill_md_but_real_user_content(self) -> None:
        """Independent review (PR #45), bug 1: a skill directory that has
        no SKILL.md at all (deleted or never written) but does carry real
        user-authored content must never be deleted wholesale — there is
        no generated content there to safely remove in the first place."""
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        (repo / "Cargo.toml").unlink()
        skill_dir = repo / ".claude" / "skills" / "rust-development"
        (skill_dir / "SKILL.md").unlink()
        (skill_dir / "my-own-notes.txt").write_text("do not delete me\n", encoding="utf-8")

        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:01+00:00")
        self.assertTrue(skill_dir.is_dir())
        self.assertEqual((skill_dir / "my-own-notes.txt").read_text(), "do not delete me\n")

    def test_orphan_removal_never_deletes_a_hand_edited_asset_file(self) -> None:
        """Independent review (PR #45), bug 2: an asset file (no marker of
        its own, unlike SKILL.md) that was hand-edited must survive even
        though the skill's own SKILL.md is untouched and gets removed."""
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        asset_files = list((repo / ".claude" / "skills" / "rust-development" / "references").glob("*.md"))
        if not asset_files:
            self.skipTest("rust-development currently has no reference asset files to verify against")
        hand_edited = asset_files[0]
        hand_edited.write_text("hand-edited reference content, no marker\n", encoding="utf-8")
        (repo / "Cargo.toml").unlink()

        outcomes = rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:01+00:00")
        self.assertTrue(hand_edited.is_file())
        self.assertEqual(hand_edited.read_text(), "hand-edited reference content, no marker\n")
        # SKILL.md itself (never hand-edited) is still removed, and the
        # skill dir survives specifically because the hand-edited asset
        # is still in it.
        self.assertFalse((repo / ".claude" / "skills" / "rust-development" / "SKILL.md").exists())
        self.assertTrue((repo / ".claude" / "skills" / "rust-development").is_dir())
        self.assertIn("removed", outcomes["skills"])

    def test_orphan_removal_refuses_a_symlinked_skill_file(self) -> None:
        """Independent review (PR #45): confirms the existing
        _reject_unsafe_symlink guard actually fires on the new removal
        path too, for a skill file replaced by a symlink pointing outside
        the repo — the same HORO-533 bug class the write path already
        guards against. _reject_unsafe_symlink fires on any known-path
        entry that exists, before any content comparison, so this needn't
        even construct matching canonical content to prove the guard
        runs."""
        repo = _tmp_git_repo()
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        (repo / "Cargo.toml").unlink()
        skill_md = repo / ".claude" / "skills" / "rust-development" / "SKILL.md"
        outside = Path(tempfile.mkdtemp()) / "canary.txt"
        outside.write_text("do not delete me\n", encoding="utf-8")
        skill_md.unlink()
        skill_md.symlink_to(outside)

        with self.assertRaises(rb.AdoptionError):
            rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:01+00:00")
        self.assertTrue(outside.is_file())
        self.assertEqual(outside.read_text(), "do not delete me\n")


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

    def test_consume_then_check_end_to_end(self) -> None:
        repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            exit_code = rb.main(["consume", str(repo), "--org", "ai-agent-assembly"])
            self.assertEqual(exit_code, 0)
            exit_code = rb.main(["check", str(repo)])
            self.assertEqual(exit_code, 0)

    def test_check_reports_both_blocks_when_both_markers_present(self) -> None:
        """Independent review finding: `check` used to silently report only
        one mode's drift when both markers were present at once."""
        repo = _tmp_git_repo()
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            rb.main(["adopt", str(repo), "--org", "horonomy"])
            rb.main(["consume", str(repo), "--org", "ai-agent-assembly", "--force"])
            results = rb.check_consumption(repo)  # sanity: consumption marker really was written
            self.assertTrue((repo / rb.CONSUMPTION_MARKER_FILENAME).is_file())
            self.assertTrue((repo / rb.ADOPTION_MARKER_FILENAME).is_file())
            exit_code = rb._cmd_check(argparse.Namespace(repo=str(repo)))
        self.assertEqual(exit_code, 1)  # both-present is a FAIL, not silently PASS


class OnboardingContractTest(unittest.TestCase):
    """HORO-982 AC item 5, the "onboarding contract": a fresh coding-agent
    session opened in a representative repository must receive applicable
    stack Skills only, no unrelated language skill flood, and no
    dependency on another checkout being the current working directory.
    Written from the onboarding-session angle itself (what would a fresh
    session actually SEE after `adopt`/`consume`), not re-testing
    resolve_applicable_skills()'s own evidence table (covered elsewhere)."""

    def setUp(self) -> None:
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def _skill_names_visible_to_a_fresh_session(self, repo: Path) -> set[str]:
        """What a fresh Claude/Codex session opened in `repo` would
        actually discover under .claude/skills/ — the real onboarding
        surface, not an internal API result."""
        return {p.parent.name for p in (repo / ".claude" / "skills").glob("*/SKILL.md")}

    def test_pure_python_repo_sees_no_unrelated_language_skills(self) -> None:
        """A representative single-stack repo (real HORO-969 §3 example
        shape: a Python service) must never see rust-development,
        go-development, swift-development, typescript-development,
        container-development, or terraform-development — no flood."""
        repo = _tmp_git_repo()
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        visible = self._skill_names_visible_to_a_fresh_session(repo)
        for unrelated in ("rust-development", "go-development", "swift-development", "typescript-development", "container-development", "terraform-development"):
            self.assertNotIn(unrelated, visible)
        self.assertIn("python-development", visible)

    def test_multi_stack_repo_composes_correctly(self) -> None:
        """HORO-969 §3's own worked example: a PyO3-style SDK gets BOTH
        python-development and rust-development, not just one — evidence-
        based composition, not "one repo = one language" (a forbidden
        shortcut per that same section)."""
        repo = _tmp_git_repo()
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        visible = self._skill_names_visible_to_a_fresh_session(repo)
        self.assertIn("python-development", visible)
        self.assertIn("rust-development", visible)
        self.assertNotIn("go-development", visible)
        self.assertNotIn("swift-development", visible)

    def test_bare_repo_with_no_stack_evidence_still_gets_company_process_skills(self) -> None:
        """A repo with no filesystem stack evidence at all still onboards
        the company-wide, non-stack-dependent skills (jira-delivery,
        engineering-loop, etc.) — "no flood" doesn't mean "nothing"."""
        repo = _tmp_git_repo()
        rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        visible = self._skill_names_visible_to_a_fresh_session(repo)
        self.assertIn("engineering-loop", visible)
        self.assertIn("jira-delivery", visible)
        for stack_specific in ("rust-development", "python-development", "go-development", "design-qa"):
            self.assertNotIn(stack_specific, visible)

    def test_onboarding_does_not_depend_on_another_checkout_as_cwd(self) -> None:
        """Standalone-clone boundary: adopting a repo from a path far away
        from horonomy/.github's own checkout must work identically —
        adopt() resolves its canonical source via SCRIPTS_DIR/module
        location, never the process's current working directory."""
        repo = _tmp_git_repo()
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        original_cwd = os.getcwd()
        scratch_cwd = Path(tempfile.mkdtemp())
        try:
            os.chdir(scratch_cwd)
            rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
        finally:
            os.chdir(original_cwd)
        visible = self._skill_names_visible_to_a_fresh_session(repo)
        self.assertIn("python-development", visible)


if __name__ == "__main__":
    unittest.main()
