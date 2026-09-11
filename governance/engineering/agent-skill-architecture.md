# Agent skill taxonomy, applicability model, and cross-org contract

Detailed rule text for `governance/README.md`'s Company layer (HORO-969,
Agent-Native Engineering v1). Extends
[ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md)
decisions #6 (shared-skill ownership) and #10 (ADR vs. governance-doc vs.
skill boundary) — this document does not touch source-of-truth, security,
or public-contract semantics, so per ADR-0005 §10 it is a governance doc,
not a new ADR. It does not alter Company → Product → Repository precedence
(ADR-0005 §1) or the canonical/projection model (ADR-0005 §4–5).

## 1. Skill taxonomy

The v1 top-level capability domains, each one stable agent-facing Skill:

| Skill | Owns |
|---|---|
| `engineering-loop` | Explore → Narrow → Validate → Escalate → Full Gate; the L0–L3 progressive diagnostic contract; the efficiency eval harness. |
| `rust-development` | Cargo/nextest/clippy workflows, reusing AI Agent Assembly's benchmark evidence. |
| `python-development` | uv/pytest/Ruff/typing/native-extension workflows. |
| `typescript-development` | pnpm/project-aware typecheck/Vitest/native-binding workflows. |
| `go-development` | Package-scoped test/vet/build workflows. |
| `swift-development` | Xcode/SwiftPM targeted iteration and iOS validation. |
| `terraform-development` | Plan-first IaC iteration. |
| `container-development` | Docker/BuildKit/Compose high-signal iteration. |
| `shell-development` | Safe, portable, low-noise shell automation. |
| `credential-operations` | Safe credential discovery/consumption/API-fallback procedure (the non-waivable prohibition itself stays governance — see §5). |
| `repo-scaffold` | Archetype- and stack-aware new-repository composition (distinct from `repo-bootstrap`, which adopts/refreshes governance in an existing repo). |
| `product-validation` | Product-level smoke/E2E/user-journey QA, defect lifecycle, quality reporting. |
| `design-qa` | Conditionally-composed rendered UX/accessibility/responsive/motion verification. |
| `documentation-experience` | Progressive information architecture, five-second entry clarity, persona/scenario/usage guidance, Docs Impact, documentation QA. |

Existing `governance-doctor`, `repo-bootstrap`, `jira-delivery`,
`release-assurance`, `public-release-reconcile` remain separate, unchanged
skills — extended by HORO-970's projection work, not duplicated or
folded into this list.

**Binding rule**: one stable capability domain ≈ one agent-facing Skill.
Detailed build/test/debug/FFI/cache/QA/platform/documentation techniques
live under a Skill's progressive `references/`/`examples/`/`scripts/`/
`tests/` (§2) — never as a separate top-level Skill (no
`rust-build`/`rust-test`/`rust-debug` explosion), and never merged into one
universal `software-development` mega-skill.

### Product-experience ownership boundaries

Added 2026-09-11, deliberately distinct from each other and from
implementation testing:

- **`product-validation`** owns product-level smoke/E2E/user-journey QA,
  quality evidence/reporting, and defect lifecycle. It does **not** replace
  unit/component/integration testing, which stays owned by the relevant
  development Skill.
- **`design-qa`** composes conditionally — only when a change touches a
  material rendered UI/UX surface. It owns visual/usability/accessibility/
  responsive/motion/perceived-quality validation. It is not required, and
  must resolve `NOT_APPLICABLE`, for a repo with no rendered surface.
- **`documentation-experience`** owns progressive information architecture,
  five-second entry-page clarity, persona/scenario/usage-guideline design,
  Docs Impact classification, and documentation QA. It does not replace a
  product's own Product Truth/Constitution — it presents that truth, never
  invents it.

## 2. Skill-package contract

Canonical package shape, extending the current flat single-file skills
(`governance-doctor`, `jira-delivery`, `public-release-reconcile`,
`release-assurance`, `repo-bootstrap` today ship `SKILL.md` only — that
remains valid, not deprecated):

```text
agents/skills/<name>/
  SKILL.md          # mandatory — always the canonical entry point
  manifest.yaml     # optional — present only when applicability/assets need it
  references/       # optional — detailed techniques, loaded on demand
  examples/         # optional — worked examples, loaded on demand
  scripts/          # optional — deterministic helpers, not prose-only advice
  tests/            # optional — fixtures proving the skill's own claims
```

- **Mandatory**: `SKILL.md`. A skill with no stack-specific applicability
  (e.g. the existing five) needs nothing else — adding a manifest is not
  required just to look uniform with the new skills.
- **Optional, added only when needed**: everything else. `manifest.yaml`
  declares applicability metadata (§3) when a skill's activation depends on
  repository evidence rather than always applying. `references/` holds
  content `SKILL.md` links to but does not inline (§4). `scripts/` holds
  helpers only where they add real deterministic value over prose — never
  a helper whose only job is to look automated.
- **Naming**: skill directory names are lowercase-kebab-case, matching the
  taxonomy in §1 exactly. No nested skill directories.
- **Provenance**: every generated projection (Claude/Codex adapters) keeps
  the source-commit provenance header already established for the existing
  five skills (ADR-0005 §9) — a new asset class doesn't change that
  contract, it's projected the same way.
- **Adapters never become a second source of truth**: `references/`,
  `examples/`, `scripts/`, `tests/` are consumed by generators, not
  hand-copied per adapter. A Claude-specific or Codex-specific formatting
  concern lives in the projection/generator logic (HORO-970's scope), never
  duplicated into the skill's own canonical content.

## 3. Applicability and composition

Applicability is determined by deterministic repository evidence, plus
explicit safe overrides when evidence is ambiguous or a repo needs to
narrow/strengthen it. Two forbidden shortcuts:

- **"One repo = one language"** — a repo may compose multiple stacks.
- **"Every repo receives every skill"** — an unrelated skill must never be
  projected merely because it exists.

Evidence signals per stack (non-exhaustive, extended by HORO-970's actual
implementation):

| Stack | Evidence |
|---|---|
| Rust | `Cargo.toml` |
| Python | `pyproject.toml`, `uv.lock` |
| TypeScript | `package.json` with TS deps, `tsconfig.json` |
| Go | `go.mod` |
| Swift | `Package.swift`, `.xcodeproj`/`.xcworkspace` |
| Terraform | `*.tf` |
| Container | `Dockerfile`, `docker-compose.yml` |

**Product-experience detection is deliberately explicit, not prose**, since
two implementers reading only "a rendered surface exists" could reasonably
build incompatible detectors — and `design-qa` must correctly resolve
`NOT_APPLICABLE` for a non-visual repo, so a wrong-in-either-direction
detector is a real correctness bug, not just an inconvenience. HORO-970
implements applicability against these concrete signals, checked in order:

1. **Explicit override always wins** — a repo/task declaring applicability
   directly (§ "explicit safe overrides" above) short-circuits detection
   entirely, in both directions.
2. **Framework/build evidence for a rendered surface**: a frontend
   framework dependency (e.g. React/Vue/Svelte/Next.js/Docusaurus in
   `package.json`), a static-site generator config, or an iOS/native UI
   target (`.xcodeproj`/`.xcworkspace` with a UI target, not a pure SwiftPM
   library package) → `design-qa` applicable.
3. **No such evidence** → `design-qa = NOT_APPLICABLE` by default. A CLI,
   library, backend service, or SDK with no framework/UI-target evidence
   does not get `design-qa` merely because it has *some* documentation or
   a metadata entry.
4. **`product-validation` and `documentation-experience` applicability is
   broader and independent of #2/#3** — they apply to any repo with a
   supported end-to-end user path and any repo with user-facing
   documentation, respectively, whether or not that path/documentation
   involves a rendered UI. A CLI with real docs and a real supported
   command flow gets both, with `design-qa = NOT_APPLICABLE`.

Company product metadata (`metadata/company.yaml`'s catalog, product
registries) is corroborating evidence for *which* product a repo belongs
to, never the primary applicability signal — a product being catalogued
publicly says nothing about whether the specific repo being evaluated has
a rendered surface.

### Worked composition examples

- PyO3 SDK → `python-development` + `rust-development` + `engineering-loop`.
- napi-rs SDK → `typescript-development` + `rust-development` + `engineering-loop`.
- Service with Terraform + container → language skill + `terraform-development` + `container-development`.
- Monorepo → union of each subproject's applicable stacks, resolved per touched-surface, not blindly for the whole tree.
- Web product/docs site → frontend stack + `product-validation` + `documentation-experience` + `design-qa`.
- Non-visual CLI/service → development stack + `product-validation` + `documentation-experience`, with `design-qa` explicitly resolving `NOT_APPLICABLE` (a truthful outcome, not a gap).
- Technical documentation site → `documentation-experience` + `design-qa` + docs-driven `product-validation` where applicable.

The task's actually-touched surface controls composition — editing only
`Cargo.toml`-adjacent code in a mixed Python+Rust repo does not require
projecting the Python skill for that specific task, even though the repo as
a whole is applicable to both.

## 4. Progressive disclosure

`SKILL.md` is always loaded; it must stay concise enough to be routinely
useful, not a full manual. It should contain: purpose, when to
use/not use, the routing decision (what to do first), and pointers into
`references/`/`examples/` for depth. The existing five skills' ~60–70 line
`SKILL.md` files are the working size reference — not a hard quota, but a
sign that an entry file materially longer than that is probably absorbing
content that belongs in a reference instead.

No arbitrary token ceiling is imposed — a skill that genuinely needs a
longer entry point because its routing decision is inherently more complex
should not be artificially truncated. The heuristic is qualitative: if a
reader has to scroll past implementation detail to find the routing logic,
move the detail to `references/`.

## 5. Rule vs. Skill vs. Governance vs. ADR

| Kind | When | Where |
|---|---|---|
| **ADR** | Durable, cross-repo, touches source-of-truth/security/public-contract (ADR-0005 §10's own test). | `horonomy/internal-docs` ADR series. |
| **Governance doc + generated always-on rule** | Company non-waivable invariant. | `governance/**` (canonical), generated Claude `.claude/rules/*.md`/Codex-equivalent adapter (projected, never hand-forked). |
| **Skill** | Repeatable task workflow / operational knowledge. | `agents/skills/<name>/SKILL.md`. |
| **Skill reference** | Detailed technique, evidence, or example. | `agents/skills/<name>/references/`, `examples/`. |

Concretely: the credential-plaintext prohibition itself (never print/log/
cat a secret) is a non-waivable governance rule with a generated always-on
adapter — it must be enforced the same way in every session regardless of
task. The *procedure* for safely discovering and consuming an available
credential capability, and falling back MCP → CLI → documented API, is a
Skill (`credential-operations`) — it's operational knowledge an agent
applies when the situation calls for it, not a constraint that must always
render into context.

The same split applies to the new product-quality domain: "implementation
CI green ≠ Product QA PASS", "a material QA defect must be tracked in
Jira", and "a behavior/security/API change requires a Docs Impact
classification" are non-waivable DoD invariants (governance), while the
actual mechanics of running a golden user journey, composing Design QA, or
writing a scenario page are Skills.

## 6. Optional-tool abstraction

RTK, CodeGraph, Playwright, XCUITest, and similar tools are **current
preferred implementations**, never company semantic invariants. Every
skill that names a preferred accelerator must also define its native
fallback, and must never become unusable — or silently skip real
verification — merely because the accelerator isn't installed.

Two failure modes this forbids:

- **Silent degradation to nothing**: an accelerator missing must not
  quietly turn a required check into a no-op. Missing RTK/CodeGraph means
  "use the native tool", not "skip the check."
- **Treating navigation as proof**: CodeGraph-style static/dependency-graph
  output is evidence for *finding* relevant code, never proof that a
  change has no runtime impact. A compiler/test/build/runtime gate is
  never replaced by a static graph saying "no callers found."

Platform-specific tooling stays behind a semantic capability contract, not
a single hardcoded tool: "real browser evidence" for a web surface and
"native/XCUITest evidence" for an Apple-native surface are the actual
requirement; Playwright and XCUITest are today's respective
implementations of that requirement, swappable if a better tool emerges.

## 7. Cross-org ownership contract

- `horonomy/.github` owns Horonom company-common engineering capability
  sources (canonical Skills, governance, rule adapters) — unchanged from
  ADR-0005 §1/§6.
- `horonomy/*` repos adopt normal company governance **and** shared
  engineering capabilities through the existing `repo-bootstrap` path,
  extended by HORO-970/982 to include the new skill classes.
- `ai-agent-assembly/*` is an **independent product-governance boundary**.
  `repo-bootstrap`'s normal Horonom-adoption mode **must not** run against
  it — a repo whose canonical remote resolves to `ai-agent-assembly/*` (or
  any other non-`horonomy` org) is never treated as a `horonomy` repo
  regardless of its local directory name or which local workspace root it
  happens to sit under.
- Instead, `ai-agent-assembly/*` (and any future external/product
  consumer) uses an explicit **shared-engineering-capability consumer
  mode** (HORO-982's scope): it can import/sync/refresh company-common
  engineering Skills (the development-language skills, `engineering-loop`,
  `product-validation`, `design-qa`, `documentation-experience`,
  `credential-operations`, `repo-scaffold`) without adopting Horonom
  product/company governance, without rewriting its own CLAUDE/AGENTS
  product truth, without changing its AAASM Jira/branch/release rules, and
  without maintaining a hand-copied fork of the shared content — consumed
  content stays refreshable from the canonical `horonomy/.github` source
  and carries the same provenance header as any other projection.
- Product/repo-specific measured policy is always narrower authority for
  its own codebase. The clearest existing example: `rust-development`
  must defer to `ai-agent-assembly/agent-assembly`'s own benchmark-backed
  Rust performance policy (`docs/bench-5992/`) rather than universalizing
  AA-specific machine numbers as company-wide fact (HORO-972's scope).
- The same narrowing rule applies to the three new product-experience
  skills: a product's own QA/design/documentation constitution is narrower
  truth. `design-qa` verifies conformance and usability against a
  product's *existing* visual/design constitution — it does not invent or
  impose one aesthetic across all products. Circinus does not need to look
  like Lifekin.
- **Enforcement severity is pinned here, mechanism is HORO-982's scope.**
  Per ADR-0005 decision #2 (non-waivable invariants are mechanically
  enforced, not just declared), an org-boundary violation this contract
  forbids — Horonom-governance adoption markers/content landing in an
  `ai-agent-assembly/*` repo, or vice versa — is a **FAIL**, never a WARN,
  in whichever doctor/CI check HORO-982 implements. This is a security/
  governance-boundary defect, not a staleness warning, and must be
  detectable and blocking from the moment HORO-982 ships. Until HORO-982
  lands, `repo-bootstrap`'s existing remote/org resolution (it already
  requires knowing the canonical remote — see ADR-0005 §1) is the interim
  safeguard: it must not be invoked in Horonom-adoption mode against a
  repo whose canonical remote isn't `horonomy/*`, and no other ticket in
  this campaign may bypass that check to move faster.

## 8. Workspace-root portability

Both local development roots used during dogfooding
(`${HOME}/Bryant-Developments/horonomy` and
`${HOME}/Bryant-Developments/AI-agent-assembly`) are **runtime-local
defaults**, never committed literally. Any committed governance, config,
generated projection, Skill, Rule, test, or script that needs a workspace
root must resolve it from a configurable/portable source (an environment
variable, a discovered `.git` remote, an explicit CLI argument) — consistent
with the existing `$HORONOM_WORKSPACE_ROOT` contract (ADR-0005 §3). This
document does not introduce a new mechanism; it reaffirms the existing one
applies to every new asset class introduced here.

## 9. Compatibility

This contract is additive. The five existing shared skills, their current
single-`SKILL.md` shape, and `repo-bootstrap`'s current adoption behavior
remain valid without modification — the new asset classes, applicability
model, and consumer mode are opt-in extensions a skill adopts only when it
actually needs them (a stack-specific skill needs applicability metadata;
`jira-delivery` does not).

## 10. Migration note for HORO-970/HORO-506–511

HORO-970 implements the mechanics this document defines: package/asset
projection, applicability resolution, rule projection, and the cross-org
consumer mode. HORO-982 extends `horonom doctor`/onboarding to detect drift
against this contract (stale skills, wrong applicability, accidental
cross-org governance contamination). Neither ticket needs to re-derive the
architecture — this document is the frozen input both build against.
