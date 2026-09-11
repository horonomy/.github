# Web adapter: real browser automation for Design QA evidence

## Evidence status — read this first

**Real, repo-owned Playwright suites already exist in this workspace** —
this adapter is not a from-scratch invention, it generalizes patterns
already in production use:

- `horologium/web/playwright.config.ts` — runs against the real
  `horologium-server` + real Postgres + golden fixture (HORO-467/HORO-469b),
  deliberately no mocked API (that's what the Vitest component tests are
  for); `trace: "retain-on-failure"` chosen specifically because
  `retries: 0` makes `on-first-retry` never fire.
- `fornax-cloud/frontend/playwright.config.ts` — a second independent
  product's own E2E suite.
- `eridanus/tests/e2e/playwright.config.ts` (+ `playwright.compose.config.ts`)
  — a Compose-backed E2E suite (`golden-path.compose.spec.ts`,
  `investigator-ui.spec.ts`) exercising a real containerized deployment
  shape.
- `horonom-site/scripts/visual-qa/playwright.config.mjs` — a Design-QA-
  shaped (not functional-E2E) visual capture script, the closest existing
  precedent to this skill's own visual/first-impression dimension.

None of these were authored as this Skill's own dogfood run, and none is
yet invoked *through* `design-qa`'s structured verdict format — that
integration (running one of these, or a new capture, through this skill's
own PASS/FAIL/BLOCKED reporting contract) is still deferred to HORO-983's
rollout scope. But the claim that Playwright is unused in this workspace
was wrong; treat the commands below as generalizing real, currently-running
patterns, not a hypothetical target shape.

## What Playwright is for here

Real browser automation for deterministic routes/states — not a mock DOM,
not a static HTML snapshot. It drives an actual Chromium/WebKit/Firefox
engine so captured evidence (layout, fonts, computed styles, real paint) is
what a real visitor would see.

## Deterministic routes/states

- Navigate to explicit routes, not just the root — a Design QA pass on a
  homepage is not automatically a pass on every other page.
- Reach a specific *state* deliberately (logged out / logged in / empty
  list / error banner shown) via real interaction or a documented test
  fixture/seed — never by directly injecting DOM content that a real user
  could never produce, which would invalidate the evidence.
- Wait on a real signal (`page.waitForSelector`, `waitForLoadState`), never
  a fixed sleep long enough to mask a real timing problem.

## Screenshots across viewports

Capture the same route/state at representative desktop, tablet, and mobile
viewport widths (e.g. ~1440px desktop, ~834px tablet, ~390px mobile —
adjust to the product's actual documented breakpoints if it has published
ones; do not invent product-specific breakpoints this adapter doesn't own).
`page.setViewportSize(...)` before each capture; `page.screenshot({fullPage:
true})` to capture below-the-fold content, not just the initial viewport.

## Keyboard / focus / touch-mode interaction

- Keyboard: drive `Tab`/`Shift+Tab`/`Enter`/`Escape` through the page and
  capture focus-visible state at each stop — Playwright can query
  `:focus-visible` or screenshot mid-sequence.
- Touch-mode: Playwright's mobile device emulation (`hasTouch: true`,
  device descriptors) approximates touch-target sizing and tap behavior; it
  is an approximation of a real touch device, not a substitute for one —
  say so in the verdict rather than implying device-equivalent proof.

## Reduced motion

`page.emulateMedia({ reducedMotion: 'reduce' })` before capture — compare
against a normal-motion capture of the same state to confirm motion is
actually suppressed/substituted, not just that the page still renders.

## Console / network / runtime failure capture

- Attach a `page.on('console', ...)` listener before navigating and record
  any `error`/`warning`-level message during the interaction sequence, not
  just on load.
- Attach `page.on('requestfailed', ...)`/`page.on('response', ...)` to
  catch failed requests and non-2xx responses that a purely visual check
  would miss (a broken image that still occupies its layout box, a failed
  background fetch that silently leaves a stale UI).
- Attach `page.on('pageerror', ...)` for uncaught JS runtime exceptions.

## Accessibility checks

- Playwright's accessibility snapshot (`page.accessibility.snapshot()` or
  the newer ARIA-snapshot tooling) gives a structured accessibility tree —
  use it to check landmark/heading structure and accessible names per
  dimension 4 in `design-qa-dimensions.md`.
- This is real evidence of the computed accessibility tree, not a
  substitute for an actual automated audit tool (e.g. axe-core) where the
  product has one wired in — if it does, prefer its output over
  hand-rolled tree inspection; if it doesn't, the accessibility snapshot is
  still meaningfully better than skipping the dimension.

## What this adapter does not prove

A full green Playwright evidence pass (all viewports captured, no console
errors, accessibility tree sane) is strong rendered-DOM evidence. It is not
proof of real-device battery/thermal behavior, real network conditions
beyond what's emulated, or that the *design itself* is good — dimension
judgment (hierarchy, comprehension, friction) still has to be applied to
what the captures show, per `SKILL.md`'s "screenshots are evidence, not a
complete oracle" rule.
