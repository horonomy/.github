#!/usr/bin/env python3
"""Tests for horonom doctor (HORO-510).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import doctor
import doctor_checks as checks
import horonom_codex as hc
import horonom_workspace as hw
import public_release_reconcile as prr


def _tmp_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    return d


class SkillAdapterMarkersTest(unittest.TestCase):
    def test_not_applicable_when_no_adapters_present(self) -> None:
        result = checks.check_skill_adapter_markers(_tmp_repo())
        self.assertEqual(result.status, checks.NOT_APPLICABLE)

    def test_pass_when_all_files_marked(self) -> None:
        repo = _tmp_repo()
        skill = repo / ".claude" / "skills" / "jira-delivery"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("<!-- horonom:generated -->\ncontent\n", encoding="utf-8")
        result = checks.check_skill_adapter_markers(repo)
        self.assertEqual(result.status, checks.PASS)

    def test_project_local_skill_without_marker_is_ignored(self) -> None:
        """HORO-533: dogfooding this check against the real Eridanus repo
        found it flagging Eridanus's own pre-existing, ADR-governed project
        skills (jira-ticket-lifecycle, adr-management, etc.) as "hand-edited"
        and telling the operator to restore them from horonomy/.github's
        canonical set — which doesn't contain those names at all. Only
        canonical company-projected skill names should ever be checked."""
        repo = _tmp_repo()
        local_skill = repo / ".claude" / "skills" / "jira-ticket-lifecycle"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("hand-authored, no marker — this is correct\n", encoding="utf-8")
        canonical_skill = repo / ".claude" / "skills" / "jira-delivery"
        canonical_skill.mkdir(parents=True)
        (canonical_skill / "SKILL.md").write_text("<!-- horonom:generated -->\ncontent\n", encoding="utf-8")
        result = checks.check_skill_adapter_markers(repo)
        self.assertEqual(result.status, checks.PASS)

    def test_only_project_local_skills_present_is_not_applicable(self) -> None:
        repo = _tmp_repo()
        local_skill = repo / ".claude" / "skills" / "jira-ticket-lifecycle"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("hand-authored, no marker — this is correct\n", encoding="utf-8")
        result = checks.check_skill_adapter_markers(repo)
        self.assertEqual(result.status, checks.NOT_APPLICABLE)

    def test_fail_when_a_projected_file_was_hand_edited(self) -> None:
        """Intentionally-broken adoption fixture: a projected SKILL.md with
        no generated marker at all — someone hand-edited a file that's
        supposed to be regenerated only."""
        repo = _tmp_repo()
        skill = repo / ".claude" / "skills" / "jira-delivery"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("hand-edited, no marker\n", encoding="utf-8")
        result = checks.check_skill_adapter_markers(repo)
        self.assertEqual(result.status, checks.FAIL)


class RepoAdoptionCheckTest(unittest.TestCase):
    def test_not_applicable_when_never_adopted(self) -> None:
        result = checks.check_repo_adoption(_tmp_repo())
        self.assertEqual(result.status, checks.NOT_APPLICABLE)

    def test_pass_after_real_adoption(self) -> None:
        import subprocess

        repo = _tmp_repo()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with mock.patch.object(checks.rb.hw, "load_governance_version", return_value=1):
            checks.rb.adopt(repo, org="horonomy", now="2026-01-01T00:00:00+00:00")
            result = checks.check_repo_adoption(repo)
        self.assertEqual(result.status, checks.PASS)


class RemoteSanityTest(unittest.TestCase):
    def test_pass_when_expected_org_remote_present(self) -> None:
        fake = mock.Mock(returncode=0, stdout="origin\thttps://github.com/horonomy/.github.git (fetch)\n")
        result = checks.check_remote_sanity(Path("/x"), "horonomy", run_git=lambda args: fake)
        self.assertEqual(result.status, checks.PASS)

    def test_warn_when_no_matching_remote(self) -> None:
        fake = mock.Mock(returncode=0, stdout="origin\thttps://github.com/someone-else/fork.git (fetch)\n")
        result = checks.check_remote_sanity(Path("/x"), "horonomy", run_git=lambda args: fake)
        self.assertEqual(result.status, checks.WARN)

    def test_fail_when_not_a_git_repo(self) -> None:
        fake = mock.Mock(returncode=128, stdout="")
        result = checks.check_remote_sanity(Path("/x"), "horonomy", run_git=lambda args: fake)
        self.assertEqual(result.status, checks.FAIL)

    def test_expected_org_is_regex_escaped(self) -> None:
        """Adversarial: an --expected-org value containing regex metacharacters
        must not be interpreted as a pattern (regex-injection guard)."""
        fake = mock.Mock(returncode=0, stdout="origin\thttps://github.com/horonomyX/evil.git (fetch)\n")
        result = checks.check_remote_sanity(Path("/x"), "horonom.", run_git=lambda args: fake)
        # "horonom." as a literal string does not match "horonomyX" — if the
        # dot were treated as a regex wildcard, this would wrongly PASS.
        self.assertEqual(result.status, checks.WARN)


class PublicReleaseAdoptionMappingTest(unittest.TestCase):
    """AC: 'False claims such as public surface complete when a surface is
    N/A/not-yet-public are prevented.' Directly tests every state mapping,
    with special attention to NOT_YET_PUBLIC/DEFERRED never becoming PASS."""

    def _evidence_file(self, lifecycle: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "product.yaml"
        p.write_text(
            f"product: ghost\nclaimed_lifecycle: {lifecycle}\ngithub:\n  org: horonomy\n  repo: ghost\n",
            encoding="utf-8",
        )
        return p

    def test_not_yet_public_never_maps_to_pass(self) -> None:
        fx = prr.Fetchers(gh_api=lambda p: [] if p.endswith("releases") else {"description": "x"}, http_get=lambda u: (200, "no mention"))
        evidence_path = self._evidence_file("not_yet_public")
        with mock.patch.object(checks.prr, "COMPANY_YAML_PATH", _empty_company_yaml()), mock.patch.object(
            checks.prr, "PROFILE_README_PATH", _empty_readme()
        ):
            result = checks.check_public_release_adoption(evidence_path, fetchers=fx)
        self.assertEqual(result.status, checks.NOT_APPLICABLE)
        self.assertIn("not evaluated as complete", result.detail)

    def test_deferred_never_maps_to_pass(self) -> None:
        fx = prr.Fetchers(gh_api=lambda p: [] if p.endswith("releases") else {"description": "x"}, http_get=lambda u: (200, "no mention"))
        evidence_path = self._evidence_file("experimental")
        with mock.patch.object(checks.prr, "COMPANY_YAML_PATH", _empty_company_yaml()), mock.patch.object(
            checks.prr, "PROFILE_README_PATH", _empty_readme()
        ):
            result = checks.check_public_release_adoption(evidence_path, fetchers=fx)
        self.assertIn(result.status, (checks.NOT_APPLICABLE, checks.WARN))
        self.assertNotEqual(result.status, checks.PASS)

    def test_missing_evidence_file_is_not_applicable_not_fail(self) -> None:
        result = checks.check_public_release_adoption(Path("/nonexistent/evidence.yaml"))
        self.assertEqual(result.status, checks.NOT_APPLICABLE)

    def test_failed_release_state_maps_to_fail(self) -> None:
        # beta claim + zero releases => FAILED per public_release_reconcile.
        fx = prr.Fetchers(gh_api=lambda p: [] if p.endswith("releases") else {"description": "x"}, http_get=lambda u: (200, "x"))
        evidence_path = self._evidence_file("beta")
        with mock.patch.object(checks.prr, "COMPANY_YAML_PATH", _empty_company_yaml()), mock.patch.object(
            checks.prr, "PROFILE_README_PATH", _empty_readme()
        ):
            result = checks.check_public_release_adoption(evidence_path, fetchers=fx)
        self.assertEqual(result.status, checks.FAIL)


def _empty_company_yaml() -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "company.yaml"
    p.write_text("products: []\n", encoding="utf-8")
    return p


def _empty_readme() -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "README.md"
    p.write_text("nothing relevant\n", encoding="utf-8")
    return p


class SecretScanTest(unittest.TestCase):
    def test_pass_on_clean_files(self) -> None:
        repo = _tmp_repo()
        f = repo / "CLAUDE.md"
        f.write_text("just navigation text\n", encoding="utf-8")
        result = checks.check_no_secrets_in_generated_files([f])
        self.assertEqual(result.status, checks.PASS)

    def test_fail_on_planted_secret(self) -> None:
        """Intentionally-broken fixture: a generated file that somehow
        picked up a private-key-shaped block."""
        repo = _tmp_repo()
        f = repo / "AGENTS.md"
        f.write_text("-----BEGIN PRIVATE KEY-----\nMIIBogIBAAJ...\n-----END PRIVATE KEY-----\n", encoding="utf-8")
        result = checks.check_no_secrets_in_generated_files([f])
        self.assertEqual(result.status, checks.FAIL)

    def test_ignores_nonexistent_paths(self) -> None:
        result = checks.check_no_secrets_in_generated_files([Path("/does/not/exist.md")])
        self.assertEqual(result.status, checks.PASS)


class WorkspaceBootstrapCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fail_when_not_bootstrapped(self) -> None:
        result = checks.check_workspace_bootstrap(self.root)
        self.assertEqual(result.status, checks.FAIL)

    def test_pass_on_healthy_workspace(self) -> None:
        """AC: 'at least one healthy workspace' fixture."""
        hw.do_sync(self.root, force=False, dry_run=False)
        result = checks.check_workspace_bootstrap(self.root)
        self.assertEqual(result.status, checks.PASS)

    def test_warn_on_stale_workspace(self) -> None:
        hw.do_sync(self.root, force=False, dry_run=False)
        (self.root / "CLAUDE.md").write_text("stray edit\n", encoding="utf-8")
        result = checks.check_workspace_bootstrap(self.root)
        self.assertEqual(result.status, checks.WARN)


class OverallComputationTest(unittest.TestCase):
    def test_fail_beats_warn_and_pass(self) -> None:
        results = [
            checks.CheckResult("a", checks.PASS, ""),
            checks.CheckResult("b", checks.WARN, ""),
            checks.CheckResult("c", checks.FAIL, ""),
        ]
        self.assertEqual(doctor.compute_overall(results), checks.FAIL)

    def test_not_applicable_and_pass_only_is_pass(self) -> None:
        results = [
            checks.CheckResult("a", checks.PASS, ""),
            checks.CheckResult("b", checks.NOT_APPLICABLE, ""),
        ]
        self.assertEqual(doctor.compute_overall(results), checks.PASS)

    def test_warn_beats_pass_and_not_applicable(self) -> None:
        results = [
            checks.CheckResult("a", checks.PASS, ""),
            checks.CheckResult("b", checks.NOT_APPLICABLE, ""),
            checks.CheckResult("c", checks.WARN, ""),
        ]
        self.assertEqual(doctor.compute_overall(results), checks.WARN)


class MainCLIIntegrationTest(unittest.TestCase):
    """End-to-end through doctor.main() against a real healthy fixture and a
    real broken fixture, satisfying the AC's 'detects at least one broken
    adoption fixture and one healthy workspace' as a single CLI run."""

    def test_healthy_workspace_reports_overall_pass_or_warn_not_fail(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name) / "workspace"
            exit_code = doctor.main(["--workspace-root", str(root)])
            # First run bootstraps nothing (doctor is read-only) — expect FAIL
            # since it hasn't been bootstrapped yet; this documents that
            # doctor never silently treats "never bootstrapped" as healthy.
            self.assertEqual(exit_code, 1)

            hw.do_sync(root, force=False, dry_run=False)
            exit_code = doctor.main(["--workspace-root", str(root), "--json"])
            self.assertEqual(exit_code, 0)
        finally:
            tmp.cleanup()

    def test_broken_repo_fixture_reports_overall_fail(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            repo = Path(tmp.name) / "repo"
            skill = repo / ".claude" / "skills" / "jira-delivery"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("hand-edited, no marker\n", encoding="utf-8")
            exit_code = doctor.main(["--repo", str(repo)])
            self.assertEqual(exit_code, 1)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
