# DogFood conformance method: vocabulary and check shape (HORO-1381)

Defines how [ADR-0012](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0012-dogfood-dual-profile-evidence-and-monthly-transfer-contracts.md)'s
§16 `DFC-<AREA>-<NN>` test-scenario vocabulary is classified, how
time-dependent scenarios are proven without waiting on real wall-clock time,
and the general shape of the cross-product harness that will eventually
check conformance. This document is vocabulary and method only — it
carries no product-specific findings and no specific test results. The
per-product, per-ID reconciliation register is a separate document in
`horonomy/internal-docs` (private, since it may reference internal
implementation gaps).

Read ADR-0012 first — this document only fixes the *method* by which §16's
79 named scenario IDs are classified and eventually checked; it does not
change the event schema (§3), the transport state machine (§5), the
eligibility rules (§7–8), or any reuse decision (§11).

## Why this needs to be pinned centrally

Five products (Circinus, Eltanin, Horologium, Ophiuchus, Libra Governor)
each independently implement a DogFood evidence adapter
([HORO-1376](https://lightning-dust-mite.atlassian.net/browse/HORO-1376),
[HORO-1463](https://lightning-dust-mite.atlassian.net/browse/HORO-1463)),
plus a sixth product (Eridanus) hosts the reference implementation and
receiver ([HORO-1373](https://lightning-dust-mite.atlassian.net/browse/HORO-1373),
[HORO-1465](https://lightning-dust-mite.atlassian.net/browse/HORO-1465)).
Six independent test suites, in two languages, will not converge on the
same idea of "proven" without a shared vocabulary fixed in one place —
exactly the failure mode ADR-0012 itself exists to prevent, one layer up.
This document is that fixed point for *conformance classification*, the
way `dogfood-evidence-canonicalization-v1.md` is the fixed point for
*hashing*.

## Classification vocabulary

Every `DFC-<AREA>-<NN>` scenario ID is classified using exactly one of the
following labels. A reconciliation register must use these labels
verbatim — no synonyms, no partial credit between them.

| Label | Meaning |
|---|---|
| `PROVEN` | A real test exists, tagged with this DFC ID, and is currently passing. The property is demonstrated end to end by an executable assertion, not by inspection or by design intent. |
| `PROVEN (virtual-time)` | Same bar as `PROVEN`, for a scenario whose ADR wording describes a real elapsed period (a monthly window, a multi-day sleep, a long-elapsed-time observation). The test proves the property using a virtual or injected clock, not a real wall-clock wait. See "Virtual time" below — this label must never be silently upgraded to plain `PROVEN`. |
| `UNTAGGED-PROVEN` | The underlying property is genuinely exercised by a passing test somewhere in the merged code, but that test carries no DFC-ID tag or comment connecting it to this vocabulary. The property is proven; the traceability link is missing. |
| `TAGGED-UNPROVEN` | A DFC ID is referenced somewhere (a test name, a comment, a docstring) but no test actually proves the scenario end to end — e.g. the test exercises a related but weaker property, is a stub, or asserts the *absence* of a capability rather than the scenario's own pass condition. |
| `UNTESTABLE-AS-SPECIFIED` | No mechanism exists in the product to make this scenario provable, and none is planned under the product's current design. This is an honest architectural finding, not a test-coverage gap — e.g. a scenario presupposes a capability (an enrolled-scope concept, an enforce mode reachable in production) that product does not have. |
| `BLOCKED-UNKNOWN` | The scenario cannot be run today because it depends on an external fact that is itself unresolved (a deploy status, an access grant) rather than on anything the product's own code could change. ADR-0012 §16 names exactly one ID this way by design (`DFC-ADAPT-10`, gated on a hosted-receiver deploy status) and states it stays `SKIPPED` until that Phase A `UNKNOWN` resolves — it is not assumed runnable in the meantime. |
| `NOT-APPLICABLE` | The scenario's precondition does not exist in this product at all (e.g. a tool/command-execution record type in a product with no execution concept). Distinct from `UNTESTABLE-AS-SPECIFIED`: that label means the property *should* apply and can't be proven; this label means the scenario was never about this product's mechanism in the first place. |
| `NEW` | No existing merged test maps to this scenario at all — most commonly a cross-product scenario (e.g. "do independently-pinned `content_hash` values from five adapters agree given the same canonical input") that only a shared harness, not any single product's own suite, can prove. A reconciliation register lists these as not yet started; it does not invent a result for them. |

A register entry never mixes labels ("mostly PROVEN") and never rounds a
weaker label up because the gap seems small. `TAGGED-UNPROVEN` and
`UNTESTABLE-AS-SPECIFIED` are both legitimate, reportable findings — the
point of this vocabulary is to make the honest gaps visible, not to
minimize their count.

## Virtual time — never a real wall-clock wait, never overstated

Several ADR-0012 scenarios describe a real elapsed period: a monthly
transfer window, a host asleep across a window boundary, "personal observe
across a long elapsed period" (`DFC-MODE-03`). None of these are ever
proven by actually waiting — this campaign's standing rule is that a
time-dependent scenario is proven with an injected or virtual clock, never
a real sleep, and a test built this way is *never* described as a plain
`PASS` or `PROVEN` without the `(virtual-time)` qualifier.

Rules for a time-dependent conformance check:

1. **Inject the clock.** The adapter or worker under test must accept a
   clock/time source as a parameter or trait object it does not construct
   itself, so a test can substitute a fake clock that jumps instantly
   between instants.
2. **Prove the transition, not the duration.** A monthly-window scenario is
   proven by asserting the state machine's behavior at the two instants
   that bound the window (just before, just after), not by running the
   worker for a simulated month of ticks. A missed-window scenario
   (`DFC-XPORT-07`, `DFC-FAIL-09`) is proven by jumping the fake clock past
   several window boundaries and asserting the catch-up-cap and
   skipped-window bookkeeping, not by advancing tick by tick.
3. **Label every such test `PROVEN (virtual-time)`, never `PROVEN`.** The
   qualifier is part of the classification, not a footnote — a reader of a
   register scanning only the classification column must be able to tell
   which scenarios have and have not run over real wall-clock time.
4. **Never claim a human month-long pass has actually happened.** No
   document, dashboard, or status field produced by this campaign may
   assert that a real month-long run occurred unless one genuinely did.
   `PROVEN (virtual-time)` is the correct and sufficient claim; it is not a
   weaker substitute awaiting a future real-time confirmation run — for
   v1 conformance purposes it is the whole claim.
5. **No exactly-once language.** ADR-0012 §14 is explicit that transfer is
   at-least-once, never exactly-once, made safe by durable receiver ACK
   and tenant-scoped dedup on `(tenant_id, event_id)`. Any scenario
   description or conformance-check assertion that touches delivery
   semantics (duplicate delivery, partial ack, retry/backoff) must phrase
   its pass condition in at-least-once + dedup terms. A check or a register
   entry that describes delivery as "exactly once" is wrong regardless of
   what the underlying test actually asserts, and must be corrected.

## The conformance harness — shape, not implementation

Eventually, conformance across all six products should be checkable in one
place rather than re-derived by reading six repos by hand each time. The
shape that satisfies ADR-0012 §14's non-goals (no new shared SaaS, broker,
gateway, daemon, or runtime) is:

- **Read-only.** The harness never writes to a product's store, never
  calls a product's uploader against a real destination, and never
  provisions or depends on any live receiver. It observes and validates
  structure only.
- **Shells out to each product's own already-merged `dogfood-evidence` CLI
  (or equivalent local command).** Each of the five adapter products
  already ships a way to produce or inspect its own DogFood evidence
  locally under HORO-1376/HORO-1463; Eridanus's reference implementation
  and Gateway are exercised the same way, through their own existing
  fixture/CLI surfaces. The harness does not reimplement any product's
  adapter logic — it drives the binary that already exists and inspects
  what comes back.
- **Validates structurally.** Given a product's own emitted event(s), the
  harness checks conformance to the frozen §3 schema, the §6 integrity
  envelope (including recomputing `horonom-evidence-canon-v1` per
  `dogfood-evidence-canonicalization-v1.md` and comparing `content_hash`),
  the §7/§8 eligibility rules, and the §16 pass conditions that are
  checkable from structure alone. It does not re-run each product's own
  test suite — that stays owned by the product repo — it checks the
  *outputs* against the shared contract.
- **No portfolio-wide runtime.** The harness is a script or small
  standalone check suite, not a service. It has no daemon, no persistent
  process, no database of its own, and nothing else depends on it being
  up. It runs on demand (locally, or as a CI job in whichever repo hosts
  it) and produces a report.
- **Deletable without touching any product code.** Because it only shells
  out to interfaces each product already owns and ships independently,
  removing the harness removes nothing from any of the six product repos.
  It is additive tooling, not a dependency any product takes on.
- **No new repo.** The harness lives inside an existing repo (its exact
  home is an implementation decision for whoever picks up building it,
  not fixed by this document) rather than establishing a seventh
  DogFood-adjacent codebase.

This section fixes the shape already decided for the harness; it does not
redesign it and does not commit to a specific repo, language, or schedule
for building it.

## Who this is for

Every DFC-ID classification produced under HORO-1381, and any future
conformance check built against ADR-0012 §16. This is guidance and a
pinned vocabulary, not a shared library — mirroring the precedent set by
[`dogfood-evidence-canonicalization-v1.md`](./dogfood-evidence-canonicalization-v1.md),
[`dogfood-evidence-store-test-contract.md`](./dogfood-evidence-store-test-contract.md),
and, before them,
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md).
Each product's own test suite keeps using its own language and tooling;
this document only fixes what "proven" means and how virtual time is
represented, so a reconciliation register built from six independent repos
still reads as one honest, comparable account.
