#!/usr/bin/env python3
"""Agent Efficiency Eval harness: RAW vs compact, made re-runnable (HORO-971).

A prior one-shot manual session produced real RAW-vs-compact evidence
(Python pass/fail, Rust compile-fail) by hand, once. This script
formalizes that into a standing, reproducible harness with two modes:

  * `fixture` / `run-all` - deterministic, offline. Scores the bundled
    `fixtures/*.json` captures (Python/pytest, Rust/cargo build+test,
    TypeScript/Vitest) through `diagnostic_compact.py` and reports RAW vs
    compact byte counts and whether the exit code and failure signal
    survived compaction. No toolchain (cargo/npm/pytest) needs to be
    installed to run this mode - that is what makes it reproducible in
    any CI runner, not just a workstation with every language installed.
  * `live` - actually executes a real command in a real repo
    (`--cwd DIR -- <command...>`) and scores that real run the same way.
    This is the "pointed at a repo + command" mode the ticket also asks
    for; it is not used for the bundled fixtures because it would make
    the test suite depend on cargo/npm/pytest being present.

## Why bundled fixtures instead of only live runs

`references/diagnostic-contract.md` and this harness both care about
*signal preservation under compaction*, not about re-verifying that
pytest/cargo/vitest themselves work. Captured, hand-authored-but-realistic
raw output (see `fixtures/*.json`) is deterministic and lets
`test_efficiency_eval.py` assert exact numbers in CI without installing
three language toolchains. The `live` mode exists precisely so a human or
agent can also point this at a real failing build when they want fresh
evidence instead of the canned fixtures.

## TypeScript/Vitest coverage note

No Horonom repo currently wires up Vitest (`horonom-site` uses Docusaurus/
tsc only; the `octans-example-*` TypeScript repos use `node --test`). Per
the ticket's own fallback clause ("if a suitable Horonom TS repo is
available"), `fixtures/typescript_vitest_fail.json` is a small,
self-contained, realistic Vitest failure capture instead of a pointer into
an external repo — this keeps the fixture set complete and reproducible
without inventing Vitest usage in a product repo that doesn't have it.

## CLI

    python3 efficiency_eval.py list
    python3 efficiency_eval.py fixture <name> [--level {0,1,2}]
    python3 efficiency_eval.py run-all [--level {0,1,2}]
    python3 efficiency_eval.py live --cwd DIR [--level {0,1,2}] -- <command...>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostic_compact as dc

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class FixtureCase:
    name: str
    description: str
    raw: str
    exit_code: int
    tool_hint: str | None


class FixtureError(RuntimeError):
    """A fixture file or --cwd/command invocation is malformed."""


def list_fixtures() -> list[str]:
    return sorted(p.stem for p in _FIXTURES_DIR.glob("*.json"))


def load_fixture(name: str) -> FixtureCase:
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.is_file():
        raise FixtureError(f"unknown fixture {name!r}; available: {list_fixtures()}")
    try:
        raw_json = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path}: not valid JSON: {exc}") from exc
    for key in ("raw", "exit_code"):
        if key not in raw_json:
            raise FixtureError(f"{path}: fixture missing required key {key!r}")
    return FixtureCase(
        name=name,
        description=raw_json.get("description", ""),
        raw=raw_json["raw"],
        exit_code=raw_json["exit_code"],
        tool_hint=raw_json.get("tool_hint"),
    )


@dataclass(frozen=True)
class EvalResult:
    name: str
    tool: str
    exit_code: int
    raw_bytes: int
    compact_bytes: int
    reduction_pct: float
    exit_code_preserved: bool
    failure_signal_preserved: bool


def evaluate(raw: str, exit_code: int, *, name: str = "(unnamed)", tool_hint: str | None = None, level: int = 1) -> EvalResult:
    """Score one RAW/exit-code pair: run it through the real diagnostic
    classifier (not a re-implementation of it), and compare RAW bytes to
    the compact rendering's bytes.

    `exit_code_preserved` and `failure_signal_preserved` are correctness
    checks on the compaction itself, not vanity metrics: a compact form
    that shrank bytes by lying about the outcome is a defect, not a win
    (diagnostic-contract.md rule 2/3).
    """
    diag = dc.classify_output(raw, exit_code, tool_hint=tool_hint)
    compact = dc.render(diag, level)

    raw_bytes = len(raw.encode("utf-8"))
    compact_bytes = len(compact.encode("utf-8"))
    reduction_pct = 0.0 if raw_bytes == 0 else round((1 - compact_bytes / raw_bytes) * 100, 1)

    exit_code_preserved = diag.exit_code == exit_code
    # A failing case must still show FAIL and (when the tool has structured
    # output at all) at least one located failure in the compact form; a
    # passing case trivially preserves signal by staying PASS.
    if exit_code == 0:
        failure_signal_preserved = diag.passed is True
    else:
        failure_signal_preserved = diag.passed is False and ("FAIL" in compact)

    return EvalResult(
        name=name,
        tool=diag.tool,
        exit_code=exit_code,
        raw_bytes=raw_bytes,
        compact_bytes=compact_bytes,
        reduction_pct=reduction_pct,
        exit_code_preserved=exit_code_preserved,
        failure_signal_preserved=failure_signal_preserved,
    )


def evaluate_fixture(name: str, level: int = 1) -> EvalResult:
    case = load_fixture(name)
    return evaluate(case.raw, case.exit_code, name=case.name, tool_hint=case.tool_hint, level=level)


def evaluate_live(cwd: Path, command: list[str], level: int = 1) -> EvalResult:
    """Actually run `command` in `cwd` and score the real output. This is
    the "point it at a repo + command" mode; it is deliberately kept out
    of the offline test suite (a real command's output is not
    deterministic across environments/toolchain versions)."""
    if not cwd.is_dir():
        raise FixtureError(f"--cwd {cwd} is not a directory")
    proc = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return evaluate(proc.stdout, proc.returncode, name=" ".join(command), level=level)


def format_table(results: list[EvalResult]) -> str:
    header = f"{'name':<28} {'tool':<8} {'exit':>5} {'raw B':>8} {'compact B':>10} {'reduce%':>8} {'exit-ok':>8} {'signal-ok':>10}"
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.name:<28} {r.tool:<8} {r.exit_code:>5} {r.raw_bytes:>8} {r.compact_bytes:>10} "
            f"{r.reduction_pct:>7}% {r.exit_code_preserved!s:>8} {r.failure_signal_preserved!s:>10}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="efficiency_eval.py",
        description="Agent Efficiency Eval harness: RAW vs compact bytes and exit-code/signal preservation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List bundled fixture names.")

    p_fixture = sub.add_parser("fixture", help="Evaluate one bundled fixture.")
    p_fixture.add_argument("name")
    p_fixture.add_argument("--level", type=int, choices=(0, 1, 2), default=1)

    p_all = sub.add_parser("run-all", help="Evaluate every bundled fixture and print a summary table.")
    p_all.add_argument("--level", type=int, choices=(0, 1, 2), default=1)

    p_live = sub.add_parser("live", help="Run a real command in a real repo and evaluate its output.")
    p_live.add_argument("--cwd", type=Path, required=True)
    p_live.add_argument("--level", type=int, choices=(0, 1, 2), default=1)
    p_live.add_argument("cmd", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    commands = {
        "list": _cmd_list,
        "fixture": _cmd_fixture,
        "run-all": _cmd_run_all,
        "live": _cmd_live,
    }

    try:
        return commands[args.command](args)
    except FixtureError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _cmd_list(_args: argparse.Namespace) -> int:
    for name in list_fixtures():
        print(name)
    return 0


def _cmd_fixture(args: argparse.Namespace) -> int:
    result = evaluate_fixture(args.name, level=args.level)
    print(format_table([result]))
    return 0 if (result.exit_code_preserved and result.failure_signal_preserved) else 1


def _cmd_run_all(args: argparse.Namespace) -> int:
    results = [evaluate_fixture(name, level=args.level) for name in list_fixtures()]
    print(format_table(results))
    ok = all(r.exit_code_preserved and r.failure_signal_preserved for r in results)
    return 0 if ok else 1


def _cmd_live(args: argparse.Namespace) -> int:
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        print("ERROR: no command given after 'live' (use: live --cwd DIR -- <command...>)", file=sys.stderr)
        return 2
    result = evaluate_live(args.cwd, cmd, level=args.level)
    print(format_table([result]))
    return 0 if (result.exit_code_preserved and result.failure_signal_preserved) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
