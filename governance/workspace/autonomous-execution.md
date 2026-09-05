# Autonomous execution invariants

Detailed rule text for `governance/README.md`'s Company layer, for an agent
operating in the local `$HORONOM_WORKSPACE_ROOT` workspace (HORO-506) or a
product repo adopted into it.

## Reconcile before acting

Before making a change: read the relevant ticket(s), inspect the current
state of the repo/file the change targets, and inventory any active
worktrees or sessions that could conflict — never broadly scan unrelated
projects once the required state is known.

## Continue through a multi-step campaign

Do not stop after one PR, one ticket, or one wave of a multi-ticket
campaign merely because that unit finished. Continue autonomously through
routine implementation, review, CI triage, Jira updates, PR/merge, and
cleanup.

## Stop only for a genuinely material decision

Stop and ask only when a **new** owner/product decision, a security or
trust-boundary decision, a public-contract decision, an irreversible public
exposure, or a meaningful paid-infrastructure commitment is on the table
*and* multiple reasonable choices genuinely remain. If one option is
clearly safer, simpler, and more reversible than the alternatives, choose
it and proceed — don't manufacture a checkpoint out of routine
implementation work.

## Workspace-root portability

Never hard-code a personal home directory or absolute path outside
`$HORONOM_WORKSPACE_ROOT` into a committed file, script, or generated
projection. See `governance/README.md`'s ownership matrix and
[ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md)
decision #3 for the full standalone-clone boundary this protects.

## Never move or delete another session's worktree

Inventory active worktrees and sessions first. Adopting a repo into the
target workspace layout defers physical relocation of a worktree that
belongs to another active session until it's idle — never force-migrate,
move, or delete it merely to satisfy layout.
