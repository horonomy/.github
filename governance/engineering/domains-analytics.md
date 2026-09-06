# Domain surface and public analytics — non-waivable invariant

Detailed rule text for `governance/README.md`'s Company layer. Full
rationale, hostname templates, security checklist, and analytics
constitution: [ADR-0006](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0006-horonom-domain-surface-and-analytics-constitution.md)
in `horonomy/internal-docs` (HORO-566). This file is the concise pointer
every agent reads by default — it does not fork a second copy of that
ADR's content; read the ADR for the full checklist before executing a
domain/analytics migration ticket.

## The rule, in one line

> **Horonomy lives on `.com`. Horonomy products run on `.run`.**

## What this means for an agent working in any Horonom repo

- `<product>.horonom.com` is the canonical public product/marketing site.
  `docs.<product>.horonom.com` is the canonical human-facing docs surface
  once a product has enough documentation to warrant a dedicated one
  (otherwise docs live at `<product>.horonom.com/docs`).
- `app.<product>.horo.run` / `api.<product>.horo.run` /
  `ingest.<product>.horo.run` (and similar) are executable/runtime service
  boundaries — provisioned only when the real service they name actually
  exists. No decorative DNS.
- **`<product>.horo.run` is not the default canonical marketing hostname**
  for a product that has migrated. It may remain as a redirect/alias to
  `<product>.horonom.com`, per ADR-0006 §3.
- **The bare root `horo.run` is a deliberate, permanent exception**: it is
  the Horonom Product Atlas / product-directory entry point (source:
  `horonomy/official-website`'s `atlas/`), not a runtime endpoint and not a
  second corporate site. It uses the Horonom **company** GA4 property, never
  a product-specific one. Full rationale and the Atlas's analytics boundary:
  [ADR-0008](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0008-product-atlas-root-exception-and-analytics-boundary.md)
  (HORO-594/595).
- A domain change is never "just DNS" — it requires reconciling OAuth/OIDC
  redirect URIs, CORS allowed origins, CSP, cookie scope, and
  webhook/callback URLs (ADR-0006 §5). Do not treat a hostname migration as
  complete until that checklist is done.
- GA4 marketing analytics and authenticated product telemetry are separate
  privacy domains — GA4 is allowed only on approved public marketing/docs
  surfaces, never fed prompt/agent/finding/repo/tenant/PII/credential/
  authenticated-SaaS content (ADR-0006 §6). Authenticated telemetry is a
  separate, not-yet-designed problem this ADR does not authorize.
- Product-specific domain/DNS/analytics implementation is **product-owned**
  — it happens in the product's own repo/session, not centralized into a
  company-common ticket. See HORO-566's Execution Model.
- Current per-product migration state (current vs. target hostnames, DNS
  status, GA4 IDs, blockers) is tracked in
  [`metadata/domain-migration-inventory.yaml`](../../metadata/domain-migration-inventory.yaml)
  (this repo) and the narrative
  [Product domain migration register](https://github.com/horonomy/internal-docs/blob/main/docs/registers/product-domain-migration.md)
  (`horonomy/internal-docs`) — check both before assuming a product's
  current domain/analytics state; do not re-derive it from memory or an
  old Jira comment.

## Reporting identity: hostname + page path, never path alone (HORO-596)

A GA4 **property** is the company/product analytics boundary; a **web
stream** is a distinct public surface within it (a property can hold more
than one stream — the Horonom company property does). A Measurement ID is
issued **per stream**, not per property, so it is the authoritative,
platform-enforced way to attribute an event to one specific surface — see
[`metadata/company-analytics-registry.yaml`](../../metadata/company-analytics-registry.yaml)
for the recorded stream/Measurement ID pairs.

**Never treat page path alone as a unique reporting identity** when a
property has more than one stream/host — `/` exists on every surface.
Report and reconcile by **hostname + page path**: `horonom.com` + `/`,
`horo.run` + `/`, `horonom.com` + `/blog`,
`docs.fornax.horonom.com` + `/quick-start`, etc. A meaningful interaction
beyond a page view is a privacy-safe custom event (see the docs-analytics
convention below for the vocabulary constraints that apply to those).

Cross-domain session continuity (GA4's `linker`/cross-domain measurement
feature) across the `horonom.com → horo.run → product surface` journey is
**intentionally not configured** — each hop is a deliberate property
boundary by design (ADR-0006 §6, ADR-0008), and stitching sessions across
them would blur exactly the ownership/privacy separation those ADRs
establish. Attribution across the journey instead relies on each
property's own referrer-based acquisition reporting, which requires
outbound links to preserve the `Referer` header (`rel="noopener"`, not
`rel="noopener noreferrer"`) — see HORO-614 for a known gap in this on
`horonom.com`'s own outbound links.

## Precedence

Company constitution (ADR-0006) → a product's own deliberate, recorded
exception (e.g. keeping a standalone domain like `agent-assembly.com`) →
implementation convenience. Convenience never justifies deviating from the
hostname templates or the security checklist.
