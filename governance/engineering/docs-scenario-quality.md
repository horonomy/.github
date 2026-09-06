# Scenario-first documentation and ground-truth reconciliation

Detailed rule text for `governance/README.md`'s Company layer. Distilled
from the cross-product HORO-638 campaign (2026-09) so future products adopt
the same discipline instead of each product session reinventing it.

Product capability truth, maturity, and doc content stay product-owned (see
the ownership matrix in `governance/README.md`) — this file standardizes
**vocabulary and process**, not any specific product's claims.

## Maturity vocabulary (use exactly these five terms)

`SHIPPED` / `PARTIAL` / `PLANNED` / `INTERNAL-ONLY` / `NOT IMPLEMENTED`.
Every product's docs should be able to label a capability with one of
these, unambiguously, in a maturity table near the top of its "Start
here"/"Why this product" page. Don't invent product-specific synonyms
(`beta`, `experimental`, `coming soon`, etc.) for the same axis — those are
fine as *marketing* maturity labels elsewhere (see
`metadata/README.md`'s separate `coming_soon`/lifecycle vocabulary for the
company catalog, a different axis: public-release status, not
implementation-completeness), but a docs maturity table should use this
fixed five-term set so a reader learns it once and it means the same thing
in every product's docs.

## Scenario-first expectation

A product's usage docs should let a first-time reader answer, within
minutes and without reading source or asking an engineer: what problem it
solves, when to use it, what's actually usable today, the shortest
happy-path scenario, what success looks like, and what's explicitly not
implemented yet. See HORO-638 for the full required-pattern checklist
(start-here page, >=3 realistic scenarios where the product supports that
many, an explicit "what do you hand the other party" section for any
information-exchange product, usage guidelines, and a maturity table) —
that ticket's description is the canonical detailed template; don't copy
it into this file and let the two drift.

## Ground-truth reconciliation, every time

Documentation claims must be reconciled against actual shipped
source/CLI/API/production behavior at the time of writing — not inherited
from a prior pass's prose. Re-run the product's real commands against a
fresh build/clone before describing them; don't assume last month's
verified output is still accurate. When a re-check finds a docs claim the
current source doesn't support, fix the docs (or open a product-decision
ticket) — never implement the feature silently under a docs-only ticket.

## Diagrams

Add a Mermaid architecture/sequence/data-flow diagram only where it
materially shortens understanding over prose — not decoratively. If a
product's docs site has never rendered Mermaid before, verify the
rendering pipeline actually works (a real `<svg>` in a real browser, not
just a code fence) before treating the diagram as shipped documentation.

## Pre-ship checklist for any doc change

Run before merging, not just before closing the ticket:

- No dead links, no stale hostnames, no accidental 404s.
- No disabled-but-clickable control presented as usable.
- No documentation claim unsupported by current source/runtime ground
  truth.
- Real-browser verification on the actual production surface where the
  product has one — a synthetic DOM check or `curl` of the HTML is not a
  substitute for a real page load when the product's own acceptance bar
  requires user-visible behavior (this mirrors the standing rule in
  `releases/public-surfaces.md`: don't accept a weaker proof than the
  claim requires).

## Independent/adversarial review before closing

A second pass — ideally a separate agent or session — should try to
actually use the product from the docs alone and attempt to find gaps,
not confirm the happy path. Findings get fixed and re-verified before the
ticket is marked Done; a defect found this way that's out of the docs
ticket's own scope (a real product bug, a cross-product adapter gap, a
protocol decision) gets filed as its own ticket rather than folded in or
silently dropped — see HORO-638's execution model for the "product-local
session owns its own child ticket" pattern this generalizes.
