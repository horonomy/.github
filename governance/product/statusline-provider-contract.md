# Statusline provider contract v1 (HORO-1564)

The wire contract by which a Horonom product contributes live status to a
coding agent's statusline **without** knowing how to rewrite that agent's
configuration or how to format the rest of the line.

North star: *add useful live product status without taking ownership of the
user's statusline.* Read
[`product-integration-safety.md`](./product-integration-safety.md) and
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)
first — this contract assumes that invariant and its ownership vocabulary as
given, and the lifecycle that installs a compositor is governed by them, not
by this document.

Normative reference implementation and its test suite:
[`scripts/statusline_contract.py`](../../scripts/statusline_contract.py),
[`scripts/test_statusline_contract.py`](../../scripts/test_statusline_contract.py).
That module is the host side. A product implements the *provider* side in its
own language — see [Implementing a provider](#implementing-a-provider).

What the host then *does* with those documents — the single configured command,
the user's original statusline as an upstream provider, the deadlines, the
iconography and the degradation ladder — is
[`statusline-host-compositor.md`](./statusline-host-compositor.md).

## Why a wire contract and not a library

The three first providers are Fornax (Rust), Libra (Rust) and Circinus
(Python). A shared library would have to be written twice, which is the same
conclusion [the ownership test contract reached](./host-config-ownership-test-contract.md#shared-helper-decision)
for the same reason. What is genuinely shared here is narrower and does not
need code to be shared: one host program that owns the single statusline slot,
and one versioned JSON document shape that every provider emits.

So the split is:

| Concern | Owner |
|---|---|
| Owning the host's single statusline slot | The Horonom statusline host, exactly one implementation |
| Rewriting host configuration | The same host, exactly one config patcher |
| Iconography, separators, widths, truncation, presentation modes | The same host |
| Formatting numbers and spans into text (`5d4h`, `2 of 14`) | The same host — a product sends seconds and a noun, never a rendered span |
| Ordering, deadlines, caching, failure isolation | The same host |
| What is true about this product right now | Each product, in its own repo |

A product contributes a read-only subcommand that prints one JSON document and
exits. It does not link the host, does not learn a settings-file format, and
does not choose a glyph.

## The document

One provider answer, one JSON object on stdout. Every field below is either a
bounded enum, a bounded integer, or a short label that has passed the privacy
allowlist. There is deliberately **no free-text field**: see
[Progressive disclosure](#progressive-disclosure).

### Top level

| Field | Required | Type | Meaning |
|---|---|---|---|
| `contract_version` | yes | int | Version of this document shape. `1` today. |
| `provider` | yes | string | Provider id, `^[a-z][a-z0-9_-]{0,31}$`. Unique per render. |
| `provider_version` | yes | string | The product's own version token, for capability negotiation and for reading evidence later. |
| `scope` | yes | enum | `host` / `session` / `project`. No default. |
| `availability` | yes | enum | `available` / `unavailable` / `unsupported` / `unknown` / `error`. |
| `segments` | no | array | Up to 4 segment objects. Keys unique within a provider. |
| `observed_at` | no | string | `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, a real UTC calendar instant. |
| `cache_ttl_seconds` | no | int | How long the host may reuse this answer. 0–60. |
| `order_hint` | no | int | 0–1000, lower renders first. Default 500. |
| `fallback_text` | no | string | Optional convenience rendering, same privacy rules, ≤120 chars. Never the only representation. |
| `clear_authority` | no | enum | `host` (default) / `provider`. See [Clear-mode selection](#clear-mode-selection). |

### Segment

| Field | Required | Type | Meaning |
|---|---|---|---|
| `key` | yes | string | `^[a-z][a-z0-9_]{0,39}$`. Stable across renders — the host keys presentation off it. |
| `state` | yes | enum | `ok` / `attention` / `warn` / `critical` / `neutral` / `unknown`. |
| `label` | yes | string | Short human prose, ≤48 chars, privacy-allowlisted. |
| `reason_code` | no | string | Bounded machine token for *why*. |
| `reason_label` | no | string | Short prose for the same reason. |
| `confidence` | no | enum | `low` / `medium` / `high`. |
| `confidence_of` | no | enum | `preflight_estimate` / `verification` / `policy_decision` / `unspecified`. |
| `age_seconds` | no | int | Freshness of the underlying reading. |
| `count` / `total` | no | int | A numerator, optionally out of a denominator. |
| `count_label` | no | string | The noun the count counts. Required whenever `count` is set. |
| `duration_seconds` | no | int | A forward-looking span — a remaining-work estimate, a budget. Not an age; `age_seconds` looks backwards. |
| `duration_label` | no | string | The noun qualifying the span (`P90`, `remaining`). Required whenever `duration_seconds` is set. |
| `hypothetical` | no | bool | This segment describes what *would* have happened, not what did. |
| `explain_key` | no | string | Dotted key the shared explain surface resolves. |
| `order_hint` | no | int | 0–1000 within the provider. |
| `clear_role` | no | enum | `exception` / `posture` / `vital` / `supporting`. See [Clear-mode selection](#clear-mode-selection). |
| `fresh_for_seconds` | no | int | How long this reading stays current. Requires `age_seconds`. |
| `semantic_state` | no | enum | `safe` / `info` / `caution` / `warning` / `critical` / `unavailable` / `neutral`. How much pressure this reading carries. See [Semantic state](#semantic-state). |

`duration_seconds` / `duration_label` were added for HORO-1569 under
`contract_version` 1: both are optional and host-side, which the [version
evolution](#version-evolution) rules already permit without a bump. They exist
so a product never formats a span itself — see the last row of the ownership
table above.

`clear_role` and `fresh_for_seconds` (HORO-1626) and `clear_authority`
(HORO-1631) arrived the same way, for the same reason one level up: a product
declares which of its facts earns the one-line summary and when that fact goes
stale, and the host still owns every character of the rendering. `semantic_state`
(HORO-1719) is the same division applied to emphasis.

### Truthfulness rules the host enforces

These are checked at construction and again on parse, so a violating payload
cannot reach the renderer:

- `unknown != healthy`. A provider whose `availability` is not `available`
  cannot carry an `ok` segment.
- `unavailable != zero`. A provider whose `availability` is not `available`
  cannot carry a `count`, because a not-running product reporting `0 blocked`
  is a false all-clear.
- `probe_failed != nothing_happened`. `error` renders at `warn` weight;
  `unsupported` and `unavailable` render as `neutral`. They are not
  interchangeable.
- The same provider cannot carry a `confidence`. A confidence is a claim about
  a result it computed, so `unavailable` plus `high` is incoherent in the most
  misleading direction available. `age_seconds` *is* still allowed, because
  "last read two hours ago, unavailable now" is true and useful.
- The same provider cannot carry a `duration_seconds`. A remaining-work estimate
  is a claim about live work, so `remaining P90 5d4h` beside an unreachable
  daemon is the same false-currency claim a `count` would be.
- A bare `count` is refused without its `count_label` — an unlabelled number is
  exactly the opaque abbreviation this contract replaces. `duration_seconds`
  carries the same rule for the same reason: a bare `5d4h` beside a task id
  could be elapsed, remaining, a budget or a timeout.
- `confidence` and `confidence_of` must be set together. A bare
  `high`/`medium`/`low` reads as risk or priority; Libra's is *preflight
  confidence*, and the contract will not let that be ambiguous.
- The same provider cannot carry a `safe` or `info` `semantic_state`. Both assert
  that a live reading was taken — `safe` that it came back good, `info` that
  something is actively watching — so either beside an unreachable daemon is the
  false all-clear in its most direct form, and it would be rendered in the colour
  a reader trusts most.
- Not-available states are never silent. An empty `segments` array renders as
  nothing, and nothing reads as all-clear, so a provider that cannot answer
  still emits one segment saying so with a bounded reason.

### Clear-mode selection

The host renders at two information depths (HORO-1626). Detail shows a
provider's snapshot as the provider ordered it. Clear shows one short executive
phrase per product, and the question Clear asks is *"if this product gets one
phrase, which of its facts earns it"* — which is a product judgement, not a
severity calculation. A task id and a delivery estimate are both `neutral` facts
of equal severity; one is an operator's whole reason to look at the line.

Three optional fields carry that judgement, and the host still owns all
formatting at both depths:

- **`clear_role`** — the part one segment plays. `posture` is the primary
  reading. `vital` is a signal that changes how the primary reads. `supporting`
  is context Clear omits. `exception` is deliberately narrow: the product is
  broken, unavailable, or genuinely waiting on the operator. It is *not* "the
  worst thing currently true" — a `warn`-state budget posture is still a
  posture, and promoting it is how a line starts claiming work is blocked when
  nothing is.
- **`fresh_for_seconds`** — when the product's own reading expires. Only the
  product knows whether five minutes is current for it. A stale `vital` is
  dropped from Clear; a stale `exception` is not, because an old unanswered
  approval request is still an unanswered approval request.
- **`clear_authority`** — `provider` means "this payload declares its whole
  Clear projection; do not infer over it".

A payload at `clear_authority: provider` is validated rather than trusted, and a
declaring payload that fails any of these is **refused**, not quietly
re-inferred:

- at least one segment — authority over an empty projection is a claim about
  nothing;
- a `clear_role` on **every** segment — a half-declared projection would need
  the rest inferred, which is the merge this mode exists to prevent, and is
  indistinguishable from a provider written before the field existed;
- at most one `posture` — two primaries is no primary;
- at least one `posture` or `exception` — otherwise the projection names no
  primary at all.

Those four rules are what make a declaration binding: the host's fallback ladder
only ever fills in a segment whose role is still unset, so a complete declaration
passes through it unchanged. The ladder itself stays the documented default for
every provider that has not declared, in this order — an action-required state is
an exception wherever it sits; then exactly one `posture`, preferring a segment
that carries a measurement over one that merely names something; then everything
remaining is `vital` if measured or emphatic and `supporting` otherwise.

One pairing rule is keyed on the declaration rather than on the authority mode:
an `exception` may be shown beside at most one still-fresh **declared** `vital`,
never an inferred one. `Approval · 12% budget left` says what the approval costs,
and only the product knows which reading qualifies its own exception; a
host-guessed reading next to a stop-work state reads as a diagnosis of the
stoppage.

An unrecognised `clear_authority` value degrades to `host`, and an unrecognised
`clear_role` degrades to undeclared — both are complete documented behaviours,
so a future provider loses a presentation hint rather than its whole health
claim. Declaring authority relaxes no other rule: a declared `exception` on an
unavailable provider still cannot carry a `count`.

### Semantic state

`state` answers *which glyph*; `semantic_state` answers *how alarming*. They are
separate fields because Libra separates them: its budget segment is `neutral` in
every healthy posture — a budget share is a posture, not a health claim — and yet
a task that has drawn 92% of its envelope is in trouble while one that has drawn
4% is not. Deriving emphasis from `state` makes those two indistinguishable.

The vocabulary is a pressure axis, and the two members in the middle of it are
the ones products get wrong:

| Token | Means | Not |
|---|---|---|
| `safe` | A live reading came back good. | A reading nobody took. |
| `info` | Something is deliberately happening and is worth noticing. | Good news. Fornax `observing` and Circinus shadow mode are *stances*, not verdicts. |
| `caution` | Pressure is building; nothing is blocked. | A warning. |
| `warning` | Action is likely needed, or something *would* have happened. | An executed outcome. Circinus's would-block lives here. |
| `critical` | The thing happened: an envelope is consumed, a claim is contradicted, a block was enforced. | A forecast. |
| `unavailable` | No reading could be taken, or the last one has expired. | `safe`. This is the whole reason the token exists. |
| `neutral` | Context carrying no pressure at all, rendered with no emphasis. | Reassurance. |

**A product declares a band only for the thing a band can describe.** Libra's
bands are a function of *utilisation of the active task's envelope*, so a payload
with no task under governance carries no budget segment to tone at all — an idle
governor at "100% remaining" of a default it is not spending against would read
as a healthy task that does not exist. The host cannot police this, because it
never sees which budget scope a reading came from; it is the product's rule, and
the host's half of it is that emphasis is **never** derived from a label. `38%
budget left` is 61% pressure, and anything that reads the number a user can see
lands two rungs below where the product put it.

Two caps the host applies over whatever a provider declared, which a provider
cannot opt out of:

- a `hypothetical` reading may not reach `critical` — the top of the scale is for
  something that happened, and a shadow-mode would-block is precisely something
  that did not. It renders as `warning`, distinct from both an enforced block
  above it and a would-allow below;
- a stale or unreachable reading may not stay reassuring — `safe`, `info` and
  `neutral` all let a reader stop looking, so they demote to `unavailable`.
  `caution` and worse survive untouched: an alarm nobody has refreshed is still
  the worst thing known, and dimming it is the one unsafe direction.

What the host deliberately does **not** do is second-guess a product's severity.
Circinus calls disconnected hooks `attention` and Fornax calls a contradicted
claim `critical`; both come through as declared. Cross-product inconsistency in
those judgements is a real open question (HORO-1651) and it stays visible rather
than being colour-corrected into an agreement that was never reached.

Colour itself is **supplemental everywhere**. A coloured line stripped of its
escapes is byte-identical to the plain line at every mode, depth and width, so
the words never depended on the colour arriving: no colour in the JSON surfaces,
none when `NO_COLOR` is set or the stream is not a terminal, none by default, and
a 16-colour terminal separates `caution` from `warning` by weight because it has
no amber. A provider that emits its own ANSI is **refused**, not stripped —
silently filtering it would leave that provider shipping a contract violation
that appears to work.

### Scope is explicit

`scope` has no default and no `unknown` member, and an unrecognised value is
refused outright rather than falling back. Host-wide state silently rendering
as session-scoped is the specific failure this rule exists to prevent. The
host may render scope as a glyph, but the semantic value is always the enum,
and the deterministic text fallback (`[host]`, `[session]`, `[project]`) is
always available.

### Segment-level scope (HORO-1602)

A single provider's document may be mostly host-wide and still have exactly
one segment that is genuinely this session's own behavioral state — Circinus
is the motivating case: `install` and `mode` are facts about the daemon, but
`latest_decision` can be the answer for *this* session specifically.
Collapsing the whole document to `Scope.SESSION` to make room for that one
segment would mislabel `install`/`mode` as session-local, which is exactly
the false-attribution failure `scope` exists to prevent in the first place;
leaving the document at `Scope.HOST` to protect those two fields would make
`latest_decision` lie the other way.

A `Segment` may therefore declare its own `scope`, narrower than the
document's. The rule is deliberately narrow in both directions:

- **It must actually narrow, never restate.** A segment whose `scope` equals
  the document's own `scope` is a `ContractViolation` on construction — the
  field exists to carry a genuine exception, and a provider that sets it to
  the same value has not declared one. Leave it unset (`None`) when a
  segment shares the document's scope, which keeps a minimal provider's wire
  payload unchanged.
- **The only validated narrowing is to `SESSION`.** A document already at
  `Scope.HOST` with a segment at `Scope.SESSION` is the real, tested case.
  A document at some other scope with a segment at some other, differing
  scope is refused — not because it is necessarily wrong, but because no
  provider needs it yet and this contract does not validate shapes nothing
  exercises.

On the rendered line, a segment whose scope differs from its group's gets
its own scope marker folded into that one reading, immediately before it —
`circinus [host] Observing… · [session] Would block`, not a second,
separately-positioned marker. The explain surface decodes the same
distinction: a reading whose segment-level scope differs from the
provider's own reports its own `scope`/`scope_token`/`scope_means`,
alongside — never instead of — the provider-level ones `explain` already
reported before this amendment.

A provider that does not set `Segment.scope` is unaffected by any of this:
the field is optional, defaults to `None`, and a document with no
segment-level scope renders and explains exactly as it did before this
section existed.

## Privacy allowlist

Provider output is allowlisted, not filtered. A label must be short prose:
**ASCII** letters and digits, spaces, and a small set of punctuation (`. , ' -
— ( ) % + ? ! ≤ ≥`). Everything else is refused.

Alphanumerics are ASCII-only for two reasons, and the second is the
load-bearing one. It removes a homoglyph channel — Cyrillic `а` is
alphanumeric. And it keeps the length bound honest: fullwidth and CJK
characters are alphanumeric too and occupy two terminal columns each, so
without the restriction a 48-character label could be 96 columns and break the
shared line. Widening this is a deliberate contract change that has to arrive
with column-aware bounds.

That charset is the structural defence, and it is load-bearing rather than
cosmetic. A value that cannot contain `/`, `\`, `:`, `=`, `@`, `~`, `$` or a
quote cannot smuggle a POSIX path, a Windows path, a URL, a shell expansion or
a `key=value` pair into the user's terminal. Two further layers catch what the
charset cannot: a secret-prefix and high-entropy check for values that are
pure alphanumerics, and a bare-hostname check for values like
`internal.corp.example`.

Never emitted, by construction rather than by review: API keys, tokens,
credentials, any secret value or any prefix/suffix/hash/length of one; prompts
or tool payloads; raw logs; internal or private URLs; file contents; raw user
data; full local paths.

Two consequences worth stating plainly, because they have already caught real
defects:

- A raw error string cannot become a `reason_label`. Exception messages
  routinely carry a socket path or a command line, and this value is rendered
  into a terminal. Use a bounded `reason_code` plus short prose.
- Free-text product fields cannot be forwarded. Fornax's `Finding.rationale`
  is transcript-derived; there is no field for it, and it would fail the label
  rules if there were.

Permanent tests in the reference suite fail if a secret-shaped or path-shaped
value enters a segment or the fallback rendering.

## Capability model

Host support is not assumed. Host capability is its own enum —
`supported` / `unsupported` / `unavailable` / `unknown` — and only
`supported` permits an install. `unsupported` is a valid, final result: a host
with no real statusline extension point is recorded as unsupported rather than
approximated with ANSI output or a background writer.

## Ownership model

Which artifacts the lifecycle may write, in ADR-0009's vocabulary:

| Artifact | Class |
|---|---|
| The user's existing statusline command/script | user-owned |
| Other keys in the user's agent settings | user-owned |
| The shared agent settings file itself | host-owned (shared artifact, never exclusively ours) |
| Keys defined by the host tool's own schema | host-owned |
| The `statusLine.command` value, once enabled | Horonom-host-owned |
| The Horonom compositor program | Horonom-host-owned |
| The provider registry | Horonom-host-owned |
| The preserved record of the original upstream statusline | Horonom-host-owned |
| A product's own provider entry in that registry | product-provider-owned |
| A product's own state store | product-provider-owned |
| A `statusLine.command` with no ownership marker | **unknown** |
| Legacy unmarked Horonom-era state | **unknown** |

Only Horonom-host-owned and product-provider-owned artifacts are writable by
the lifecycle. `unknown` is not writable — it fails safe, which is
[`LEGACY_OWNERSHIP_UNKNOWN_FAILS_SAFE`](./host-config-ownership-test-contract.md#the-14-required-contract-properties).
Note that host-owned is deliberately *not* writable either: the shared settings
file is never exclusively ours, so it is preserved and patched, never replaced.

The user's original statusline command becomes a *registered upstream
provider* — preserved verbatim, invoked first, its stdin payload passed
through byte-for-byte, its output never suppressed by a Horonom provider's
failure. It is never edited, never parsed, never rewritten, and never assumed
to live at a particular path.

## Version evolution

`contract_version` is a single integer for the document shape. The rules are
asymmetric on purpose.

**A provider may, without a version bump:** add a segment (within the bound),
add an optional field the host already knows, stop emitting an optional field,
change a `label`, or change its own `provider_version`.

**Requires a version bump:** removing or renaming a required field, changing a
field's type, changing the meaning of an existing enum member, or tightening a
bound in a way that invalidates previously valid documents. Adding a *new*
enum member to `availability`, `state`, `confidence_of`, `clear_role` or
`clear_authority` does not require a bump, because those degrade (below).

**How a host reads a document it does not fully understand:**

| Input | Behaviour | Why |
|---|---|---|
| An unknown field, top level or in a segment | Ignored, and not carried into the parsed value | A host that cannot interpret a field cannot render it, and re-emitting it invents a guarantee |
| An unrecognised `availability`, `state`, or `confidence_of` | Degrades to `unknown` / `unspecified` | These have an honest unknown member; a future minor version should not fail the render |
| An unrecognised `clear_role` or `clear_authority` | Degrades to undeclared / `host` | Both have a complete documented default, so a future provider loses a presentation hint rather than its health claim |
| A `clear_authority: provider` payload that breaks a [Clear-mode selection](#clear-mode-selection) rule | **Refused** | The provider claimed *this* contract and failed it; degrading would restore the defect the field exists to close, invisibly |
| An unrecognised `scope` | **Refused** | There is no unknown scope, and every fallback would be a specific wrong answer |
| An unrecognised `confidence` | **Refused** | Guessing between `low` and `high` is not degradation, it is fabrication |
| An unknown `contract_version` | **Refused** | The host cannot know which of the above rules still hold |

One interaction is deliberate: because an unrecognised `availability` degrades
to `unknown`, the `unknown != healthy` rule then refuses any `ok` segment in
the same document, rejecting the whole provider. A caller that catches the
violation and renders that provider as unknown is truthful; one that had
accepted the `ok` would not be.

Note that "ignored" is the correct behaviour *here* and the opposite of the
rule for host configuration, where
[`UNKNOWN_FUTURE_FIELDS_ARE_PRESERVED`](./host-config-ownership-test-contract.md#the-14-required-contract-properties)
requires unknown keys survive untouched. The difference is that a settings
file is the user's durable state, while a provider document is a transient
report regenerated on every render — there is nothing to preserve, and
carrying an uninterpretable field forward would only let it leak into a
rendering path that never validated it.

## Progressive disclosure

The statusline is a few dozen columns, so the contract does not try to make it
carry an explanation. A segment sets `explain_key`, namespaced to its own
provider (`fornax.latest_verdict`, `circinus.would_block`,
`libra.preflight`), and the shared explain surface resolves it on demand.

This is why there is no free-text segment field. Adding one would make every
provider's verbosity the host's rendering problem and would reopen the privacy
surface the allowlist closes.

The corollary: a compact token is never the only carrier of meaning. `pf:high`
becomes a `confidence` of `high` whose `confidence_of` is
`preflight_estimate`; `wb` becomes a `hypothetical` segment with a labelled
`count`/`total`. The abbreviation may still appear in a compact presentation
mode, but the semantics are in the document and the explanation is one key
away.

## Rendering is read-only

There is no field through which a provider can offer an action — no `command`,
no `callback`, no approval affordance. Rendering a statusline must not evaluate
a policy, enforce or block anything, mutate a decision, start a daemon, reach
an LLM or a cloud API, install anything, build anything, or scan a tree. A
provider that cannot answer within its budget from already-persisted state
reports `unavailable` with a reason.

Where a product's persisted configuration and its running process disagree,
the provider reports *running* truth or an explicit stale/restart-required
state. It does not report the configured value as if it were live.

## Implementing a provider

1. Add a read-only subcommand to your product's own CLI (conventionally
   `<product> statusline provider`) that prints one document and exits 0.
2. Read only already-persisted local state. No network, no daemon start, no
   build, no tree scan. Exit non-zero rather than blocking past your budget.
3. Emit `unavailable`/`unsupported`/`error` with a bounded `reason_code` when
   you have no reading. Do not exit silently.
4. Choose no glyphs, separators, or colours. Emit semantics.
5. Mirror the reference suite's truthfulness and privacy assertions in your own
   language, against your own real state values — reference this document and
   the property names by URL, do not copy its prose into your repo.

The host validates every document it parses, so a provider bug becomes that
provider rendering as unknown rather than a corrupted line or a leaked value.
That is a backstop, not a substitute for the provider's own tests.
