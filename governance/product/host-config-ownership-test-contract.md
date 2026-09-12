# Host-config ownership test contract (HORO-998)

Reusable, tool-agnostic verification contract for any Horonom product that
mutates a third-party/native host tool's configuration. This is
**guidance and a shared vocabulary, not a mandatory library** — see
[Shared-helper decision](#shared-helper-decision) for why. Read
[`product-integration-safety.md`](./product-integration-safety.md) and
[ADR-0009](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0009-product-integration-safety-non-destructive-host-configuration.md)
first — this document assumes that invariant and ownership-class
vocabulary as given.

## Who this is for

A product session implementing or auditing an install/repair/upgrade/
uninstall lifecycle against host-tool configuration (HORO-1001). Apply
these named properties as real tests in your product's own test suite, in
your own language/framework — this contract does not ship executable code,
it ships the property names, the fixture shape they need, and the negative
control that proves each property is actually being checked.

## The 14 required contract properties

Each property below is named so a product's test suite and a certification
matrix (HORO-999/HORO-1003) can refer to it by one consistent ID.

| ID | What it proves |
|---|---|
| `PREEXISTING_UNOWNED_CONFIG_IS_PRESERVED` | Install does not alter any key/entry that existed before install and isn't product-owned. |
| `UNKNOWN_FUTURE_FIELDS_ARE_PRESERVED` | A field the product's own schema doesn't recognize (simulating a newer host-tool version) survives every lifecycle op byte/semantically unchanged. |
| `OTHER_PRODUCT_CONFIG_IS_PRESERVED` | A second product's marked entry in the same shared file survives this product's full install→repair→upgrade→remove sequence. |
| `REPEATED_INSTALL_IS_IDEMPOTENT` | Running install twice in a row produces the same on-disk state as running it once (no duplicate entries, no drift). |
| `POST_INSTALL_USER_CHANGES_SURVIVE_REPAIR` | A hand-edit to an *unrelated* key made after install is still present after repair. |
| `POST_INSTALL_USER_CHANGES_SURVIVE_UPGRADE` | Same, across an upgrade/reconcile operation. |
| `REMOVE_TOUCHES_ONLY_PRODUCT_OWNED_STATE` | Uninstall's diff contains only this product's own keys/entries/files — the `A+B+C → A+C` invariant, checked structurally, not just "looks fine." |
| `STALE_RECEIPT_CANNOT_OVERWRITE_CURRENT_CONFIG` | Running repair/rollback from an old receipt never reverts state that changed *after* that receipt was written. |
| `SHARED_CONFIG_IS_NEVER_DELETED_BY_DEFAULT` | Uninstall never deletes a file the product doesn't exclusively own, even if the product created that specific entry inside it. |
| `MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION` | A config file that fails to parse causes install/repair to abort before any write — assert the file's bytes are unchanged after the attempt. |
| `LEGACY_OWNERSHIP_UNKNOWN_FAILS_SAFE` | A receipt/state file predating ownership metadata (or missing it) does not enable a destructive automatic action. |
| `CONCURRENT_CHANGE_DOES_NOT_CLOBBER` | A file modified between plan and apply is detected (fingerprint/hash mismatch) and the operation aborts or re-plans — never blind-writes. |
| `FAILED_MUTATION_IS_ATOMIC` | An interrupted/failed write leaves either the pre-write state or the fully-applied post-write state — never a partial/corrupt file. |
| `HOST_TOOL_REMAINS_USABLE_AFTER_EACH_LIFECYCLE_STEP` | After every install/repair/upgrade/remove step, the host tool itself can still parse/start/launch normally — a preservation test that "reads back clean" but breaks the tool doesn't count. |

A product doesn't need all 14 to exist as separate test functions — several
naturally collapse into one parameterized test — but each ID must be
traceable to a specific assertion your team can point to, not asserted by
name only in documentation.

## Rich fixture requirements

A fixture built from `{}` proves nothing — none of the 14 properties above
are distinguishable from a no-op against an empty file. Every fixture used
to exercise this contract must include, at minimum:

- scalar keys (strings, numbers, booleans)
- at least one nested object
- at least one array/list (and, where the host format supports it, more
  than one entry in that list belonging to *different* owners)
- multiple hook/plugin-style entries if the surface is a hook/list config
- at least one unknown/future field your product's schema has never seen
- at least one path/URL/env-var-shaped value (these are the values most
  likely to be mangled by a naive re-serialization pass)
- at least one marker belonging to an unrelated product
- at least one organization/MDM-managed-shaped value, where the surface
  supports one
- at least one value that is genuinely this product's own
- at least one value changed *after* install (to drive the
  `POST_INSTALL_*_SURVIVE_*` properties)
- at least one malformed/partially-valid variant (truncated, wrong type,
  syntax error) to drive `MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION`
- where the host format is textual and format/order/comments are load-bearing
  to a human editing that file by hand (e.g. a shell rc file, a commented
  YAML/TOML), a fixture that specifically checks those survive too — not
  every format makes this material (a plist's XML formatting isn't
  typically hand-curated; a `.zshrc`'s comments and ordering usually are)

## Two real worked tool shapes (HORO-999 evidence, not hypothetical)

This contract requires concrete examples across at least two structurally
different host-config shapes, per its own acceptance criteria. HORO-999's
real inventory already supplies two:

### Shape 1 — JSON key/list merge (Circinus → Claude Code `.claude/settings.json`)

Circinus's `src/circinus/adapters/claude_code/settings_file.py` is the
strongest existing real-world implementation of this contract in the
company today. Its `merge_install`/`merge_uninstall` functions merge at
the `hooks.<event>` array level, mark every entry it owns with a
`_circinus: <version>` key, and fall back to a narrow literal-command-match
only when that marker has been stripped by a foreign editor. Its own test
suite already demonstrates real, passing instances of:
`PREEXISTING_UNOWNED_CONFIG_IS_PRESERVED` and
`OTHER_PRODUCT_CONFIG_IS_PRESERVED` (`test_apply_uninstall_removes_only_circinus_hooks`,
asserting a foreign matcher group survives uninstall byte-for-byte),
`CONCURRENT_CHANGE_DOES_NOT_CLOBBER` (`test_apply_install_detects_concurrent_modification`),
and a form of `FAILED_MUTATION_IS_ATOMIC` via its backup-then-atomic-replace
sequence. HORO-1020 tracks its remaining gaps against this same contract
(backup retention, TOCTOU-ordering, an ownership-fallback edge case) —
those gaps are exactly the properties above that aren't yet covered, not a
reason to distrust the ones that are.

### Shape 2 — whole-file, exclusively-namespaced XML (Glomeris → macOS `launchd` plist)

Glomeris's `src/platform/macos/launchd.rs` writes a whole `.plist` file at
`~/Library/LaunchAgents/com.glomeris.monitor.plist`. This is a legitimate
case of mutation-preference-order tier 4 (whole-file replacement) being
correct rather than a violation: the file is uniquely keyed to Glomeris's
own `Label`, so there is no *other* product's data that could ever live in
it — `OTHER_PRODUCT_CONFIG_IS_PRESERVED` is trivially satisfied by
construction, not by merge logic. What this shape still owes the contract,
and currently doesn't have (HORO-1021), is `REPEATED_INSTALL_IS_IDEMPOTENT`
(today, a second install silently discards a hand-edited `StartInterval`),
`FAILED_MUTATION_IS_ATOMIC` (today, a direct `fs::write`, not
temp-file-then-rename), and `STALE_RECEIPT_CANNOT_OVERWRITE_CURRENT_CONFIG`
in the sense of "no backup exists to even attempt a stale restore, so
recoverability rests entirely on the generator being pure" — acceptable
today only because the generated content has no user-supplied customization
path yet; the moment one is added, that assumption breaks.

These two shapes were chosen because they're real, not constructed:  one is
a shared multi-tenant JSON file requiring key-level ownership tracking, the
other is a single-tenant XML file where whole-file ownership is legitimate
by construction. A product auditing a third shape (TOML, YAML, a SQLite
database, an opaque managed API) should identify which of these two closer
resembles its situation and start from that reasoning, not invent a third
pattern from scratch.

## Mutation / negative controls

A contract that only tests the happy path proves nothing about whether it
would catch a regression. For each property above, your suite should also
contain (or be run once against) a **deliberately broken** implementation
and confirm the corresponding test **fails**:

| Deliberately broken behavior | Property it must break |
|---|---|
| Whole-file replacement of a shared config | `PREEXISTING_UNOWNED_CONFIG_IS_PRESERVED`, `OTHER_PRODUCT_CONFIG_IS_PRESERVED` |
| Replacing an entire hooks/list collection instead of merging | `OTHER_PRODUCT_CONFIG_IS_PRESERVED`, `POST_INSTALL_USER_CHANGES_SURVIVE_REPAIR` |
| Parse failure → fall back to an empty/default config | `MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION` |
| Stale-backup restore on repair/rollback | `STALE_RECEIPT_CANNOT_OVERWRITE_CURRENT_CONFIG`, `POST_INSTALL_USER_CHANGES_SURVIVE_REPAIR` |
| Remove deletes the whole shared file | `SHARED_CONFIG_IS_NEVER_DELETED_BY_DEFAULT` |
| Dropping unknown fields during deserialize/re-serialize | `UNKNOWN_FUTURE_FIELDS_ARE_PRESERVED` |
| No fingerprint/hash check before write | `CONCURRENT_CHANGE_DOES_NOT_CLOBBER` |
| Direct write with no temp-file/rename | `FAILED_MUTATION_IS_ATOMIC` |
| Treating a legacy receipt with no ownership metadata as fully trusted | `LEGACY_OWNERSHIP_UNKNOWN_FAILS_SAFE` |

If you can't point to a place your suite would actually fail when one of
these is deliberately reintroduced, the corresponding property isn't
proven yet — it's asserted.

## Tool-shape portability — do not force one representation

The contract's properties are semantic; the fixture/adapter shape is not
forced to be uniform. Expected variation:

- **JSON/TOML/YAML structured config** — key-level merge is usually
  natural (Shape 1 above).
- **List/registry/plugin config** — ownership tracking happens per-entry,
  usually via a marker field or an exact-match fallback (Shape 1's hooks
  arrays).
- **Project-local vs. user-global scope** — the *same* merge logic often
  applies to both; what differs is blast radius (a bug in user-global
  scope affects every project on the machine — see Circinus's own
  scope-based risk tiering in HORO-999's inventory).
- **Product-owned drop-in files** — the easiest case: the whole file is
  legitimately owned, same reasoning as Shape 2, *provided* the file is
  truly exclusive (verify this, don't assume it from a plausible-looking
  filename).
- **Host-managed/MDM files** — mutation may be forbidden outright; the
  correct "test" here is often "assert this surface classifies as
  `MACHINE_HOST_MANAGED_WRITE` or is never touched," not a merge test.

Do not build one lossy AST/dict representation and force every host
format through it — a plist is not a JSON object with different syntax,
and treating it as one is how format-specific guarantees (XML structure,
binary plist variants, code-signing-adjacent quirks) get silently dropped.

## Shared-helper decision

HORO-998 requires this decision be made explicitly after at least two real
product audits, and HORO-999 now supplies exactly that: Circinus (Python,
JSON, project+user scope, already mature) and Glomeris (Rust, whole-file
XML, single scope). **Decision: no shared cross-product library is
justified yet — this contract stays as portable guidance/properties,
never shared executable code.**

Reasoning: the two real implementations are in different languages
entirely (Python vs. Rust), solving structurally different problems
(shared-file key-level merge vs. exclusively-owned whole-file). A shared
library would have to either (a) be reimplemented per language anyway,
defeating the point, or (b) impose a common intermediate representation
that risks exactly the lossy-AST failure mode this document just warned
against. The actual value this contract provides — and the actual value
two more product audits would add before revisiting this decision — is
the shared vocabulary (the 14 property IDs) and the shared reasoning
pattern (which mutation-preference tier applies, whether whole-file
ownership is provably exclusive), not shared code. Revisit this decision
if a third real audit finds two products in the **same language** solving
the **same shape** of problem — that's the concrete signal that would
justify a narrow, ownership-aware helper (planning/diff/conflict/
atomic-write/read-back primitives only, per HORO-998's own scoping
constraint), not before.

## Consuming this contract without forking it

Reference these 14 property IDs and this document by URL from your
product's own test files/docs (a comment naming the ID next to the
assertion that proves it is enough) — do not copy this document's prose
into your repo. If your product's audit finds a real gap, file it as your
own product-owned ticket (per HORO-999/HORO-1001) and link it here; this
document itself only changes when the contract's shared properties
change, not per-product remediation progress.
