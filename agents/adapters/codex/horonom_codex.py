#!/usr/bin/env python3
"""Horonom-scoped Codex launcher and config sync (HORO-508).

Gives Codex the same canonical Horonom governance and shared skills as the
Claude adapter (HORO-507), without touching the user's global `~/.codex`
and without a second hand-maintained policy corpus (ADR-0005 decision #5).

Mechanism: Codex reads its config from `$CODEX_HOME` (default `~/.codex`).
This adapter sets `CODEX_HOME=$HORONOM_WORKSPACE_ROOT/.codex` for the
launched process only — ordinary `codex` invocations outside this launcher,
anywhere else on the machine, are completely unaffected. `.codex/config.toml`
sets `project_root_markers` so Codex's ancestor-AGENTS.md walk extends up to
`$HORONOM_WORKSPACE_ROOT` (marked by the `.horonom/` directory
`scripts/horonom_workspace.py` already creates) instead of stopping at a
product repo's own git root — so it picks up the workspace root's generated
`AGENTS.md` (HORO-506) the same way Claude Code does.

Commands:
    sync     Write/refresh $HORONOM_WORKSPACE_ROOT/.codex/config.toml.
    launch   Resolve the scoped environment and exec real `codex`.
    status   Report resolved paths and config freshness; write nothing.

Deliberately does NOT reuse the general-purpose `coding-agent-profile`
overlay system some engineers may have installed locally (`ca-codex`,
`~/.coding-agent-profiles`) — that's personal machine tooling, not
something every engineer or CI has. This adapter is self-contained inside
`horonomy/.github` so it works the same way for anyone.

Usage:
    python3 agents/adapters/codex/horonom_codex.py sync --root /path/to/workspace
    python3 agents/adapters/codex/horonom_codex.py status
    python3 agents/adapters/codex/horonom_codex.py launch -- <codex args...>
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import horonom_workspace as hw  # noqa: E402  (reuses root resolution — no second copy)

GENERATED_MARKER = "# horonom:generated"


class CodexAdapterError(RuntimeError):
    pass


def resolve_workspace_root(explicit: str | None) -> Path:
    # Delegate entirely to horonom_workspace's resolver: same env var, same
    # "never assume a default" rule, one place this logic lives.
    try:
        return hw.resolve_workspace_root(explicit)
    except hw.WorkspaceError as exc:
        raise CodexAdapterError(str(exc)) from exc


def _codex_home(root: Path) -> Path:
    codex_home = (root / ".codex").resolve()
    real_home_codex = (Path.home() / ".codex").resolve()
    if codex_home == real_home_codex:
        # A malformed/misconfigured workspace root (e.g. set to $HOME
        # itself) must never resolve to the user's real global Codex home
        # — that would silently defeat the entire scoping purpose of this
        # adapter and could corrupt unrelated Codex projects.
        raise CodexAdapterError(
            f"refusing to use {codex_home} as the scoped Codex home — it is "
            f"the user's real global Codex home. Check $HORONOM_WORKSPACE_ROOT/--root."
        )
    return codex_home


def render_config_toml(governance_version: int) -> str:
    return (
        f"{GENERATED_MARKER}\n"
        f"# Source: horonomy/.github agents/adapters/codex/horonom_codex.py, "
        f"governance_version={governance_version}. Do not hand-edit — rerun "
        f"`python3 agents/adapters/codex/horonom_codex.py sync`.\n\n"
        f'project_root_markers = [".horonom"]\n'
    )


def do_sync(root: Path, *, force: bool) -> str:
    """Returns one of: "written", "unchanged", "skipped-conflict"."""
    governance_version = hw.load_governance_version()
    codex_home = _codex_home(root)
    config_path = codex_home / "config.toml"
    content = render_config_toml(governance_version)
    if config_path.exists():
        current = config_path.read_text(encoding="utf-8")
        if current == content:
            return "unchanged"
        if not current.startswith(GENERATED_MARKER) and not force:
            return "skipped-conflict"
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    return "written"


def do_status(root: Path) -> dict[str, object]:
    codex_home = _codex_home(root)
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        return {"codex_home": str(codex_home), "ready": False, "reason": "config.toml missing"}
    current = config_path.read_text(encoding="utf-8")
    expected = render_config_toml(hw.load_governance_version())
    return {
        "codex_home": str(codex_home),
        "ready": current == expected,
        "reason": None if current == expected else "config.toml is stale — rerun sync",
    }


def verify_ready(root: Path) -> Path:
    """Fails clearly rather than silently launching against stale/absent
    governance (HORO-508 AC)."""
    status = do_status(root)
    if not status["ready"]:
        raise CodexAdapterError(
            f"Codex governance is not ready for this workspace: {status['reason']}. "
            f"Run `python3 agents/adapters/codex/horonom_codex.py sync` first — "
            f"refusing to launch Codex against stale or missing company policy."
        )
    return Path(status["codex_home"])


def build_launch_env(codex_home: Path, base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    env["CODEX_HOME"] = str(codex_home)
    return env


def find_real_codex(path_env: str | None = None) -> str:
    codex_bin = shutil.which("codex", path=path_env)
    if not codex_bin:
        raise CodexAdapterError("'codex' not found on PATH")
    this_file = str(Path(__file__).resolve())
    if Path(codex_bin).resolve() == Path(this_file).resolve():
        raise CodexAdapterError("PATH resolves 'codex' back to this adapter — refusing to self-exec")
    return codex_bin


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_sync(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    outcome = do_sync(root, force=args.force)
    print(f".codex/config.toml: {outcome}")
    if outcome == "skipped-conflict":
        print(
            "WARNING: .codex/config.toml exists and is not generated — left untouched. "
            "Move it aside and rerun, or pass --force.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    status = do_status(root)
    print(f"codex_home: {status['codex_home']}")
    print(f"ready: {status['ready']}")
    if status["reason"]:
        print(f"reason: {status['reason']}")
    return 0 if status["ready"] else 1


def _strip_leading_separator(codex_args: list[str]) -> list[str]:
    # argparse.REMAINDER captures a literal "--" separator if the caller
    # wrote `launch -- --version` (the natural way to pass flags through).
    # Left in place, codex itself would treat "--" as ending its own option
    # parsing and read "--version" as a positional prompt instead of the
    # --version flag — exactly the bug that produced a confusing "stdin is
    # not a terminal" failure during manual end-to-end testing.
    if codex_args and codex_args[0] == "--":
        return codex_args[1:]
    return codex_args


def _cmd_launch(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    codex_home = verify_ready(root)
    codex_bin = find_real_codex()
    env = build_launch_env(codex_home)
    codex_args = _strip_leading_separator(args.codex_args)
    os.execve(codex_bin, [codex_bin, "-c", 'project_root_markers=[".horonom"]', *codex_args], env)
    return 0  # unreachable — execve replaces the process


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="Workspace root (overrides $HORONOM_WORKSPACE_ROOT).")

    p_sync = sub.add_parser("sync", parents=[common], help="Write/refresh .codex/config.toml.")
    p_sync.add_argument("--force", action="store_true", help="Overwrite a non-generated config.toml if present.")
    p_sync.set_defaults(func=_cmd_sync)

    p_status = sub.add_parser("status", parents=[common], help="Report state without writing anything.")
    p_status.set_defaults(func=_cmd_status)

    p_launch = sub.add_parser("launch", parents=[common], help="Exec real `codex` with the scoped environment.")
    p_launch.add_argument("codex_args", nargs=argparse.REMAINDER, help="Arguments passed through to codex.")
    p_launch.set_defaults(func=_cmd_launch)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CodexAdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
