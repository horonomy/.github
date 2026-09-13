#!/usr/bin/env python3
"""Progressive (L0-L3) diagnostic classifier/compactor (HORO-971).

Implements the `engineering-loop` skill's non-negotiable diagnostic
contract (`references/diagnostic-contract.md`) as a small, deterministic,
testable helper instead of leaving it as prose an agent has to
re-implement ad hoc in every session:

    L0 - PASS/FAIL + counts
    L1 - file/line + diagnostic, one line per failure
    L2 - relevant traceback/compiler context around each failure
    L3 - complete raw output, always recoverable, never the default

Non-negotiable rules this script enforces (see the reference doc for the
full rationale):

  1. Raw evidence is always recoverable - `classify_output()` keeps the
     complete raw text on the returned `Diagnostic`, and `render(level=3)`
     always returns it verbatim, however aggressively L0-L2 collapsed it.
  2. Exit status is always preserved and is the *only* source of the
     PASS/FAIL verdict - `Diagnostic.passed` is `exit_code == 0`, full
     stop. Parsed text (counts, summary lines) is never consulted to
     decide PASS/FAIL, only to describe *why*.
  3. A parser/wrapper failure never presents as PASS - if the per-tool
     parser raises, `classify_output()` catches it, falls back to the
     "unknown" tool with no structured failures, and still reports
     PASS/FAIL from the exit code alone.
  4. No blind `tail -N` truncation - L2's context blocks are extracted
     around each detected failure location, not from an arbitrary tail of
     the stream, and truncation (when a cap is hit) says so explicitly
     rather than silently dropping content.
  5. Successful, repetitive output collapses hard - a passing run's L0 is
     one line regardless of how verbose the underlying tool was.

Stdlib only, matches `agents/skills/repo-scaffold/scripts/repo_scaffold.py`'s
contract (HORO-969 SS2: "scripts/ holds helpers only where they add real
deterministic value").

## CLI

    python3 diagnostic_compact.py run [--level {0,1,2,3}] [--raw-log PATH] -- <command...>
    python3 diagnostic_compact.py classify --exit-code N [--raw-file PATH] [--level {0,1,2,3}]

`run` executes the given command, captures its combined stdout/stderr and
exit code, and prints the classified report. `classify` is the offline
form: feed it a previously captured exit code + raw output (from a file or
stdin) - this is what `efficiency_eval.py` uses to score fixtures without
re-running a real toolchain.

Both subcommands exit with the *underlying* command/fixture's exit code
(not 0-on-success-of-the-wrapper), so a caller scripting against this tool
still sees the real pass/fail signal on `$?`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Tool detection + per-tool parsing
# ---------------------------------------------------------------------------

# Cap on how many failure lines / context blocks L1/L2 will show before
# collapsing the remainder into a "+N more" note (rule 5: collapse the
# repetitive part, never the first evidence).
_MAX_ITEMS = 20
_MAX_L2_CHARS = 4000


_NO_SUMMARY_LINE_FOUND = "no summary line found"


@dataclass(frozen=True)
class Failure:
    location: str  # "path:line" (or best-effort equivalent)
    message: str
    context: str = ""  # raw block for L2; may be empty if none was found


@dataclass(frozen=True)
class ParseResult:
    tool: str
    summary: str  # one-line human summary for L0, e.g. "2 failed, 3 passed"
    failures: tuple[Failure, ...]


def _parse_pytest(raw: str) -> ParseResult:
    counts = re.findall(r"(\d+) (passed|failed|error(?:s)?|skipped|xfailed|xpassed)", raw)
    summary = ", ".join(f"{n} {label}" for n, label in counts) if counts else _NO_SUMMARY_LINE_FOUND

    failures: list[Failure] = []
    seen: set[str] = set()
    for m in re.finditer(r"^(\S+\.py):(\d+): (.+)$", raw, re.MULTILINE):
        path, line, message = m.group(1), m.group(2), m.group(3).strip()
        key = f"{path}:{line}"
        if key in seen:
            continue
        seen.add(key)
        context = _context_block(raw, m.start(), m.end())
        failures.append(Failure(location=key, message=message, context=context))
    return ParseResult(tool="pytest", summary=summary, failures=tuple(failures))


def _parse_cargo(raw: str) -> ParseResult:
    summary_parts: list[str] = []
    for m in re.finditer(r"test result: (ok|FAILED)\. (\d+) passed; (\d+) failed[^\n]*", raw):
        summary_parts.append(m.group(0))
    if not summary_parts:
        summary_parts = re.findall(r"^error(?:\[E\d+\])?: .+$", raw, re.MULTILINE)[:1]
    summary = "; ".join(summary_parts) if summary_parts else _NO_SUMMARY_LINE_FOUND

    failures: list[Failure] = []
    seen: set[str] = set()

    # Compiler errors: "error[E0308]: message\n  --> src/main.rs:10:5"
    for m in re.finditer(r"error(?:\[(E\d+)\])?: (.+)\n\s*-->\s*(\S+):(\d+):(\d+)", raw):
        code, message, path, line, _col = m.groups()
        key = f"{path}:{line}"
        if key in seen:
            continue
        seen.add(key)
        label = f"error[{code}]: {message}" if code else f"error: {message}"
        context = _context_block(raw, m.start(), m.end())
        failures.append(Failure(location=key, message=label, context=context))

    # Test panics: "thread '...' panicked at 'msg', src/lib.rs:42:5"
    for m in re.finditer(r"panicked at '([^']*)', (\S+):(\d+):(\d+)", raw):
        message, path, line, _col = m.groups()
        key = f"{path}:{line}"
        if key in seen:
            continue
        seen.add(key)
        context = _context_block(raw, m.start(), m.end())
        failures.append(Failure(location=key, message=f"panicked: {message}", context=context))

    return ParseResult(tool="cargo", summary=summary, failures=tuple(failures))


def _parse_vitest(raw: str) -> ParseResult:
    summary_parts: list[str] = []
    tf = re.search(r"Test Files\s+.+", raw)
    if tf:
        summary_parts.append(tf.group(0).strip())
    ts = re.search(r"Tests\s+.+", raw)
    if ts:
        summary_parts.append(ts.group(0).strip())
    summary = "; ".join(summary_parts) if summary_parts else _NO_SUMMARY_LINE_FOUND

    failures: list[Failure] = []
    seen: set[str] = set()
    for m in re.finditer(r"❯\s+(\S+\.tsx?):(\d+):\d+", raw):
        path, line = m.groups()
        key = f"{path}:{line}"
        if key in seen:
            continue
        seen.add(key)
        # Best-effort message: nearest non-blank line before the location
        # marker (vitest prints the assertion/error just above it).
        preceding = raw[: m.start()].rstrip().splitlines()
        message = preceding[-1].strip() if preceding else "(no message line found)"
        context = _context_block(raw, m.start(), m.end())
        failures.append(Failure(location=key, message=message, context=context))
    return ParseResult(tool="vitest", summary=summary, failures=tuple(failures))


def _parse_generic(raw: str) -> ParseResult:
    lines = raw.splitlines()
    failures: list[Failure] = []
    seen: set[str] = set()
    for i, text in enumerate(lines):
        if re.search(r"\b(error|fail(?:ed|ure)?)\b", text, re.IGNORECASE):
            key = text.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            context = "\n".join(lines[max(0, i - 2) : i + 3])
            failures.append(Failure(location=f"line {i + 1}", message=key, context=context))
    return ParseResult(tool="generic", summary=f"{len(lines)} lines of output", failures=tuple(failures))


def _context_block(raw: str, start: int, end: int, radius: int = 300) -> str:
    """A bounded window of raw text around a match - not a blind tail, an
    evidence-anchored window (rule 4)."""
    lo = max(0, start - radius)
    hi = min(len(raw), end + radius)
    return raw[lo:hi].strip("\n")


# Detection order matters: check the most specific signal first so a
# pytest run that happens to mention the word "cargo" in a docstring
# doesn't get misclassified.
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Bounded `.{0,N}` rather than greedy `.*` around the alternation - caps
    # worst-case backtracking on adversarial/pathological input instead of
    # relying on unbounded wildcard matching.
    ("pytest", re.compile(r"={3,}.{0,300}\b(passed|failed|error)\b.{0,300}={3,}|^={3,} FAILURES ={3,}", re.MULTILINE)),
    ("cargo", re.compile(r"^(running \d+ tests?|test result:|error\[E\d+\]:)", re.MULTILINE)),
    ("vitest", re.compile(r"^\s*(Test Files|RUN v\d|✓|❯|FAIL)\b", re.MULTILINE)),
)

_PARSERS = {
    "pytest": _parse_pytest,
    "cargo": _parse_cargo,
    "vitest": _parse_vitest,
    "generic": _parse_generic,
}


def detect_tool(raw: str) -> str:
    for name, pattern in _DETECTORS:
        if pattern.search(raw):
            return name
    return "generic"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    exit_code: int
    passed: bool
    tool: str
    summary: str
    failures: tuple[Failure, ...]
    raw: str
    parse_error: str | None = field(default=None)


def classify_output(raw: str, exit_code: int, tool_hint: str | None = None) -> Diagnostic:
    """Classify `raw` output against the L0-L3 contract.

    `passed` is derived from `exit_code == 0` alone - see module docstring
    rule 2. Parsing failures are caught and degrade to an unstructured
    "generic" result rather than raising or ever flipping the verdict.
    """
    passed = exit_code == 0
    tool = tool_hint if tool_hint in _PARSERS else detect_tool(raw)
    parse_error: str | None = None
    try:
        result = _PARSERS[tool](raw)
    except Exception as exc:  # noqa: BLE001 - a parser bug must degrade, never crash the caller into guessing.
        parse_error = f"{tool} parser raised {exc.__class__.__name__}: {exc}"
        try:
            result = _parse_generic(raw)
        except Exception as exc2:  # noqa: BLE001 - even the generic fallback must never throw past this point.
            parse_error = f"{parse_error}; generic fallback also raised {exc2.__class__.__name__}: {exc2}"
            result = ParseResult(tool="unknown", summary="(unparseable output)", failures=())

    return Diagnostic(
        exit_code=exit_code,
        passed=passed,
        tool=result.tool,
        summary=result.summary,
        failures=result.failures,
        raw=raw,
        parse_error=parse_error,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(diag: Diagnostic, level: int) -> str:
    if level not in (0, 1, 2, 3):
        raise ValueError(f"level must be 0-3, got {level!r}")
    if level == 3:
        # Complete raw output, verbatim, always fully recoverable - no pointer needed.
        return diag.raw

    body = _render_l0(diag)
    if level >= 1:
        body = _render_l1(diag, body)
    if level >= 2:
        body = _render_l2(diag, body)

    # A pointer to escalate only makes sense when there is something to
    # investigate; a clean PASS with nothing parsed has nothing to escalate
    # to (rule 5: collapse the successful case hard, no dangling prompts).
    needs_pointer = (not diag.passed) or bool(diag.failures)
    return _with_pointer(body, level, needs_pointer)


def _render_l0(diag: Diagnostic) -> str:
    verdict = "PASS" if diag.passed else "FAIL"
    l0 = f"{verdict} (exit={diag.exit_code}) tool={diag.tool}: {diag.summary}"
    if diag.parse_error:
        l0 += f" [parser degraded: {diag.parse_error}]"
    return l0


def _render_l1(diag: Diagnostic, l0: str) -> str:
    if not diag.failures:
        return l0 if diag.passed else l0 + "\n(no structured failures parsed - escalate to --level 2 or 3)"
    shown = diag.failures[:_MAX_ITEMS]
    lines = [f"{f.location}: {f.message}" for f in shown]
    if len(diag.failures) > _MAX_ITEMS:
        lines.append(f"... +{len(diag.failures) - _MAX_ITEMS} more (escalate to --level 2 or 3)")
    return l0 + "\n" + "\n".join(lines)


def _render_l2(diag: Diagnostic, l1_body: str) -> str:
    if not diag.failures:
        return l1_body
    blocks = []
    used = 0
    truncated = False
    for f in diag.failures[:_MAX_ITEMS]:
        block = f.context or "(no context captured)"
        if used + len(block) > _MAX_L2_CHARS:
            truncated = True
            break
        blocks.append(f"--- {f.location} ---\n{block}")
        used += len(block)
    l2_body = l1_body + "\n\n" + "\n\n".join(blocks)
    if truncated:
        l2_body += "\n\n(context truncated - escalate to --level 3 for the complete raw output)"
    return l2_body


def _with_pointer(body: str, level: int, needs_pointer: bool) -> str:
    if level >= 3 or not needs_pointer:
        return body
    return body + f"\n(escalate: --level {level + 1}{' or --level 3 for raw' if level < 2 else ''})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_command(command: list[str]) -> tuple[str, int]:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return proc.stdout, proc.returncode
    except FileNotFoundError as exc:
        # The command itself couldn't even start - this is unambiguously a
        # failure, never a silent PASS (rule 3).
        return f"could not execute {command!r}: {exc}", 127


def _safe_path(raw: Path, *, flag: str) -> Path:
    """Resolve a CLI-supplied path and refuse one that escapes an allowed tree.

    This script is designed to be invoked with LLM-generated arguments (per
    HORO-971's engineering-loop contract). A prompt-injected or malformed
    ``--raw-log``/``--raw-file`` value must not be able to read from or
    write to an arbitrary filesystem location - resolve it (eliminating any
    ``..`` traversal) and require the result to land under the invoking
    working directory or the system temp directory (where CI/test scratch
    output legitimately lives), rather than trusting the argument as-is.
    """
    resolved = raw.resolve()
    allowed_roots = (Path.cwd().resolve(), Path(tempfile.gettempdir()).resolve())
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise SystemExit(
            f"ERROR: {flag} path {raw!s} resolves to {resolved} which is outside "
            f"the allowed working/temp directories; refusing for safety"
        )
    return resolved


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="diagnostic_compact.py",
        description="Classify tool output into the engineering-loop L0-L3 progressive diagnostic contract.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run a command, capture its output, and print the classified report.")
    p_run.add_argument("--level", type=int, choices=(0, 1, 2, 3), default=0)
    p_run.add_argument("--raw-log", type=Path, default=None, help="Also write the complete raw output to this path.")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run, e.g. -- pytest -q")

    p_classify = sub.add_parser("classify", help="Classify previously captured output (offline, no subprocess).")
    p_classify.add_argument("--exit-code", type=int, required=True)
    p_classify.add_argument("--raw-file", type=Path, default=None, help="Read raw output from this file instead of stdin.")
    p_classify.add_argument("--level", type=int, choices=(0, 1, 2, 3), default=0)
    p_classify.add_argument("--tool", choices=tuple(_PARSERS), default=None, help="Force a tool parser instead of auto-detecting.")

    args = parser.parse_args(argv)

    if args.command == "run":
        cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not cmd:
            print("ERROR: no command given after 'run' (use: run -- <command...>)", file=sys.stderr)
            return 2
        raw, exit_code = _run_command(cmd)
        if args.raw_log is not None:
            raw_log = _safe_path(args.raw_log, flag="--raw-log")
            raw_log.parent.mkdir(parents=True, exist_ok=True)
            raw_log.write_text(raw, encoding="utf-8")
        diag = classify_output(raw, exit_code)
        print(render(diag, args.level))
        return exit_code

    if args.command == "classify":
        raw = (
            _safe_path(args.raw_file, flag="--raw-file").read_text(encoding="utf-8")
            if args.raw_file is not None
            else sys.stdin.read()
        )
        diag = classify_output(raw, args.exit_code, tool_hint=args.tool)
        print(render(diag, args.level))
        return args.exit_code

    return 2  # unreachable - argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
