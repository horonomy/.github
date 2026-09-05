#!/usr/bin/env python3
"""Tests for agents/common/project_skills.py (HORO-509).

Stdlib unittest only. Run with:
    python3 -m unittest discover -s agents/common
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import project_skills as ps


class DiscoverSkillsTest(unittest.TestCase):
    def test_real_skills_directory_has_all_five(self) -> None:
        names = set(ps.discover_skills())
        self.assertEqual(
            names,
            {
                "governance-doctor",
                "repo-bootstrap",
                "jira-delivery",
                "release-assurance",
                "public-release-reconcile",
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


if __name__ == "__main__":
    unittest.main()
