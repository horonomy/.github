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
the evidence itself — two of the three standing hypotheses turned out to be true
for a different and deeper reason than the one recorded, which is why the
distinction matters.
