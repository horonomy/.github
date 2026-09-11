<!-- horonom:generated -->
<!-- Source: horonomy/.github agents/skills/design-qa/SKILL.md. Provisional Codex projection shape — see agents/common/README.md. Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->

# SKILL.md — design-qa

## Purpose

Verify a product's *rendered* user experience — not just that it functions.
This is the company-common capability referenced by
`governance/engineering/agent-skill-architecture.md` (HORO-969) §1: it owns
visual/usability/accessibility/responsive/motion/perceived-quality
validation for products with a material rendered UI/UX surface. It does not
own functional E2E correctness (that is `agents/skills/product-validation/
SKILL.md`, authored in parallel this wave) and it does not own unit/
component/integration testing (owned by the relevant `*-development`
skill).

## Type

Conditionally-composed, judgment-gated — **no `manifest.yaml`**. Every other
applicability question in this taxonomy (`stacks: [rust]`, `[python]`, …)
reduces to deterministic filesystem evidence: a `Cargo.toml`, a
`pyproject.toml`. Whether a product *has a material rendered surface worth
design-scrutiny* is not that kind of question — any stack (CLI, library,
backend service, native app, web app) could have a rendered UI or none at
all. Filesystem evidence (a `package.json` with a frontend framework) can
signal that *some* code renders HTML; it cannot tell "this route is a
real user-facing screen" from "this route is an internal health-check JSON
endpoint that happens to share a web framework" — that distinction needs
judgment, not detection. So applicability here is: **invoked explicitly by
`product-validation` when it has judged a product has material UI/UX**, or
by an explicit task override in either direction. No standalone
auto-trigger, no stack-evidence table.

**Default when nothing invokes this skill: `NOT_APPLICABLE`.** A pure
backend service, library, SDK, or CLI with no rendered surface does not run
Design QA — the absence of an invocation is itself the correct, truthful
outcome, not a gap to fill.

## When to use

- Invoked **by `product-validation`**, never run standalone as a
  replacement for it, when the product under change has a material
  rendered UI/UX surface (a web app/site, a native app with a UI target).
- When a task explicitly overrides applicability on ("this static JSON
  service has a customer-facing status page, check it") or off ("this
  route is internal-only, skip Design QA").

## When NOT to use

- As a standalone substitute for `product-validation`'s functional E2E/
  user-journey QA. Design QA is layered evidence on top of a product that
  already has a functional-QA verdict, not a replacement for one.
- On a pure backend/library/CLI/SDK repo with no rendered surface — resolve
  `NOT_APPLICABLE` and move on.
- To claim functional PASS implies design PASS, or vice versa. **A green
  E2E run is not a Design QA PASS if the experience is confusing** — broken
  hierarchy, dead-end flows, and inaccessible controls can all sit behind
  passing assertions. Screenshots and visual diffs are *evidence*, gathered
  per `references/design-qa-dimensions.md`, never a complete design-quality
  oracle on their own — an agent still has to look at what they show.
- To force a web tool onto a non-web product. **Never run Playwright
  against a native iOS product** — use
  `references/native-apple-adapter.md`. The reverse applies too: XCUITest
  is not a fallback for a web surface.
- To silently repair a regression by moving the goalposts. **Never
  auto-update a visual baseline to make a diff pass** — a baseline changes
  only through an explicit, reviewed decision that the new rendering is the
  intended one, never as a side effect of a failing comparison.
- To invent an aesthetic. A product's own Design Constitution/North Star
  (where one exists) is **narrower, product-owned authority** — this skill
  verifies conformance to it and general usability principles; it never
  overrides it with a company-wide "better" look. Circinus does not need to
  look like Lifekin (HORO-969 §7).

## Composition

Composes with `engineering-loop` for routing only — Explore/Narrow/
Validate/Escalate/Full Gate discipline still applies when iterating on a
fix Design QA surfaced, but this skill's actual verification work (looking
at rendered output, judging the seven dimensions) is not a build/test/lint
loop `engineering-loop` drives directly.

## The seven dimensions

First-impression/comprehension, visual hierarchy/clarity, responsive/
input-mode behavior, accessibility, motion/interaction feedback, usability/
task friction, performance/perceived quality — operationalized concretely
(what to actually look at, per dimension) in
`references/design-qa-dimensions.md`.

## Evidence adapters

The actual requirement is real rendered-surface evidence, not a specific
tool (HORO-969 §6):

- **Web** → `references/web-playwright-adapter.md`.
- **Apple-native (iOS/macOS)** → `references/native-apple-adapter.md`.

## Verdict

Every Design QA pass ends in a structured verdict — surfaces/states/devices
tested, per-dimension observations, bugs/known limitations, recommendation
— worked end-to-end in
`examples/web-homepage-five-second-check.md`. Verdicts are one of `PASS`,
`PASS_WITH_KNOWN_LIMITATIONS`, `FAIL`, `BLOCKED` (evidence could not be
gathered at all).
