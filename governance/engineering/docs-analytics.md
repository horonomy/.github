# Documentation analytics — non-waivable invariant

Detailed rule text for `governance/README.md`'s Company layer. Full
rationale, the three-boundary model, common event vocabulary, parameter
allow/forbid list, and the real-browser verification standard:
[ADR-0007](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0007-documentation-analytics-constitution.md)
in `horonomy/internal-docs` (HORO-587), which extends — does not
duplicate or supersede —
[`domains-analytics.md`](./domains-analytics.md) /
[ADR-0006](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0006-horonom-domain-surface-and-analytics-constitution.md).
This file is the concise pointer every agent reads by default; read the
ADR before executing a documentation-analytics implementation ticket.

## The rule, in one line

> A documentation GA4 stream is a separate analytics/security boundary
> from marketing analytics and from authenticated product telemetry —
> never assume they share a stream, a property, or an event vocabulary.

## What this means for an agent working in any Horonom repo

- **Three boundaries, not one:** marketing analytics (acquisition/intent
  on `<product>.horonom.com`), documentation analytics (developer
  learning/adoption on `docs.<product>.horonom.com` or
  `<product>.horonom.com/docs`), and authenticated product telemetry
  (logged-in usage — **not** GA4 by default, a separate privacy/product
  design this ADR does not authorize).
- **A Documentation GA4 stream URL existing proves nothing about DNS.**
  Verify the docs host actually resolves and serves the expected content
  before wiring the stream — see the
  [Documentation analytics migration register](https://github.com/horonomy/internal-docs/blob/main/docs/registers/docs-analytics-migration.md)
  for what's actually been verified per product (several founder-provided
  docs streams predate any live DNS record for their host).
- **Common docs event vocabulary** (implement only what real UI
  supports): `page_view` (route-specific) plus `docs_search`,
  `docs_search_no_results`, `docs_toc_click`, `code_copy`,
  `install_command_copy`, `quickstart_click`, `github_click`,
  `docs_to_app_click`, `docs_to_marketing_click`, `docs_feedback`,
  `docs_version_switch`, `docs_tab_switch`, `docs_404_view`.
- **Forbidden event parameters, no exceptions:** raw search query text,
  copied code/command contents, prompts/agent content, repository names,
  private file paths, tenant/org/customer identity, user/email/PII,
  credentials/tokens, authenticated app data, findings/evidence/
  provenance/Product Truth content, security incident contents, internal
  debug/error payloads with sensitive context. This list is intentionally
  explicit here so a future agent cannot silently widen it — see ADR-0007
  §3 before adding any new event parameter.
- **Verification standard (the FORNX-329 lesson):** a Measurement ID in a
  bundle, `gtag.js` returning 200, a `dataLayer` config call, or a
  hand-crafted `g/collect` returning 204 are **not** proof analytics
  works. Required evidence is a real production browser performing real
  route navigation and a real interaction, with the browser itself
  emitting the collect request — correct Measurement ID, correct
  `page_location`, correct event name, no duplicate ID, no forbidden
  payload.
- **Cross-host journeys** (`<product>.horonom.com` → docs, whether
  subdomain or path) get a referral/session/duplicate-tag/cookie-domain
  audit — use GA4/browser standard cross-domain behavior, do not invent a
  custom identity-propagation mechanism.
- Product-specific docs-analytics implementation is **product-owned** —
  it happens in the product's own repo/session, not centralized into a
  company-common ticket. See HORO-587's Execution Model.
- Current per-product docs-stream state (stream ID, Measurement ID,
  reconciled surface, verification status, blockers) is tracked in
  [`metadata/docs-analytics-registry.yaml`](../../metadata/docs-analytics-registry.yaml)
  (this repo) and the narrative
  [Documentation analytics migration register](https://github.com/horonomy/internal-docs/blob/main/docs/registers/docs-analytics-migration.md)
  (`horonomy/internal-docs`) — check both before assuming a product's
  current docs-analytics state.

## Precedence

Company constitution (ADR-0006 + ADR-0007) → a product's own deliberate,
recorded exception → implementation convenience. Convenience never
justifies a forbidden event parameter or skipping real-browser
verification.
