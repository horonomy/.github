# CI failure classification

Detailed rule text for `governance/README.md`'s Company layer, applied to
every Horonom repo's CI.

Before treating red or missing CI as a merge blocker, classify it as
exactly one of two states — they require different responses.

## CI_FAILED

A required check **actually executed** and reported a genuine failure
caused by code, tests, lint, build, security, or another engineering
defect. **Must not be bypassed.** Reproduce locally, find the root cause,
fix it, verify, then re-push.

## CI_UNAVAILABLE_EXTERNAL

The required CI **could not execute** for a verified external
infrastructure reason unrelated to the change (CI provider billing/quota
exhaustion, an account-wide suspension, a known provider outage). This is
not equivalent to a failing check — nothing ran, so nothing reported a
defect.

When CI is genuinely `CI_UNAVAILABLE_EXTERNAL`: inspect the blocked
workflow definitions, reproduce every meaningful gate locally, record
commands/exit codes/test counts as evidence, run an independent
risk-based review, and use any independent checks that remain available.
Merge is authorized only when local-equivalent CI passes, independent
review passes, the PR is mergeable, and no unresolved correctness/security
finding exists — and only via the standard merge-commit strategy, never as
a reason to squash or rebase. Never classify a real product or test failure
as externally unavailable to route around it.

## Trust green CI

Don't rerun a genuinely green CI run without a specific reason to doubt it.
