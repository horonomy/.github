#!/usr/bin/env python3
"""Individual `horonom doctor` checks (HORO-510).

Each check is a pure-ish function taking explicit inputs and returning a
`CheckResult` — PASS / WARN / FAIL / NOT_APPLICABLE with a human-readable
`detail` and, for anything not PASS, an actionable `fix` hint. Keeping each
check a small function with explicit inputs (rather than one big function
reading global state) is what makes them independently unit-testable and
lets `scripts/doctor.py` decide which checks apply to a given target
(a repo path, a workspace root, or both) without a check needing to know.

No check here ever scans arbitrary repo content — each one reads a fixed,
named set of files it's documented to read. That's the "bounded, not a
broad exploratory scan" requirement from the HORO-510 AC, and it's also
what keeps a hostile/malformed target repo from being able to make a check
walk somewhere it shouldn't.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "agents" / "adapters" / "codex"))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "agents" / "common"))

import generate_company_metadata as company_meta  # noqa: E402  (reuse secret-pattern guard)
import horonom_workspace as hw  # noqa: E402
import project_skills  # noqa: E402
import public_release_reconcile as prr  # noqa: E402
import repo_bootstrap as rb  # noqa: E402

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"

_PRECEDENCE = [FAIL, WARN]  # NOT_APPLICABLE and PASS never drive overall to worse than PASS


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    fix: str | None = None


# ---------------------------------------------------------------------------
# Repo-level checks — work from a plain repo clone, no workspace root needed.
# ---------------------------------------------------------------------------
def check_skill_adapter_markers(repo: Path) -> CheckResult:
    """A repo that has adopted shared skills should have every *company-projected*
    SKILL.md carry the generated-provenance marker — a missing marker means
    someone hand-edited a file that's supposed to be regenerated.

    Only checks the canonical company skill names (`project_skills.discover_skills()`)
    — a repo may also carry its own project-local skills under the same
    `.claude/skills/`/`.codex/skills/` directories (e.g. Eridanus's own
    jira-ticket-lifecycle/adr-management/etc., predating this campaign, ADR-governed,
    never meant to carry this marker). Globbing every file there without this filter
    flags those as "hand-edited" and tells the operator to restore them from
    horonomy/.github's canonical set — which doesn't even contain those names — a
    false positive found while dogfooding this check against a real adopted repo
    (HORO-533)."""
    try:
        canonical_names = set(project_skills.discover_skills())
    except project_skills.SkillProjectionError as exc:
        # Independent review (HORO-533): silently treating this as "zero
        # canonical names" would make a genuinely broken governance
        # checkout (missing/misconfigured agents/skills/) indistinguishable
        # from "governance not yet adopted here" (NOT_APPLICABLE) — a real
        # internal-tooling fault masquerading as nothing-to-see. This is a
        # fault in the .github checkout doctor itself is running from, not
        # in the target repo being audited, so it's WARN rather than FAIL.
        return CheckResult(
            "skill_adapter_markers",
            WARN,
            f"could not determine canonical company skill names: {exc}",
            fix="run this from a healthy horonomy/.github checkout with agents/skills/ intact",
        )
    claude_skills = repo / ".claude" / "skills"
    codex_skills = repo / ".codex" / "skills"
    files = []
    if claude_skills.is_dir():
        files.extend(f for f in claude_skills.glob("*/SKILL.md") if f.parent.name in canonical_names)
    if codex_skills.is_dir():
        files.extend(f for f in codex_skills.glob("*.md") if f.stem in canonical_names)
    if not files:
        return CheckResult(
            "skill_adapter_markers",
            NOT_APPLICABLE,
            "no company-projected skill files found — governance not yet adopted here (or this repo only carries its own project-local skills)",
            fix="run the repo-bootstrap skill, then agents/common/project_skills.py",
        )
    unmarked = [f for f in files if not f.read_text(encoding="utf-8", errors="replace").startswith("<!-- horonom:generated -->")]
    if unmarked:
        names = ", ".join(str(f.relative_to(repo)) for f in unmarked)
        return CheckResult(
            "skill_adapter_markers",
            FAIL,
            f"{len(unmarked)} projected skill file(s) missing the generated marker: {names}",
            fix="these were hand-edited — restore from horonomy/.github's canonical agents/skills/ and rerun project_skills.py",
        )
    return CheckResult("skill_adapter_markers", PASS, f"{len(files)} projected skill file(s), all carry the generated marker")


def check_repo_adoption(repo: Path) -> CheckResult:
    """Delegates to scripts/repo_bootstrap.py check — a repo that has never
    been adopted (no .horonom-adoption.yaml) is NOT_APPLICABLE, not FAIL:
    plenty of repos haven't adopted governance yet, and that's a
    repo-bootstrap job, not a doctor finding."""
    if not (repo / rb.ADOPTION_MARKER_FILENAME).is_file():
        return CheckResult(
            "repo_adoption",
            NOT_APPLICABLE,
            "not adopted (no .horonom-adoption.yaml) — this is fine, not every repo has adopted governance yet",
            fix="run scripts/repo_bootstrap.py adopt <this repo> if it should be adopted",
        )
    try:
        results = rb.check(repo)
    except Exception as exc:  # noqa: BLE001 — surface as a doctor FAIL, never crash the whole run
        return CheckResult("repo_adoption", FAIL, f"repo_bootstrap check() raised: {exc}")
    worst = PASS
    details = []
    for name, status, detail in results:
        details.append(f"{name}={status}")
        if status == "FAIL":
            worst = FAIL
        elif status == "WARN" and worst != FAIL:
            worst = WARN
    return CheckResult(
        "repo_adoption",
        worst,
        "; ".join(details),
        fix=None if worst == PASS else "run scripts/repo_bootstrap.py check <repo> for per-item detail",
    )


def check_contributing_present(repo: Path) -> CheckResult:
    if (repo / "CONTRIBUTING.md").is_file():
        return CheckResult("contributing_present", PASS, "CONTRIBUTING.md exists")
    return CheckResult(
        "contributing_present",
        WARN,
        "no CONTRIBUTING.md in this repo",
        fix="add one per NEW_REPO_CHECKLIST.md, or confirm the org-level community-health fallback covers this repo",
    )


def check_pr_template_present(repo: Path) -> CheckResult:
    if (repo / ".github" / "pull_request_template.md").is_file():
        return CheckResult("pr_template_present", PASS, ".github/pull_request_template.md exists")
    return CheckResult(
        "pr_template_present",
        WARN,
        "no .github/pull_request_template.md in this repo",
        fix="copy the shape from horonomy/.github/.github/pull_request_template.md",
    )


def check_claude_entrypoint_present(repo: Path) -> CheckResult:
    if (repo / ".claude" / "CLAUDE.md").is_file() or (repo / "CLAUDE.md").is_file():
        return CheckResult("claude_entrypoint_present", PASS, "CLAUDE.md (or .claude/CLAUDE.md) exists")
    return CheckResult(
        "claude_entrypoint_present",
        WARN,
        "no CLAUDE.md found",
        fix="run the repo-bootstrap skill to add one pointing at the org baseline",
    )


def check_remote_sanity(repo: Path, expected_org: str, *, run_git: Any = None) -> CheckResult:
    """Confirms at least one remote points at the expected GitHub org,
    without assuming which remote name (never `origin`-only)."""
    run = run_git or (lambda args: subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10))
    try:
        result = run(["remote", "-v"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("remote_sanity", FAIL, f"could not run git: {exc}", fix="confirm this is a real git checkout")
    if result.returncode != 0:
        return CheckResult("remote_sanity", FAIL, "not a git repository (or no remotes)", fix="run from inside a real git checkout")
    pattern = re.compile(r"github\.com[:/]" + re.escape(expected_org) + r"/", re.IGNORECASE)
    if pattern.search(result.stdout):
        return CheckResult("remote_sanity", PASS, f"a remote points at github.com/{expected_org}/*")
    return CheckResult(
        "remote_sanity",
        WARN,
        f"no remote points at github.com/{expected_org}/* — this may be a fork or an unrelated checkout",
        fix="verify with `git remote -v` before pushing; never assume `origin` is the canonical remote",
    )


# ---------------------------------------------------------------------------
# Workspace-level checks — need $HORONOM_WORKSPACE_ROOT.
# ---------------------------------------------------------------------------
def check_workspace_bootstrap(root: Path) -> CheckResult:
    status = hw.do_status(root)
    if not status["bootstrapped"]:
        return CheckResult(
            "workspace_bootstrap", FAIL, "workspace has not been bootstrapped", fix="run scripts/horonom_workspace.py bootstrap"
        )
    problems = [f"{name}={state}" for name, state in status["files"].items() if state != "current"]
    if status["manifest_drift"] or problems:
        detail = "workspace files are stale: " + ", ".join(problems) if problems else "manifest has drifted since last sync"
        return CheckResult("workspace_bootstrap", WARN, detail, fix="run scripts/horonom_workspace.py sync")
    return CheckResult("workspace_bootstrap", PASS, "workspace CLAUDE.md/AGENTS.md are current, manifest matches")


def check_codex_adapter(root: Path, *, horonom_codex_module: Any) -> CheckResult:
    status = horonom_codex_module.do_status(root)
    if status["ready"]:
        return CheckResult("codex_adapter", PASS, "Codex adapter config is current")
    reason = status["reason"] or "not ready"
    # Codex adoption is optional per repo/engineer — a missing config is a
    # WARN (something to set up), never a FAIL that blocks an otherwise
    # healthy Claude-only workspace.
    return CheckResult(
        "codex_adapter", WARN, f"Codex adapter not ready: {reason}", fix="run agents/adapters/codex/horonom_codex.py sync"
    )


# ---------------------------------------------------------------------------
# Public-release adoption — never claims "complete" for N/A or not-yet-public.
# ---------------------------------------------------------------------------
_RELEASE_STATE_TO_DOCTOR = {
    prr.VERIFIED: PASS,
    prr.REQUIRED: WARN,
    prr.DEFERRED: NOT_APPLICABLE,
    prr.NOT_APPLICABLE: NOT_APPLICABLE,
    prr.NOT_YET_PUBLIC: NOT_APPLICABLE,
    prr.BLOCKED_EXTERNAL: WARN,
    prr.FAILED: FAIL,
}


def check_public_release_adoption(evidence_path: Path, *, fetchers: Any = prr.REAL_FETCHERS) -> CheckResult:
    """Maps the Public Release Surface Contract's overall verdict onto
    doctor's PASS/WARN/FAIL/NOT_APPLICABLE scale — critically, NOT_YET_PUBLIC
    and DEFERRED map to NOT_APPLICABLE, never PASS. A doctor report that
    said PASS for a product that is intentionally not yet public would be
    exactly the false "public surface complete" claim this check exists to
    prevent."""
    if not evidence_path.exists():
        return CheckResult(
            "public_release_adoption",
            NOT_APPLICABLE,
            "no release-evidence config for this product",
            fix="add metadata/release-evidence/<product>.yaml if this product should be reconciled",
        )
    try:
        evidence = prr.load_evidence(evidence_path)
        result = prr.reconcile(evidence, fetchers)
    except prr.ReconcileError as exc:
        return CheckResult("public_release_adoption", FAIL, f"invalid release-evidence config: {exc}")
    overall = result["overall"]
    status = _RELEASE_STATE_TO_DOCTOR[overall]
    note = ""
    if overall in (prr.NOT_YET_PUBLIC, prr.DEFERRED):
        note = " (correctly not evaluated as complete — this is not a gap)"
    return CheckResult(
        "public_release_adoption",
        status,
        f"Public Release Surface Contract overall={overall}{note}",
        fix=None if status == PASS else "see `python3 scripts/public_release_reconcile.py <evidence>` for per-surface detail",
    )


# ---------------------------------------------------------------------------
# Secret scan — bounded to a fixed, named set of generated files.
# ---------------------------------------------------------------------------
def check_no_secrets_in_generated_files(paths: list[Path]) -> CheckResult:
    hits = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in company_meta._SECRET_VALUE_RES:
            if pattern.search(text):
                hits.append(str(path))
                break
    if hits:
        return CheckResult(
            "no_secrets_in_generated_files",
            FAIL,
            f"credential-shaped content found in: {', '.join(hits)}",
            fix="remove the value immediately and rotate the credential — generated files must never carry secrets",
        )
    return CheckResult("no_secrets_in_generated_files", PASS, f"scanned {len(paths)} generated file(s), no credential-shaped content")
