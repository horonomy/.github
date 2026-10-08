# Product Positioning Registry (HORO-1733)

Canonical, reviewable projection of each product's positioning, pinned to a
product-owned (T1) source. This directory does **not** originate identity —
every identity-bearing field cites a verbatim quote from the product's own
repository, and a lint (HORO-1740) is expected to verify the quote still
matches that source. A failed verbatim check means this record is stale and
must be fixed to match the source — the source is never changed to match a
stale record.

## Precedence rule

A product-owned T1 source always wins. This record may only quote or narrow
it. Every surface derived from it — `horonomy/official-website`'s
`productRegistry.ts`, the Horonom corporate card, the org profile — may only
simplify this record, never broaden it. See
`horonom-site/design/product-narrative-hierarchy.md` §3 for the general rule
this directory implements per-product.

## Non-goals

- Not a source of lifecycle/maturity truth — that stays in
  `../release-evidence/<id>.yaml` (`claimed_lifecycle`) and
  `horonom-site/src/data/productRegistry.ts` (`maturity`). `maturity_ref`
  below points at it; this directory never repeats the value.
- Does not imply publication. A record existing here is not a reason to add
  a Libra or Eltanin card to `productRegistry.ts` or any public surface —
  that decision belongs to HORO-1695.
- Does not change `productRegistry.ts`, `metadata/generated/company.json`,
  or `scripts/generate_company_metadata.py`'s schema.
- Does not use the ADR 0033 §6 claim-term vocabulary (that's a different
  axis — behavior-on-evidence, not portfolio positioning).

## Schema

See any `<product-id>.yaml` file in this directory for the fields in use.
Every identity-bearing field is `{text, source: {repo, path}}` (or
`{repo, path, heading}` when it's a sub-section) so a future guard can check
`text` against the cited location verbatim. Prose values are single-line,
double-quoted strings — this directory's records are not currently parsed by
`scripts/generate_company_metadata.py`'s `parse_yaml`, but keeping this shape
means they could be without a rewrite, since that parser does not handle
block scalars.

## Index

| Product | File | Status |
|---|---|---|
| Fornax | `fornax.yaml` | Identity fixed HORO-1735, cites `fornax-core` README post-fix |
| Circinus | `circinus.yaml` | Identity fixed HORO-1736, cites `circinus` README + site hero post-fix |
| Libra Governor | `libra-governor.yaml` | Identity fixed HORO-1737, cites `libra-governor` PRODUCT.md post-fix |
| Ophiuchus | `ophiuchus.yaml` | Identity fixed HORO-1738, cites `ophiuchus` README post-fix |
| Horologium | `horologium.yaml` | Already compliant — no remediation ticket needed |
| Octans | `octans.yaml` | Deliberate intrinsic domain boundary — not an agent product, not generalized |
