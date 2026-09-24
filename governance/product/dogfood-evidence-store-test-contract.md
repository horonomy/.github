# DogFood evidence store test contract (HORO-1372)

Reusable, tool-agnostic verification contract for the **local durable
evidence store** layer of the DogFood Readiness campaign. This is
**guidance and a shared vocabulary, not a mandatory library** — see
[Shared-helper decision](#shared-helper-decision) for why, mirroring the
precedent set by
[host-config-ownership-test-contract.md](./host-config-ownership-test-contract.md).
Read
[ADR-0012](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0012-dogfood-dual-profile-evidence-and-monthly-transfer-contracts.md)
first — this document assumes its frozen event schema (§3), transport
state machine (§5), and reuse decisions (§11) as given, and only narrows
them to the storage-layer subset that
[HORO-1372](https://lightning-dust-mite.atlassian.net/browse/HORO-1372)
scopes: durable bounded storage, retention, and observable overflow. The
monthly-transfer worker mechanics (window scheduling, batching, backoff)
are [HORO-1373](https://lightning-dust-mite.atlassian.net/browse/HORO-1373)'s
concern, not this document's.

## Who this is for

A product adding or auditing the local evidence store beneath a DogFood
adapter (HORO-1376): Circinus, Eltanin, Horologium, Ophiuchus, Libra
Governor. Apply these named properties as real tests in your product's own
test suite, in your own language/framework — this contract does not ship
executable code, it ships the property IDs, the fixture shape they need,
and the negative control that proves each property is actually checked.
Eridanus needs no new work here — see
[Reference implementation](#reference-implementation-eridanus).

## Vocabulary — reuse ADR-0012's, do not invent a second one

HORO-1372's own description uses the phrase "pending -> leased/inflight ->
acknowledged." That is the **same** state machine as ADR-0012 §5's
`transport_state` (`pending → inflight → acknowledged`, with `expired` and
`poison` as the two non-happy-path terminals) — "leased" names the
mechanism (an inflight record is held under a time-bounded lease with an
owner id and heartbeat, exactly as ADR-0012 §9's transfer-lock
requirement describes for the worker layer), not a fourth state. Do not
introduce a `leased` value distinct from `inflight` in a product's schema;
name the column/field `transport_state` and give `inflight` records a
`lease_expires_at` timestamp.

## The required contract properties

Each property is named so a product's test suite and the campaign's own
conformance tracking (HORO-1381) can refer to it by the same ID used in
[ADR-0012 §16](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0012-dogfood-dual-profile-evidence-and-monthly-transfer-contracts.md#16-test-scenario-ids-requires-sign-off-before-horo-1381).
This document only covers the storage-layer subset of that full list —
transfer-worker properties (`DFC-XPORT-01/02/03/05/08/09/10/13/14/15`) stay
with HORO-1373.

| ID | What it proves |
|---|---|
| `DFC-REUSE-01` | Store-once / deliver-verbatim: bytes delivered are byte-identical to bytes originally stored — no re-derivation, re-serialization, or lossy round-trip between append and read-back. |
| `DFC-REUSE-02` | Restart durability: `pending` and `inflight` records survive process death and are recovered on restart; an `inflight` record whose worker died is **never** assumed delivered — it returns to `pending`. |
| `DFC-REUSE-03` | Retention is independent of acknowledgement: an `acknowledged` record is retained per the byte-cap/retention-target policy, not deleted on ack; a `pending`/`inflight` record is never purged early to make room without first surfacing a gap (see `DFC-RETN-03`). |
| `DFC-XPORT-04` | Lease/window identity persists across restart — an `inflight` record's `lease_expires_at` and owning worker id are read back unchanged, not recomputed from the current clock. |
| `DFC-XPORT-06` | A stale lease (owner crashed mid-send) is reclaimed by **timeout expiry**, never by force-unlock at startup — recovery is passive (the next lease-check tick notices `lease_expires_at` has passed) not an unconditional "assume everything inflight is actually pending" reset on boot. |
| `DFC-XPORT-11` | Partial acknowledgement: when a batch is partially acked, acked members move to `acknowledged` and unacked members return to `pending` — an already-acked member is never re-sent, and an unacked member never stays stuck in `inflight` past its lease. |
| `DFC-XPORT-12` | A non-retryable rejection moves exactly the rejected record to `poison`, individually — it never blocks or corrupts sibling records in the same batch. |
| `DFC-FAIL-01` | Disk-cap reached: new appends stop **before** the filesystem actually fills; a `coverage=gap`/`gap_reason=disk_cap` marker is emitted with a nonzero `dropped_count`; **no unacknowledged record is deleted** to relieve pressure. |
| `DFC-FAIL-02` | Store corruption on read/open: the store fails closed (refuses further writes to the damaged segment), never truncates or silently recreates itself empty; the damaged span is quarantined and a gap marker covers exactly that span. |
| `DFC-FAIL-03` | A migration that cannot preserve `origin_profile`, `permanently_ineligible`, or `transport_state` **aborts** rather than defaulting those fields; the affected record is quarantined, not silently downgraded. |
| `DFC-FAIL-04` | An interrupted/killed migration is resumable and leaves the store in either the pre-migration or fully-migrated state — never a partially-migrated state accepted as healthy. |
| `DFC-RETN-01` | Under a disk cap smaller than 90 days of realistic volume, the cap wins: retention is bounded by the cap, and the shortfall is recorded as gaps, never silently absorbed by dropping the oldest data with no marker. |
| `DFC-RETN-02` | Operator/dashboard-facing status reports gap count, `dropped_count`, and **oldest pending record age** — these must be real queryable values, not hardcoded zero. |
| `DFC-RETN-03` | An unacknowledged (`pending`/`inflight`) record is never deleted under capacity pressure — only `acknowledged`, `expired`, or `poison` records are eligible for space reclamation, and only per the retention policy, never as an emergency measure to free space for new appends. |
| `DFC-RETN-04` | `expired` and `poison` records remain locally visible and locally queryable (not hard-deleted) and are counted in operator status, distinct from `acknowledged`. |
| `DFC-CONC-01` *(new — no ADR-0012 ID; storage-specific)* | Two concurrent writers (e.g. two hook invocations racing) never corrupt the store or silently drop one writer's append — both appends are durably present, or one deterministically fails loudly (never a torn write). |
| `DFC-CONC-02` *(new)* | A crash mid-append (process killed between "write started" and "commit") leaves the store in a state where, on reopen, either the full record is present or none of it is — never a half-written record readable as valid. |

A product doesn't need all of these as separate test functions — several
naturally collapse into one parameterized test against the store's actual
transaction boundary — but each ID must be traceable to a specific
assertion your team can point to, not asserted by name only in
documentation.

## Rich fixture requirements

A fixture built from a single clean append proves nothing — none of the
properties above are distinguishable from a no-op against a trivial store.
Every fixture used to exercise this contract must include, at minimum:

- at least one record in each `transport_state` (`pending`, `inflight`
  with a live lease, `inflight` with an **expired** lease, `acknowledged`,
  `expired`, `poison`)
- at least one record whose `coverage != full` (a real gap marker with a
  populated `gap_reason` and nonzero `dropped_count`)
- realistic byte volume — a fixture of 5 tiny records does not exercise
  disk-cap or 90-day-retention behavior; use a generator that produces the
  byte/record volume a real 31-day personal-observe window would produce,
  not a handful of literals
- a fixture aged with **virtual time**, not wall-clock sleeps — HORO-1372's
  own acceptance requires 31-day-plus and missed-window scenarios, which
  are not something a real test suite should wait 31 real days to run
- a corrupted variant (truncated file, torn write, invalid record framing)
  to drive `DFC-FAIL-02`
- a migration fixture carrying an old-schema record populated with
  `origin_profile`/`permanently_ineligible`/`transport_state` already set,
  to drive `DFC-FAIL-03`/`DFC-FAIL-04`
- a concurrent-writer harness (two real threads/processes appending
  against the same store handle, not a mocked lock) to drive `DFC-CONC-01`

## Reference implementation: Eridanus

[Eridanus](https://github.com/horonomy/eridanus)'s `crates/eridanus-edge/src/buffer.rs`
is the campaign's reference implementation of this contract (ADR-0012
§11.1): WAL-mode SQLite, store-once/deliver-verbatim, with its own
restart-durability test suite (`crates/eridanus-edge/tests/restart_durability.rs`).
It needs **no new work under HORO-1372** — the ticket's own scope note is
explicit: "do not refactor all seven products or duplicate Eridanus
HORO-480." A product adopting this contract should read Eridanus's
existing tests as a worked example of `DFC-REUSE-01`/`DFC-REUSE-02` before
writing new tests from scratch, the same way the host-config contract
points to Circinus's and Glomeris's real implementations rather than a
hypothetical one.

## Mutation / negative controls

For each property above, a suite should also contain (or be run once
against) a **deliberately broken** implementation and confirm the
corresponding test **fails**:

| Deliberately broken behavior | Property it must break |
|---|---|
| Delete unacknowledged records when the disk cap is hit | `DFC-FAIL-01`, `DFC-RETN-03` |
| Truncate/recreate the store empty on a corrupt read instead of quarantining | `DFC-FAIL-02` |
| Migration silently defaults a missing `origin_profile` to `personal` instead of aborting | `DFC-FAIL-03` |
| Force-unlock all `inflight` leases unconditionally on every boot | `DFC-XPORT-06` |
| Re-send an already-`acknowledged` member on the next partial-ack cycle | `DFC-XPORT-11` |
| One poisoned record blocks its whole batch from proceeding | `DFC-XPORT-12` |
| Report a hardcoded `0` for oldest-pending-age regardless of actual store contents | `DFC-RETN-02` |
| Hard-delete `expired`/`poison` records instead of retaining them | `DFC-RETN-04` |
| No transaction boundary around append — a killed process can leave a half-written record | `DFC-CONC-02` |
| Last-writer-wins with no locking under concurrent append | `DFC-CONC-01` |

If you can't point to a place your suite would actually fail when one of
these is deliberately reintroduced, the corresponding property isn't
proven yet — it's asserted.

## Tool-shape portability — do not force one representation

Expected variation across the five adapter products, per the Phase A
register and ADR-0012 §11.1/§11.3:

- **SQLite-backed stores** (Circinus, Ophiuchus, Libra Governor) — the
  natural fit is a `transport_state` column plus a `lease_expires_at`
  column, with WAL mode for crash-atomicity. This is structurally the
  closest to Eridanus's own shape.
- **Append-only NDJSON** (Eltanin) — by explicit product design this store
  has no upload/receiver leg (ADR-0012 §12), so `DFC-XPORT-*` properties
  in this contract don't apply to it at all; only `DFC-REUSE-*`,
  `DFC-FAIL-*`, `DFC-RETN-*`, and `DFC-CONC-*` are relevant, scoped to
  local capture durability rather than transport.
- **Postgres** (Horologium) — has no upload leg either (own-scope
  product); this contract's transport-state properties don't apply. Its
  relevant subset is the same as Eltanin's.
- **Zero-network by design** (Libra Governor's `evidence_report_cmd.rs`,
  ADR-0012 §11.4) — this contract's `DFC-XPORT-*` properties never apply
  to that specific module; adding a `transport_state` column to a store
  this module reads from does not itself violate the zero-network guard,
  since the guard is about network *symbols*, not about tracking
  transport state locally for a future adapter outside that module
  boundary.

Do not build one lossy cross-language ORM/store abstraction to unify
these — a Python SQLite store and a Rust SQLite store solving the same
shape of problem is exactly the signal that would justify a narrow shared
helper (see below), but forcing NDJSON and Postgres through the same
abstraction as SQLite would repeat the lossy-AST failure mode the
host-config contract already warned against.

## Shared-helper decision

Per ADR-0012 §11.1, **literal `eridanus-edge` crate reuse is optional and
Rust-only**; it is not available to Circinus or Ophiuchus (Python,
confirmed via `pyproject.toml`/`uv.lock` at their repo roots, no
`Cargo.toml`). Mirroring the host-config contract's own precedent:
**no shared cross-language library is mandated by this document.** The
contract stays as portable property IDs and fixture requirements; a
product may adopt the `eridanus-edge` crate literally if it is Rust-hosted
and chooses to, but that is a product-local decision, not a campaign
requirement.

If a future audit finds two products in the **same language** independently
solving the **same shape** of storage problem (e.g. two Rust products both
hand-rolling SQLite lease-recovery logic), that is the concrete signal that
would justify a narrow, language-scoped shared helper — not before.

## Consuming this contract without forking it

Reference these property IDs and this document by URL from your product's
own test files/docs (a comment naming the ID next to the assertion that
proves it is enough) — do not copy this document's prose into your repo.
If your product's audit finds a real gap, file it as your own
product-owned ticket under HORO-1372/HORO-1376 and link it here; this
document itself only changes when the contract's shared properties
change, not per-product remediation progress.
