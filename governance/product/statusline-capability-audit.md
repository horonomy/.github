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

Four verdicts, deliberately not three. The distinction between the last two is
the point: a host that *has no such feature* and a host we *have not established
the answer for* are different facts, and collapsing them would let an
unexamined host pass as a decided one.

| Verdict | Meaning | What the entry must carry |
|---|---|---|
| `SUPPORTED` | Eligible on all seven criteria | A shipped provider, or an owning implementation ticket |
| `BLOCKED` | Would be eligible but for a specific missing prerequisite | The exact prerequisite, and which criterion it fails |
| `NOT_APPLICABLE` | Structurally out of scope, and no prerequisite would change that | The reason, and what *would* change the answer |
| `UNKNOWN` | Not established | Why it could not be established, and what evidence would settle it |

`UNKNOWN` is a legitimate resting state for an entry. It is not a placeholder to
be cleared by guessing.

## Method

Every verdict below was reached by reading the product's current `main` and, where
the product is installed, by running its own read-only surface. Ticket
descriptions and prior DogFood notes were used to decide *what to check*, never as
the evidence itself. Of the two standing DogFood hypotheses, one held exactly as
recorded and one held for a different and deeper reason than the one recorded —
which is why the distinction matters.

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
