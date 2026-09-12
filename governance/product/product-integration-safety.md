# Product Integration Safety — non-waivable invariant

Detailed rule text for `governance/README.md`'s Company layer. Full context,
ownership-class table, mutation-order rationale, lifecycle proof, and worked
examples: [ADR-0009](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0009-product-integration-safety-non-destructive-host-configuration.md)
in `horonomy/internal-docs` (HORO-996/HORO-997). This file is the concise
pointer every product session reads by default — it does not fork a second
copy of that ADR's content; read the ADR before implementing or auditing an
integration lifecycle.

## Scope — is this rule relevant to what you're building?

This applies to any Horonom product/integration that **reads or mutates
configuration belonging to a third-party or native developer tool** — a
coding agent (Claude Code, Codex, GitHub Copilot, Windsurf), an IDE
(VS Code, JetBrains), git/GitHub CLI config, MCP client/server config,
shell startup files, SSH config, Docker/container tooling, Kubernetes/cloud
CLI config, or CI/CD config a product installs or reconciles on a user's
behalf. If your product never touches another tool's configuration, this
rule is `NOT_APPLICABLE` to you — record that truthfully rather than
skipping the classification (HORO-999).

This is **product-design/runtime governance, not HORO-967 engineering-
execution governance** — it constrains what a *shipped product* does to a
host at install/runtime, not how engineers/agents write and ship code. The
two are related but neither is a child of the other; owing one never
substitutes for the other.

## The rule, in one line

> **A Horonom product MUST preserve all host-tool configuration it does not
> explicitly own. Unknown/unowned state = preserve or abort.**

## What this means for a product session

- Classify every host-config surface you read or write into one of the
  ADR's exact ownership classes: `Host/user-owned`, `Organization/MDM-owned`,
  `Tool-generated`, `Other-product/plugin-owned`, `Horonom-product-owned
  whole artifact`, `Horonom-product-owned key/list-entry/record`,
  `Unknown/unattributable`, `Legacy ownership unknown`, `Concurrently
  modified after planning`. Writing into a shared file establishes
  ownership of what you wrote, never of the whole file.
- Prefer, in order: a native dedicated drop-in surface → a product-owned
  artifact the host merely references → a key/list-entry patch inside a
  shared file with explicit ownership markers → whole-file replacement
  only with *proven* whole-file ownership.
- Uninstall/rollback is delta-based, never stale-snapshot-based: given
  `A → install → A+B → user edits → A+B+C → remove`, the result must be
  `A+C`. Only your own `B` may disappear. A backup may exist for disaster
  recovery; it must never be the default removal mechanism.
- Parse failure, unrecognized schema, unknown ownership, or a concurrent
  edit detected between planning and applying a mutation → **abort with
  zero mutation**. Never fall back to "write a fresh default" or "restore
  the last backup." The one narrow exception (see ADR-0009): a planned
  unowned delete/replace is permitted when the user explicitly authorized
  *that exact destructive change* as the task itself (e.g. "wipe my Claude
  Code settings") — never inferred from a general install/repair request
  ("install Circinus" is not implicit authorization to delete anything).
- A confirmed destructive mutation of unowned host configuration is a
  **release-blocking product defect** — not a severity to negotiate down
  because a backup exists or reinstall is possible.
- This rule sets a floor. A product-local rule may narrow it further (e.g.
  refuse to touch a surface this rule would technically permit) but may
  **never weaken it**.

## What this does not require

- No mandatory single cross-product config-mutation library exists or is
  assumed — see [`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)'s
  shared-helper decision. Don't build one preemptively for your product
  alone either.
- No claim that every host tool shares Claude Code's config shape. Treat
  Claude Code as one worked example, not the template every tool must fit.

## Verifying your own integration

Apply the reusable, tool-agnostic property list in
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)
(HORO-998) — 14 named properties, rich-fixture requirements, and negative
controls that prove your test suite would actually catch a regression, not
just pass once.

## Disclosing your own integration to its users

Apply the common UX/disclosure semantics in
[`host-config-safety-ux-disclosure.md`](./host-config-safety-ux-disclosure.md)
(HORO-1000) — what a plan/dry-run must distinguish, automation consent vs.
OS-level authorization, and the documentation checklist every mutation-
capable integration's docs should satisfy.

## Certification and coordination

Company-common (HORO-996) owns: the constitution (this file + ADR-0009),
the reusable ownership-aware test contract (HORO-998), the cross-product
inventory and certification matrix (HORO-999), the UX/disclosure
contract (HORO-1000), and final independent certification (HORO-1003).
**Your product session owns**: auditing your own actual mutation surfaces,
creating/reusing your own product-local Jira ticket for any gap found,
implementing the fix, adding permanent regression coverage, and producing
your product's final PASS/BLOCKED/NOT_APPLICABLE evidence
(HORO-1001). Do not wait for company-common to fix your product's code,
and do not route your product's fix through the company-common ticket —
link it instead.

`AAASM-6091` (AI Agent Assembly project, external org) is the motivating
incident and its own product's concrete remediation — it stays owned by
that project; this rule consumes its lessons without duplicating or
closing it.

## Precedence

ADR-0009 is a floor, not a target — see its own Consequences section. A
product-local rule may narrow it (refuse a surface this rule would
technically permit); it may never weaken it. This follows the same
Company → Product → Repository precedence model as every other
non-waivable invariant in `governance/README.md` (full rationale in
ADR-0005). Convenience never justifies a destructive mutation of unowned
configuration.
