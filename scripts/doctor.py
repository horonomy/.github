#!/usr/bin/env python3
"""`horonom doctor` — detect workspace/repo/policy-adoption drift (HORO-510).

Runs the checks in scripts/doctor_checks.py against whichever targets the
caller provides (`--repo`, `--workspace-root`, both, or neither), classifies
each PASS/WARN/FAIL/NOT_APPLICABLE with an actionable fix hint, and computes
an overall status (worst of FAIL/WARN wins; NOT_APPLICABLE and PASS never
make things look worse than PASS).

Read-only: this command never writes anything. Where a check found the
underlying tool that *could* fix a problem, it names that tool's own
sync/bootstrap command rather than doctor attempting to fix anything itself
— HORO-510's own scope explicitly wants any future `--fix` behavior kept
separate, explicit, and bounded.

Usage:
    python3 scripts/doctor.py --repo /path/to/some/horonom-repo
    python3 scripts/doctor.py --workspace-root /path/to/workspace
    python3 scripts/doctor.py --repo . --workspace-root "$HORONOM_WORKSPACE_ROOT" --product circinus
    python3 scripts/doctor.py --repo . --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR.parent / "agents" / "adapters" / "codex"))

import doctor_checks as checks  # noqa: E402
import horonom_workspace as hw  # noqa: E402

try:
    import horonom_codex  # type: ignore  # noqa: E402
except ImportError:
    horonom_codex = None


def _resolve_evidence_path(repo: Path, workspace_root: Path | None, product: str) -> Path:
    """The release-evidence file lives in the governance (`.github`) repo's
    `metadata/release-evidence/`, not in a product's own repo — resolve it
    relative to `--repo` when `--repo` *is* the governance checkout, else
    fall back to the workspace manifest's `governance` entry (`repo:
    .github`) when a `--workspace-root` is given. Without this fallback,
    `doctor --repo <product-repo> --product X` always resolves the same
    wrong path and always reports NOT_APPLICABLE, even when the evidence
    genuinely exists in the sibling governance checkout — a false negative
    found while dogfooding this exact invocation shape against a real
    adopted product repo (HORO-533)."""
    candidate = repo / "metadata" / "release-evidence" / f"{product}.yaml"
    if candidate.is_file() or workspace_root is None:
        return candidate
    try:
        manifest = hw.load_manifest()
    except hw.WorkspaceError:
        return candidate
    governance_entry = next((e for e in manifest if e["repo"] == ".github"), None)
    if governance_entry is None:
        return candidate
    governance_repo = hw._repo_path(workspace_root, governance_entry)
    fallback = governance_repo / "metadata" / "release-evidence" / f"{product}.yaml"
    return fallback if fallback.is_file() else candidate


def run_checks(
    *,
    repo: Path | None,
    workspace_root: Path | None,
    expected_org: str,
    product: str | None,
) -> list[checks.CheckResult]:
    results: list[checks.CheckResult] = []

    if repo is not None:
        results.append(checks.check_repo_adoption(repo))
        results.append(checks.check_skill_adapter_markers(repo))
        results.append(checks.check_contributing_present(repo))
        results.append(checks.check_pr_template_present(repo))
        results.append(checks.check_claude_entrypoint_present(repo))
        results.append(checks.check_remote_sanity(repo, expected_org))
        generated_targets = [
            repo / ".codex" / "config.toml",
            *((repo / ".claude" / "skills").glob("*/SKILL.md") if (repo / ".claude" / "skills").is_dir() else []),
            *((repo / ".codex" / "skills").glob("*.md") if (repo / ".codex" / "skills").is_dir() else []),
        ]
        results.append(checks.check_no_secrets_in_generated_files(generated_targets))
        if product:
            evidence_path = _resolve_evidence_path(repo, workspace_root, product)
            results.append(checks.check_public_release_adoption(evidence_path))
    else:
        results.append(
            checks.CheckResult("repo_checks", checks.NOT_APPLICABLE, "no --repo given — repo-level checks skipped")
        )

    if workspace_root is not None:
        results.append(checks.check_workspace_bootstrap(workspace_root))
        if horonom_codex is not None:
            results.append(checks.check_codex_adapter(workspace_root, horonom_codex_module=horonom_codex))
        results.append(
            checks.check_no_secrets_in_generated_files(
                [workspace_root / "CLAUDE.md", workspace_root / "AGENTS.md", workspace_root / ".codex" / "config.toml"]
            )
        )
    else:
        results.append(
            checks.CheckResult(
                "workspace_checks", checks.NOT_APPLICABLE, "no --workspace-root given and $HORONOM_WORKSPACE_ROOT unset — workspace-level checks skipped"
            )
        )

    return results


def compute_overall(results: list[checks.CheckResult]) -> str:
    statuses = {r.status for r in results}
    for candidate in checks._PRECEDENCE:
        if candidate in statuses:
            return candidate
    return checks.PASS


def _resolve_optional_path(value: str | None, env_var: str | None = None) -> Path | None:
    if value:
        return Path(value).expanduser().resolve()
    if env_var:
        import os

        raw = os.environ.get(env_var)
        if raw:
            return Path(raw).expanduser().resolve()
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="Path to a Horonom repo checkout to check.")
    parser.add_argument(
        "--workspace-root", help="Path to $HORONOM_WORKSPACE_ROOT (defaults to the env var if set)."
    )
    parser.add_argument("--expected-org", default="horonomy", help="GitHub org a repo's remote should point at (default: horonomy).")
    parser.add_argument("--product", help="Product name to check public-release adoption for, if --repo is also given.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    workspace_root = _resolve_optional_path(args.workspace_root, "HORONOM_WORKSPACE_ROOT")

    results = run_checks(repo=repo, workspace_root=workspace_root, expected_org=args.expected_org, product=args.product)
    overall = compute_overall(results)

    if args.json:
        print(
            json.dumps(
                {
                    "overall": overall,
                    "checks": [
                        {"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix} for r in results
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"horonom doctor: overall={overall}")
        for r in results:
            line = f"  {r.name}: {r.status} — {r.detail}"
            print(line)
            if r.fix:
                print(f"    fix: {r.fix}")

    return 1 if overall == checks.FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
