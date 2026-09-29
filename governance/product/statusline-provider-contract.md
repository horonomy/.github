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
| `hypothetical` | no | bool | This segment describes what *would* have happened, not what did. |
| `explain_key` | no | string | Dotted key the shared explain surface resolves. |
| `order_hint` | no | int | 0–1000 within the provider. |

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
- A bare `count` is refused without its `count_label` — an unlabelled number is
  exactly the opaque abbreviation this contract replaces.
- `confidence` and `confidence_of` must be set together. A bare
  `high`/`medium`/`low` reads as risk or priority; Libra's is *preflight
  confidence*, and the contract will not let that be ambiguous.
- Not-available states are never silent. An empty `segments` array renders as
  nothing, and nothing reads as all-clear, so a provider that cannot answer
  still emits one segment saying so with a bounded reason.

### Scope is explicit

`scope` has no default and no `unknown` member, and an unrecognised value is
refused outright rather than falling back. Host-wide state silently rendering
as session-scoped is the specific failure this rule exists to prevent. The
host may render scope as a glyph, but the semantic value is always the enum,
and the deterministic text fallback (`[host]`, `[session]`, `[project]`) is
always available.

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
enum member to `availability`, `state` or `confidence_of` does not require a
bump, because those degrade (below).

**How a host reads a document it does not fully understand:**

| Input | Behaviour | Why |
|---|---|---|
| An unknown field, top level or in a segment | Ignored, and not carried into the parsed value | A host that cannot interpret a field cannot render it, and re-emitting it invents a guarantee |
| An unrecognised `availability`, `state`, or `confidence_of` | Degrades to `unknown` / `unspecified` | These have an honest unknown member; a future minor version should not fail the render |
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
