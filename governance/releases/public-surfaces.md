# Release and public-surface invariants

Detailed rule text for `governance/README.md`'s Company layer. HORO-512
lands the full Public Release Surface Contract (state machine, per-surface
reconciliation procedure) as an addition to this directory; this file
carries the invariants that already apply company-wide today.

## Never publish to make a gate green

Never publish, promote, or represent a product as more available, mature,
or public than it genuinely is, in order to satisfy a checklist or gate.
An unreleased or internal product stays represented as such everywhere —
company catalog, org profile, website — until its own release-surface
reconciliation independently verifies otherwise. Governance/workspace
adoption work on a product repo (bootstrapping `.claude`/`.codex`, adding
shared skills) is never itself evidence of release readiness.

## Company catalog owns catalog-level facts only

`metadata/company.yaml` may state a product's name, website, GitHub org,
and catalog-level lifecycle label. It must never restate or redefine a
product's capability claims — those are owned by the product's own repo.
See `metadata/README.md` for the full ownership boundary and
[ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md)
decision #7 for how this interacts with the Public Release Surface
Contract once HORO-512 lands it.

## Visible UI changes need real evidence

A PR that changes anything rendered on a public surface (`horonom.com`, an
org profile, a hosted docs site) attaches real screenshot evidence of the
changed view — captured from the actual running app/site, not asserted in
prose. Verify only the flows that actually changed; broad visual re-testing
is not required when nothing visual changed.

## CI classification

See [`../workspace/ci-classification.md`](../workspace/ci-classification.md)
for how to tell a genuine engineering failure (`CI_FAILED`) apart from CI
that could not run for an external reason
(`CI_UNAVAILABLE_EXTERNAL`) — the distinction that governs whether a
release or merge may proceed.
