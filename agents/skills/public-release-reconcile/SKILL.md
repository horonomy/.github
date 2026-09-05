# SKILL.md — public-release-reconcile

## Purpose

Determine and record a product's genuine Public Release Surface state
(HORO-512's 7-state contract) by checking what's actually true across
every public surface — never by asserting a state to make a checklist
green.

## Type

Auto-used. Invoke when adopting a product into governance (HORO-511/513/
517), when a product's public presence changes, and as part of the final
campaign QA gate (HORO-533).

## When to use

Any Horonom product being represented (or considered for representation)
on a public surface: `metadata/company.yaml`, the GitHub org profile,
`horonom.com`, a product's own repo/docs/domain.

## When NOT to use

- To justify publishing an unreleased or internal product "because the
  gate needs to be green." The gate reports what's true; it never becomes
  the reason to make something true prematurely.
- On a product whose own repo already has a more specific, stricter
  release process — run this reconciliation in addition to that process,
  not instead of it.

## Procedure

1. For the product, check each surface independently: product repo's own
   truth/maturity claim, product docs, hosted app (if any), domain/TLS,
   GitHub repo metadata (description, topics, visibility), tags/GitHub
   Releases, `metadata/company.yaml`'s catalog entry, the GitHub org
   profile (`profile/README.md`), and `horonom.com`'s product listing.
2. Classify the product's overall state using the 7-state contract in
   `governance/releases/public-release-contract.md`
   (VERIFIED / REQUIRED / DEFERRED / NOT_APPLICABLE / NOT_YET_PUBLIC /
   BLOCKED_EXTERNAL / FAILED) — pick the state the *evidence* supports,
   not the state that would look best.
3. Where two surfaces disagree (e.g. `company.yaml` says one lifecycle,
   the product's own repo says another), the product's own repo is
   authoritative for capability/maturity truth; `company.yaml` may only
   restate catalog-level facts already true there
   (`governance/releases/public-surfaces.md`).
4. Record the reconciliation result and any gap found; fix a gap only
   where it's genuinely this ticket's scope — don't repair an unrelated
   product defect a reconciliation pass happens to surface, unless it
   blocks governance correctness itself.
5. A visible change to `horonom.com` or the org profile as a result of
   this reconciliation needs real screenshot evidence in its PR (see
   `release-assurance`).

## References

- `governance/releases/public-release-contract.md` — the full 7-state
  contract (HORO-512).
- `governance/releases/public-surfaces.md` — the never-publish-to-turn-
  the-gate-green rule and the catalog-ownership boundary.
