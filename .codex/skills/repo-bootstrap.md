<!-- horonom:generated -->
<!-- Source: horonomy/.github agents/skills/repo-bootstrap/SKILL.md. Provisional Codex projection shape — see agents/common/README.md. Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->

# SKILL.md — repo-bootstrap

## Purpose

Adopt Horonom company governance into a repo that doesn't have it yet, or
onboard a brand-new Horonom repo, without inventing a one-off convention
per repo.

## Type

Auto-used. Invoke when a repo has no `.claude/CLAUDE.md`/root `CLAUDE.md`
pointing at the org baseline, or when creating a new Horonom repository.

## When to use

- A new Horonom repository, per `NEW_REPO_CHECKLIST.md`.
- An existing Horonom repo (e.g. HORO-511/513/517's product adoption)
  that predates this governance model.

## When NOT to use

- A repo outside the `horonomy` GitHub org with its own independent
  governance baseline (e.g. `ai-agent-assembly/*`) — respect its own
  baseline instead of imposing this one.
- A repo whose existing rules are **stricter** than the company floor —
  adopt this skill's procedure but keep the stricter repo rule; never
  loosen it to match the company default.

## Procedure

1. Confirm the repo's canonical remote and default branch (never assume
   `origin`/`main`).
2. Add (or update) the repo's `.claude/CLAUDE.md` (or root `CLAUDE.md`) per
   `NEW_REPO_CHECKLIST.md`'s structure: pointer to the org baseline,
   Company → Product → Repository precedence statement, then only what's
   actually specific to this repo.
3. Disable squash-merge and rebase-merge in the repo's GitHub settings
   (**Settings → General → Pull Requests**) — merge-commit-only is a
   company-wide invariant (`governance/engineering/git-pr-merge.md`), not
   a per-repo choice.
4. Add `.github/pull_request_template.md` if missing, matching the shape
   in `horonomy/.github`.
5. Once HORO-507/HORO-508 land the Claude/Codex adapters, run their
   projection to populate `.claude/`/`.codex/` from canonical content —
   don't hand-author either directory's content.
6. Record adoption in `governance/workspace/manifest.yaml` if the repo
   belongs in the local dev workspace (HORO-506).

## When adopting an existing repo with in-flight work

Never force-migrate or interrupt another active session's worktree to
apply this skill. Inventory active worktrees/sessions first
(`governance/workspace/autonomous-execution.md`); defer to when the repo
is idle, or apply the change in a way that doesn't touch in-flight files.

## References

- `NEW_REPO_CHECKLIST.md` — full new/existing-repo checklist this skill
  operationalizes.
- `governance/engineering/git-pr-merge.md` — merge-strategy invariant.
- `governance/workspace/autonomous-execution.md` — worktree/session safety.
