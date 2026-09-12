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

This is **product-design/runtime governance, not [[HORO-967]] engineering-
execution governance** — it constrains what a *shipped product* does to a
host at install/runtime, not how engineers/agents write and ship code. The
two are related but neither is a child of the other; owing one never
substitutes for the other.

## The rule, in one line

> **A Horonom product MUST preserve all host-tool configuration it does not
> explicitly own. Unknown/unowned state = preserve or abort.**

## What this means for a product session

- Classify every host-config surface you read or write into one of:
  `host/user-owned`, `org/MDM-owned`, `tool-generated`,
  `other-product/plugin-owned`, `product-owned whole artifact`,
  `product-owned key/list-entry/record`, `unknown/unattributable`, `legacy
  ownership unknown`, `concurrently modified after planning`. Writing into
  a shared file establishes ownership of what you wrote, never of the
  whole file.
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
  the last backup."
- A confirmed destructive mutation of unowned host configuration is a
  **release-blocking product defect** — not a severity to negotiate down
  because a backup exists or reinstall is possible.
- This rule sets a floor. A product-local rule may narrow it further (e.g.
  refuse to touch a surface this rule would technically permit) but may
  **never weaken it**.

## What this does not require

- No mandatory single cross-product config-mutation library exists or is
  assumed. [[HORO-998]] evaluates that only after real product audits —
  don't build one preemptively for your product alone either.
- No claim that every host tool shares Claude Code's config shape. Treat
  Claude Code as one worked example, not the template every tool must fit.

## Certification and coordination

Company-common ([[HORO-996]]) owns: the constitution (this file + ADR-0009),
the reusable ownership-aware test contract ([[HORO-998]]), the cross-product
inventory and certification matrix ([[HORO-999]]), the UX/disclosure
contract ([[HORO-1000]]), and final independent certification ([[HORO-1003]]).
**Your product session owns**: auditing your own actual mutation surfaces,
creating/reusing your own product-local Jira ticket for any gap found,
implementing the fix, adding permanent regression coverage, and producing
your product's final PASS/BLOCKED/NOT_APPLICABLE evidence
([[HORO-1001]]). Do not wait for company-common to fix your product's code,
and do not route your product's fix through the company-common ticket —
link it instead.

`AAASM-6091` (AI Agent Assembly project, external org) is the motivating
incident and its own product's concrete remediation — it stays owned by
that project; this rule consumes its lessons without duplicating or
closing it.

## Precedence

Company constitution (ADR-0009) → a product's own deliberate, recorded,
*stricter* exception → implementation convenience. Convenience never
justifies a destructive mutation of unowned configuration.
