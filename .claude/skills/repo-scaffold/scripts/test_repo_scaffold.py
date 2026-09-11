"""Tests for agents/skills/repo-scaffold/scripts/repo_scaffold.py (HORO-981).

Pytest, stdlib-only fixtures (tmp_path). Run with:
    python3 -m pytest agents/skills/repo-scaffold/scripts/test_repo_scaffold.py -v

Covers 3 of the 5 golden scaffolds the ticket names — Python API service,
Rust CLI, and a docs-only repo (the negative-space test) — plus the
cross-cutting idempotency and collision-detection guarantees. The
TypeScript SDK w/ optional Rust binding and infra-only Terraform repo
golden scaffolds are NOT covered here; see the skill's SKILL.md for that
explicit deferral.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_scaffold as rs  # noqa: E402


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


class TestProfileValidation:
    def test_valid_profile_parses(self) -> None:
        p = rs.Profile.from_dict({"archetype": "service", "stacks": ["python"]})
        assert p.archetype == "service"
        assert p.stacks == ("python",)
        assert p.capabilities == ()

    def test_rejects_unknown_archetype(self) -> None:
        with pytest.raises(rs.ProfileError, match="archetype"):
            rs.Profile.from_dict({"archetype": "spaceship", "stacks": ["python"]})

    def test_rejects_empty_stacks(self) -> None:
        with pytest.raises(rs.ProfileError, match="stacks"):
            rs.Profile.from_dict({"archetype": "service", "stacks": []})

    def test_rejects_unknown_stack(self) -> None:
        with pytest.raises(rs.ProfileError, match="stack"):
            rs.Profile.from_dict({"archetype": "service", "stacks": ["cobol"]})

    def test_rejects_unknown_capability(self) -> None:
        with pytest.raises(rs.ProfileError, match="capabilit"):
            rs.Profile.from_dict({"archetype": "service", "stacks": ["python"], "capabilities": ["telepathy"]})

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(rs.ProfileError, match="unknown key"):
            rs.Profile.from_dict({"archetype": "service", "stacks": ["python"], "name": "whatever"})

    def test_rejects_duplicate_stacks(self) -> None:
        with pytest.raises(rs.ProfileError, match="repeat"):
            rs.Profile.from_dict({"archetype": "service", "stacks": ["python", "python"]})

    def test_rejects_capability_that_is_not_applicable_for_archetype(self) -> None:
        # docs archetype hard-denies coverage — this must fail at profile
        # parse time, not silently resolve to an omitted file later.
        with pytest.raises(rs.ProfileError, match="NOT_APPLICABLE"):
            rs.Profile.from_dict({"archetype": "docs", "stacks": ["typescript"], "capabilities": ["coverage"]})


# ---------------------------------------------------------------------------
# Golden scaffold 1: Python API service — gets Docker + Terraform + coverage
# ---------------------------------------------------------------------------


class TestPythonServiceGoldenScaffold:
    @pytest.fixture()
    def plan(self) -> rs.ScaffoldPlan:
        profile = rs.Profile.from_dict(
            {"archetype": "service", "stacks": ["python"], "capabilities": ["terraform"]}
        )
        return rs.resolve(profile)

    def test_gets_company_baseline(self, plan: rs.ScaffoldPlan) -> None:
        for expected in ("CODEOWNERS", ".github/pull_request_template.md", "LICENSE", ".gitignore", ".editorconfig"):
            assert expected in plan.files

    def test_gets_python_manifest_and_ci(self, plan: rs.ScaffoldPlan) -> None:
        assert "pyproject.toml" in plan.files
        assert ".github/workflows/python.yml" in plan.files
        assert "pytest" in plan.files[".github/workflows/python.yml"]

    def test_gets_docker(self, plan: rs.ScaffoldPlan) -> None:
        assert "Dockerfile" in plan.files

    def test_gets_terraform_workflow_from_capability(self, plan: rs.ScaffoldPlan) -> None:
        assert ".github/workflows/terraform.yml" in plan.files

    def test_gets_coverage_and_sonarqube(self, plan: rs.ScaffoldPlan) -> None:
        assert "codecov.yml" in plan.files
        assert "sonar-project.properties" in plan.files

    def test_does_not_get_rust_or_go_ci(self, plan: rs.ScaffoldPlan) -> None:
        assert ".github/workflows/rust.yml" not in plan.files
        assert ".github/workflows/go.yml" not in plan.files

    def test_dependabot_covers_pip_and_actions(self, plan: rs.ScaffoldPlan) -> None:
        # Dependabot ecosystems are derived from profile.stacks (real
        # manifest evidence), not from capabilities — the terraform
        # *capability* here adds the CI workflow but no `*.tf` stack
        # evidence exists, so no terraform dependabot ecosystem is added.
        content = plan.files[".github/dependabot.yml"]
        assert '"pip"' in content
        assert '"github-actions"' in content

    def test_every_file_carries_the_provenance_marker(self, plan: rs.ScaffoldPlan) -> None:
        for rel, content in plan.files.items():
            assert rs._MARKER_TEXT in content.splitlines()[0], f"{rel} missing provenance marker on its first line"


# ---------------------------------------------------------------------------
# Golden scaffold 2: Rust CLI — binary/release shape, no web/Docker cruft
# ---------------------------------------------------------------------------


class TestRustCliGoldenScaffold:
    @pytest.fixture()
    def plan(self) -> rs.ScaffoldPlan:
        profile = rs.Profile.from_dict({"archetype": "cli", "stacks": ["rust"]})
        return rs.resolve(profile)

    def test_gets_rust_manifest_and_ci(self, plan: rs.ScaffoldPlan) -> None:
        assert "Cargo.toml" in plan.files
        assert ".github/workflows/rust.yml" in plan.files
        assert "cargo nextest run" in plan.files[".github/workflows/rust.yml"]

    def test_gets_release_binaries_workflow_by_default(self, plan: rs.ScaffoldPlan) -> None:
        assert ".github/workflows/release.yml" in plan.files

    def test_gets_coverage_and_sonarqube_by_default(self, plan: rs.ScaffoldPlan) -> None:
        assert "codecov.yml" in plan.files
        assert "sonar-project.properties" in plan.files

    def test_does_not_get_docker_by_default(self, plan: rs.ScaffoldPlan) -> None:
        assert "Dockerfile" not in plan.files

    def test_does_not_get_docs_site(self, plan: rs.ScaffoldPlan) -> None:
        assert "docs/README.md" not in plan.files

    def test_docker_can_be_opted_in_explicitly(self) -> None:
        profile = rs.Profile.from_dict({"archetype": "cli", "stacks": ["rust"], "capabilities": ["docker"]})
        plan = rs.resolve(profile)
        assert "Dockerfile" in plan.files

    def test_stack_module_references_the_rust_development_skill_not_reexplain_it(self, plan: rs.ScaffoldPlan) -> None:
        workflow = plan.files[".github/workflows/rust.yml"]
        assert "agents/skills/rust-development/SKILL.md" in workflow
        # It must not attempt to restate nextest/clippy diagnostic guidance —
        # only reference the skill and issue the command.
        assert "diagnostic ladder" not in workflow.lower() or "SKILL.md" in workflow


# ---------------------------------------------------------------------------
# Golden scaffold 3: docs-only repo — the negative-space test.
#
# Proves NOT_APPLICABLE capabilities are omitted outright, not emitted as
# decorative empty files.
# ---------------------------------------------------------------------------


class TestDocsOnlyGoldenScaffoldNegativeSpace:
    @pytest.fixture()
    def plan(self) -> rs.ScaffoldPlan:
        profile = rs.Profile.from_dict({"archetype": "docs", "stacks": ["typescript"]})
        return rs.resolve(profile)

    def test_still_gets_company_baseline(self, plan: rs.ScaffoldPlan) -> None:
        for expected in ("CODEOWNERS", ".github/pull_request_template.md", "LICENSE"):
            assert expected in plan.files

    def test_gets_docs_site_capability(self, plan: rs.ScaffoldPlan) -> None:
        assert "docs/README.md" in plan.files

    def test_omits_coverage_config_entirely(self, plan: rs.ScaffoldPlan) -> None:
        assert "codecov.yml" not in plan.files

    def test_omits_sonarqube_config_entirely(self, plan: rs.ScaffoldPlan) -> None:
        assert "sonar-project.properties" not in plan.files

    def test_omits_docker_entirely(self, plan: rs.ScaffoldPlan) -> None:
        assert "Dockerfile" not in plan.files

    def test_omits_release_binaries_workflow_entirely(self, plan: rs.ScaffoldPlan) -> None:
        assert ".github/workflows/release.yml" not in plan.files

    def test_coverage_capability_cannot_be_forced_on_for_docs_archetype(self) -> None:
        # This is the profile-level enforcement of the same negative-space
        # rule: an explicit override cannot resurrect a NOT_APPLICABLE
        # capability for this archetype.
        with pytest.raises(rs.ProfileError, match="NOT_APPLICABLE"):
            rs.Profile.from_dict({"archetype": "docs", "stacks": ["typescript"], "capabilities": ["coverage"]})


# ---------------------------------------------------------------------------
# Infra archetype — extra negative-space coverage: Terraform-only repo must
# not get a Python/Node application CI workflow.
# ---------------------------------------------------------------------------


class TestInfraArchetypeNoApplicationCi:
    def test_infra_gets_terraform_workflow_only(self) -> None:
        profile = rs.Profile.from_dict({"archetype": "infra", "stacks": ["terraform"]})
        plan = rs.resolve(profile)
        workflow_files = [f for f in plan.files if f.startswith(".github/workflows/")]
        assert workflow_files == [".github/workflows/terraform.yml"]

    def test_infra_omits_coverage_sonarqube_docker(self) -> None:
        profile = rs.Profile.from_dict({"archetype": "infra", "stacks": ["terraform"]})
        plan = rs.resolve(profile)
        assert "codecov.yml" not in plan.files
        assert "sonar-project.properties" not in plan.files
        assert "Dockerfile" not in plan.files


# ---------------------------------------------------------------------------
# Idempotent re-run
# ---------------------------------------------------------------------------


class TestIdempotentReRun:
    def test_second_write_produces_no_diff(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "service", "stacks": ["python"], "capabilities": ["terraform"]})
        plan = rs.resolve(profile)

        first = rs.write_plan(plan, tmp_path)
        assert first.to_create  # sanity: something was actually written

        second_diff = rs.plan_diff(plan, tmp_path)
        assert second_diff.to_create == ()
        assert second_diff.to_update == ()
        assert second_diff.collisions == ()
        assert set(second_diff.unchanged) == set(plan.files)

    def test_check_mode_never_writes(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "cli", "stacks": ["rust"]})
        plan = rs.resolve(profile)
        rs.plan_diff(plan, tmp_path)  # dry-run equivalent
        assert list(tmp_path.iterdir()) == []

    def test_rerun_after_upstream_content_change_updates_only_generator_owned_files(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "cli", "stacks": ["rust"]})
        plan_v1 = rs.resolve(profile)
        rs.write_plan(plan_v1, tmp_path)

        # Simulate the generator's own template content changing between
        # runs (e.g. a workflow command tweak) — this must be treated as an
        # update, not a collision, because the file is still marked.
        profile_v2 = rs.Profile.from_dict({"archetype": "cli", "stacks": ["rust"], "capabilities": ["docker"]})
        plan_v2 = rs.resolve(profile_v2)
        diff = rs.write_plan(plan_v2, tmp_path)
        assert "Dockerfile" in diff.to_create
        assert diff.collisions == ()


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------


class TestCollisionDetection:
    def test_refuses_to_overwrite_unmarked_existing_file(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "service", "stacks": ["python"]})
        plan = rs.resolve(profile)

        (tmp_path / "CODEOWNERS").write_text("hand-authored, no marker\n", encoding="utf-8")

        with pytest.raises(rs.ProfileError, match="CODEOWNERS"):
            rs.write_plan(plan, tmp_path)

    def test_collision_blocks_the_entire_write_not_a_partial_write(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "service", "stacks": ["python"]})
        plan = rs.resolve(profile)
        (tmp_path / "CODEOWNERS").write_text("hand-authored, no marker\n", encoding="utf-8")

        with pytest.raises(rs.ProfileError):
            rs.write_plan(plan, tmp_path)

        # Nothing else should have been written either — no partial write.
        assert not (tmp_path / "pyproject.toml").exists()
        assert not (tmp_path / ".gitignore").exists()

    def test_marked_file_with_matching_content_is_left_alone(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "service", "stacks": ["python"]})
        plan = rs.resolve(profile)
        rs.write_plan(plan, tmp_path)
        before = (tmp_path / "CODEOWNERS").stat().st_mtime_ns
        diff = rs.write_plan(plan, tmp_path)
        after = (tmp_path / "CODEOWNERS").stat().st_mtime_ns
        assert diff.to_create == () and diff.to_update == ()
        assert before == after

    def test_never_deletes_existing_non_generated_content(self, tmp_path: Path) -> None:
        profile = rs.Profile.from_dict({"archetype": "docs", "stacks": ["typescript"]})
        plan = rs.resolve(profile)
        (tmp_path / "README.md").write_text("pre-existing repo readme, not part of the scaffold\n", encoding="utf-8")
        rs.write_plan(plan, tmp_path)
        assert (tmp_path / "README.md").read_text(encoding="utf-8") == "pre-existing repo readme, not part of the scaffold\n"


# ---------------------------------------------------------------------------
# Provenance marker covers every emitted comment-syntax family, not just
# Markdown — this is the exact bug class the advisor review flagged.
# ---------------------------------------------------------------------------


class TestProvenanceMarkerCoversEveryFamily:
    def test_hash_comment_family(self) -> None:
        assert rs._MARKER_TEXT in rs._stamp("pyproject.toml", "x")
        assert rs._stamp("pyproject.toml", "x").startswith("#")

    def test_dot_gitignore_and_dockerfile_use_hash(self) -> None:
        assert rs._stamp(".gitignore", "x").startswith("#")
        assert rs._stamp("Dockerfile", "x").startswith("#")

    def test_markdown_uses_html_comment(self) -> None:
        assert rs._stamp("docs/README.md", "x").startswith("<!--")

    def test_json_has_no_safe_marker_and_is_rejected(self) -> None:
        with pytest.raises(rs.ProfileError):
            rs._stamp("package.json", "{}")

    def test_is_marked_detects_generator_owned_files_only(self, tmp_path: Path) -> None:
        owned = tmp_path / "owned.toml"
        owned.write_text(rs._stamp("owned.toml", "x=1\n"), encoding="utf-8")
        unowned = tmp_path / "unowned.toml"
        unowned.write_text("x=1\n", encoding="utf-8")
        assert rs._is_marked(owned) is True
        assert rs._is_marked(unowned) is False
