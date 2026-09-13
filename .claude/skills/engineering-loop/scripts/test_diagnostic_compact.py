"""Tests for agents/skills/engineering-loop/scripts/diagnostic_compact.py (HORO-971).

Pytest. Run with:
    python3 -m pytest agents/skills/engineering-loop/scripts/test_diagnostic_compact.py -v

Covers the diagnostic contract's non-negotiable rules directly:
  * exit code alone decides PASS/FAIL, never parsed text;
  * a parser that raises degrades gracefully instead of crashing or lying;
  * raw output is always fully recoverable at level 3;
  * L1 surfaces file:line + message for pytest/cargo/vitest fixtures that
    mirror the diagnostic-contract.md worked examples.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_compact as dc

PYTEST_PASS_RAW = "collected 5 items\n\n..... [100%]\n\n===== 5 passed in 0.12s =====\n"

PYTEST_FAIL_RAW = (
    "collected 2 items\n\n"
    "F. [100%]\n\n"
    "===== FAILURES =====\n"
    "___ test_login_rejects_bad_password ___\n\n"
    "    def test_login_rejects_bad_password():\n"
    ">       assert resp.status_code == 401\n"
    "tests/test_auth.py:88: AssertionError: expected 401, got 200\n\n"
    "===== 1 failed, 1 passed in 0.34s =====\n"
)

CARGO_BUILD_FAIL_RAW = (
    "   Compiling app v0.1.0\n"
    "error[E0308]: mismatched types\n"
    "  --> src/main.rs:10:5\n"
    "   |\n"
    "10 |     42\n"
    "   |     ^^ expected `()`, found integer\n\n"
    "error: could not compile `app` due to previous error\n"
)

CARGO_TEST_FAIL_RAW = (
    "running 2 tests\n"
    "test tests::test_add ... ok\n"
    "test tests::test_sub ... FAILED\n\n"
    "failures:\n\n"
    "---- tests::test_sub stdout ----\n"
    "thread 'tests::test_sub' panicked at 'assertion failed: `(left == right)`', src/lib.rs:42:5\n\n"
    "failures:\n    tests::test_sub\n\n"
    "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n"
)

VITEST_FAIL_RAW = (
    " RUN v1.6.0\n\n"
    " FAIL  src/sum.test.ts > sum > adds 1 + 2 to equal 3\n"
    "AssertionError: expected 4 to be 3 // Object.is equality\n\n"
    " ❯ src/sum.test.ts:5:20\n"
    "      3| test('adds 1 + 2 to equal 3', () => {\n"
    "      4|   expect(sum(1, 2)).toBe(3)\n"
    "      5|                     ^\n"
    "      6| })\n\n"
    " Test Files  1 failed (1)\n"
    "      Tests  1 failed | 0 passed (1)\n"
)


class TestExitCodeIsAuthoritative:
    """Rule 2: exit status decides PASS/FAIL, never parsed text."""

    def test_zero_exit_is_pass_even_with_no_recognizable_output(self) -> None:
        diag = dc.classify_output("", 0)
        assert diag.passed is True
        assert "PASS" in dc.render(diag, 0)

    def test_nonzero_exit_is_fail_even_when_text_looks_successful(self) -> None:
        # Output claims success; exit code says otherwise. Exit code wins.
        diag = dc.classify_output(PYTEST_PASS_RAW, 1)
        assert diag.passed is False
        assert "FAIL" in dc.render(diag, 0)

    def test_zero_exit_is_pass_even_when_text_looks_like_failure(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 0)
        assert diag.passed is True
        assert "PASS" in dc.render(diag, 0)


class TestParserDegradesNeverCrashesNeverLies:
    """Rule 3: parser/wrapper failure must never present as PASS."""

    def test_a_raising_parser_degrades_to_generic_without_crashing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_raw: str) -> dc.ParseResult:
            raise RuntimeError("simulated parser bug")

        monkeypatch.setitem(dc._PARSERS, "pytest", boom)
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1, tool_hint="pytest")
        assert diag.passed is False  # exit code still authoritative
        assert diag.parse_error is not None
        assert "simulated parser bug" in diag.parse_error

    def test_degraded_parse_still_reports_fail_from_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_raw: str) -> dc.ParseResult:
            raise ValueError("nope")

        monkeypatch.setitem(dc._PARSERS, "cargo", boom)
        diag = dc.classify_output(CARGO_BUILD_FAIL_RAW, 101, tool_hint="cargo")
        rendered = dc.render(diag, 0)
        assert "FAIL" in rendered
        assert "PASS" not in rendered


class TestRawAlwaysRecoverable:
    """Rule 1: raw evidence is always recoverable, however much L0-L2 collapsed."""

    def test_level_3_returns_the_complete_unmodified_raw_text(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        assert dc.render(diag, 3) == PYTEST_FAIL_RAW

    def test_level_0_never_contains_the_full_raw_text(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        l0 = dc.render(diag, 0)
        assert len(l0) < len(PYTEST_FAIL_RAW)

    def test_level_0_and_1_carry_an_escalation_pointer(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        assert "--level" in dc.render(diag, 0)
        assert "--level" in dc.render(diag, 1)

    def test_level_3_has_no_escalation_pointer_appended(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        assert dc.render(diag, 3) == PYTEST_FAIL_RAW  # no pointer text appended


class TestPytestParsing:
    def test_pass_summary(self) -> None:
        diag = dc.classify_output(PYTEST_PASS_RAW, 0)
        assert diag.tool == "pytest"
        assert "5 passed" in diag.summary
        assert diag.failures == ()

    def test_fail_l1_matches_the_diagnostic_contract_worked_example(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        l1 = dc.render(diag, 1)
        assert "tests/test_auth.py:88: AssertionError: expected 401, got 200" in l1

    def test_fail_l2_includes_surrounding_context(self) -> None:
        diag = dc.classify_output(PYTEST_FAIL_RAW, 1)
        l2 = dc.render(diag, 2)
        assert "test_login_rejects_bad_password" in l2


class TestCargoParsing:
    def test_build_failure_l1_has_file_and_line(self) -> None:
        diag = dc.classify_output(CARGO_BUILD_FAIL_RAW, 101)
        assert diag.tool == "cargo"
        l1 = dc.render(diag, 1)
        assert "src/main.rs:10: error[E0308]: mismatched types" in l1

    def test_test_failure_l1_has_panic_location(self) -> None:
        diag = dc.classify_output(CARGO_TEST_FAIL_RAW, 101)
        assert diag.tool == "cargo"
        l1 = dc.render(diag, 1)
        assert "src/lib.rs:42" in l1
        assert "1 passed; 1 failed" in diag.summary


class TestVitestParsing:
    def test_failure_l1_has_file_and_line(self) -> None:
        diag = dc.classify_output(VITEST_FAIL_RAW, 1)
        assert diag.tool == "vitest"
        l1 = dc.render(diag, 1)
        assert "src/sum.test.ts:5" in l1
        assert "1 failed" in diag.summary


class TestGenericFallback:
    def test_unrecognized_tool_still_flags_error_like_lines(self) -> None:
        raw = "doing setup\nerror: disk full\nfailed to write output\ncleanup done\n"
        diag = dc.classify_output(raw, 1)
        assert diag.tool == "generic"
        l1 = dc.render(diag, 1)
        assert "disk full" in l1
        assert "failed to write output" in l1

    def test_generic_pass_collapses_to_one_line(self) -> None:
        raw = "line one\nline two\nline three\n"
        diag = dc.classify_output(raw, 0)
        l0 = dc.render(diag, 0)
        assert "PASS" in l0
        assert l0.count("\n") == 0


class TestRenderValidation:
    def test_invalid_level_raises(self) -> None:
        diag = dc.classify_output(PYTEST_PASS_RAW, 0)
        with pytest.raises(ValueError, match="level"):
            dc.render(diag, 4)


class TestCliClassify:
    def test_classify_from_stdin_exits_with_underlying_code(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(PYTEST_FAIL_RAW))
        code = dc.main(["classify", "--exit-code", "1", "--level", "1"])
        assert code == 1
        out = capsys.readouterr().out
        assert "tests/test_auth.py:88" in out

    def test_classify_from_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        raw_file = tmp_path / "raw.log"
        raw_file.write_text(CARGO_BUILD_FAIL_RAW, encoding="utf-8")
        code = dc.main(["classify", "--exit-code", "101", "--raw-file", str(raw_file), "--level", "0"])
        assert code == 101
        assert "FAIL" in capsys.readouterr().out


class TestCliRun:
    def test_run_captures_real_subprocess_exit_code_and_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dc.main(["run", "--level", "0", "--", sys.executable, "-c", "print('boom'); import sys; sys.exit(3)"])
        assert code == 3
        out = capsys.readouterr().out
        assert "FAIL (exit=3)" in out

    def test_run_zero_exit_is_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dc.main(["run", "--", sys.executable, "-c", "print('ok')"])
        assert code == 0
        assert "PASS (exit=0)" in capsys.readouterr().out

    def test_run_writes_raw_log_when_requested(self, tmp_path: Path) -> None:
        log_path = tmp_path / "nested" / "raw.log"
        dc.main(["run", "--raw-log", str(log_path), "--", sys.executable, "-c", "print('hello world')"])
        assert log_path.exists()
        assert "hello world" in log_path.read_text(encoding="utf-8")

    def test_run_missing_command_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dc.main(["run", "--"])
        assert code == 2
        assert "ERROR" in capsys.readouterr().err

    def test_run_nonexistent_binary_is_reported_as_failure_not_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dc.main(["run", "--", "this-binary-does-not-exist-anywhere"])
        assert code != 0
        assert "PASS" not in capsys.readouterr().out
