# Git, PR, and merge invariants

Detailed rule text for `governance/README.md`'s Company layer. Branch
naming, commit format, and PR title/body mechanics are unchanged from
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) — this file adds the invariants
that make merges themselves safe and auditable.

## Merge strategy — non-waivable

**Every merge into a Horonom-owned repository's default branch uses "Create
a merge commit."** Squash-merge and rebase-merge are prohibited, company-
wide, with no per-repo or per-reviewer exception ([ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md),
decision #0). This supersedes any earlier "reviewer/assignee picks the
strategy" language in a repo's own docs.

**Enforcement** (per `governance/README.md`'s "mechanically enforced, not
just declared" rule): disable squash-merge and rebase-merge in each repo's
GitHub settings (**Settings → General → Pull Requests**) wherever repo
admin access allows it. Until a repo's settings are updated, the rule is
still binding on every contributor and reviewer — the settings change
closes the last gap, it isn't what makes the rule apply.

## Admin-merge bypass — narrowly scoped

Repository-admin merge (bypassing the human-approval requirement) is
authorized **only** to resolve a solo-maintainer review deadlock — where
every other merge condition already holds (CI green, no conflicts, no
unresolved `Request Changes`, no unresolved correctness/security finding,
diff already self-reviewed against the ticket's acceptance criteria) and no
second human reviewer is available. It must **never** be used to bypass
failing or pending CI, merge conflicts, an unresolved correctness or
security finding, or an unresolved owner decision.

## Never merge directly to the default branch

Every change goes through a PR, even a one-line doc fix. This is required
so the PR review checklist below always runs, and so CI has a chance to run
before the change lands.

## Pre-merge review checklist

Before merging (as the reviewer, or as an orchestrating agent reviewing a
sub-agent's proposed change — a sub-agent implementing a change must never
merge its own PR):

1. Read the actual diff — not just the PR description.
2. Compare the diff against the ticket's acceptance criteria; note anything
   the AC asked for that the diff doesn't do.
3. Check correctness, design, and security.
4. Check whether the diff introduces or updates tests where the change
   warrants them.
5. Check that CI is genuinely green (see `governance/workspace/ci-classification.md`
   for what counts as green vs. externally unavailable).
6. For a frontend/UI change, verify the changed flow runs and looks right
   (see `governance/releases/public-surfaces.md`'s screenshot-evidence
   requirement for anything visible on a public surface).
7. Fix every genuine finding before merging; re-review after a fix lands.

## Worktrees

Develop each ticket in its own git worktree off the verified default branch
of the verified canonical remote — never assume `origin` points at
`horonomy/<repo>` (check `git remote -v`); never assume the default branch
is `main` without confirming (`git ls-remote --symref <remote> HEAD`).
Remove the worktree (and its branch, once merged) after merge.

**Before any destructive worktree operation** (`rm -rf` on a worktree
directory, `git worktree remove`, `git worktree prune`, or any cleanup
sweep) — not only "for tidiness," but during verification, disk-space
cleanup, or any other automated pass — check `git worktree list` and
confirm the worktree's branch name matches the ticket you are actively
assigned. A worktree whose branch doesn't match your own ticket is
unexpected repository state: leave it, report it, never delete it,
regardless of how stale or unrelated it looks. This applies with extra
force to a subagent dispatched for "cleanup," whose scope must be
explicitly bounded to its own dispatching ticket, never "whatever is
lying around." A real incident (HORO-1871, `horonomy/horologium`)
involved a dispatched subagent broadly `rm -rf`-ing a worktree directory
and deleting an unrelated, unmerged ticket's working copy this way.

## Commit granularity

Keep commits **very small** — one logical change per commit, described
completely by its subject line, so a reviewer can trace what happened
without reading the full diff. Never bundle a new file with an unrelated
cleanup, or two independent fixes, into one commit.
