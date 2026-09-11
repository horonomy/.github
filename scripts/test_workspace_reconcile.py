#!/usr/bin/env python3
"""Tests for scripts/workspace_reconcile.py (HORO-982 §3).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workspace_reconcile as wr
import repo_bootstrap as rb


def _tmp_git_repo(*, remote: str | None = None) -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=d, check=True)
    return d


class ResolveRootsTest(unittest.TestCase):
    def test_explicit_roots_used_verbatim(self) -> None:
        roots = wr.resolve_roots(["/a", "/b"])
        self.assertEqual([str(r) for r in roots], ["/a", "/b"])

    def test_env_var_used_when_no_explicit_roots(self) -> None:
        with mock.patch.dict("os.environ", {"HORONOM_RECONCILE_ROOTS": "/x:/y"}, clear=False):
            roots = wr.resolve_roots(None)
        self.assertEqual([str(r) for r in roots], ["/x", "/y"])

    def test_raises_when_nothing_given(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(wr.ReconcileError):
                wr.resolve_roots(None)


class DiscoverReposTest(unittest.TestCase):
    def test_missing_root_returns_empty(self) -> None:
        self.assertEqual(wr.discover_repos(Path("/definitely/does/not/exist")), [])

    def test_finds_direct_child_git_repos_only(self) -> None:
        root = Path(tempfile.mkdtemp())
        repo_a = root / "repo-a"
        repo_a.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo_a, check=True)
        (root / "not-a-repo").mkdir()
        (root / "some-file.txt").write_text("x", encoding="utf-8")
        nested = root / "nested-dir" / "repo-b"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=nested, check=True)

        found = wr.discover_repos(root)
        self.assertEqual(found, [repo_a])  # nested-dir/repo-b is NOT found — one level only


class IsWorktreeTest(unittest.TestCase):
    def test_main_checkout_is_not_a_worktree(self) -> None:
        repo = _tmp_git_repo()
        self.assertFalse(wr.is_worktree(repo))

    def test_linked_worktree_is_detected(self) -> None:
        main_repo = _tmp_git_repo()
        (main_repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=main_repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=main_repo, check=True)
        wt_path = Path(tempfile.mkdtemp()) / "linked-worktree"
        subprocess.run(["git", "worktree", "add", "-q", str(wt_path), "-b", "wt-branch"], cwd=main_repo, check=True)
        self.assertTrue(wr.is_worktree(wt_path))
        self.assertFalse(wr.is_worktree(main_repo))


class ClassifyModeTest(unittest.TestCase):
    def test_adopt_when_remote_matches_expected_org(self) -> None:
        repo = _tmp_git_repo(remote="https://github.com/horonomy/.github.git")
        self.assertEqual(wr.classify_mode(repo, expected_org="horonomy"), "adopt")

    def test_consume_when_remote_is_a_different_org(self) -> None:
        repo = _tmp_git_repo(remote="https://github.com/ai-agent-assembly/agent-assembly.git")
        self.assertEqual(wr.classify_mode(repo, expected_org="horonomy"), "consume")

    def test_none_when_no_remote(self) -> None:
        repo = _tmp_git_repo()
        self.assertIsNone(wr.classify_mode(repo, expected_org="horonomy"))


class MapOutcomeTest(unittest.TestCase):
    def test_written_maps_to_updated(self) -> None:
        self.assertEqual(wr._map_outcome({"claude_md": "written"}), wr.UPDATED)

    def test_created_maps_to_updated(self) -> None:
        self.assertEqual(wr._map_outcome({"agents_md": "created"}), wr.UPDATED)

    def test_would_write_count_in_skills_maps_to_updated(self) -> None:
        self.assertEqual(
            wr._map_outcome({"skills": "3 applicable (a, b, c); 3 would-write, 0 unchanged", "consumption_marker": "would-write"}),
            wr.UPDATED,
        )

    def test_unchanged_maps_to_already_current(self) -> None:
        self.assertEqual(wr._map_outcome({"claude_md": "unchanged", "agents_md": "unchanged"}), wr.ALREADY_CURRENT)

    def test_marker_always_written_does_not_alone_imply_updated(self) -> None:
        """adoption_marker/consumption_marker always say 'written' every
        real run (their own timestamp changes) — that alone must not make
        an otherwise fully-unchanged repo report UPDATED."""
        self.assertEqual(
            wr._map_outcome({"claude_md": "unchanged", "agents_md": "unchanged", "adoption_marker": "written"}),
            wr.ALREADY_CURRENT,
        )

    def test_skipped_conflict_maps_to_deferred_even_alongside_written(self) -> None:
        """A partial write (some artifacts written, one hand-edited file
        skipped) must report DEFERRED, not UPDATED — UPDATED would claim
        the whole repo was cleanly reconciled when a real conflict was
        left untouched."""
        self.assertEqual(
            wr._map_outcome({"claude_md": "written", "skills": "5 written, 1 skipped-conflict"}), wr.DEFERRED
        )


class ReconcileEndToEndTest(unittest.TestCase):
    """Real fixtures, real git, real rb.adopt()/rb.consume() calls — no
    mocking of the reconcile logic itself, only of load_governance_version
    (matching every other repo_bootstrap.py test's own pattern)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self._patch = mock.patch.object(rb.hw, "load_governance_version", return_value=1)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def _make_repo(self, name: str, *, remote: str | None) -> Path:
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        if remote:
            subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
        return repo

    def test_plan_never_writes_and_reports_updated_for_adopt_repo(self) -> None:
        self._make_repo("horonom-repo", remote="https://github.com/horonomy/widget.git")
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=False)
        self.assertEqual(len(outcomes), 1)
        o = outcomes[0]
        self.assertEqual(o.mode, "adopt")
        self.assertEqual(o.status, wr.UPDATED)
        self.assertFalse((self.root / "horonom-repo" / rb.ADOPTION_MARKER_FILENAME).exists())  # plan wrote nothing

    def test_apply_actually_writes_for_adopt_repo(self) -> None:
        self._make_repo("horonom-repo", remote="https://github.com/horonomy/widget.git")
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        self.assertEqual(outcomes[0].status, wr.UPDATED)
        self.assertTrue((self.root / "horonom-repo" / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_apply_consumes_a_non_horonomy_repo(self) -> None:
        self._make_repo("aa-repo", remote="https://github.com/ai-agent-assembly/agent-assembly.git")
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        self.assertEqual(outcomes[0].mode, "consume")
        self.assertEqual(outcomes[0].status, wr.UPDATED)
        self.assertTrue((self.root / "aa-repo" / rb.CONSUMPTION_MARKER_FILENAME).exists())
        self.assertFalse((self.root / "aa-repo" / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_second_apply_is_already_current(self) -> None:
        self._make_repo("horonom-repo", remote="https://github.com/horonomy/widget.git")
        wr.reconcile([self.root], expected_org="horonomy", apply=True)
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        self.assertEqual(outcomes[0].status, wr.ALREADY_CURRENT)

    def test_dirty_repo_is_deferred_not_overwritten(self) -> None:
        repo = self._make_repo("dirty-repo", remote="https://github.com/horonomy/widget.git")
        (repo / "tracked.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        self.assertEqual(outcomes[0].status, wr.DEFERRED)
        self.assertFalse((repo / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_worktree_is_deferred_never_touched(self) -> None:
        main_repo = self._make_repo("main-repo", remote="https://github.com/horonomy/widget.git")
        (main_repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=main_repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=main_repo, check=True
        )
        wt_path = self.root / "linked-worktree"
        subprocess.run(["git", "worktree", "add", "-q", str(wt_path), "-b", "wt-branch"], cwd=main_repo, check=True)

        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        by_path = {o.path: o for o in outcomes}
        self.assertEqual(by_path[str(wt_path)].status, wr.DEFERRED)
        self.assertFalse((wt_path / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_no_remote_repo_is_not_applicable(self) -> None:
        self._make_repo("no-remote-repo", remote=None)
        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        self.assertEqual(outcomes[0].status, wr.NOT_APPLICABLE)

    def test_multiple_roots_all_discovered(self) -> None:
        root_2 = Path(tempfile.mkdtemp())
        self._make_repo("repo-in-root-1", remote="https://github.com/horonomy/widget.git")
        (root_2 / "repo-in-root-2").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root_2 / "repo-in-root-2", check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/ai-agent-assembly/agent-assembly.git"],
            cwd=root_2 / "repo-in-root-2",
            check=True,
        )
        outcomes = wr.reconcile([self.root, root_2], expected_org="horonomy", apply=False)
        self.assertEqual(len(outcomes), 2)
        modes = {Path(o.path).name: o.mode for o in outcomes}
        self.assertEqual(modes, {"repo-in-root-1": "adopt", "repo-in-root-2": "consume"})

    def test_error_status_never_raises_stops_other_repos(self) -> None:
        """One repo raising AdoptionError must not abort reconcile of the
        others — a multi-repo operation partially applies, per this
        module's own docstring."""
        self._make_repo("good-repo", remote="https://github.com/horonomy/widget.git")
        bad_repo = self._make_repo("bad-repo", remote="https://github.com/horonomy/other.git")
        (bad_repo / rb.CONSUMPTION_MARKER_FILENAME).write_text("org: x\n", encoding="utf-8")  # forces adopt() to raise

        outcomes = wr.reconcile([self.root], expected_org="horonomy", apply=True)
        by_name = {Path(o.path).name: o for o in outcomes}
        self.assertEqual(by_name["good-repo"].status, wr.UPDATED)
        self.assertEqual(by_name["bad-repo"].status, wr.ERROR)


class MainCLIIntegrationTest(unittest.TestCase):
    def test_plan_end_to_end(self) -> None:
        root = Path(tempfile.mkdtemp())
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/horonomy/widget.git"], cwd=repo, check=True)
        with mock.patch.object(rb.hw, "load_governance_version", return_value=1):
            exit_code = wr.main(["plan", "--root", str(root)])
        self.assertEqual(exit_code, 0)
        self.assertFalse((repo / rb.ADOPTION_MARKER_FILENAME).exists())

    def test_missing_roots_is_an_error_not_a_crash(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            exit_code = wr.main(["plan"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
