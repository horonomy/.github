# Statusline capability audit (HORO-1570)

Which Horonom products should contribute a statusline segment, which host tools
can carry one, and — for every product and host that cannot — the exact reason.

North star: *add useful live product status without taking ownership of the
user's statusline.* The corollary this document exists to enforce is that a
statusline segment is **not** a checkbox every product must tick. A product with
nothing truthful and useful to say should say nothing, and that absence should be
a recorded decision rather than an omission nobody examined.

Read [`statusline-provider-contract.md`](./statusline-provider-contract.md) for
the wire contract a supported product implements, and
[`statusline-host-compositor.md`](./statusline-host-compositor.md) for what the
host does with the documents it collects.

## Eligibility criteria

A product is eligible when **all seven** hold. Failing any one is enough to keep
it out, and the audit records which one failed rather than a summary verdict.

| # | Criterion | Why it is disqualifying on its own |
|---|---|---|
| 1 | A stable read-only state surface exists | Without one there is nothing to read, and inventing one is a product decision, not statusline wiring |
| 2 | The state is useful *during* normal agent work | A segment that never changes within a session spends columns to say nothing |
| 3 | Its scope is host, session or project, and that can be stated truthfully | A segment whose scope is a guess misattributes state to the wrong thing |
| 4 | It can be obtained cheaply, without starting heavy services | The hot path forbids builds, installs, daemon startups and cloud calls |
| 5 | It can avoid sensitive content | The privacy floor is absolute, not a tuning parameter |
| 6 | Unavailable is distinguishable from a healthy zero | `UNAVAILABLE != ZERO`; a product that cannot tell them apart will render a lie |
| 7 | A segment materially helps the operator rather than adding noise | Symmetry across products is not a reason |

## Verdict vocabulary

Products and hosts are being asked different questions — *should this product
contribute a segment* versus *can this host carry one at all* — so they draw from
overlapping but distinct verdict sets. `UNKNOWN` is shared, and is the reason
there are five verdicts rather than four.

| Verdict | Applies to | Meaning | What the entry must carry |
|---|---|---|---|
| `SUPPORTED` | product, host | Eligible on all seven criteria / the host can carry a segment | A shipped provider or an owning implementation ticket; for a host, the mechanism |
| `BLOCKED` | product | Would be eligible but for a specific missing prerequisite | The exact prerequisite, and which criteria it fails |
| `NOT_APPLICABLE` | product | Structurally out of scope, and no prerequisite would change that | The reason, and what *would* change the answer |
| `UNSUPPORTED` | host | The host has no mechanism for this capability | What its nearest feature does instead, and the version examined |
| `UNKNOWN` | product, host | Not established | Why it could not be established, and what evidence would settle it |

The distinction that matters most is `UNSUPPORTED` against `UNKNOWN`. A host that
*has no such mechanism* and a host we *have not established the answer for* are
different facts, and collapsing them would let an unexamined host pass as a
decided one. `UNKNOWN` is therefore a legitimate resting state for an entry, not a
placeholder to be cleared by guessing.

`BLOCKED` and `NOT_APPLICABLE` are product-side only, deliberately. A host does
not have a *prerequisite* a Horonom campaign could supply — it either exposes the
mechanism or it does not — so offering a host those verdicts would invite an entry
that reads as actionable when nothing here can act on it.

## Method

Every verdict below was reached by reading the product's current `main` and, where
the product is installed, by running its own read-only surface. Ticket
descriptions and prior DogFood notes were used to decide *what to check*, never as
the evidence itself. Of the two standing DogFood hypotheses, one held exactly as
recorded and one held for a different and deeper reason than the one recorded —
which is why the distinction matters.

## Product verdicts

| Product | Verdict | Owner or blocker |
|---|---|---|
| Fornax | `SUPPORTED` | Shipped, HORO-1567 |
| Circinus | `SUPPORTED` | Shipped, HORO-1568 |
| Libra Governor | `SUPPORTED` | Shipped, HORO-1569 |
| Glomeris | `SUPPORTED` | HORO-1577 |
| Ophiuchus | `BLOCKED` | No persisted relay activity record — criteria 1, 2 and 6 |
| Eltanin | `BLOCKED` | Status surface returns liveness, not state, by protocol design — criterion 1 |
| Eridanus | `BLOCKED` | No local read-only projection over the Edge buffer — criteria 1 and 4 |
| Horologium | `NOT_APPLICABLE` | Server-side; state is review-time, not session-time |
| AASM | `NOT_APPLICABLE` | Separate organization; not a Horonom product |

No entry is `UNKNOWN`. That is an outcome, not a target — four verdicts exist so
that an unexamined product cannot pass as a decided one, and had any product
resisted examination it would be sitting in that row.

Four of the nine surfaces audited contribute a segment. That ratio is the audit
working correctly, not a gap to close.

## Products — `SUPPORTED`, already shipped

Three providers exist and are merged. Each is listed with the criterion that was
hardest for it, because that is the part a future change is most likely to break.

### Fornax — `SUPPORTED`

Provider shipped under HORO-1567 (`horonomy/fornax-core`, `crates/fornax-cli/src/statusline.rs`,
merged as `f626b01`). Scope `host`, order hint 300.

Hardest criterion was **6**. Fornax's verdict vocabulary already distinguishes
`UNVERIFIED` from `UNAVAILABLE`, and the pre-existing rule that an `UNVERIFIED`
claim does not become healthy because a daemon has events is the same rule
criterion 6 states. The provider inherited it rather than re-deriving it.

### Circinus — `SUPPORTED`

Provider shipped under HORO-1568 (`horonomy/circinus`, `src/circinus/statusline/`,
merged as `a7ec15b`). Scope `host`, order hint 400.

Hardest criterion was **7**, in an unusual direction: Circinus's shadow-mode
would-block is genuinely useful *and* is the single most dangerous thing in this
audit to render badly, because a hypothetical block that reads as an actual
enforcement block would make the operator believe work was stopped when it was
not. The host carries that weight — `HYPOTHETICAL_TEXT` is welded to the label
rather than left to the provider — which is why the segment is admissible.

### Libra Governor — `SUPPORTED`

Provider shipped under HORO-1569 (`horonomy/libra-governor`,
`crates/cli/src/statusline_provider.rs`, merged as `2d59977`). Scope `host`,
order hint 500.

Hardest criterion was **3**. Libra's daemon holds one `current_task` machine-wide,
so `host` is the only honest scope; a `session` scope would have implied per-session
governance that does not exist. Criterion 5 also bit harder here than elsewhere: the
estimator's free-text reason is exactly the kind of field that leaks, and it is
excluded from both the document and the explain surface by a test whose fixture
deliberately carries a path and a secret-shaped token.

## Products — `SUPPORTED`, not yet shipped

### Glomeris — `SUPPORTED`, owning ticket HORO-1577

The only product outside the three above that passes all seven criteria.

`glomeris status --json` is statfs-based and daemon-independent, and answered in
0.00–0.01 s over five consecutive runs — criterion 4 with very large headroom
against the host's 250 ms per-provider default, without starting anything. Its
health field is a closed enum (`HEALTHY` / `WARN` / `PRESSURED` / `CRITICAL` /
`EMERGENCY`) that maps onto the contract's `SegmentState` without inventing a
vocabulary, which is what makes criterion 6 satisfiable. `glomeris daemon status
--json` separately reports `loaded` and `heartbeat_age_secs`, a real freshness
cue rather than an inferred one. Criteria 2 and 7 hold on merit: disk pressure
changes during a session and is the kind of state that silently ruins a long
agent run.

Two constraints are load-bearing and are written into HORO-1577 rather than left
to the implementer to rediscover:

- The provider must read `status`, never `detect`. `detect` runs unbounded probes
  and can write through a version-manager shim, so it fails criterion 4 outright
  and is not merely slower.
- `plist_path` must not appear in the document. It is the one field in the
  product's own output that criterion 5 excludes.

HORO-1577 exists because criterion 2 of *this audit's* acceptance — every
`SUPPORTED` product names a shipped provider or an owning ticket — is not
satisfied by a verdict alone. A `SUPPORTED` row with no owner is how an audit
turns into a wish list.

## Products — `BLOCKED`

Each entry names the exact missing prerequisite and the criterion it fails.
`BLOCKED` is not a soft `NOT_APPLICABLE`: these products would be eligible if the
named prerequisite existed, so each entry doubles as the specification of what
would unblock it.

### Ophiuchus — `BLOCKED` on criteria 1, 2 and 6

**Missing prerequisite:** a persisted, locally readable record of relay activity.

`ophiuchus status` returns the version plus a redacted view of *configuration*.
Configuration is not session state — it does not change while the operator works,
so even if it were richer it would fail criterion 2.

The state a statusline would want is relay receipts, and receipts are transient by
design: they are validated and printed, never stored. Usage history is
relay-operator-side and off by default — and the product's own documentation for
that path already names the consequence: a relay that should be recording and is
not looks exactly like a relay nobody used. That is criterion 6 failing at the
source rather than in the rendering. A provider cannot distinguish unavailable
from a healthy zero when the underlying store cannot either, so this is not
something careful provider code could recover from.

This confirms the standing DogFood hypothesis as recorded.

### Eltanin — `BLOCKED` on criterion 1, for a deeper reason than the recorded hypothesis

**Missing prerequisite:** a read-only status surface that returns state rather
than liveness — which means a deliberate protocol decision would have to be
revisited, not a bug fixed.

The recorded hypothesis was that Eltanin is simply inactive and unconfigured here.
That is true — its CLI exits 69, there is no state directory, no `agentd`, and no
socket — but it is the secondary reason, and recording only it would leave the
impression that activating Eltanin would make a provider possible.

It would not. `AgentStatusView` carries a protocol version and nothing else, and
its own documentation says it deliberately omits the agent's issuer instance
identity. The surrounding protocol is coarsened in the same direction on purpose:
denial reasons are generalized, release outcomes collapse to a single refusal, and
the error type carries no free-text field, specifically so that an unprivileged
client cannot enumerate policy or leases. A statusline provider *is* an
unprivileged client. Everything criterion 1 would need is the thing Eltanin's
protocol is designed not to disclose.

So the prerequisite is a product decision — a separate, explicitly authorized
read-only projection scoped to what a local operator may see — and not a matter of
starting a daemon. This is the audit's clearest example of why criterion 1 says
inventing a state surface is a product decision rather than statusline wiring.

### Eridanus — `BLOCKED` on criteria 1 and 4

**Missing prerequisite:** a local read-only projection over the Edge buffer.

Eridanus's only Claude Code surface today is a write-path capture hook. It
requires a tenant identifier, a bearer token and a gateway endpoint, which fails
criterion 4 (a remote call on the hot path) and sits uncomfortably against
criterion 5 (credential handling in a process that runs on every statusline
refresh).

There is no read-only projection over the Edge buffer to read instead. If one is
added and is genuinely local, Eridanus becomes reassessable — the buffer's depth
and lag are exactly the kind of state criterion 2 wants.

## Products — `NOT_APPLICABLE`

### Horologium — `NOT_APPLICABLE`

A server-side product backed by Postgres, with no local status CLI. Its state is
*review-time* rather than session-time, so it fails criterion 2 by nature rather
than by omission: nothing it knows changes in a way the operator needs to see
while coding.

What would change the answer: Horologium acquiring a local per-developer surface
whose state moves during a working session. Adding a network call to a shared
server so the statusline can show shared review state would not qualify — that is
criterion 4, and the segment would be showing other people's work, not this
session's.

### AASM — `NOT_APPLICABLE`

AASM lives in a separate organization and is not a Horonom product. That alone
settles it, so the remaining observation is offered as evidence rather than as a
verdict: `aasm status` renders an uptime of 0 s, 0 connections and 0 ms lag
alongside an unreachable health verdict.

That is precisely the `UNAVAILABLE != ZERO` antipattern criterion 6 exists to keep
out of the statusline. It is recorded here because it is the most concrete
demonstration available of what criterion 6 prevents, and because a reader
reasonably asks whether the criterion is theoretical. It is not.

## Host capability

A host is `SUPPORTED` only if it can be configured to run an operator-supplied
command and render its output. That is a narrow capability, and deliberately so:
the compositor's whole design depends on owning one configured command slot.

`UNSUPPORTED` and `UNKNOWN` are kept apart here for the reason the vocabulary
section gives. A host with no such feature is a settled fact; a host nobody could
examine is not.

| Host | Version assessed | Verdict | Basis |
|---|---|---|---|
| Claude Code | 2.1.226, installed | `SUPPORTED` | Native single-slot `statusLine.command`; proven by the shipped host and three providers |
| Codex | 0.154.0, installed | `UNSUPPORTED` for this capability | Has a status line, but its content is a closed set of built-in identifiers |
| OpenCode | current development branch, not installed | `UNSUPPORTED` for this capability | No statusline or footer key in its configuration schema |

The version column is load-bearing. A host capability verdict is only a claim
about the version examined, and every one of these three could change in a
release — so a reader with a newer version should treat the row as a starting
point and the criterion as the durable part.

### Claude Code — `SUPPORTED`

One `statusLine.command` per scope, receiving a JSON payload on stdin and
rendering stdout. This is the capability the compositor was designed against, and
it is proven end to end rather than from documentation: the merged host composes
the operator's own command with three providers.

The single-slot property is the reason the whole campaign is shaped as a
compositor rather than as three independent integrations. It is a constraint, not
a limitation to route around.

### Codex — `UNSUPPORTED` for this exact capability

This verdict is about the *installed* version, because a capability claim read off
a changelog is not evidence about the tool the operator is running.

Codex does have a status line, and it is configured on this workstation. But the
feature composes a closed set of built-in identifiers — model, reasoning, task
progress, current directory, project name, git branch, used tokens, thread title,
hostname, pull-request number, branch changes, permissions, approval mode, context
window size, raw output, workspace headline. There is no `command` or `exec`
member of that set, and none appears anywhere in the surrounding region of the
binary. The sibling terminal-title feature rejects unknown identifiers the same
way, which is consistent with a closed vocabulary rather than an undocumented
escape hatch.

What was explicitly *not* accepted as support: emitting ANSI sequences or echoing
from a background process to paint text into Codex's UI. That would produce
something that looks like a statusline without being one — no defined refresh,
no payload, and no ownership model — so the campaign's non-destructive and
truthfulness guarantees would not hold. Per the campaign's own rule, a host is
recorded as unsupported unless the currently installed version proves a real
supported equivalent.

Probing was done with invocation-scoped overrides only; the operator's Codex
configuration was verified unchanged afterwards.

### OpenCode — `UNSUPPORTED` for this capability, with one honestly unresolved question

Assessed from the canonical repository's current development branch, because
OpenCode is not installed on this workstation. That is a real limitation of the
evidence and is stated rather than smoothed over.

Its configuration schema has no statusline, status or footer key of any kind. The
footer is a built-in TUI component, so there is no slot to own and the
config-driven-command capability is absent.

OpenCode does have a plugin mechanism. Whether a plugin can contribute footer
content is **not established** by this audit — it was not determined, and
guessing either way would be worse than recording the gap. If that question is
answered affirmatively, OpenCode is reassessable, and the answer would be a
different capability from the one this table measures.
