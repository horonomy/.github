#!/usr/bin/env python3
"""Horonom local agent-workspace bootstrap and context resolver (HORO-506).

Projects a navigation entrypoint (`CLAUDE.md`, `AGENTS.md`) and a
company/product directory skeleton into a configurable
`$HORONOM_WORKSPACE_ROOT`, per ADR-0005's decisions #3 (workspace-root is
additive and optional — no product repo may depend on it) and #4 (generated
projections, not symlinks, for anything that could be checked into a repo;
this script itself only ever writes inside the workspace root, never inside
a product repo).

This script never hard-codes a personal path. The workspace root is always
either `--root` or the `HORONOM_WORKSPACE_ROOT` environment variable —
there is no third, defaulted location.

Commands:
    bootstrap   First-time setup: create the root layout and generated files.
    sync        Re-run bootstrap; safe and idempotent, refreshes drift.
    status      Report workspace/repo/drift state without writing anything.

`bootstrap` and `sync` are intentionally the same operation under two names
(HORO-506 AC: "re-running bootstrap is idempotent") — there is no
meaningfully different "first run" behavior to give a second command.

Stdlib only, matching scripts/generate_company_metadata.py's contract; reuses
its minimal YAML parser rather than adding a second one.

Usage:
    python3 scripts/horonom_workspace.py bootstrap --root /path/to/workspace
    python3 scripts/horonom_workspace.py sync
    python3 scripts/horonom_workspace.py status
    python3 scripts/horonom_workspace.py status --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import generate_company_metadata as company_meta

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "governance" / "workspace" / "manifest.yaml"
GOVERNANCE_VERSION_PATH = REPO_ROOT / "metadata" / "governance.yaml"

# Every generated file starts with this exact line. A file that exists but
# doesn't start with it is treated as user-authored — bootstrap/sync refuse
# to overwrite it (HORO-506 AC: "never overwrite a user-authored local file
# without detecting ownership/drift").
GENERATED_MARKER = "<!-- horonom:generated -->"

_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_STATE_SCHEMA_VERSION = 1


class WorkspaceError(RuntimeError):
    """Raised for any condition that should abort before writing anything."""


# ---------------------------------------------------------------------------
# Manifest and governance-version loading
# ---------------------------------------------------------------------------
def load_manifest() -> list[dict[str, str]]:
    try:
        text = MANIFEST_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkspaceError(f"manifest not found: {MANIFEST_PATH}") from exc
    try:
        data = company_meta.parse_yaml(text)
    except company_meta.YamlError as exc:
        raise WorkspaceError(f"manifest.yaml: {exc}") from exc

    repos = data.get("repos")
    if not isinstance(repos, list) or not repos:
        raise WorkspaceError("manifest.yaml: 'repos' must be a non-empty list")

    seen: set[str] = set()
    for entry in repos:
        if not isinstance(entry, dict):
            raise WorkspaceError(f"manifest.yaml: repo entry is not a mapping: {entry!r}")
        for field in ("name", "org", "repo", "category"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                raise WorkspaceError(f"manifest.yaml: entry missing '{field}': {entry!r}")
        name = entry["name"]
        # Path-traversal / shell-injection guard: names become directory
        # segments and, in printed clone hints, argv tokens. Reject anything
        # that isn't a plain lowercase identifier.
        if not _SAFE_NAME_RE.match(name):
            raise WorkspaceError(f"manifest.yaml: unsafe repo name {name!r}")
        if not _SAFE_NAME_RE.match(entry["org"]) or not _SAFE_REPO_RE.match(entry["repo"]):
            raise WorkspaceError(f"manifest.yaml: unsafe org/repo for {name!r}")
        if entry["category"] not in ("company", "product"):
            raise WorkspaceError(
                f"manifest.yaml: unknown category {entry['category']!r} for {name!r}"
            )
        if name in seen:
            raise WorkspaceError(f"manifest.yaml: duplicate repo name {name!r}")
        seen.add(name)
    return repos


def load_governance_version() -> int:
    try:
        text = GOVERNANCE_VERSION_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkspaceError(f"governance version file not found: {GOVERNANCE_VERSION_PATH}") from exc
    data = company_meta.parse_yaml(text)
    raw = data.get("governance_version")
    try:
        return int(str(raw))
    except (TypeError, ValueError) as exc:
        raise WorkspaceError(f"metadata/governance.yaml: invalid governance_version {raw!r}") from exc


# ---------------------------------------------------------------------------
# Workspace root resolution — never a hard-coded default
# ---------------------------------------------------------------------------
def resolve_workspace_root(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("HORONOM_WORKSPACE_ROOT")
    if not raw:
        raise WorkspaceError(
            "HORONOM_WORKSPACE_ROOT is not set and --root was not given. "
            "This tool never assumes a default location — export "
            "HORONOM_WORKSPACE_ROOT=/path/to/your/workspace, or pass --root."
        )
    root = Path(raw).expanduser().resolve()
    return root


def _category_dir(root: Path, category: str) -> Path:
    return root / ("company" if category == "company" else "products")


def _repo_path(root: Path, entry: dict[str, str]) -> Path:
    root = root.resolve()
    path = (_category_dir(root, entry["category"]) / entry["name"]).resolve()
    # Belt-and-suspenders symlink/traversal guard even though manifest names
    # are already validated: the resolved path must stay under root. Both
    # sides are resolved so a root passed via an unresolved symlinked path
    # (e.g. macOS `/tmp` -> `/private/tmp`) doesn't false-positive here.
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError(f"repo path for {entry['name']!r} escapes workspace root") from exc
    return path


# ---------------------------------------------------------------------------
# Generated content
# ---------------------------------------------------------------------------
def render_root_claude_md(governance_version: int) -> str:
    return (
        f"{GENERATED_MARKER}\n"
        f"<!-- Source: horonomy/.github scripts/horonom_workspace.py, "
        f"governance_version={governance_version}. Do not hand-edit — "
        f"rerun `python3 scripts/horonom_workspace.py sync` from a "
        f"horonomy/.github checkout to refresh. -->\n\n"
        "# Horonom workspace\n\n"
        "Navigation entrypoint for an agent operating in this local\n"
        "`$HORONOM_WORKSPACE_ROOT`. This file is generated — canonical\n"
        "policy content lives in `horonomy/.github`'s `CLAUDE.md` and\n"
        "`governance/**`; read `company/governance/` (once cloned into this\n"
        "workspace) or https://github.com/horonomy/.github for the actual\n"
        "rules.\n\n"
        "## Layout\n\n"
        "- `company/` — Horonom-owned non-product repos (governance,\n"
        "  official-website, internal-docs).\n"
        "- `products/` — Horonom product repos.\n"
        "- `.horonom/state.json` — bootstrap/adoption state for this\n"
        "  workspace, not policy — see `governance/workspace/manifest.yaml`\n"
        "  in `company/governance/` for the canonical repo list.\n\n"
        "## Precedence\n\n"
        "Company → Product → Repository. A narrower context may add to\n"
        "or strengthen a rule, never weaken a company non-waivable\n"
        "invariant. Full rule: `company/governance/governance/README.md`.\n\n"
        "## Standalone-clone boundary\n\n"
        "Nothing under any `products/<name>` repo may depend on this file\n"
        "or on this workspace root existing — a bare clone of that repo\n"
        "builds and tests exactly as it does today.\n"
    )


def render_root_agents_md(governance_version: int) -> str:
    return (
        f"{GENERATED_MARKER}\n"
        f"<!-- Source: horonomy/.github scripts/horonom_workspace.py, "
        f"governance_version={governance_version}. Do not hand-edit. -->\n\n"
        "# AGENTS.md\n\n"
        "Compatibility pointer for tooling that reads `AGENTS.md` instead of\n"
        "`CLAUDE.md`. This file carries no independent policy content — see\n"
        "[`CLAUDE.md`](./CLAUDE.md), the actual navigation entrypoint for\n"
        "this workspace (ADR-0005 decision #5: an adapter never forks a\n"
        "second copy of canonical content).\n"
    )


def _manifest_checksum(manifest_text: str) -> str:
    return hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Filesystem operations
# ---------------------------------------------------------------------------
def write_generated(path: Path, content: str, *, force: bool, dry_run: bool) -> str:
    """Returns one of: "written", "unchanged", "skipped-conflict"."""
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == content:
            return "unchanged"
        if not current.startswith(GENERATED_MARKER) and not force:
            return "skipped-conflict"
    if dry_run:
        return "written"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "written"


def do_sync(root: Path, *, force: bool, dry_run: bool) -> dict[str, Any]:
    root = root.resolve()
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = load_manifest()
    governance_version = load_governance_version()

    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
        (root / "company").mkdir(parents=True, exist_ok=True)
        (root / "products").mkdir(parents=True, exist_ok=True)

    claude_result = write_generated(
        root / "CLAUDE.md", render_root_claude_md(governance_version), force=force, dry_run=dry_run
    )
    agents_result = write_generated(
        root / "AGENTS.md", render_root_agents_md(governance_version), force=force, dry_run=dry_run
    )

    repos: dict[str, Any] = {}
    for entry in manifest:
        path = _repo_path(root, entry)
        present = path.is_dir() and (path / ".git").exists()
        repos[entry["name"]] = {
            "category": entry["category"],
            "path": str(path.relative_to(root)),
            "clone_url": f"https://github.com/{entry['org']}/{entry['repo']}.git",
            "present": present,
        }

    state = {
        "schema_version": _STATE_SCHEMA_VERSION,
        "governance_version": governance_version,
        "manifest_checksum": _manifest_checksum(manifest_text),
        "repos": repos,
    }
    if not dry_run:
        state_dir = root / ".horonom"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "root": str(root),
        "claude_md": claude_result,
        "agents_md": agents_result,
        "repos": repos,
        "governance_version": governance_version,
    }


def do_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    state_path = root / ".horonom" / "state.json"
    if not state_path.exists():
        return {"root": str(root), "bootstrapped": False}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    current_checksum = _manifest_checksum(manifest_text)
    current_governance_version = load_governance_version()

    def _current_content(name: str, path: Path) -> str:
        return render_root_claude_md(current_governance_version) if name == "CLAUDE.md" else render_root_agents_md(
            current_governance_version
        )

    drift = {}
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = root / name
        if not path.exists():
            drift[name] = "missing"
        elif path.read_text(encoding="utf-8") != _current_content(name, path):
            drift[name] = "stale"
        else:
            drift[name] = "current"

    repo_status = {}
    for name, info in state.get("repos", {}).items():
        path = root / info["path"]
        repo_status[name] = {**info, "present": path.is_dir() and (path / ".git").exists()}

    return {
        "root": str(root),
        "bootstrapped": True,
        "governance_version": {
            "recorded": state.get("governance_version"),
            "current": current_governance_version,
            "drift": state.get("governance_version") != current_governance_version,
        },
        "manifest_drift": state.get("manifest_checksum") != current_checksum,
        "files": drift,
        "repos": repo_status,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_sync_result(result: dict[str, Any], verb: str) -> None:
    print(f"{verb} workspace at {result['root']} (governance_version={result['governance_version']})")
    for name, outcome in (("CLAUDE.md", result["claude_md"]), ("AGENTS.md", result["agents_md"])):
        print(f"  {name}: {outcome}")
        if outcome == "skipped-conflict":
            print(
                f"    WARNING: {name} exists and is not a generated file — left untouched. "
                f"Move it aside and rerun, or pass --force to replace it."
            )
    for name, info in sorted(result["repos"].items()):
        marker = "present" if info["present"] else "not cloned yet"
        print(f"  repo {name} ({info['category']}) -> {info['path']}: {marker}")
        if not info["present"]:
            print(f"    clone with: git clone {info['clone_url']} {info['path']}")


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    result = do_sync(root, force=args.force, dry_run=False)
    _print_sync_result(result, "Bootstrapped")
    if any(v == "skipped-conflict" for v in (result["claude_md"], result["agents_md"])):
        return 1
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    result = do_sync(root, force=args.force, dry_run=False)
    _print_sync_result(result, "Synced")
    if any(v == "skipped-conflict" for v in (result["claude_md"], result["agents_md"])):
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = resolve_workspace_root(args.root)
    result = do_status(root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if not result["bootstrapped"]:
        print(f"{result['root']}: not bootstrapped (run `bootstrap` first)")
        return 1
    print(f"{result['root']}: bootstrapped")
    gv = result["governance_version"]
    print(f"  governance_version: recorded={gv['recorded']} current={gv['current']} drift={gv['drift']}")
    print(f"  manifest_drift: {result['manifest_drift']}")
    for name, state in result["files"].items():
        print(f"  {name}: {state}")
    for name, info in sorted(result["repos"].items()):
        print(f"  repo {name} ({info['category']}): present={info['present']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="Workspace root (overrides $HORONOM_WORKSPACE_ROOT).")

    p_bootstrap = sub.add_parser("bootstrap", parents=[common], help="First-time workspace setup.")
    p_bootstrap.add_argument(
        "--force", action="store_true", help="Overwrite a non-generated CLAUDE.md/AGENTS.md if present."
    )
    p_bootstrap.set_defaults(func=_cmd_bootstrap)

    p_sync = sub.add_parser("sync", parents=[common], help="Re-run bootstrap; idempotent.")
    p_sync.add_argument(
        "--force", action="store_true", help="Overwrite a non-generated CLAUDE.md/AGENTS.md if present."
    )
    p_sync.set_defaults(func=_cmd_sync)

    p_status = sub.add_parser("status", parents=[common], help="Report state without writing anything.")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p_status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
