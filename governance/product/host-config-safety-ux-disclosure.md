# Safe install/repair/remove UX and disclosure contract (HORO-1000)

Common **semantics** every mutation-capable Horonom integration should
expose to its user, in whatever CLI/UI vocabulary is native to that
product. This document does not mandate one CLI flag spelling or one UI
shape — it mandates that the *information* below is discoverable before and
after a mutation happens. Read
[`product-integration-safety.md`](./product-integration-safety.md) and
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)
first; this document is the user-facing half of the same invariant.

## Why this exists as semantics, not syntax

A CLI's flag names are product-native and should stay that way — `circinus
install claude-code --print` and a Terraform-style `plan`/`apply` split
solve the same problem in each product's own idiom. What must be common is
that a user (or another engineer reading the product's docs) can answer six
questions before a mutation happens, and three more after it:

**Before mutating:**
1. What exact surface(s) will this touch?
2. What scope is that — project, user, machine, or org-managed?
3. What will be added/changed, in concrete terms (not "hooks will be
   configured" — the actual keys/entries)?
4. What will this explicitly leave alone?
5. Does this need OS-level privilege/admin authorization, separate from
   the product's own consent?
6. If this can't safely proceed, why not — which specific conflict or
   unknown blocked it?

**After mutating:**
7. Did a read-back verification confirm the change landed as planned?
8. What would removing this undo, specifically?
9. If a prior/legacy version might have already caused damage, how does
   the user find out and recover?

## Required UX contract

For any integration where install/repair/upgrade/remove touches host-tool
configuration, provide the closest native equivalent of:

- **A plan/dry-run before mutation.** The user (or their automation) sees
  the real diff before it's applied, not just a "would you like to
  proceed?" toggle.
- **Explicit scope labeling** — project/user/machine/org-managed — on
  every planned change, not just once in a README.
- **Owned-surface disclosure** — which specific keys/records/files this
  operation now owns, so a later "what does this product touch?" question
  has a concrete answer.
- **Material-change summary** — the actual keys/values changing, not a
  vague "configuration updated" message.
- **Privilege/authorization disclosure** — whether OS-level admin/sudo is
  required, stated separately from the product's own `--yes`/confirmation
  flag (see [Automation consent vs. OS authorization](#automation-consent-vs-os-authorization)).
- **Conflict/refusal reasons that name the actual conflict** — "refusing:
  `hooks.PreToolUse[2]` was modified outside Circinus since the last
  install" is useful; "operation failed" is not.
- **Rollback/remove semantics stated up front** — what a later `remove`
  will and won't touch, disclosed at install time, not discovered at
  removal time.
- **Read-back verification result** — did the applied state match the
  plan, checked after the write, not just assumed from a successful exit
  code.
- **A recovery path after an interrupted or legacy install** — what to do
  if a previous run didn't finish, or was installed by a version that
  predates this contract.

## Disclosure requirements — the categories a plan output must distinguish

A plan/dry-run output (in whatever format is native — JSON, a rendered
diff, a table) must let a reader tell these apart, because they carry
different risk:

| Category | What it means |
|---|---|
| Product-owned ADD | A new key/entry/file this operation is about to create |
| Product-owned UPDATE | An existing product-owned key/entry this operation is about to change |
| Product-owned REMOVE | A product-owned key/entry this operation is about to delete |
| Host/user state preserved | Explicitly listed (or explicitly summarized as "N unrelated keys, unchanged") as untouched |
| Other-product state preserved | Same, but specifically calling out another product's marked entries survive |
| Organization/MDM-managed state untouched | Never silently touched; if the plan would need to touch it, that's a refusal, not a disclosed change |
| Unknown state blocking mutation | Named specifically — which field, which file, why it couldn't be classified |
| Whole-artifact vs. shared-artifact ownership | Whether this operation owns the entire file (Glomeris's launchd plist shape) or only entries within a shared one (Circinus's hooks-array shape) — the user's mental model of "what does uninstall do" differs materially between the two |

## Automation consent vs. OS authorization

These are two different gates and must not be collapsed into one flag:

- **Automation consent** (`--yes`, `-y`, `CI=true`, etc.) — the user (or
  their script) is telling *this product* "don't stop and ask me to
  confirm." This may skip the product's own interactive confirmation
  prompt.
- **OS privilege/security authorization** — sudo, a macOS admin prompt, an
  MDM-gated action — is the *operating system's* boundary, not the
  product's. An automation flag **must never** silently satisfy or bypass
  this; if the operation needs elevated privilege, it still needs it with
  `--yes` passed, and the product must not suppress the disclosure of
  *why* just because confirmation was skipped.

Put differently: `--yes` may mean "don't ask me to confirm the plan," but
it must never mean "don't tell me what happened" or "escalate privilege
silently."

## Conflict semantics — required behavior

| Situation | Required behavior |
|---|---|
| A product-owned key was changed externally since install | No-clobber conflict, unless the specific field has a documented, safe, deterministic reconciliation rule — never silently pick a side |
| Config changed on disk since the plan was formed | Re-read and re-plan, or abort — never apply a stale plan to new state |
| Config is malformed or in an unsupported shape | No mutation at all — this is `MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION` from the test contract, surfaced to the user as a real refusal, not a silent skip |
| A legacy receipt lacks enough ownership metadata to act safely | Offer a guided manual reconciliation/recovery path; never treat "I found *something* that looks like our old receipt" as license for an automatic destructive repair/remove |
| Remove is run when there's no product-owned state left to remove | Idempotent no-op *where safe*, reported as such — not an error, and not a fallback into "well, let me clean up whatever I can find" |

## Documentation checklist for product docs

Every product doc for a mutation-capable integration should state, in
plain language a non-engineer user could act on:

- [ ] What host config is touched, and its exact scope.
- [ ] Whether a backup is created, and what it's *for* (disaster recovery,
      not "proof uninstall is safe" — see ADR-0009's rejected alternative
      on this exact point).
- [ ] What uninstall removes.
- [ ] What uninstall explicitly preserves.
- [ ] How a conflict is reported, and what the user does next.
- [ ] How to tell whether a prior/older version of this product may have
      already mutated configuration in a way this contract wouldn't allow
      today, and how to recover if so.

Use your docs platform's Note/Warning/Security callout conventions for the
backup/recovery/conflict sections — these are exactly the sections a user
skims past at their own risk.

## What this does not require

- **Not** a single CLI syntax across products. `circinus install`,
  a Terraform-style `plan`/`apply`, and a GUI installer's wizard steps can
  all satisfy this contract in their own idiom.
- **Not** a new confirmation prompt where an automation flag already
  exists — the requirement is that the flag doesn't also suppress
  disclosure or bypass an OS-level boundary, not that a prompt must always
  appear interactively.

## Status

No product integration in this campaign has yet implemented this contract
end-to-end with real evidence — HORO-999's inventory recorded *mutation*
maturity, not UX/disclosure maturity, and those are genuinely different
questions. Circinus already has real building blocks toward this (a
`circinus install claude-code --print` plan-preview flag, an explicit
`--scope project|user`, and a named `ConcurrentModificationError` for
conflicts) — it doesn't yet expose the full categorized disclosure this
document requires (owned-vs-preserved-vs-unknown broken out explicitly,
surfaced read-back verification, a stated recovery path for legacy
installs), which is the gap, not an absence of any starting point. Real
demonstration against at least two integrations, or a truthful blocker per
integration, is required before this ticket's own AC is satisfied — see
HORO-1001 for the per-product remediation/certification tracking that
carries this forward.
