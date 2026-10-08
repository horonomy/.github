# Statusline host and compositor (HORO-1565)

The other half of
[`statusline-provider-contract.md`](./statusline-provider-contract.md). That
document says what a product emits; this one says what the single host-owned
command does with it, and which of its behaviours are invariants rather than
implementation details.

North star, unchanged: *add useful live product status without taking ownership
of the user's statusline.*

Normative implementation and its test suites:
[`scripts/statusline_compositor.py`](../../scripts/statusline_compositor.py)
(the I/O half),
[`scripts/statusline_render.py`](../../scripts/statusline_render.py) (the
presentation half, pure),
[`scripts/test_statusline_compositor.py`](../../scripts/test_statusline_compositor.py),
[`scripts/test_statusline_render.py`](../../scripts/test_statusline_render.py).

Config *mutation* — how a product comes to be registered, and how the user's
original command comes to be recorded — is HORO-1566 and is governed by
[`product-integration-safety.md`](./product-integration-safety.md), not by this
document. The compositor only ever reads the registry.

## The problem this exists to solve

Claude Code gives a scope exactly one `statusLine.command`. Three Horonom
products want to say something there, and the user usually already has their own
script in that slot. Without a host, the products race for the slot and the last
installer wins — which is the destructive-overwrite failure ADR-0009 forbids.

So exactly one command is configured, and everything else becomes a provider of
that command:

```
Claude Code ──stdin JSON──▶ Horonom statusline host
                              ├──▶ the user's original command  (upstream)
                              ├──▶ product A statusline provider
                              ├──▶ product B statusline provider
                              └──▶ product C statusline provider
                            ▼
                     <their line> ┃ <bounded Horonom block>
```

## The upstream invariants

These are not negotiable and each has a test that fails if it is broken.

1. **Their command string is passed to the shell unmodified.** Passing the exact
   configured string to `sh -c` *is* the non-parsing way to preserve arguments,
   spaces and quoting; tokenising it ourselves would mean adopting a quoting
   dialect the user never agreed to. The script it names is never edited,
   injected into, parsed, rewritten or read as source, and its path is never
   assumed — in particular never assumed to be `~/.claude/statusline.py`.
2. **It receives the same stdin bytes we received.** The host's payload is
   forwarded as opaque bytes. Not parsing it means there is no schema of the
   host's to track and nothing from it can reach a rendered label by accident.
3. **Its stdout is reproduced verbatim as the prefix.** Never shortened,
   reordered, annotated or re-wrapped. A width budget applies to the Horonom
   block only: the user's line is theirs, this code cannot know which part of it
   matters, and the only budget we are entitled to spend is our own.
4. **No failure of ours may suppress it.** Every stage that can raise is
   contained, including our own rendering: a defect in the Horonom block prints
   a diagnostic to stderr and emits the upstream text alone.
5. **"No upstream" is a valid state, not an error.** A user with no previous
   statusline gets the Horonom block by itself.
6. **The host never invokes itself as upstream.** Checked two ways, because
   either alone is defeatable: a same-file test on the resolved command, and an
   environment depth marker that bounds recursion even across a symlink, wrapper
   script or renamed copy the file check cannot see. An unparseable marker
   counts as *already recursing* — the failure being prevented is unbounded
   recursion, so the ambiguous case must fail towards stopping.

## Provider identity context (HORO-1602)

The upstream invariant above (#2: the host's full payload is forwarded to the
user's *own* command as opaque, unparsed bytes) is unchanged. A registered
**provider**, which is Horonom-owned code rather than the user's own script,
is a different trust boundary and had received nothing at all — every
provider was invoked with empty stdin, specifically so it could never log,
render or grow a dependency on anything the host's payload carried (paths,
model identifiers, prompts, tool content).

That remains true for everything except one field. The host reads exactly
`session_id` out of its own received payload — nothing else — and builds a
minimal document from it:

```json
{"identity_stdin_version": 1, "provider_session_id": "<verbatim session_id>"}
```

This, and only this, is what a provider's stdin may now contain. The
distinction from invariant #2 is deliberate: the user's own command is
trusted with the full payload because it is the user's; a provider is
Horonom's own code running with the user's data, so it gets the smallest
slice that lets HORO-1597/1602's per-session attribution exist at all, never
the whole thing.

**What this does not change:**

- A provider that does not read stdin observes **byte-identical** behavior
  to before this section existed — no version bump, no opt-in required, no
  registry schema change. `identity_stdin_version` lets a provider that does
  read it detect a future, incompatible reshaping of this document without
  having to guess from field presence alone.
- No other field of the host's payload — cwd, model, transcript path, tool
  content, anything else Claude Code's own schema carries — ever reaches a
  provider. This is enforced by construction (the host reads one named key
  and discards the rest of the parsed structure immediately), not by a
  downstream filter a future change could accidentally widen.
- `session_id` extraction is best-effort and silent on failure: a payload
  that does not parse, is not an object, or lacks the field, yields no
  identity context and every provider's stdin is `b""`, exactly as if this
  section did not exist. A provider must never receive a fabricated or
  default session id.
- The provider cache (`cache_version: 1` cache entries) now additionally
  records the identity a provider answered under. **Identity gates the
  cache only when `Scope.SESSION` appears anywhere in the answer** — either
  as the document's own `scope`, or (see the segment-level amendment below)
  on one of its segments — the one scope a provider can declare that means
  "this is my own session-local behavioral state." When it does, the whole
  cached entry is refused to any render carrying a different identity,
  including the transition from "no identity known" to "an identity is now
  known" in either direction — and a render whose own identity is unknown
  never matches *any* session-scoped entry, including one also written
  under no identity, because an unresolved identity cannot prove isolation
  and must fail closed rather than guess. A document whose `scope`, and
  every one of whose segments' `scope`, is `HOST`/`PROJECT` is untouched by
  any of this: none of it is session-local by the provider's own
  declaration, so it remains reusable across different identities and
  across "no identity known at all" exactly as caching behaved before this
  section existed. A provider that ignores the identity stdin and declares
  only `HOST`/`PROJECT` is unaffected either way; one with any
  `Scope.SESSION` content that ignores the stdin pays at most one extra
  probe per render with no resolvable identity, never a wrong answer served
  across sessions. (An earlier draft of this amendment gated every scope on
  identity; it was reverted after review because it defeated ordinary
  host/project caching for every install with no session id to give at
  all, to guard a leak that only session-scoped data can actually suffer.
  Fornax now declares document-level `Scope.SESSION` when its daemon
  confirms it, HORO-1601/1602.)
- This document's own identity-passthrough logic uses no new experiment,
  capture mechanism, or provider-native acquisition path — `session_id` is a
  field Claude Code's statusline invocation already includes in the payload
  every registered statusline host receives today. This is orthogonal to,
  and does not touch, HORO-1599's native-capture restrictions.

**What a provider must still never do** with the identity context, once it
opts in to reading it: forward it anywhere outside the product's own local
state, log it in a form a user would not already see rendered, or treat its
mere presence as proof the product's own capture/storage layer actually
attaches it to anything (see
[`execution-identity-contract.md`](./execution-identity-contract.md)'s
"Partial adoption is expected" — knowing the current session's id and
*attributing stored data* to it are two different, separately-earned
capabilities).

## Hot-path containment

This command runs every few seconds for the life of a session. Rendering it must
not reach an LLM or any cloud API, install anything, build anything, start a
daemon, or walk a filesystem tree. Concretely:

| Control | Rule |
|---|---|
| Per-provider timeout | Bounded, defaulting to 250 ms, capped at 2 s |
| Overall deadline | Bounded, defaulting to 600 ms, capped at 3 s |
| Upstream timeout | Separate and more generous — their script is not ours to rush |
| Concurrency | Providers and upstream run together, never serially |
| Output caps | Provider and upstream stdout are read under byte caps |
| Cache | Short TTL, capped at 60 s, keyed on more than the provider id |
| Provider count | Capped, so the registry cannot become an unbounded fan-out |

**Independent provider timeouts must never be serialised.** With N providers at
a 250 ms timeout each, serial execution makes the worst case N × 250 ms while
concurrent execution keeps it at roughly the slowest single provider. A serial
implementation passes every correctness test and fails the only thing the
deadline exists for, so concurrency has its own test measuring wall clock
against the sum.

**A timeout must kill the process group, not the child.** Killing only the
direct child leaves a grandchild — a provider that shells out — orphaned and
running past every deadline we set, once per refresh, for the life of the
session. Children are started in a new session and the whole group is signalled,
with a bounded wait afterwards so the reaped output of a partially-finished
provider is still usable.

**Cache keys include the command.** Keying on the provider id alone means a
registry edit that repoints a provider at a different command is served the old
answer until the TTL expires — the stale reading is attributed to the new
command, which is the kind of wrong that looks right. The recorded key is a
fingerprint of the resolved argv.

When the deadline is exceeded, the upstream text is preserved first and the
providers that did not answer in time become explicit not-available statuses.

## Failure is reported, never hidden

A provider that times out, exits non-zero, prints nothing, prints unparseable
JSON, or emits a document the contract refuses becomes a host-synthesised status
saying so, with a bounded reason code. It never becomes silence and never
becomes a healthy zero. The three distinctions the contract draws are enforced
here at the point they would otherwise be lost:

- `UNKNOWN` is not `HEALTHY`.
- `UNAVAILABLE` is not `ZERO`.
- `PROBE_FAILED` is not `NOTHING_HAPPENED`.

Because the host validates every document it parses, a provider bug degrades to
that provider rendering as unknown rather than to a corrupted line or a leaked
value. That is a backstop for the provider's own tests, not a replacement.

## Presentation is host-owned

All iconography, separators, widths, truncation and mode selection live in one
pure module. A product emits semantics and never chooses a glyph, because two
products independently picking an emoji for "warning" is how a shared line stops
being readable.

### Modes

`BALANCED` (default), `COMPACT`, `PLAIN`. `PLAIN` is **not** a degraded mode —
it is the correct mode for a terminal whose font or width handling makes emoji
unreliable, and it carries exactly the same state meaning as `BALANCED`. Every
glyph has a word equivalent, so a glyph is never the only carrier of meaning.
Those equivalents are words rather than sigils: a reader who has never seen this
line before can decode `WARN` and cannot decode `!`.

An unrecognised mode falls back to the default instead of refusing. It decides
how the line looks, not what gets executed, and losing the whole statusline over
a typo in a cosmetic preference is the worse outcome.

### Glyph safety

A glyph is emoji-presentation-safe iff its last codepoint is U+FE0F or every
codepoint is East-Asian wide/fullwidth. This is the mechanical form of a real
defect: live Libra output used U+2696 SCALES and U+1F6E1 SHIELD bare, both
`Emoji_Presentation=No`, so terminals were entitled to draw them as one column
— and the column accounting around them went wrong with it. U+1F9ED COMPASS in
the same line was fine, which is why the bug looked arbitrary. The host's glyph
table is asserted against this rule, so the defect cannot be reintroduced by
adding a glyph.

Truncation is grapheme-cluster-aware, so a flag, a keycap, a ZWJ sequence or a
skin-tone modifier is never split in half.

### The degradation ladder

When a width budget is set, in order: try each allowed mode and take the first
that *measures* within budget; then shed the least important provider groups
while saying how many segments went; then fall back to the single worst state
with its label truncated. If even that cannot be said honestly, the upstream
text is returned alone — the user's line is the last thing to go, never the
first.

Each candidate is **measured** rather than assumed, because `COMPACT` can be
wider than `BALANCED`: compaction spells out states that refuse to be
abbreviated. An exception must become *more* explicit under pressure, not less,
and `unknown` is emphatic for the same reason — a provider that could not read
its own state is the case a reader is most likely to misread as fine.

### Separator hierarchy

Details are parenthesised and comma-joined inside a segment; segments and
provider groups are dot-joined; the user's line is divided from the Horonom
block by the strongest divider of all. Every separator character is deliberately
**absent** from the provider contract's label allowlist, so a separator can never
be confused with a character a provider put there. Square brackets are reserved
for host-owned semantic tokens, which is why `[host]` and `[NOT ENFORCED]` share
a shape.

### No opaque abbreviations

A shortened token may not be the only carrier of a meaning. `pf:high` does not
say that "high" is *preflight confidence* rather than risk; `wb` does not say
"would block"; `!escalated` does not say that something is awaiting approval.
The host renders the subject alongside the value, and a hypothetical verdict —
a shadow-mode would-block — is marked so it can never read as an enforcement
that actually happened.

## Idempotence and read-only-ness

Two renders of unchanged state produce the same line. A render writes nothing
outside the host's own cache directory: no receipts, no logs, no backups, no
locks, and in particular nothing under the host tool's configuration directory,
which the compositor has no ownership of. Both properties have tests asserted
over the whole state tree rather than against named files, because the risk is a
write nobody thought to look for.

Rendering also evaluates no policy, enforces or blocks nothing, mutates no
decision, and starts no daemon. The statusline is not an approval interface.

## The registry document, version 1

Written by the install lifecycle (HORO-1566), read by the compositor. Lives
under Horonom-owned state, not under the host tool's configuration.

```json
{
  "registry_version": 1,
  "upstream": { "command": "<the user's original command string, verbatim>" },
  "upstream_timeout_ms": 1500,
  "providers": [
    { "provider": "fornax", "command": ["fornax", "statusline", "provider"],
      "scope": "host", "timeout_ms": 250, "enabled": true }
  ],
  "presentation": { "mode": "balanced", "width_budget": 120 },
  "deadline_ms": 600
}
```

Parsing is strict on everything that decides what gets executed or what the
user's original line was — guessing at an unrecognised shape risks either
running the wrong thing or losing their line. An unsupported `registry_version`
is refused outright rather than best-efforted, because a future writer may have
moved the very field we would be reading. Version is checked by *type* as well
as value: in Python `True` and `1.0` both compare equal to `1`, so membership
alone is not a version check.

## Adding a host

A second coding agent gets support only if it has a real, supported, documented
single-command statusline mechanism. Emitting ANSI escapes into a shared stream,
echoing from a background process, or scraping a TTY is not support, and a host
with no such mechanism is recorded as UNSUPPORTED for this capability — which is
a valid, final answer, not a gap to be worked around. Capability is judged
against the *currently installed* version of that host, not its roadmap.
