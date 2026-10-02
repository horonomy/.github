# Execution identity contract v1 (HORO-1597/1598)

The canonical, vendor-neutral envelope that names *which host, session, agent,
turn and event* a piece of Horonom state belongs to — used by statusline
providers, telemetry, evidence, caches, audits and future dashboards across
Fornax, Circinus and Libra.

North star: *a host-wide fact must never be presented or stored as if it
belonged to a specific session or agent.* This contract exists because Founder
DogFood (HORO-1563/1597) found two concurrent Claude Code sessions on one
machine rendering the same behavioral state, since the existing integration
was primarily host-scoped. Read
[`statusline-provider-contract.md`](./statusline-provider-contract.md) first —
this document reuses its `scope`/version-evolution/privacy vocabulary
deliberately, and the differences from it are called out explicitly below
rather than left implicit.

Normative reference implementation and its test suite:
[`scripts/execution_identity.py`](../../scripts/execution_identity.py),
[`scripts/test_execution_identity.py`](../../scripts/test_execution_identity.py).
A product implements its own capture logic in its own language against this
document — see [Implementing a capturer](#implementing-a-capturer). As with
the statusline contract, this is deliberately *not* a shared library: Fornax
and Libra are Rust, Circinus is Python, and a wire/data contract is what lets
three languages agree without one of them depending on another's runtime.

## Relationship to the statusline contract's `scope`

The statusline contract's `scope` (`host` / `session` / `project`) names
**what breadth of state one provider's whole status document describes**, and
deliberately has no `unknown` member — an unrecognised value is refused, not
guessed. This contract's `Scope` answers a narrower, more frequent question:
**which identity dimension owns one specific datum** (a cache entry, an
evidence row, a log line), and unlike the statusline contract it **must**
have an explicit `UNKNOWN` member, because pre-attribution legacy data is a
real and permanent case here (see
[Backward compatibility](#backward-compatibility)). The two enums are
related but not interchangeable, and a value in one must not be assumed to
translate directly into the other — a statusline provider computes its own
`scope` field from whatever `Scope` its underlying data actually carries, it
does not inherit one from the other automatically.

## The envelope

One `ExecutionIdentity` value per record. Every field declares, by its own
existence, whether it is provider-native or Horonom-generated — there is no
generic "id" field anywhere in this contract, because a generic field cannot
carry that distinction. An absent (`None`/omitted) field means *this
dimension is not known for this record*, never *this dimension does not
apply* and never zero/empty-string-as-a-stand-in-for-unknown.

### Host (Horonom-generated)

| Field | Required | Type | Meaning |
|---|---|---|---|
| `host_id` | yes | string, opaque | A stable, Horonom-generated identifier for the physical/virtual machine. Not derived from hostname, MAC address or any other value that could leak machine identity into a log; see [Redaction](#redaction). |

There is no OS- or vendor-neutral "host identity" to capture natively across
every supported platform, so `host_id` is Horonom-generated like
`session_lineage_id` below, not provider-native. It is still modeled
separately from session/agent identity because it is the one dimension that
is legitimately shared across every concurrent session on one machine — see
`statusline-provider-contract.md`'s `host` scope.

### Provider identity (provider-native, opaque, verbatim)

| Field | Required | Type | Meaning |
|---|---|---|---|
| `tool_provider` | yes | string, `^[a-z][a-z0-9_-]{0,31}$` | Which coding-agent tool. Recommended values today: `claude_code`, `codex`. Open-ended by design — a new provider is a new string, never a contract version bump. |
| `tool_instance_id` | no | string, opaque | The running tool process/instance, when the provider exposes one stable enough to be useful (e.g. distinguishing two terminal windows running the same tool on the same host). |
| `provider_session_id` | no | string, opaque | The provider's own session identifier, copied verbatim. Never reformatted, truncated, re-encoded or combined with anything else — see [Opacity](#opacity-provider-native-ids-are-never-decoded-or-reconstructed). |
| `agent_id` | no | string, opaque | The provider's own agent identifier, when the provider distinguishes agents (e.g. a subagent) within a session. Verbatim, same rules as `provider_session_id`. |
| `turn_id` | no | string, opaque | The provider's own turn/task identifier, when exposed. |

All five are *provider-native*: Horonom never invents, reformats or
backfills a value in this group. An absent field here means the provider
does not expose that dimension at all, which is a fact worth keeping exactly
as absent.

### Agent lineage (provider-native, explicit tri-state)

A parent-agent relationship is asserted **only** when the provider itself
exposes one. This is deliberately not a nullable `parent_agent_id` field,
because `None` would then be ambiguous between two very different facts:
"this agent is the root of its session" and "this provider does not tell us
whether this agent has a parent." Collapsing those is exactly the heuristic
joining this contract exists to prohibit — see HORO-1598's AC #5 and the
`agent_id = known, parent_agent_id = unavailable -> preserve exactly that`
example in HORO-1597.

| Field | Required | Type | Meaning |
|---|---|---|---|
| `lineage_status` | yes | enum | `root` / `child` / `unknown`. See below. |
| `parent_agent_id` | required iff `lineage_status == child` | string, opaque | The provider-native parent agent id. Must be absent for `root` and `unknown`. |

- `root` — the provider has confirmed this agent has no parent (it is the
  session's top-level agent).
- `child` — the provider has confirmed a specific parent agent, carried in
  `parent_agent_id`.
- `unknown` — the provider exposes `agent_id` but does not expose lineage at
  all. This is the only legal state when lineage support is simply absent,
  and it is a different fact from `root`.

**Never** infer `root` or a specific `parent_agent_id` from process ancestry,
PID, cwd, tmux pane, terminal proximity, launch-timestamp proximity, or "the
previous agent in this session just finished." Those are heuristics, not
identity, and asserting lineage from them is the specific defect class this
contract exists to close.

### Horonom-generated correlation

| Field | Required | Type | Meaning |
|---|---|---|---|
| `session_lineage_id` | no | string, opaque, Horonom-generated | A durable Horonom correlation id that may span safe provider lifecycle boundaries (resume, clear, fork) that change `provider_session_id`. Optional: a product only sets this when it has a concrete, testable rule for when the boundary is safe to span — see [Session lineage is opt-in and testable](#session-lineage-is-opt-in-and-testable). |
| `event_id` | required for event-scoped records | string, opaque, Horonom-generated | A unique id for one event/action/evidence record, generated by whoever creates that record. Never reused. |

`session_lineage_id` is the one field in this contract allowed to outlive a
provider-native id changing underneath it, and that is exactly why it is
Horonom-generated rather than provider-native: the provider has no reason to
guarantee continuity across its own lifecycle boundaries, but a product may
need one. It must never be constructed by guessing continuity (same cwd,
similar start time) — only by a documented, testable transition the capturing
product can name (e.g. "Claude Code's own resume mechanism handed us the
same provider session id back").

### Context (not an identity dimension)

| Field | Required | Type | Meaning |
|---|---|---|---|
| `repo_id` | no | string, opaque | A bounded, Horonom-generated identifier for a repository, when relevant. |
| `worktree_id` | no | string, opaque | Likewise, for a worktree/project directory. |

Repository and worktree context may **filter** a query ("show me this
worktree's sessions") and may appear alongside an envelope for convenience,
but they are never a substitute for `provider_session_id`/`agent_id` and must
never be used to join or infer either. A record with `repo_id` set and every
provider-identity field absent is a record whose execution identity is
`UNKNOWN`, not one scoped to that repo.

### Observation

| Field | Required | Type | Meaning |
|---|---|---|---|
| `observed_at` | yes | string | `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, a real UTC calendar instant. |
| `envelope_version` | yes | int | Version of this envelope shape. `1` today. |

## Scope

```python
class Scope(enum.Enum):
    HOST = "host"
    SESSION = "session"
    AGENT = "agent"
    TURN_TASK = "turn_task"
    PROJECT_WORKTREE = "project_worktree"
    UNKNOWN = "unknown"
```

Every piece of behavioral state declares the **smallest truthful** scope that
actually owns it — not the broadest scope that would make the data available
everywhere, and not a narrower scope than the identity actually proven. A
per-session counter is `SESSION`, not `HOST`, even though it is easy to make
host-wide. A fact that is genuinely about a project/worktree as a whole (a
configured policy preset on disk) is legitimately `PROJECT_WORKTREE`, but a
per-session behavioral fact must never be downgraded to `PROJECT_WORKTREE`
merely because the project/worktree is the easiest thing to key on.

`UNKNOWN` is a legal, permanent scope value here (unlike the statusline
contract's `scope`), because records that predate session/agent attribution
are a real, permanent part of this contract's history — see
[Backward compatibility](#backward-compatibility). `UNKNOWN` scope data may
still be queried and displayed; it must never be silently promoted to
`HOST`, `SESSION`, or any other scope once attribution becomes available
for *new* data. A record's scope is fixed at the time it was recorded.

### Scope cannot silently narrow

A query or cache lookup that asks for `SESSION`-scoped data must fail
closed — return empty/unknown — rather than substitute `HOST`-scoped data
when no session identity is available. The reverse direction (serving
`HOST`-scoped data when `SESSION` was asked for) is the exact failure this
whole contract exists to prevent, so the reference implementation's
`cache_key()` raises rather than silently degrading scope — see
[Cache keys](#cache-keys-never-widen-on-their-own).

## Opacity: provider-native IDs are never decoded or reconstructed

Every provider-native field (`provider_session_id`, `agent_id`,
`parent_agent_id`, `turn_id`, `tool_instance_id`) is an opaque string as far
as this contract is concerned. A capturer must not parse, hash-truncate for
internal use, re-derive, or attempt to "normalize" these values — they are
stored and compared byte-for-byte. The only transformation ever applied to
them is the display-redaction step below, and that transformation is never
applied to the value used for equality/correlation or cache keys, only to a
value chosen for display.

## Redaction

A raw `provider_session_id`/`agent_id`/`host_id` is not a secret, but it is
also not something this contract requires to be shown verbatim in a public
UI or a shared log. `display_id()` in the reference implementation produces
a short, deterministic, non-reversible token (`sha256` truncated to 8 hex
characters, prefixed with the dimension name, e.g. `session:3f2a9c1d`) for
exactly that purpose:

- **Deterministic** — the same raw id always produces the same display id,
  so two log lines about the same session are recognizably about the same
  session without ever printing the raw value.
- **Non-reversible** — a display id must not be usable to reconstruct the
  raw provider id.
- **Never used for correlation internally** — `display_id()` output is for
  rendering only; the reference implementation's equality, `correlates_with`
  and `cache_key` all operate on raw values, never on display ids.

Never emitted by this contract's fields, by construction: prompts, tool
payloads, file contents, source code, credentials, full local file paths,
raw hostnames. `host_id` in particular must never be derived from the
machine's real hostname, MAC address, or serial number.

## Equality vs. correlation

Two `ExecutionIdentity` values are **equal** (`==`) only if every field
matches exactly, including `observed_at` and `event_id` — this is ordinary
value equality for a specific recorded instant.

Two values **correlate** (`correlates_with()`) when they describe the same
identity position regardless of when they were observed — every
provider-identity and lineage field matches, ignoring `observed_at` and
`event_id`. Correlation deliberately does **not** treat two `None` fields as
matching: if neither record carries a `provider_session_id`, that is not
evidence they are the same session, so `correlates_with` requires at least
one dimension to be concretely and identically populated on both sides, or
it returns `False`. "We don't know for either of them" must never collapse
into "therefore they're the same."

## Cache keys never widen on their own

`cache_key(scope)` returns the minimal tuple of fields that scope's data is
actually keyed on, and raises `ScopeIdentityMissing` if the identity needed
for that scope is not present on the envelope — it never falls back to a
broader scope's key. A `latest:<host_id>` lookup for session-local
behavioral state is exactly the anti-pattern HORO-1597 names explicitly; the
reference implementation makes it a raised error rather than a judgment
call left to each caller.

| Scope | Cache key fields | Fails if missing |
|---|---|---|
| `HOST` | `(host_id,)` | `host_id` |
| `SESSION` | `(host_id, tool_provider, provider_session_id)` | `provider_session_id` |
| `AGENT` | `(host_id, tool_provider, provider_session_id, agent_id)` | `agent_id` |
| `TURN_TASK` | `(host_id, tool_provider, provider_session_id, agent_id, turn_id)` | `turn_id` |
| `PROJECT_WORKTREE` | `(worktree_id,)` or `(repo_id,)` | both absent |
| `UNKNOWN` | Not cacheable by identity | always (this scope has no stable key by definition) |

## Session lineage is opt-in and testable

`session_lineage_id` must only be set when the capturing product can name
the specific, provider-documented mechanism that justifies carrying it
across a `provider_session_id` change — for example, Claude Code's own
resume flow handing back a value the product can test for. A product that
cannot name such a mechanism leaves `session_lineage_id` absent rather than
approximating continuity from timing or cwd. This field existing at all is
not permission to guess; see HORO-1598 AC #5.

## Backward compatibility

Records written before this contract existed are **host-only** historical
data. They remain valid, readable, and `Scope.HOST` (or `Scope.UNKNOWN` when
even host attribution cannot be reconstructed) forever. A reader must never
backfill a guessed `provider_session_id`/`agent_id` onto an old record to
make it look newer than it is — a mixed table of pre-contract and
post-contract rows is the expected, permanent shape, not a migration target.

## Version evolution

`envelope_version` is a single integer for the envelope shape, with the same
asymmetric rules as the statusline contract's `contract_version`:

**Without a version bump, a capturer may:** add a new optional field the
reader already knows how to ignore, stop emitting an optional field it
previously emitted, or add a new recognized value to `tool_provider` (it is
an open string, not a closed enum).

**Requires a version bump:** removing or renaming a required field, changing
a field's type, changing the meaning of an existing enum member
(`Scope`, `lineage_status`), or tightening a bound that invalidates
previously valid envelopes.

**How a reader handles a document it does not fully understand**, mirroring
the statusline contract's table:

| Input | Behaviour | Why |
|---|---|---|
| An unknown top-level field | Ignored, not carried into the parsed value | A reader that cannot interpret a field cannot safely act on it |
| An unrecognised `Scope` value | Refused | There is no safe fallback scope to guess — see [Scope cannot silently narrow](#scope-cannot-silently-narrow) |
| An unrecognised `lineage_status` value | Refused | A fabricated lineage state is exactly the failure mode this contract exists to prevent |
| An unrecognised `tool_provider` string | Accepted as-is | `tool_provider` is intentionally open-ended; a reader does not need to recognize a provider to store and query its envelope |
| An unknown `envelope_version` | Refused | The reader cannot know which of the above rules still hold |

## Security / privacy

- No prompts, tool payloads, file contents, or source code may ever be
  carried by an `ExecutionIdentity` value — it is pure metadata.
- No credentials or credential-derived values (hashes, prefixes, lengths).
- `host_id` must never be derived from a real hostname, MAC address, serial
  number, or any value an attacker could use to fingerprint the physical
  machine from the display id alone.
- Authorization must be checked by the caller before a query is allowed to
  select by `host_id`/`provider_session_id`/`agent_id` across users — this
  contract defines the identity shape, not an authorization model.
- Corporate-origin evidence stays text-only per existing Horonom policy;
  this contract carries no image/video/screenshot fields by design.

## Partial adoption is expected, not a defect

A product's existing identity model rarely matches this envelope field for
field on day one — Fornax's current model, for example, has a host-scoped
`device_id` and a flat per-message `session_id`, with no
`tool_instance_id`/`agent_id`/`turn_id` and no lineage concept at all yet.
That is not a reason to block adoption or to force a redesign before a
product can emit its first envelope: map what the product's current model
genuinely has (`device_id -> host_id`, its session identifier ->
`provider_session_id`, and so on), leave every other field absent, and set
`lineage_status: unknown` when the product has no lineage concept at all —
which is the truthful value for "doesn't track this" (see
[Agent lineage](#agent-lineage-provider-native-explicit-tri-state)). A
product closes the gap by adding real fields and real identity support
later, never by backfilling a guess now. This is the same principle as
[Backward compatibility](#backward-compatibility) applied to a product's
*current* records rather than its historical ones.

## Concrete examples

### Claude Code, a subagent with proven lineage

Claude Code exposes a stable provider session id to hooks, and a Task-tool
subagent invocation is attributable to a parent. This is the `lineage_status:
child` case:

```json
{
  "envelope_version": 1,
  "observed_at": "2026-10-02T12:00:00.000Z",
  "host_id": "host-9f2a1b",
  "tool_provider": "claude_code",
  "provider_session_id": "claude-sess-7e21",
  "agent_id": "claude-agent-task-3",
  "lineage_status": "child",
  "parent_agent_id": "claude-agent-root",
  "turn_id": "turn-14",
  "repo_id": "repo-libra-governor",
  "worktree_id": "wt-horo-1598"
}
```

### Codex, session identity only (no lineage support yet)

A provider may expose a stable session identifier without exposing any
agent/subagent distinction at all. This is **not** an error or a
degradation — it is `lineage_status: unknown` with `agent_id` absent,
exactly the state [Partial adoption](#partial-adoption-is-expected-not-a-defect)
describes for a capturer that has not yet built lineage support, and it is
also the correct value when the *provider itself* has no subagent concept to
expose:

```json
{
  "envelope_version": 1,
  "observed_at": "2026-10-02T12:05:00.000Z",
  "host_id": "host-9f2a1b",
  "tool_provider": "codex",
  "provider_session_id": "codex-sess-ab19",
  "lineage_status": "unknown"
}
```

Note what is absent: `agent_id`, `turn_id`, `parent_agent_id`,
`tool_instance_id`. None of these are synthesized to "fill in the shape" —
see [Implementing a capturer](#implementing-a-capturer), rule 2.

### An unsupported field on read: forward compatibility in practice

A future envelope version might add a field this version's reader has never
heard of — for example a hypothetical `cost_center_id`. Per
[Version evolution](#version-evolution), the reader ignores it rather than
failing the whole envelope:

```json
{
  "envelope_version": 1,
  "observed_at": "2026-10-02T12:10:00.000Z",
  "host_id": "host-9f2a1b",
  "tool_provider": "claude_code",
  "provider_session_id": "claude-sess-99aa",
  "lineage_status": "root",
  "cost_center_id": "eng-platform"
}
```

`ExecutionIdentity.from_wire()` on this document returns a value with no
`cost_center_id` attribute at all — the field is read by nothing in
`known_fields` and is therefore never retained, not even opaquely. A future
reader that does understand `cost_center_id` can be upgraded independently
of every capturer already emitting it, which is the entire purpose of
"ignored" rather than "refused" for an unrecognised *field* (as opposed to
an unrecognised *enum value*, which is refused — see the version-evolution
table above for why the two cases are handled differently).

## Implementing a capturer

1. Read the provider's own documented session/agent/turn exposure surface
   for your integration (hooks, environment variables, a provider API) —
   do not assume parity with another provider's capability.
2. Populate only fields the provider actually exposes. Leave every other
   field absent; do not synthesize a value "to fill in the shape."
3. Never derive `provider_session_id`/`agent_id`/`parent_agent_id` from PID,
   cwd, tmux pane, process ancestry, or timestamp proximity. If the
   provider does not expose a dimension, that dimension is absent.
4. Set `lineage_status` explicitly — `unknown` is the correct default when
   the provider exposes `agent_id` but not parent linkage, not `root`.
5. Mirror the reference suite's correlation, scope-narrowing, and
   redaction assertions in your own language against your own real values —
   reference this document and the property names by URL; do not copy its
   prose into your repo.

A reader validates every envelope it parses, so a capturer bug becomes that
record reading as `Scope.UNKNOWN` rather than contaminating another
session's state.
