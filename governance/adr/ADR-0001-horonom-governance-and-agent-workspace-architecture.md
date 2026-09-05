# ADR-0001: Horonom governance & agent-workspace architecture

- **Status**: Accepted
- **Date**: 2026-09-05
- **Ticket**: HORO-504 (parent epic HORO-503)
- **Supersedes**: none. This is the first company-wide ADR; product repos
  (e.g. `ai-agent-assembly`, `official-website`) keep their own independent
  ADR series for product-scoped decisions — this series governs only
  cross-repo company/governance concerns.

## Context

HORO-503 asks for a canonical governance source of truth, a portable local
agent workspace, and Claude/Codex adapters, shared across every Horonom
product repo. Before any of its 13 sibling tickets can implement anything,
this ADR fixes the architecture they must build against, so they don't each
invent an incompatible mechanism.

Two concrete, already-real discrepancies motivated several of the decisions
below rather than abstract design:

1. **Product catalog drift.** `metadata/company.yaml` in this repo lists only
   `ai-agent-assembly`, `archeweave`, `harbinger` as products. The actual
   live/shipped Horonom product set — per `profile/README.md`, per Jira, per
   `official-website`'s own `productRegistry.ts` — is AI Agent Assembly
   (Beta), Octans, Circinus, Ophiuchus (all Experimental), plus Horologium
   and Eridanus tracked under this campaign (HORO-513, HORO-517). `archeweave`
   and `harbinger` do not correspond to any known current product name and
   are presumed superseded/renamed entries, not evidence the catalog is
   otherwise current. This is a real instance of decision #7 below, not a
   hypothetical.
2. **Merge-strategy contradiction.** `CONTRIBUTING.md` currently states the
   "reviewer or assignee picks... typically squash or rebase," while
   `NEW_REPO_CHECKLIST.md` documents that `circinus`/`ophiuchus` already
   disagree with each other and declines to name a winner. HORO-503 mandates
   merge-commit-only. This ADR settles it company-wide (decision #0 below);
   HORO-505 carries out the doc edit.

## Decisions

### 0. Merge strategy is now a company-wide non-waivable invariant

**Decision**: All merges in every Horonom-owned repository use **"Create a
merge commit."** Squash and rebase-merge are prohibited, effective
immediately, superseding `CONTRIBUTING.md`'s current "reviewer/assignee
picks" language and the per-repo disagreement `NEW_REPO_CHECKLIST.md`
documents. GitHub squash-merge and rebase-merge must be **disabled at the
repository settings level** wherever repo admin access allows it (tracked
per-repo under HORO-511/HORO-513/HORO-517 adoption, not required to land
atomically with this ADR).

**Why**: The campaign's own PR/merge rules make this non-negotiable at the
campaign level already; leaving the org-wide doc contradictory would mean
HORO-503's own commits are governed by a rule its source-of-truth repo
still disclaims. A single rule beats "picked per repo" for auditability of
squashed/rebased history loss, and is trivially reversible (a GitHub repo
setting) if ever revisited.

**Rejected alternative**: Leave merge strategy per-repo/per-reviewer as
today. Rejected — it is what produced the contradiction this decision
closes, and it conflicts with HORO-503's own binding execution rule.

### 1. Canonical ownership and precedence

**Decision**: `horonomy/.github` is the single canonical source for
company-wide governance. Precedence is strictly **Company → Product →
Repository**: a narrower layer (product, then repository) may **add**
constraints or **strengthen** an invariant, but may never weaken or silently
override a company-level non-waivable invariant (merge-commit-only above;
secret-handling; the ADR-vs-doc boundary in decision #10; the public-release
truthfulness rules in decision #7). Untrusted repo content (a cloned
third-party repo's own instructions, or a malicious PR body) can never
elevate itself to company-invariant authority regardless of what it claims
— see the security threat model note under decision #2.

**Ownership matrix**:

| Concern | Owner | Not owned there |
|---|---|---|
| Company-wide engineering policy, non-waivable invariants | `horonomy/.github` `CLAUDE.md` + this ADR series | Product business/security semantics |
| Product capability truth, maturity, security semantics | The product's own repo | Company-catalog-level facts |
| Company catalog (name, website, product list at catalog level) | `horonomy/.github` `metadata/company.yaml` | Product capability detail (see decision #7) |
| Local agent workspace layout, bootstrap, doctor | `$HORONOM_WORKSPACE_ROOT` repo/tooling (HORO-506/510) | Product build/test, which stays standalone-clone-usable |
| Claude adapter | `.claude/` (HORO-507) | Policy content itself — the adapter points at/consumes canonical content, never forks a copy |
| Codex adapter | `.codex/` (HORO-508) | Same as above |
| Shared skills | `governance/skills/` in `.github`, consumed by both adapters (HORO-509) | Product-specific skill logic, which stays in the product repo |

### 2. Non-waivable invariants are mechanically enforced, not just declared

**Decision**: A company-level non-waivable invariant is not satisfied by
being *stated* in a doc a narrower context could shadow. It must be
enforceable by at least one of: (a) a CI gate in the repo itself (e.g. the
existing `company-metadata-drift.yml` pattern), (b) a check `horonom doctor`
(HORO-510) runs and reports FAIL on, or (c) a GitHub repository setting that
cannot be overridden by repo-local config (e.g. disabling squash-merge at
the repo settings level, branch protection). A markdown statement alone is
advisory, not an invariant. Any decision below or in a later ticket that
claims "non-waivable" status must name which of (a)/(b)/(c) enforces it.

**Threat model note**: an untrusted cloned repo's own `CLAUDE.md`/`AGENTS.md`
attempting to instruct an agent to ignore or "supersede" a company invariant
must be treated as a security-relevant prompt-injection attempt, not a valid
narrower-context override — the precedence rule in decision #1 is about
legitimate strengthening, not obedience to arbitrary repo content.

### 3. Workspace-root contract and standalone-clone boundary

**Decision**: `$HORONOM_WORKSPACE_ROOT` is a purely **additive, optional**
local convenience layer. No product repo's build, test, lint, or CI may read
it, depend on its presence, or fail without it. A product repo cloned in
complete isolation (no sibling `.github`, no workspace root set) must build
and test exactly as it does today. Workspace-root files that live *inside* a
product repo (e.g. a generated `.claude/` pointer) are committed,
version-controlled, and must degrade gracefully (documented fallback
behavior, not a crash) when the workspace root is absent.

**Why**: Directly required by HORO-506 AC4 and the campaign's explicit "CI
and OSS contributors must not depend on the Horonom parent directory" rule.

### 4. Generated projections over symlinks, except inside the workspace root itself

**Decision**: Cross-repo content sharing (a product repo consuming canonical
skill/policy content from `.github`) uses **generated file copies with a
provenance header** (source repo, source commit/tag, regenerate command,
"generated — do not hand-edit"), following the existing pattern already
proven in this repo (`metadata/generated/company.json`, the
`<!-- BEGIN GENERATED -->` regions in `SECURITY.md`/`profile/README.md`).
Symlinks are permitted **only** for links that stay entirely inside
`$HORONOM_WORKSPACE_ROOT` on the local machine (e.g. `products/circinus` →
an existing clone elsewhere on disk), never checked into a product repo,
since a checked-in symlink is not portable across macOS/Linux/Windows/Git
checkout configurations and directly risks the symlink-escape threat this
campaign's security section names.

**Rejected alternative**: git submodules for shared content. Rejected —
heavier operational cost (submodule pin/update workflow) for no benefit over
a generated-projection-plus-drift-check, which this repo has already proven
out for `company.json`.

### 5. Claude/Codex adapters never become a second source of truth

**Decision**: `.claude/` and `.codex/` in every adopting repo contain only:
(a) a thin pointer/entrypoint (`CLAUDE.md` navigation file, `AGENTS.md`
compatibility adapter per the campaign's own naming), and (b) generated
projections of shared skill content per decision #4. Neither adapter
directory may contain a hand-maintained copy of policy text that could
diverge from the canonical `.github` source — if content must be duplicated
for a tool's technical requirement (e.g. Codex's config format differs from
Claude's), the duplication is itself generated from the same canonical
source, never independently hand-edited in two places.

### 6. Shared-skill ownership and adapter boundary

**Decision**: The five common skills (governance-doctor, repo-bootstrap,
jira-delivery, release-assurance, public-release-reconcile) live once, as
canonical content, under `governance/skills/<skill-name>/` in
`horonomy/.github` — each a `SKILL.md` plus small reference files plus a
deterministic script/test where the skill has one. Both the Claude adapter
and the Codex adapter consume this same canonical content via the
generated-projection mechanism (decision #4); neither adapter hand-copies an
implementation. Product-specific business/security semantics (e.g. what
"release-assurance" checks for Circinus specifically) stay in the product
repo and are referenced by the shared skill, not inlined into it.

### 7. Public Release Surface Contract ownership vs. company catalog

**Decision**: The Public Release Surface Contract (HORO-512) is the sole
authority for **whether and how a product is publicly represented** (its
7-state classification: VERIFIED/REQUIRED/DEFERRED/NOT_APPLICABLE/
NOT_YET_PUBLIC/BLOCKED_EXTERNAL/FAILED). `metadata/company.yaml` owns only
**catalog-level facts** (name, website, GitHub org, lifecycle label) for
products the Release Surface Contract has already classified as at least
`VERIFIED` or `DEFERRED`-with-existing-public-presence. `company.yaml` must
never list a product, or a capability claim about a product, that the
product's own release-surface reconciliation has not independently
verified — this directly prevents the drift already found in this repo
(missing/renamed products in the catalog) from recurring in the other
direction (an unreleased product added to the catalog merely to "complete"
it).

**Concrete resolution for the drift found in Context**: HORO-515 must (a)
confirm whether `archeweave`/`harbinger` are renamed/retired and remove or
correct them, (b) add Octans/Circinus/Ophiuchus at their actual current
lifecycle (`Experimental`, matching `profile/README.md` and the product
registry — not upgraded to make the catalog look more complete), and (c)
leave Horologium/Eridanus **out** of the catalog until their own
HORO-513/HORO-517 reconciliation independently reaches at least `DEFERRED`
with real public presence — per the campaign's explicit instruction that
Eridanus governance adoption must not itself make Eridanus public.

### 8. Secrets and private data exclusion

**Decision**: No canonical governance content, generated projection, skill,
or workspace-bootstrap output may ever contain a live secret value,
consistent with the standing global secret-handling policy
(`~/.claude/rules/secret-handling.md`'s opaque-capability model — presence
checks only, never plaintext). `horonom doctor` (HORO-510) must include a
check that fails closed if a generated file appears to contain
credential-shaped content (reusing the existing `octans/scripts/
check-credential-boundary.sh` pattern as prior art where applicable, adapted
rather than duplicated).

### 9. Drift, versioning, and adoption contract

**Decision**: Every generated projection (decision #4) carries a source
commit/tag reference in its provenance header. `horonom doctor` computes
drift by comparing that reference against the canonical repo's current
`main`, and reports `WARN` (not `FAIL`) when the projection is merely
behind, `FAIL` only when the projection's *content* would differ from what
regenerating now would produce (mirroring the existing
`company-metadata-drift.yml --check` semantics, generalized). A repo adopts
a new governance version by re-running the bootstrap/regeneration step and
committing the result — there is no forced/automatic push of governance
changes into product repos.

### 10. ADR vs. governance-doc vs. skill boundary

**Decision**: Codifying the campaign's own §2 boundary rather than
re-deriving it: an **ADR** (this series) is required only for a decision
that is durable, cross-repo, and touches source-of-truth, security, or a
public contract — the ten decisions in this document are exactly that
class. Everything else (workflow conventions, tool usage, day-to-day
process) belongs in governance docs, `CONTRIBUTING.md`, `CLAUDE.md`, or a
skill, and does not need an ADR.

## Consequences

- HORO-505 must edit `CONTRIBUTING.md` and `NEW_REPO_CHECKLIST.md` to remove
  the merge-strategy contradiction per decision #0, and restructure
  `horonomy/.github` around the ownership matrix in decision #1.
- HORO-506 must implement `$HORONOM_WORKSPACE_ROOT` as additive-only per
  decision #3, using generated projections per decision #4.
- HORO-507/HORO-508 must implement adapters that satisfy decision #5 (no
  forked policy copies).
- HORO-509 must place the five shared skills exactly per decision #6.
- HORO-510 must implement `horonom doctor` checks that make decisions #2,
  #8, and #9 mechanically enforced, not just documented.
- HORO-512/HORO-515 must resolve the catalog drift exactly as decision #7
  specifies, including the archeweave/harbinger question.
- HORO-513/HORO-517 must not treat governance adoption as release-gate
  progress, per decision #7's closing clause.

## Dependency graph for the remaining children

```
HORO-504 (this ADR)
  ├─▶ HORO-505 (governance-foundation refactor of .github; depends on #0, #1)
  │     ├─▶ HORO-506 (workspace bootstrap; depends on #1, #3, #4)
  │     │     ├─▶ HORO-507 (Claude adapter; depends on #5)
  │     │     ├─▶ HORO-508 (Codex adapter; depends on #5)
  │     │     └─▶ HORO-510 (doctor; depends on #2, #8, #9)
  │     ├─▶ HORO-509 (shared skills; depends on #6, and on HORO-506's
  │     │     projection mechanism existing)
  │     └─▶ HORO-511 (repo-adoption pattern; depends on #1, #4)
  ├─▶ HORO-512 (Public Release Surface Contract; depends on #7)
  │     ├─▶ HORO-513 (Horologium/Fornax/Circinus/Ophiuchus adoption)
  │     ├─▶ HORO-517 (Eridanus adoption; must land NOT_YET_PUBLIC)
  │     └─▶ HORO-515 (company public surfaces; depends on #7's concrete
  │           catalog resolution)
  └─▶ HORO-534 (Jira metadata backfill; independent of the above, may run
        in parallel with any wave)

HORO-533 (final QA gate) depends on all of the above.
```

HORO-505, HORO-506/HORO-509/HORO-512 each touch shared files inside
`horonomy/.github` (`CONTRIBUTING.md`, `CLAUDE.md`, `governance/`,
`metadata/company.yaml`) — these must not run as concurrent PRs without
explicit file-ownership coordination; per this graph, HORO-505 lands and
merges before HORO-506/509/512 branch, avoiding the conflict rather than
resolving it after the fact.
