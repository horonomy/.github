# DogFood evidence canonicalization: `horonom-evidence-canon-v1` (HORO-1376)

Pins the one value ADR-0012 §6 names but never defines:
`integrity.canonicalization = "horonom-evidence-canon-v1"`. Read
[ADR-0012](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0012-dogfood-dual-profile-evidence-and-monthly-transfer-contracts.md)
first — this document only fixes the canonicalization/hashing procedure
behind that one field; it does not change the event schema (§3), the
transport state machine (§5), or any reuse decision (§11).

## Why this needs to be pinned centrally

Five products (Circinus, Eltanin, Horologium, Ophiuchus, Libra Governor)
each independently implement a DogFood evidence adapter
([HORO-1376](https://lightning-dust-mite.atlassian.net/browse/HORO-1376),
[HORO-1463](https://lightning-dust-mite.atlassian.net/browse/HORO-1463)).
§6 makes `content_hash` mandatory specifically so a receiver can dedup on
`(tenant_id, event_id)` and detect a mismatched replay of the same id — but
a hash is only comparable across products if every adapter serializes the
same logical event to the same bytes before hashing. Five independent
guesses at "the obvious way to serialize JSON" will not agree on key
ordering, whitespace, or number formatting, and the resulting
`content_hash` values will be silently incomparable. This document exists
so that never happens.

## The procedure

Given a §3 event object (already validated against the schema):

1. **Omit the `integrity` member entirely** before hashing — `integrity`
   (including `content_hash` itself) is metadata *about* the event, not
   part of the event being hashed. Hashing it would make `content_hash`
   depend on itself.
2. **Canonicalize the remaining object per [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)
   (JSON Canonicalization Scheme / JCS)**: object keys sorted by UTF-16
   code unit, no insignificant whitespace, numbers per JCS's ECMAScript
   `Number::toString` rule, strings escaped per JCS's minimal-escape rule.
3. **Encode the canonical form as UTF-8 bytes.**
4. **Hash with SHA-256.** `content_hash = { alg: "sha256", value: <lowercase hex> }`.
5. **`canonicalization = "horonom-evidence-canon-v1"`** is the literal
   string stamped on every record produced this way. A future
   incompatible change to steps 1–4 is `horonom-evidence-canon-v2` — this
   value never silently changes meaning.

This mirrors the domain-separated RFC 8785 JCS discipline
`horonomy/circinus`'s `src/circinus/domain/journal.py` already uses for its
own hash chain — reuse that vocabulary in any product that already links
an RFC 8785 implementation; do not introduce a second canonicalization
convention for the same product.

## Worked example

Event (before hashing — `integrity` omitted):

```json
{"actual_action":"allow","coverage":"full","decision_mode":"observe","destination":"local_only","dropped_count":0,"eligibility":"replayable_evidence","event_id":"3f9a...","ingested_at":"2026-09-24T10:15:03.001Z","occurred_at":"2026-09-24T10:15:03.000Z","origin_profile":"personal","payload_classification":"metadata_only","permanently_ineligible":false,"product":"circinus","product_version":"0.4.2","profile":"personal","schema_version":1,"scope_id":null,"tenant_id":null,"transport_state":"pending","would_action":"deny"}
```

RFC 8785 JCS is already the canonical form shown above (sorted keys, no
whitespace) — hash its UTF-8 bytes with SHA-256 to get `content_hash.value`.
A conformance fixture with this exact input/output pair belongs in each
adapter's test suite as its `DFC-SCHEMA-09` (content-hash stability)
anchor — see the storage contract's naming convention.

## Negative control

Reordering two top-level keys, or serializing `dropped_count: 0` as
`0.0`/`"0"`, must change nothing about the *event* but **must** be caught
by a test that (a) constructs the same logical event with keys in a
different insertion order and (b) asserts the resulting `content_hash` is
identical. If a product's JSON library does not sort keys by default
(most don't — this is precisely the coincidence §6 warns against), the
adapter must canonicalize explicitly rather than relying on library
insertion-order behavior.

## Who this is for

Every DogFood adapter under HORO-1376/HORO-1463. This is guidance and a
pinned procedure, not a shared library — mirroring the precedent set by
[`dogfood-evidence-store-test-contract.md`](./dogfood-evidence-store-test-contract.md)
and, before it,
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md).
Each product implements the four-step procedure above in its own
language using whatever RFC 8785 library (or hand-rolled equivalent, if
none exists for that language) fits its existing dependency policy — reuse
an existing canonicalization implementation already in the product
(Circinus's `journal.py` discipline) before adding a new dependency.
