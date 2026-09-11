# Worked example: 5-second-comprehension check on a marketing homepage

This walks the dimension-1 heuristic from `references/
design-qa-dimensions.md` end-to-end on a generic marketing/product
homepage, and shows the full structured-verdict format the ticket requires.
The product name and copy below are illustrative — this is a worked
*procedure*, not a report against a specific captured Horonom product (see
`references/web-playwright-adapter.md`'s honest evidence-status note: no
Playwright harness has actually been dogfooded against a real Horonom web
product in this workspace yet — that is HORO-983 rollout scope).

## 1. Gather evidence

```
page.setViewportSize({ width: 1440, height: 900 })
page.goto('https://example-product.horonom.com/')
page.waitForLoadState('networkidle')
page.screenshot({ path: 'homepage-desktop.png', fullPage: false })
```

Deliberately capture only the *first viewport* (`fullPage: false`) for the
5-second check — the heuristic is about what a visitor sees before
scrolling, not the whole page. A separate full-page capture is taken
afterward for the other dimensions (hierarchy, responsive), but is not part
of this specific check.

## 2. Look, for about five seconds, and answer three questions

Looking only at the captured above-the-fold screenshot:

- **What is this for?** A large headline reads "Ship autonomous agents you
  can actually govern." A subhead beneath it: "Horonom gives your AI
  agents boundaries, audit trails, and rollback." → answerable in under a
  second; the product category (agent governance infrastructure) is
  immediately legible.
- **What's the one action wanted here?** A single high-contrast button
  reads "Get started" above the fold, with a secondary lower-contrast
  "Read the docs" link beside it. → one clearly primary action, one
  clearly secondary — passes.
- **Who is this for?** Supporting copy and a small row of logos below the
  fold (outside the 5-second window) — but the headline itself uses
  "your AI agents," which reads generically rather than naming a specific
  buyer (platform team? individual developer? enterprise security team?).
  → partially answerable; audience is implied but not sharp.

## 3. Record the verdict

```markdown
## Design QA verdict — example-product homepage (desktop, first viewport)

**Surfaces tested:** `/` (homepage), desktop viewport (1440×900) only —
tablet/mobile and full-page capture not covered by this specific check.

**States tested:** logged-out, first load, no query params.

**Devices/viewports:** Chromium via Playwright, 1440×900. No real-device
or Apple-native surface involved.

### Dimension 1 — First-impression / comprehension (5-second check)

- Purpose: PASS — headline + subhead communicate category and value
  proposition immediately.
- Primary action: PASS — one high-contrast CTA, one clearly secondary link,
  no competing third CTA above the fold.
- Audience: PASS_WITH_KNOWN_LIMITATIONS — "your AI agents" signals the
  product category but not a specific buyer persona; a first-time visitor
  from a different segment (e.g. a compliance stakeholder rather than a
  developer) may need to scroll to confirm relevance.

### Other dimensions

Not evaluated in this pass — this worked example is scoped to dimension 1
only. A real Design QA pass would cover all seven per
`references/design-qa-dimensions.md` before reaching an overall verdict;
partial coverage like this must be labeled as such, never presented as a
full pass.

### Bugs / known limitations

- Audience specificity in the hero copy is a soft usability observation,
  not a functional defect — recommend to product/design, not filed as a
  blocking bug.

### Recommendation

**PASS_WITH_KNOWN_LIMITATIONS** for dimension 1 on this surface/state/
viewport. Overall Design QA verdict withheld — dimensions 2–7 were not run
in this example, so no overall PASS/FAIL is claimed.
```

## What this example does and does not establish

It shows the mechanics: how to gather a deliberately-scoped screenshot, how
to apply the three-question heuristic without over-reading into content
outside the 5-second window, and how to write a verdict that is honest
about partial coverage rather than rounding up to a full PASS. It is not a
report of this check having actually been run against a real Horonom
product's real homepage — see the evidence-status note at the top of this
file and in `references/web-playwright-adapter.md`.
