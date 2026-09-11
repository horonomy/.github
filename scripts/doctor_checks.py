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


def check_applicability_drift(repo: Path) -> CheckResult:
    """HORO-982 AC item 6's "wrong applicability projections" sub-bullet —
    distinct from `check_skill_adapter_markers` (which only asks "is every
    projected file's provenance marker intact") and `check_repo_adoption`/
    `rb.check_consumption` (which check the CLAUDE.md/marker-file governance
    layer, not which *skills* were chosen). This asks a third, independent
    question: does the set of skills actually projected on disk right now
    still match what `resolve_applicable_skills(repo)` would compute today?
    A repo that added a `Cargo.toml` (or removed one) since its last
    `adopt`/`consume` run has drifted — `rust-development` is missing (or
    stale) until the tool is rerun — and neither of the other two checks
    would ever catch that, since the provenance marker and governance
    files can both still be perfectly valid.

    Works for both distribution modes: NOT_APPLICABLE if neither an
    adoption nor a consumption marker is present (nothing to compare
    against — matches `check_repo_adoption`'s own "not every repo has
    adopted governance yet" semantics). Only compares *marker-carrying*
    projected skill directories against the currently-applicable set —
    a hand-edited (unmarked) skill, already reported separately by
    `check_skill_adapter_markers`, is not double-counted as "drift" here."""
    has_adoption = (repo / rb.ADOPTION_MARKER_FILENAME).is_file()
    has_consumption = (repo / rb.CONSUMPTION_MARKER_FILENAME).is_file()
    if not has_adoption and not has_consumption:
        return CheckResult(
            "applicability_drift",
            NOT_APPLICABLE,
            "repo has not run adopt or consume — nothing to compare against",
            fix="run scripts/repo_bootstrap.py adopt/consume if this repo should track shared skills",
        )

    try:
        current_applicable = set(project_skills.resolve_applicable_skills(repo))
    except project_skills.SkillProjectionError as exc:
        return CheckResult(
            "applicability_drift",
            WARN,
            f"could not resolve current applicable skills: {exc}",
            fix="run this from a healthy horonomy/.github checkout with agents/skills/ intact",
        )

    claude_skills_dir = repo / ".claude" / "skills"
    projected: set[str] = set()
    hand_edited: set[str] = set()  # SKILL.md present but doesn't carry the marker
    if claude_skills_dir.is_dir():
        for skill_md in claude_skills_dir.glob("*/SKILL.md"):
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            if text.startswith(project_skills.GENERATED_MARKER):
                projected.add(skill_md.parent.name)
            else:
                hand_edited.add(skill_md.parent.name)

    # `current_applicable` is already a subset of discover_skills() by
    # construction (resolve_applicable_skills() only ever returns names it
    # itself discovered) — independent review (HORO-982, PR #46) found the
    # original version re-called discover_skills() here anyway, purely
    # redundantly, and left it OUTSIDE the try/except above: a canonical
    # skill set that became unreadable between the two calls crashed this
    # check uncaught instead of degrading to the same WARN the first call
    # already handles. Using current_applicable directly removes both the
    # redundancy and the unguarded second I/O call.
    still_missing = sorted(current_applicable - projected - hand_edited)
    hand_edited_and_applicable = sorted(current_applicable & hand_edited)
    stale = sorted(projected - current_applicable)
    if not still_missing and not hand_edited_and_applicable and not stale:
        return CheckResult(
            "applicability_drift", PASS, f"{len(projected)} projected skill(s) match the currently-applicable set"
        )
    details = []
    fixes = []
    if still_missing:
        details.append(f"missing (applicable but never projected): {', '.join(still_missing)}")
        fixes.append("rerun scripts/repo_bootstrap.py adopt/consume to project the missing skill(s)")
    if hand_edited_and_applicable:
        # Independent review (HORO-982, PR #46) found the universal
        # "rerun adopt/consume" fix text is actively wrong for this case:
        # adopt()/consume() both deliberately refuse to overwrite a
        # hand-edited (unmarked) file — rerunning reports skipped-conflict
        # and leaves it exactly as-is, so the WARN would persist forever
        # under the old advice. The real fix is check_skill_adapter_markers'
        # own: restore the canonical content, not rerun adopt/consume.
        details.append(f"hand-edited (marker missing, so treated as not-projected): {', '.join(hand_edited_and_applicable)}")
        fixes.append("these carry hand-edited content — restore from horonomy/.github's canonical agents/skills/ (see skill_adapter_markers), rerunning adopt/consume will not touch them")
    if stale:
        details.append(f"stale (projected but no longer applicable): {', '.join(stale)}")
        fixes.append("rerun scripts/repo_bootstrap.py adopt/consume to remove the now-inapplicable skill(s)")
    return CheckResult("applicability_drift", WARN, "; ".join(details), fix="; ".join(fixes))


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


def check_cross_org_contamination(repo: Path) -> CheckResult:
    """HORO-982: catches a repo that ended up in the wrong distribution
    mode — `adopt`'s Horonom governance block landed in a non-Horonom repo,
    `consume`'s narrower skill-only projection landed in an actual
    `horonomy` repo, or (a state neither CLI path should ever produce
    without `--force`) both markers are present at once. Bounded to the
    two marker files' own recorded `org` field plus a real `git remote -v`
    read — no broader repo content is inspected.

    Independent review (HORO-983 real dogfood, first-ever real `consume`
    run against an actual `ai-agent-assembly/*` repo) found a real bug:
    this check used to take a caller-supplied `expected_org` and compare
    the repo's actual org against IT for the adopt/consume mismatch logic
    — but `expected_org` means "the org I expect *this specific repo* to
    belong to" everywhere else in doctor.py (see `check_remote_sanity`),
    which is a different question from "is this repo's mode the horonomy
    one or not." Checking a real AA repo with the correct, honest
    `--expected-org AI-agent-assembly` (as any real AA operator naturally
    would) made `actual_org == expected_org` true for a correctly-
    consumed repo and produced a false FAIL — reproduced live before this
    fix. Cross-org contamination is always about the horonomy/non-horonomy
    boundary specifically (the same boundary `adopt()`/`consume()`
    themselves gate on via `rb.HORONOMY_ORG`), never about whatever org
    the operator happens to be verifying identity against — so this check
    no longer takes `expected_org` at all, and compares against
    `rb.HORONOMY_ORG` directly, matching `adopt()`/`consume()`'s own
    guards exactly."""
    has_adoption = (repo / rb.ADOPTION_MARKER_FILENAME).is_file()
    has_consumption = (repo / rb.CONSUMPTION_MARKER_FILENAME).is_file()

    if has_adoption and has_consumption:
        return CheckResult(
            "cross_org_contamination",
            FAIL,
            f"both {rb.ADOPTION_MARKER_FILENAME} and {rb.CONSUMPTION_MARKER_FILENAME} are present — "
            f"this repo is in two distribution modes at once",
            fix="remove the mode that doesn't apply and rerun the correct one (adopt xor consume)",
        )
    if not has_adoption and not has_consumption:
        return CheckResult(
            "cross_org_contamination", NOT_APPLICABLE, "repo has not run adopt or consume — nothing to check"
        )

    actual_org = rb.resolve_org(repo)
    if actual_org is None:
        return CheckResult(
            "cross_org_contamination", WARN, "could not resolve the repo's org from its remote", fix="confirm with `git remote -v`"
        )

    if has_adoption and actual_org != rb.HORONOMY_ORG:
        return CheckResult(
            "cross_org_contamination",
            FAIL,
            f"{rb.ADOPTION_MARKER_FILENAME} present (full Horonom governance adopted) but the repo's "
            f"remote resolves to org '{actual_org}', not '{rb.HORONOMY_ORG}' — this repo should have run "
            f"`consume`, not `adopt`",
            fix=f"run `scripts/repo_bootstrap.py consume` here instead, after removing {rb.ADOPTION_MARKER_FILENAME} and the CLAUDE.md/AGENTS.md adoption block",
        )
    if has_consumption and actual_org == rb.HORONOMY_ORG:
        return CheckResult(
            "cross_org_contamination",
            FAIL,
            f"{rb.CONSUMPTION_MARKER_FILENAME} present (skill-only consumption) but the repo's remote "
            f"resolves to org '{rb.HORONOMY_ORG}' — a real Horonom repo should run `adopt`, not `consume`, "
            f"to get full governance",
            fix=f"run `scripts/repo_bootstrap.py adopt` here instead, after removing {rb.CONSUMPTION_MARKER_FILENAME}",
        )
    return CheckResult("cross_org_contamination", PASS, f"distribution mode matches the repo's actual org ('{actual_org}')")


def check_consumption_status(repo: Path) -> CheckResult:
    """Delegates to scripts/repo_bootstrap.py's check_consumption() — the
    `consume`-mode counterpart to check_repo_adoption() above, for a
    non-Horonom repo. Independent review (HORO-982, PR #46's final
    AC-completeness pass) found `horonom doctor` itself never called
    `rb.check_consumption()` at all — only `check_repo_adoption()` (which
    only ever inspects the *adoption* marker) was wired into `run_checks()`,
    so a consume-mode repo's outdated-consumer-version drift was invisible
    to `doctor` itself, only reachable via the separate `repo_bootstrap.py
    check` CLI. A repo that has never run `consume` (no
    `.horonom-consumption.yaml`) is NOT_APPLICABLE, matching
    check_repo_adoption's own "not every repo has done this" semantics."""
    if not (repo / rb.CONSUMPTION_MARKER_FILENAME).is_file():
        return CheckResult(
            "consumption_status",
            NOT_APPLICABLE,
            "not consumed (no .horonom-consumption.yaml) — this is fine, not every repo consumes shared capabilities",
            fix="run scripts/repo_bootstrap.py consume <this repo> if it should track shared skills",
        )
    try:
        results = rb.check_consumption(repo)
    except Exception as exc:  # noqa: BLE001 — surface as a doctor FAIL, never crash the whole run
        return CheckResult("consumption_status", FAIL, f"repo_bootstrap check_consumption() raised: {exc}")
    worst = PASS
    details = []
    for name, status, detail in results:
        details.append(f"{name}={status}")
        if status == "FAIL":
            worst = FAIL
        elif status == "WARN" and worst != FAIL:
            worst = WARN
    return CheckResult(
        "consumption_status",
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
