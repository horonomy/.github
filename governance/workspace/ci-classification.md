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

**Verified GitHub Actions / SonarQube (or any other CI-adjacent gate's)
quota or billing exhaustion on a private Horonom repository is an
accepted instance of this classification** (HORO-983/HORO-993). Confirm
it's genuinely external before relying on it — check that the failure
message names a billing/quota/account condition, not a real job that ran
and failed, and cross-check against a repo whose CI ran successfully
around the same time to rule out a per-repo config problem. Once
confirmed: **do not keep waiting or polling a venue known to be
exhausted** — repeated re-checks don't change an account-level condition
and only waste cycles. Move immediately to local-equivalent validation
below.

When CI is genuinely `CI_UNAVAILABLE_EXTERNAL`:

1. Inspect the blocked workflow definitions — know exactly what gate(s)
   would have run.
2. Run the repository's own real canonical gates locally — its actual
   test, lint, type-check, security, build, doc, and render commands, not
   a narrower hand-check. **A hand-verified fix is not the same evidence
   as the tool's own output**: `horonomy/.github#49` (HORO-983) hand-wrapped
   a `ruff check` line-length violation in `repo-scaffold`'s canonical
   scripts and it passed hand-verification, but after re-adopting the
   skill into a real external consumer (`horonomy/GearMeshing-AI`), that
   repo's own `verify` CI job's `ruff format --check` step still failed,
   because the hand-wrap didn't match canonical formatter output — fixed
   properly in `horonomy/.github#51` by running the real formatter. Only
   the actual gate command catches that; run it.
3. Record the exact commands and their measured results (exit codes, test
   counts, tool output) as evidence attached to the PR/ticket.
4. Require the same quality/security thresholds the unavailable CI would
   have enforced — local-equivalent is a substitute venue, not a lower
   bar.
5. Run an independent risk-based review, and use any independent checks
   that remain reachable (e.g. SonarCloud directly, Codecov) even when
   GitHub's own Actions runs cannot execute.
6. **Explicitly record remote CI as unavailable** — state the venue
   didn't run, don't say or imply it "passed."

Merge is authorized only when: local-equivalent evidence from steps 2-5
passes; independent review passes; the PR is mergeable (no conflicts);
and no unresolved correctness/security finding exists — merged via the
standard merge-commit strategy, never as a reason to squash or rebase.
Never classify a real product or test failure as externally unavailable
to route around it — see `CI_FAILED` above.

### What this substitution does not waive

`CI_UNAVAILABLE_EXTERNAL` replaces the unavailable execution **venue**
only. It never waives:

- testing itself (relative + full-suite, run locally instead);
- security validation (the repo's real security gate, run locally);
- required CODEOWNER approval;
- required repository-owner review;
- protected-branch governance (required checks, required reviewers, merge
  restrictions);
- any consequential human decision this policy elsewhere reserves for an
  owner or engineer.

A repo with a CODEOWNERS file or an owner-gated policy stays gated exactly
as before — local-equivalent evidence makes the PR *ready* for that human,
it does not substitute for their review. Only a PR that would otherwise be
free to merge (no code-owner/owner gate, independent review already
clean) becomes mergeable once this classification and its evidence
requirements are satisfied.

### Don't let venue unavailability stall unrelated real-evidence work

A CI outage scoped to remote pipeline execution does not block work whose
evidence standard has a genuine local equivalent — a real product CLI/SDK
journey against a real checkout, a real docs build/render/link check, a
real rendered UI surface (e.g. via local browser automation). Don't
blanket-defer that work "because CI is down" — identify which specific
work genuinely has no local equivalent (state which, and why) and proceed
with the rest.

## Trust green CI

Don't rerun a genuinely green CI run without a specific reason to doubt it.
