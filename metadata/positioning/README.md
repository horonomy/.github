# Product Positioning Registry (HORO-1733)

Canonical, reviewable projection of each product's positioning, pinned to a
product-owned (T1) source. This directory does **not** originate identity —
every identity-bearing field cites a verbatim quote from the product's own
repository. `scripts/check_positioning.py verify-source` (HORO-1740) checks
that quote against the source at the sha it was last verified against. A
failed verbatim check means this record is stale and must be fixed to
match the source — the source is never changed to match a stale record.

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
`{repo, path, heading}` when it's a sub-section). Prose values are
single-line, double-quoted strings, and list/map fields use flow style
(`["a", "b"]`, `{k: v}`) — `scripts/check_positioning.py` (HORO-1740) reads
these records with `generate_company_metadata.parse_yaml` plus its own flow
decoder for exactly those two shapes; a block scalar or nested block
map/list under a positioning field will not decode.

Fields `check_positioning.py` consumes beyond the base HORO-1733 schema:

- `current_hosts.hosts`: literal current host/adapter names (`"Claude Code"`).
- `current_hosts.host_class` (optional): a generic noun class the identity
  surface must also avoid (`"coding agent"` — singular; the guard's term
  matcher allows an optional trailing `s`/`'s`, so put the class in its
  base form, not pre-pluralized).
- `identity_scope_terms: [{term, reason}]` (optional): a host/host_class
  term this product's own identity is allowed to name unqualified, because
  it names a consumer/access channel rather than the product boundary
  (Horologium's "coding agent (MCP)" and "Web dashboard").
- `boundary.intrinsic: {reason}`: **required** whenever `current_hosts.hosts`
  is `[]` — a product with zero current hosts by design (Octans) must say
  so explicitly; the guard treats an empty `hosts` list with no `intrinsic`
  reason as a broken record (exit 2), not a clean one.
- `forbidden_shorthand[].phrase` is checked on `identity` and `wedge`
  surfaces regardless of availability-marker stripping — it is a literal,
  unconditional ban, independent of the host-vocabulary check.

Run `python3 scripts/check_positioning.py --help` for the three run modes
(`text`, `html`, `verify-source`) and `scripts/test_check_positioning.py` for
the worked true-positive/true-negative/deliberate-exception fixtures.

## Index

| Product | File | Status |
|---|---|---|
| Fornax | `fornax.yaml` | Identity fixed HORO-1735, cites `fornax-core` README post-fix |
| Circinus | `circinus.yaml` | Identity fixed HORO-1736, cites `circinus` README + site hero post-fix |
| Libra Governor | `libra-governor.yaml` | Identity fixed HORO-1737, cites `libra-governor` PRODUCT.md post-fix |
| Ophiuchus | `ophiuchus.yaml` | Identity fixed HORO-1738, cites `ophiuchus` README post-fix |
| Horologium | `horologium.yaml` | Already compliant — no remediation ticket needed |
| Octans | `octans.yaml` | Deliberate intrinsic domain boundary — not an agent product, not generalized |
