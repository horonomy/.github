#!/usr/bin/env python3
"""Workspace-wide reconcile over configured local roots (HORO-982 §3).

Discovers real git repos as *direct children* of one or more configured
roots — never a recursive/broad descent (HORO-982 AC: "avoid broad
filesystem scanning outside configured roots"; a real root on this
machine holds `.pnpm-store/`, `.worktrees/`, screenshot dumps, and dozens
of ticket-suffixed worktree directories a recursive `.git` search would
walk into repeatedly) — classifies each repo's mode (`adopt` vs `consume`)
from its own git remote (never local directory naming — AC "canonical
remote/org drives mode selection"), defers a repo that's a linked
worktree or has uncommitted changes rather than touching it, and reports
per-repo UPDATED / ALREADY_CURRENT / DEFERRED / NOT_APPLICABLE / ERROR.

`plan` and `apply` mirror `repo_scaffold.py`'s subcommand vocabulary
rather than a `--dry-run` flag — a multi-repo reconcile can genuinely
partially apply (repo 3 of 10 errors), so the two are named differently
on purpose, not toggled by one flag whose meaning would blur across a
whole run.

Roots are always explicit `--root` (repeatable) or the colon-separated
`$HORONOM_RECONCILE_ROOTS` env var — this tool never hard-codes a
founder-specific path (HORO-982 AC).

Usage:
    python3 scripts/workspace_reconcile.py plan --root /path/to/horonomy --root /path/to/AI-agent-assembly
    python3 scripts/workspace_reconcile.py apply --root /path/to/horonomy --root /path/to/AI-agent-assembly
    python3 scripts/workspace_reconcile.py plan --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "agents" / "common"))

import repo_bootstrap as rb  # noqa: E402  (reuse adopt()/consume()/resolve_org(), never reimplemented)

UPDATED = "UPDATED"
ALREADY_CURRENT = "ALREADY_CURRENT"
DEFERRED = "DEFERRED"
NOT_APPLICABLE = "NOT_APPLICABLE"
ERROR = "ERROR"


class ReconcileError(RuntimeError):
    pass


@dataclass
class RepoOutcome:
    path: str
    mode: str  # "adopt" | "consume" | "none"
    status: str
    detail: str


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------
def resolve_roots(explicit: list[str] | None) -> list[Path]:
    raw: list[str] = list(explicit or [])
    if not raw:
        env = os.environ.get("HORONOM_RECONCILE_ROOTS")
        if env:
            raw = [p for p in env.split(":") if p]
    if not raw:
        raise ReconcileError(
            "no roots given — pass --root (repeatable) or set "
            "$HORONOM_RECONCILE_ROOTS (colon-separated). This tool never "
            "assumes a default location."
        )
    return [Path(r).expanduser().resolve() for r in raw]


# ---------------------------------------------------------------------------
# Discovery — bounded to direct children, one level deep, no recursion.
# ---------------------------------------------------------------------------
def discover_repos(root: Path) -> list[Path]:
    """Direct children of `root` that are real git checkouts (a `.git`
    entry present, file or directory — see `is_worktree`). A missing root
    yields an empty list rather than an error: a not-yet-cloned root is a
    legitimate, common state, not a failure."""
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / ".git").exists():
            found.append(child)
    return found


def is_worktree(repo: Path) -> bool:
    """True for a linked worktree (`.git` is a *file* pointing at the main
    checkout's worktree metadata), false for a main checkout (`.git` is a
    *directory*). Per this workspace's own convention
    (governance/engineering/git-pr-merge.md's per-ticket worktree
    workflow), a live linked worktree means in-flight ticket work —
    reconcile must defer it, never write through it, and never guess at
    "session activity" some other way this tool has no way to observe."""
    return (repo / ".git").is_file()


def _run_git(repo: Path, args: list[str]):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)


def _is_dirty(repo: Path) -> bool:
    """Mirrors repo_bootstrap.py's own `_git_status_clean` semantics
    (tracked changes only; untracked files don't count as "in-flight").
    A git failure is treated as dirty — reconcile must never write to a
    repo it couldn't actually confirm was clean."""
    result = _run_git(repo, ["status", "--porcelain"])
    if result.returncode != 0:
        return True
    tracked_changes = [line for line in result.stdout.splitlines() if not line.startswith("??")]
    return bool(tracked_changes)


def classify_mode(repo: Path, *, expected_org: str) -> str | None:
    """"adopt" when the repo's remote resolves to `expected_org`,
    "consume" when it resolves to any other real org, None (the caller
    reports NOT_APPLICABLE) when no remote could be resolved at all —
    mode selection never falls back to guessing from the directory name."""
    org = rb.resolve_org(repo)
    if org is None:
        return None
    return "adopt" if org == expected_org else "consume"


_SKILLS_COUNT_RE = re.compile(r"(\d+) (?:written|would-write)")


def _map_outcome(result: dict) -> str:
    """`rb.adopt()`/`rb.consume()`'s own per-artifact outcome strings,
    mapped onto this tool's report vocabulary.

    'skipped-conflict' (in any field) means hand-authored content was
    deliberately left untouched — DEFERRED, not UPDATED, even though the
    underlying call didn't raise; reporting it as UPDATED would claim a
    write that never happened.

    The marker fields (`adoption_marker`/`consumption_marker`) always say
    "written"/"would-write" on every call — the marker's own `adopted_at`/
    `consumed_at` timestamp changes on every real run, so they're a poor
    idempotency signal and are deliberately excluded here; only the actual
    content fields (`claude_md`/`agents_md`, and the leading write count in
    `skills`) decide UPDATED vs. ALREADY_CURRENT — matching what a repeat
    `plan`/`apply` against an already-current repo should honestly report."""
    if any("skipped-conflict" in str(v) for v in result.values()):
        return DEFERRED
    for key in ("claude_md", "agents_md"):
        if result.get(key) in ("written", "created", "would-write"):
            return UPDATED
    skills_value = result.get("skills")
    if isinstance(skills_value, str):
        match = _SKILLS_COUNT_RE.search(skills_value)
        if match and int(match.group(1)) > 0:
            return UPDATED
    return ALREADY_CURRENT


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------
def reconcile(roots: list[Path], *, expected_org: str, apply: bool, force: bool = False) -> list[RepoOutcome]:
    outcomes: list[RepoOutcome] = []
    for root in roots:
        for repo in discover_repos(root):
            path_str = str(repo)
            if is_worktree(repo):
                outcomes.append(
                    RepoOutcome(path_str, "none", DEFERRED, "linked worktree — in-flight ticket work, never touched by reconcile")
                )
                continue
            if _is_dirty(repo):
                outcomes.append(RepoOutcome(path_str, "none", DEFERRED, "uncommitted changes — deferred, never overwritten"))
                continue

            mode = classify_mode(repo, expected_org=expected_org)
            if mode is None:
                outcomes.append(RepoOutcome(path_str, "none", NOT_APPLICABLE, "no resolvable git remote"))
                continue

            try:
                if mode == "adopt":
                    result = rb.adopt(repo, org=expected_org, dry_run=not apply, force=force)
                else:
                    result = rb.consume(repo, dry_run=not apply, force=force)
            except rb.AdoptionError as exc:
                outcomes.append(RepoOutcome(path_str, mode, ERROR, str(exc)))
                continue
            outcomes.append(RepoOutcome(path_str, mode, _map_outcome(result), json.dumps(result, sort_keys=True)))
    return outcomes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _run(args: argparse.Namespace, *, apply: bool) -> int:
    try:
        roots = resolve_roots(args.root)
        outcomes = reconcile(roots, expected_org=args.expected_org, apply=apply, force=args.force)
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(o) for o in outcomes], indent=2, sort_keys=True))
    else:
        verb = "Applied" if apply else "Planned"
        print(f"{verb} reconcile over {len(roots)} root(s), {len(outcomes)} repo(s) discovered:")
        for o in outcomes:
            print(f"  {o.path} [{o.mode}]: {o.status} — {o.detail}")

    return 1 if any(o.status == ERROR for o in outcomes) else 0


def _cmd_plan(args: argparse.Namespace) -> int:
    return _run(args, apply=False)


def _cmd_apply(args: argparse.Namespace) -> int:
    return _run(args, apply=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", action="append", help="A workspace root to reconcile (repeatable).")
    common.add_argument("--expected-org", default="horonomy", help="Org that selects adopt mode (default: horonomy).")
    common.add_argument("--force", action="store_true", help="Passed through to adopt()/consume() for a deliberate mode switch.")
    common.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    p_plan = sub.add_parser("plan", parents=[common], help="Report what reconcile would do; writes nothing.")
    p_plan.set_defaults(func=_cmd_plan)

    p_apply = sub.add_parser("apply", parents=[common], help="Actually reconcile every discovered repo.")
    p_apply.set_defaults(func=_cmd_apply)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
