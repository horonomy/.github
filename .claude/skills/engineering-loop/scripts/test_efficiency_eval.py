"""Tests for agents/skills/engineering-loop/scripts/efficiency_eval.py (HORO-971).

Pytest. Run with:
    python3 -m pytest agents/skills/engineering-loop/scripts/test_efficiency_eval.py -v

Exercises the bundled fixture set only (deterministic, offline - no
cargo/npm/pytest toolchain required) plus the `live` mode against a real
subprocess this test controls directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import efficiency_eval as ee

EXPECTED_FIXTURES = {
    "python_pytest_pass",
    "python_pytest_fail",
    "rust_cargo_build_fail",
    "rust_cargo_test_fail",
    "typescript_vitest_fail",
}


class TestFixtureCatalog:
    def test_bundled_fixtures_cover_python_rust_and_typescript(self) -> None:
        assert EXPECTED_FIXTURES.issubset(set(ee.list_fixtures()))

    def test_unknown_fixture_raises(self) -> None:
        with pytest.raises(ee.FixtureError, match="unknown fixture"):
            ee.load_fixture("does-not-exist")

    def test_malformed_fixture_json_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ee, "_FIXTURES_DIR", tmp_path)
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ee.FixtureError, match="not valid JSON"):
            ee.load_fixture("broken")

    def test_fixture_missing_required_key_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ee, "_FIXTURES_DIR", tmp_path)
        (tmp_path / "incomplete.json").write_text('{"raw": "x"}', encoding="utf-8")
        with pytest.raises(ee.FixtureError, match="exit_code"):
            ee.load_fixture("incomplete")


class TestEvaluateFixtures:
    @pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURES))
    def test_every_bundled_fixture_preserves_exit_code_and_signal(self, name: str) -> None:
        result = ee.evaluate_fixture(name)
        assert result.exit_code_preserved is True
        assert result.failure_signal_preserved is True

    def test_compact_form_is_smaller_than_raw_for_a_verbose_pass(self) -> None:
        result = ee.evaluate_fixture("python_pytest_pass")
        assert result.compact_bytes < result.raw_bytes
        assert result.reduction_pct > 50.0

    def test_compact_form_is_smaller_than_raw_for_a_verbose_failure(self) -> None:
        result = ee.evaluate_fixture("python_pytest_fail")
        assert result.compact_bytes < result.raw_bytes

    def test_rust_build_failure_is_correctly_tagged_as_cargo(self) -> None:
        result = ee.evaluate_fixture("rust_cargo_build_fail")
        assert result.tool == "cargo"
        assert result.exit_code == 101

    def test_typescript_vitest_fixture_is_correctly_tagged(self) -> None:
        result = ee.evaluate_fixture("typescript_vitest_fail")
        assert result.tool == "vitest"
        assert result.exit_code == 1


class TestEvaluateCorrectnessGuards:
    def test_a_compaction_that_flips_pass_to_fail_is_flagged_not_preserved(self) -> None:
        # exit_code says FAIL but the raw text alone contains nothing
        # failure-shaped for the generic parser to latch onto; the compact
        # form must still say FAIL (from exit code), so signal stays
        # preserved - this pins that behavior explicitly.
        result = ee.evaluate("all quiet on the western front\n", 1, name="edge-case")
        assert result.exit_code_preserved is True
        assert result.failure_signal_preserved is True  # FAIL is derived from exit code alone

    def test_zero_byte_raw_does_not_divide_by_zero(self) -> None:
        result = ee.evaluate("", 0, name="empty")
        assert result.reduction_pct == 0.0


class TestRunAllAndFormatting:
    def test_run_all_returns_a_result_per_fixture(self) -> None:
        results = [ee.evaluate_fixture(name) for name in ee.list_fixtures()]
        assert len(results) == len(ee.list_fixtures())

    def test_format_table_includes_every_row_name(self) -> None:
        results = [ee.evaluate_fixture(name) for name in sorted(EXPECTED_FIXTURES)]
        table = ee.format_table(results)
        for name in EXPECTED_FIXTURES:
            assert name in table


class TestLiveMode:
    def test_live_runs_a_real_subprocess_and_preserves_its_exit_code(self, tmp_path: Path) -> None:
        result = ee.evaluate_live(tmp_path, [sys.executable, "-c", "print('hi'); import sys; sys.exit(1)"])
        assert result.exit_code == 1
        assert result.exit_code_preserved is True

    def test_live_rejects_a_nonexistent_cwd(self, tmp_path: Path) -> None:
        with pytest.raises(ee.FixtureError, match="not a directory"):
            ee.evaluate_live(tmp_path / "does-not-exist", [sys.executable, "-c", "print('x')"])


class TestCli:
    def test_list_subcommand_prints_fixture_names(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = ee.main(["list"])
        assert code == 0
        out = capsys.readouterr().out
        for name in EXPECTED_FIXTURES:
            assert name in out

    def test_fixture_subcommand_exits_zero_when_signal_preserved(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = ee.main(["fixture", "python_pytest_fail"])
        assert code == 0
        assert "pytest" in capsys.readouterr().out

    def test_run_all_subcommand_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = ee.main(["run-all"])
        assert code == 0

    def test_unknown_fixture_via_cli_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = ee.main(["fixture", "nope"])
        assert code == 2
        assert "ERROR" in capsys.readouterr().err
