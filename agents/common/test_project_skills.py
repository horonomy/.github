#!/usr/bin/env python3
"""Tests for agents/common/project_skills.py (HORO-509).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s agents/common
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_skills as ps


class DiscoverSkillsTest(unittest.TestCase):
    def test_real_skills_directory_has_the_expected_skills(self) -> None:
        names = set(ps.discover_skills())
        self.assertEqual(
            names,
            {
                "governance-doctor",
                "repo-bootstrap",
                "jira-delivery",
                "release-assurance",
                "public-release-reconcile",
                "engineering-loop",
                "rust-development",
                "python-development",
                "typescript-development",
                "go-development",
                "terraform-development",
                "container-development",
                "shell-development",
                "swift-development",
                "credential-operations",
                "repo-scaffold",
                "product-validation",
                "design-qa",
                "documentation-experience",
            },
        )


class BuildProjectionsTest(unittest.TestCase):
    def test_every_skill_gets_a_claude_and_codex_projection(self) -> None:
        projections = ps.build_projections()
        names = ps.discover_skills()
        for name in names:
            self.assertIn(ps.CLAUDE_SKILLS_DIR / name / "SKILL.md", projections)
            self.assertIn(ps.CODEX_SKILLS_DIR / f"{name}.md", projections)

    def test_projection_contains_generated_marker_and_source_content(self) -> None:
        projections = ps.build_projections()
        name = ps.discover_skills()[0]
        canonical = (ps.SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        claude_content = projections[ps.CLAUDE_SKILLS_DIR / name / "SKILL.md"]
        self.assertTrue(claude_content.startswith(ps.GENERATED_MARKER))
        self.assertIn(canonical, claude_content)

    def test_all_projections_land_under_adapter_dirs(self) -> None:
        for path in ps.build_projections():
            under_claude = ps.CLAUDE_SKILLS_DIR in path.parents
            under_codex = ps.CODEX_SKILLS_DIR in path.parents
            self.assertTrue(under_claude or under_codex, f"{path} escaped the adapter dirs")


class MainCheckModeTest(unittest.TestCase):
    def test_check_mode_reports_zero_when_up_to_date(self) -> None:
        # Ensure a real run first so the working tree matches, then --check
        # should report clean (drift == 0) without writing anything further.
        ps.main([])
        exit_code = ps.main(["--check"])
        self.assertEqual(exit_code, 0)

    def test_check_mode_detects_drift(self) -> None:
        ps.main([])  # bring projections up to date first
        target = ps.CLAUDE_SKILLS_DIR / "jira-delivery" / "SKILL.md"
        original = target.read_text(encoding="utf-8")
        try:
            target.write_text(original + "\nstray hand edit\n", encoding="utf-8")
            exit_code = ps.main(["--check"])
            self.assertEqual(exit_code, 1)
        finally:
            target.write_text(original, encoding="utf-8")


class DiscoverSkillsRejectsUnsafeNamesTest(unittest.TestCase):
    def test_unsafe_directory_name_raises(self) -> None:
        with mock.patch.object(ps, "SKILLS_DIR", _fixture_with_unsafe_name()):
            with self.assertRaises(ps.SkillProjectionError):
                ps.discover_skills()


def _fixture_with_unsafe_name() -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp())
    bad = d / "../etc"
    # Can't literally create a dir named "..", so simulate the check by
    # using an uppercase name instead — same regex rejects both.
    bad = d / "Not_Safe"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("x", encoding="utf-8")
    return d


def _skills_fixture() -> Path:
    """A fresh SKILLS_DIR-shaped fixture with one plain skill, for tests
    that need to construct their own skill without touching the real
    agents/skills/ tree."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    skill = d / "sample-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# sample-skill\n", encoding="utf-8")
    return d


class SkillAssetDiscoveryTest(unittest.TestCase):
    def test_no_asset_dirs_returns_empty_list(self) -> None:
        with mock.patch.object(ps, "SKILLS_DIR", _skills_fixture()):
            self.assertEqual(ps.discover_skill_assets("sample-skill"), [])

    def test_files_under_all_four_asset_dirs_are_discovered(self) -> None:
        fixture = _skills_fixture()
        skill = fixture / "sample-skill"
        for d in ("references", "examples", "scripts", "tests"):
            (skill / d).mkdir()
            (skill / d / "a.md").write_text("x", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            found = {str(p) for p in ps.discover_skill_assets("sample-skill")}
        self.assertEqual(
            found,
            {"references/a.md", "examples/a.md", "scripts/a.md", "tests/a.md"},
        )

    def test_unrecognized_top_level_dir_is_not_descended(self) -> None:
        fixture = _skills_fixture()
        skill = fixture / "sample-skill"
        (skill / "not-a-recognized-asset-dir").mkdir()
        (skill / "not-a-recognized-asset-dir" / "a.md").write_text("x", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            self.assertEqual(ps.discover_skill_assets("sample-skill"), [])

    def test_symlinked_asset_dir_raises(self) -> None:
        fixture = _skills_fixture()
        skill = fixture / "sample-skill"
        outside = fixture.parent / "outside-target"
        outside.mkdir(exist_ok=True)
        (skill / "references").symlink_to(outside, target_is_directory=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.discover_skill_assets("sample-skill")

    def test_symlinked_file_inside_asset_dir_raises(self) -> None:
        fixture = _skills_fixture()
        skill = fixture / "sample-skill"
        (skill / "references").mkdir()
        outside_file = fixture.parent / "outside-secret.md"
        outside_file.write_text("not part of this skill", encoding="utf-8")
        (skill / "references" / "linked.md").symlink_to(outside_file)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.discover_skill_assets("sample-skill")


class LoadManifestTest(unittest.TestCase):
    def test_no_manifest_returns_none(self) -> None:
        with mock.patch.object(ps, "SKILLS_DIR", _skills_fixture()):
            self.assertIsNone(ps.load_manifest("sample-skill"))

    def test_valid_manifest_parses(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [rust, python]\n  requires_all: true\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            manifest = ps.load_manifest("sample-skill")
        self.assertEqual(manifest, {"stacks": ["rust", "python"], "requires_all": True})

    def test_manifest_defaults_requires_all_false(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [go]\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            manifest = ps.load_manifest("sample-skill")
        self.assertEqual(manifest, {"stacks": ["go"], "requires_all": False})

    def test_unknown_stack_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [cobol]\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")

    def test_missing_applicability_key_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "stacks: [rust]\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")

    def test_empty_stacks_list_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: []\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")

    def test_unrecognized_key_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [rust]\n  bogus_key: true\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")

    def test_symlinked_manifest_raises(self) -> None:
        fixture = _skills_fixture()
        skill = fixture / "sample-skill"
        real = fixture.parent / "real-manifest.yaml"
        real.write_text("applicability:\n  stacks: [rust]\n", encoding="utf-8")
        (skill / "manifest.yaml").symlink_to(real)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")


class ResolveApplicableSkillsTest(unittest.TestCase):
    def _fixture_with_two_skills(self) -> Path:
        fixture = _skills_fixture()  # gives "sample-skill", no manifest -> always applicable
        rust_only = fixture / "rust-only-skill"
        rust_only.mkdir()
        (rust_only / "SKILL.md").write_text("# rust-only-skill\n", encoding="utf-8")
        (rust_only / "manifest.yaml").write_text("applicability:\n  stacks: [rust]\n", encoding="utf-8")
        return fixture

    def test_no_manifest_skill_always_applicable(self) -> None:
        fixture = self._fixture_with_two_skills()
        repo = fixture.parent / "empty-repo"
        repo.mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertIn("sample-skill", applicable)
        self.assertNotIn("rust-only-skill", applicable)

    def test_stack_evidence_activates_manifest_skill(self) -> None:
        fixture = self._fixture_with_two_skills()
        repo = fixture.parent / "rust-repo"
        repo.mkdir(exist_ok=True)
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertIn("rust-only-skill", applicable)

    def test_explicit_override_forces_skill_in(self) -> None:
        fixture = self._fixture_with_two_skills()
        repo = fixture.parent / "empty-repo-2"
        repo.mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo, overrides={"rust-only-skill": True})
        self.assertIn("rust-only-skill", applicable)

    def test_explicit_override_forces_skill_out(self) -> None:
        fixture = self._fixture_with_two_skills()
        repo = fixture.parent / "rust-repo-2"
        repo.mkdir(exist_ok=True)
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo, overrides={"rust-only-skill": False})
        self.assertNotIn("rust-only-skill", applicable)

    def test_requires_all_needs_every_stack(self) -> None:
        fixture = self._fixture_with_two_skills()
        (fixture / "rust-only-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [rust, python]\n  requires_all: true\n",
            encoding="utf-8",
        )
        repo = fixture.parent / "rust-only-repo"
        repo.mkdir(exist_ok=True)
        (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertNotIn("rust-only-skill", applicable)  # has rust, missing python


class DesignQaEvidenceGatedApplicabilityTest(unittest.TestCase):
    """HORO-982: design-qa is the one no-manifest skill that must default
    NOT_APPLICABLE absent rendered-surface evidence (HORO-969 §3 points
    2/3) — every other no-manifest skill stays always-applicable."""

    def _fixture_with_design_qa(self) -> Path:
        fixture = _skills_fixture()  # "sample-skill", no manifest -> always applicable
        design_qa = fixture / "design-qa"
        design_qa.mkdir()
        (design_qa / "SKILL.md").write_text("# design-qa\n", encoding="utf-8")
        return fixture

    def test_not_applicable_with_no_rendered_surface_evidence(self) -> None:
        fixture = self._fixture_with_design_qa()
        repo = fixture.parent / "backend-only-repo"
        repo.mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertNotIn("design-qa", applicable)
        self.assertIn("sample-skill", applicable)  # other no-manifest skills unaffected

    def test_applicable_with_frontend_framework_dependency(self) -> None:
        fixture = self._fixture_with_design_qa()
        repo = fixture.parent / "react-app-repo"
        repo.mkdir(exist_ok=True)
        (repo / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}', encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertIn("design-qa", applicable)

    def test_not_applicable_with_non_frontend_package_json(self) -> None:
        """A package.json with no frontend-framework dep (e.g. a pure CLI
        tool's Node dependencies) must not trigger design-qa."""
        fixture = self._fixture_with_design_qa()
        repo = fixture.parent / "node-cli-repo"
        repo.mkdir(exist_ok=True)
        (repo / "package.json").write_text('{"dependencies": {"commander": "^12.0.0"}}', encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertNotIn("design-qa", applicable)

    def test_applicable_with_xcodeproj(self) -> None:
        fixture = self._fixture_with_design_qa()
        repo = fixture.parent / "ios-app-repo"
        repo.mkdir(exist_ok=True)
        (repo / "App.xcodeproj").mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo)
        self.assertIn("design-qa", applicable)

    def test_explicit_override_still_wins(self) -> None:
        fixture = self._fixture_with_design_qa()
        repo = fixture.parent / "backend-only-repo-2"
        repo.mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            applicable = ps.resolve_applicable_skills(repo, overrides={"design-qa": True})
        self.assertIn("design-qa", applicable)


class BuildProjectionsApplicableOnlyTest(unittest.TestCase):
    def test_applicable_only_restricts_projection_set(self) -> None:
        names = ps.discover_skills()
        subset = names[:1]
        projections = ps.build_projections(applicable_only=subset)
        projected_names = {p.parent.name for p in projections if p.name == "SKILL.md"}
        self.assertEqual(projected_names, set(subset))

    def test_applicable_only_unknown_skill_raises(self) -> None:
        with self.assertRaises(ps.SkillProjectionError):
            ps.build_projections(applicable_only=["not-a-real-skill"])

    def test_asset_projections_applicable_only_restricts_set(self) -> None:
        canonical_projections = ps.build_asset_projections()
        # Pick a skill that actually has projected assets, if any exist.
        skill_names_with_assets = {
            p.relative_to(ps.CLAUDE_SKILLS_DIR).parts[0] for p in canonical_projections
        }
        if not skill_names_with_assets:
            self.skipTest("no skill currently carries projected assets")
        chosen = sorted(skill_names_with_assets)[0]
        filtered = ps.build_asset_projections(applicable_only=[chosen])
        got_names = {p.relative_to(ps.CLAUDE_SKILLS_DIR).parts[0] for p in filtered}
        self.assertEqual(got_names, {chosen})

    def test_dest_root_projects_into_a_different_tree(self) -> None:
        dest = Path(tempfile.mkdtemp())
        names = ps.discover_skills()
        subset = names[:1]
        projections = ps.build_projections(dest_root=dest, applicable_only=subset)
        for path in projections:
            self.assertTrue(str(path).startswith(str(dest)))


class BuildAssetProjectionsTest(unittest.TestCase):
    def test_real_engineering_loop_assets_project_correctly(self) -> None:
        # engineering-loop (HORO-971) was the first real skill to use the
        # optional asset dirs; Wave 2's language skills (HORO-972/973/974/975)
        # since added their own. Filter to engineering-loop's own projected
        # files rather than asserting on the full projection set, which now
        # spans multiple skills — a skill-by-skill pin, not a global count.
        projections = ps.build_asset_projections()
        engineering_loop_dir = ps.CLAUDE_SKILLS_DIR / "engineering-loop"
        expected_rel_paths = {
            "references/diagnostic-contract.md",
            "references/optional-tool-fallback.md",
            "examples/targeted-loop-then-full-gate.md",
            "examples/escalation-from-compact-to-raw.md",
        }
        actual_rel_paths = {
            str(p.relative_to(engineering_loop_dir))
            for p in projections
            if engineering_loop_dir in p.parents
        }
        self.assertEqual(actual_rel_paths, expected_rel_paths)

    def test_real_language_skill_assets_project_correctly(self) -> None:
        # Pins that each Wave 2 language skill's own reference/example
        # assets project under its own name — a per-skill spot check, not
        # an exhaustive enumeration of every asset in the repo.
        projections = ps.build_asset_projections()
        actual_rel_paths_by_skill: dict[str, set[str]] = {}
        for skill_name in (
            "rust-development",
            "python-development",
            "typescript-development",
            "go-development",
            "terraform-development",
            "container-development",
            "shell-development",
            "swift-development",
        ):
            skill_dir = ps.CLAUDE_SKILLS_DIR / skill_name
            actual_rel_paths_by_skill[skill_name] = {
                str(p.relative_to(skill_dir)) for p in projections if skill_dir in p.parents
            }
        self.assertEqual(
            actual_rel_paths_by_skill["rust-development"],
            {
                "references/cargo-workflow.md",
                "references/aa-evidence-classification.md",
                "examples/targeted-nextest-then-full-gate.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["python-development"],
            {
                "references/uv-pytest-workflow.md",
                "references/dynamic-python-caveats.md",
                "examples/targeted-pytest-then-full-gate.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["typescript-development"],
            {
                "references/pnpm-typecheck-workflow.md",
                "examples/targeted-vitest-then-full-gate.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["go-development"],
            {
                "references/go-list-workflow.md",
                "examples/targeted-package-test-then-full-gate.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["terraform-development"],
            {
                "references/plan-first-workflow.md",
                "references/sensitive-value-safety.md",
                "examples/fmt-validate-plan-review.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["container-development"],
            {
                "references/build-and-readiness-workflow.md",
                "examples/affected-service-then-full-integration.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["shell-development"],
            {
                "references/safe-shell-patterns.md",
                "references/process-and-filesystem-safety.md",
                "examples/safe-wrapper-with-preserved-exit-code.md",
            },
        )
        self.assertEqual(
            actual_rel_paths_by_skill["swift-development"],
            {
                "references/xcode-swiftpm-detection.md",
                "examples/targeted-swiftpm-test-then-full-build.md",
            },
        )

    def test_asset_file_projects_verbatim_under_claude_skills(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "references").mkdir()
        (fixture / "sample-skill" / "references" / "deep.md").write_text("deep content", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            projections = ps.build_asset_projections()
        expected_path = ps.CLAUDE_SKILLS_DIR / "sample-skill" / "references" / "deep.md"
        self.assertEqual(projections.get(expected_path), b"deep content")

    def test_asset_projection_has_no_injected_header(self) -> None:
        # Unlike SKILL.md's projection, assets copy byte-for-byte — a
        # prepended HTML comment would corrupt a script or fixture.
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "scripts").mkdir()
        (fixture / "sample-skill" / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            projections = ps.build_asset_projections()
        content = projections[ps.CLAUDE_SKILLS_DIR / "sample-skill" / "scripts" / "run.sh"]
        self.assertEqual(content, b"#!/bin/sh\necho hi\n")


class DuplicateManifestKeyTest(unittest.TestCase):
    def test_duplicate_stacks_key_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [rust]\n  stacks: [python]\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")

    def test_duplicate_requires_all_key_raises(self) -> None:
        fixture = _skills_fixture()
        (fixture / "sample-skill" / "manifest.yaml").write_text(
            "applicability:\n  stacks: [rust]\n  requires_all: true\n  requires_all: false\n",
            encoding="utf-8",
        )
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.load_manifest("sample-skill")


class ResolveApplicableSkillsUnknownOverrideTest(unittest.TestCase):
    def test_override_naming_nonexistent_skill_raises(self) -> None:
        fixture = _skills_fixture()
        repo = fixture.parent / "some-repo"
        repo.mkdir(exist_ok=True)
        with mock.patch.object(ps, "SKILLS_DIR", fixture):
            with self.assertRaises(ps.SkillProjectionError):
                ps.resolve_applicable_skills(repo, overrides={"typo-skill-name": True})


def _full_projection_fixture():
    """A complete fake repo root with agents/skills/, .claude/skills/, and
    .codex/skills/ all under one directory — needed by tests that exercise
    main() itself (drift/write/orphan/chmod logic), since main() reads its
    location constants directly rather than taking parameters. Caller
    patches REPO_ROOT/SKILLS_DIR/CLAUDE_SKILLS_DIR/CODEX_SKILLS_DIR to the
    returned paths."""
    import tempfile

    root = Path(tempfile.mkdtemp())
    skills_dir = root / "agents" / "skills"
    claude_dir = root / ".claude" / "skills"
    codex_dir = root / ".codex" / "skills"
    skills_dir.mkdir(parents=True)
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    skill = skills_dir / "sample-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# sample-skill\n", encoding="utf-8")

    return root, skills_dir, claude_dir, codex_dir


class MainAssetWriteTest(unittest.TestCase):
    def test_executable_bit_preserved_on_projected_script(self) -> None:
        root, skills_dir, claude_dir, codex_dir = _full_projection_fixture()
        script = skills_dir / "sample-skill" / "scripts" / "run.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        script.chmod(0o755)
        with (
            mock.patch.object(ps, "REPO_ROOT", root),
            mock.patch.object(ps, "SKILLS_DIR", skills_dir),
            mock.patch.object(ps, "CLAUDE_SKILLS_DIR", claude_dir),
            mock.patch.object(ps, "CODEX_SKILLS_DIR", codex_dir),
        ):
            exit_code = ps.main([])
        self.assertEqual(exit_code, 0)
        projected = claude_dir / "sample-skill" / "scripts" / "run.sh"
        self.assertEqual(projected.stat().st_mode & 0o777, 0o755)

    def test_orphaned_asset_is_removed_on_write(self) -> None:
        root, skills_dir, claude_dir, codex_dir = _full_projection_fixture()
        with (
            mock.patch.object(ps, "REPO_ROOT", root),
            mock.patch.object(ps, "SKILLS_DIR", skills_dir),
            mock.patch.object(ps, "CLAUDE_SKILLS_DIR", claude_dir),
            mock.patch.object(ps, "CODEX_SKILLS_DIR", codex_dir),
        ):
            self.assertEqual(ps.main([]), 0)
            # Simulate a canonical asset that existed, was projected, and
            # has since been renamed/removed from the source.
            orphan = claude_dir / "sample-skill" / "references" / "stale.md"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("stale generated content", encoding="utf-8")
            self.assertEqual(ps.main(["--check"]), 1)  # orphan counts as drift
            self.assertEqual(ps.main([]), 0)
            self.assertFalse(orphan.exists())
            # The now-empty references/ dir should also be cleaned up.
            self.assertFalse(orphan.parent.exists())

    def test_check_mode_reports_orphan_without_deleting(self) -> None:
        root, skills_dir, claude_dir, codex_dir = _full_projection_fixture()
        with (
            mock.patch.object(ps, "REPO_ROOT", root),
            mock.patch.object(ps, "SKILLS_DIR", skills_dir),
            mock.patch.object(ps, "CLAUDE_SKILLS_DIR", claude_dir),
            mock.patch.object(ps, "CODEX_SKILLS_DIR", codex_dir),
        ):
            ps.main([])
            orphan = claude_dir / "sample-skill" / "references" / "stale.md"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("stale generated content", encoding="utf-8")
            exit_code = ps.main(["--check"])
        self.assertEqual(exit_code, 1)
        self.assertTrue(orphan.exists())  # --check must not delete anything


if __name__ == "__main__":
    unittest.main()
