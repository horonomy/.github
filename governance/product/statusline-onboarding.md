# Statusline onboarding, migration and explanation (HORO-1571)

How a person turns this on, finds out what it did, understands what they are
looking at, and turns it off again — without opening a JSON file.

The three documents this one sits on top of:
[`statusline-provider-contract.md`](./statusline-provider-contract.md) (what a
product emits), [`statusline-host-compositor.md`](./statusline-host-compositor.md)
(what the single host command does with it), and
[`statusline-lifecycle.md`](./statusline-lifecycle.md) (how the slot is taken
and given back). Ownership rules are
[`product-integration-safety.md`](./product-integration-safety.md) (ADR-0009)
and are not restated here.

Implementation: [`scripts/statusline_lifecycle.py`](../../scripts/statusline_lifecycle.py);
tests: [`scripts/test_statusline_lifecycle.py`](../../scripts/test_statusline_lifecycle.py).

## The command surface

Seven subcommands, all reachable from `python3 scripts/statusline_lifecycle.py`.
Three read, four write, and the parser knows which is which — only the writing
ones accept `--dry-run`, and `presentation`, which never opens the settings
file, does not accept `--settings` at all rather than accepting and ignoring it.

| Command | Writes | Answers |
|---|---|---|
| `doctor [--probe]` | never | is this installed correctly, and would anything change if I acted |
| `list` | never | which providers are registered, and what is on the line |
| `explain [provider] [--legend]` | never | what does every token on my line mean |
| `enable --provider … --scope … --command …` | settings + registry | put this product on the line |
| `disable --provider …` | registry (+ settings if last) | take this one off |
| `uninstall` | settings + registry | take all of it off and give the slot back |
| `presentation --density … --icon-style … --depth …` | registry | render it in a way my terminal can show, at the depth I want |

`--dry-run` prints the same `Plan` object `apply` acts on. A preview generated
by separate code is a preview of nothing, so there is only one. `--json` is
available on all seven.

`enable` states the whole registration rather than patching it: `--command` is
repeated once per argument, and omitting `--timeout-ms` or `--explain-command`
un-records them. A registration assembled from several past invocations is one
no product could predict the effect of re-running.

**Nobody needs to edit `settings.json`.** That is the first acceptance
criterion, and the shape it takes in code is that every field this integration
writes is written by one of the five mutating subcommands above, and every
question a user might otherwise open the file to answer is answered by one of
the three reading ones.

**What is *not* solved here: distribution.** There is no installed binary named
`statusline` on anyone's `PATH` yet, and putting one there is a packaging
decision belonging to the owning products, not to this host. Every path in this
document is the module path. A product that forwards its own subcommand — as
Fornax, Circinus and Libra each do — is the supported user-facing spelling.

## Before, apply, after

The flow is explicit opt-in at every step, and each step is inspectable before
it happens:

```
doctor                                        # who owns the slot, and is it safe to act
enable --provider fornax --scope host \
       --command fornax --command statusline \
       --command provider --dry-run           # exactly what would change, in
                                              # HORO-1000's categories
enable --provider fornax --scope host ...     # the change
doctor                                        # what it did, read back from disk
explain                                       # what the line now says, and what
                                              # its tokens mean
```

What `enable` changes, exhaustively: `statusLine.command` becomes the
compositor, `statusLine._horonom` appears as the ownership marker, and a
registry entry appears under `~/.horonom/statusline/`. Nothing else in the
settings file is touched — not the `statusLine` object around those two keys,
not `model`, not `permissions`, not other products' `hooks`, not `env`.

What stays the user's: everything else in the file, the original command string
(recorded, not replaced), and the script that command points at — which is
never opened for writing, never parsed, and never assumed to be
`~/.claude/statusline.py`.

## Coming from something that already works

Three configurations exist in the wild, and all three are arriving from before
any of this did.

### A manually edited `settings.json`

The simple case. `enable` records the existing command as upstream, takes the
slot, and the compositor calls the original first. `uninstall` writes the
recorded command string back. Metadata on the `statusLine` object — `padding`,
`refreshInterval`, anything a future Claude Code adds — survives all of it,
including adjustments made *after* enabling, because restoration is a delta and
not a snapshot.

Nothing is deleted. A manual configuration is not stale merely because a
supported mechanism now exists, and the only thing remembered about it is its
command string.

### The founder's composed wrapper

One hand-written script in the slot that shells out to Fornax and Libra itself.
Registering those products as providers does not stop the wrapper from calling
them, so **the line will legitimately say each thing twice** until the reader
trims their own script.

That trimming is theirs to do. Editing the wrapper to remove the calls is
exactly the mutation this integration may not make: it is a file we do not own,
whose contents we do not parse, and whose author may be relying on details we
cannot see. The duplicate is the visible, reversible, self-explanatory outcome;
a silently rewritten script is not.

Two ways through it, both supported:

1. Trim the product calls out of the wrapper and keep it as upstream. The
   wrapper keeps whatever else it renders — git branch, model, cost — and the
   providers supply the product segments.
2. Keep the wrapper untouched and register nothing. Also a valid end state.
   Nothing here is mandatory.

### Fornax's project-local DogFood wrapper

The one with a trap in it. Claude Code's project-local settings **replace** the
global `statusLine` rather than merging with it, so migrating the global file
does not migrate a project — inside that project, the project-local wrapper
still wins, and the global compositor never runs.

Deleting `.claude/settings.local.json` to make the global configuration take
effect would be a destructive mutation of state we do not own, so it does not
happen. Two supported routes instead:

1. Point the lifecycle at that file with `--settings
   path/to/.claude/settings.local.json`. The project keeps its own upstream and
   its own unrelated keys; the global file is not read or written.
2. Remove the `statusLine` block from the project file by hand, so the global
   configuration applies. That is a deliberate act by its owner, and it is the
   one step in this document that does involve editing JSON — because the file
   belongs to the project, not to us.

### What migration never does

- Never deletes a manual configuration, a local settings file, or a script.
- Never edits, parses or rewrites the user's statusline script.
- Never restores from a backup. There is no backup — see
  [`statusline-lifecycle.md`](./statusline-lifecycle.md) for why creating a
  second copy of a credential-bearing file buys nothing that atomic rename
  does not already give. Restoration authority comes from *proven current
  ownership*, never from a stored artifact, and a stale receipt is not
  authority at all.
- Never changes a presentation preference. Only `presentation` writes that
  key, so no upgrade, repair or second `enable` can reset it.

## More than one product

The slot is taken once. The second and third products register and nothing in
the settings file changes at all — `doctor` says so in advance under
`mutation_outlook`. Disabling one leaves the others and the original upstream
exactly as they were. The original comes back only when the last provider
leaves.

Failure is contained per provider: one that is missing, hangs, crashes or
answers with nonsense becomes an explicit unavailable marker in its own place on
the line. It cannot suppress another provider and it cannot suppress the user's
own statusline, which is rendered first and preserved first when the overall
deadline runs out. The full timeout and containment model is
[`statusline-host-compositor.md`](./statusline-host-compositor.md).

## Drift — when the user changes their mind

If someone edits `statusLine.command` after enabling, that is their answer and
it stands. The marker is ours, the command is now theirs, and the state is
reported as `CONFIG_DRIFT`: `doctor` names the expected owned state, the
observed state, and a safe next step, then exits non-zero without touching
anything. `enable` refuses rather than silently retaking the slot; `uninstall`
refuses to restore over it, because restoring would overwrite a later choice
with an earlier one.

Repair is a separate, explicit act. Silently correcting drift would make
upgrade indistinguishable from install, which a user who deliberately changed
their statusline experiences as the tool fighting them.

## Reading the line — progressive disclosure

A compact line cannot choose between opaque abbreviations and prose, so it does
neither: the line stays short and `explain` carries the meaning.

```
explain                 # the key, then every live reading decoded
explain fornax          # narrowed to one product
explain --legend        # the key alone, spawning no provider at all
explain --json          # the same report as data
```

Three layers, in the order a confused reader needs them:

1. **The key.** Generated from the renderer's own meaning tables, so it is
   complete before anything is measured and cannot drift from what the line
   draws. Every state, every scope glyph, every confidence subject, with each
   token rendered by the same function that renders it on the line, in the mode
   actually in force.
2. **The decode.** Each live reading quoted exactly as the line shows it, then
   one labelled line per thing in it: state and what that state means, the
   reason clause if the provider gave one, freshness if the provider dated it,
   confidence *and what it is a confidence in*, and whether the reading is
   hypothetical.
3. **The product's own words.** Where a product registered an explain command
   (`--explain-command` at enable time), that command is run and its answer
   quoted and attributed.

Nothing is inferred. A segment that carried no reason reports no reason,
because "the provider did not say why" and "the host worked out why" are
different claims and only the first is true. `UNKNOWN` is glossed as *the
provider could not determine its own state; this is not a quiet way of saying
all clear*, and a would-block reading stays marked hypothetical all the way
down into the explanation.

### Why the deep layer is a hand-over and not a paragraph

The host could write a paragraph about Fornax's verification states. It would be
a second copy of Fornax's documentation, kept current by nobody, and wrong in a
release neither team noticed. So the command that explains a product's semantics
is the product's own, recorded at registration.

It has to be recorded rather than derived, because the three products spell it
three different ways — `fornax statusline explain`, `libra-governor statusline
explain`, `circinus statusline --explain` — and there is no convention to guess
from.

Two properties keep that hand-over safe:

- **It never runs while the line is rendered.** The recorded command has a
  five-second budget; a provider on the render path has 250ms. The compositor's
  read model has no field for it at all, so the render path cannot reach it.
  Asserted directly: the registration that answers `explain` leaves no trace
  when the compositor composes a line.
- **Only stdout is reproduced, per-line prefixed.** A product's explain output
  is designed for a person and carries that product's own privacy tests. Its
  stderr is neither, and a crash message is exactly where a filesystem path
  shows up — so a failure is reported as a status and a basename, never as
  whatever the product printed on its way down. A product printing something
  shaped like one of the host's own labelled lines still cannot pass for the
  host talking.

### Shared iconography

The same semantic concept uses the same token everywhere, because the tokens
belong to the compositor rather than to any provider. A provider emits a state,
a scope and a confidence subject; the host chooses the glyph. Scope glyphs in
particular are explained centrally and appear identically for every product —
see [`statusline-host-compositor.md`](./statusline-host-compositor.md) for the
tables themselves and why emoji are presentation only and never the data
contract.

## Presentation preferences

```
presentation --density compact|balanced
presentation --icon-style emoji|text
presentation --depth clear|detail
presentation                                  # prints what is currently set
```

Three orthogonal axes, each settable without stating the others, because a reader
whose font renders emoji as boxes has said nothing about how wide their terminal
is or about how much they want it to say. Text style is a full non-emoji
rendering, not a degraded one, for terminals and fonts where glyph handling
cannot be trusted — including the key itself, which would otherwise arrive drawn
in exactly the characters the reader turned off.

The preference lives in our registry, not in the user's settings file, and is
never rewritten by any other operation. It does not survive `uninstall`,
deliberately: it is our state, and leaving a preferences file behind after an
uninstall is litter rather than a courtesy.

### Information depth — `clear` and `detail`

Depth is a third axis rather than more modes because it answers a different
question. Density and icon style are about how much room a reading may spend;
depth is about how much there is to say. Folding them together would have
produced eight members whose only job was to re-express three independent
choices, and every site consulting density would silently also have asserted a
depth.

| depth | what the line carries |
|---|---|
| `clear` (default) | one high-signal summary per product: state, label, product identity, and — where it changes the interpretation — one secondary vital |
| `detail` | the same primary state, plus the bounded supporting context the provider reported: the reason clause, freshness, counts and confidence |

Two properties are load-bearing, and both are tested directly:

- **One state engine.** `clear` is not `detail` minus some fields, and it is not a
  second derivation either. Both are projections of the *same* provider snapshot,
  so the primary state a reader sees is identical at either depth and only the
  supporting context changes. A `clear` line can never be quieter about a problem
  than a `detail` one.
- **`detail` is not a privileged data-egress mode.** It renders more of the same
  privacy-reviewed snapshot. It collects nothing extra: no additional provider
  run, no cloud call, no scan, no daemon start. If a field would be unsafe to show
  at `clear`, "this is the diagnostic depth" is not a reason to show it.

A switch takes effect on the next render. It writes one file — our registry —
restarts no daemon, reinstalls no provider, rebuilds nothing, and never opens the
user's settings file, because which fields of a line are visible is not a fact
about how the host launches a command.

`doctor` and `explain` both name the depth in force and where the preference came
from, which is the fastest answer to *why does my line not say why*: at `clear`
the missing field is a setting, not a failure. `explain` goes further and quotes
each fragment at the depth the line is actually using, while still decoding every
reading the provider reported — the ones the current depth leaves out are marked
as not being on the line rather than listed as though they were.

## Privacy of these surfaces

`doctor`, `list` and `explain` are what get pasted into an issue, so none of
them reproduces a value read out of the settings file. They name basenames, not
paths; they count unrelated keys without listing them; and the only free text
that reaches the output is the provider's own rendered segments — which have
already passed the wire contract's allowlist — plus the host's own fixed prose.

Asserted, not asserted-about: the privacy test loads the fixture settings file
with a token-shaped `env` var, an internal URL, an MDM path and a home
directory, and requires none of them in the output. The user's own statusline
command is checked against the registry rather than the settings file, because
that path moves out of the settings document the moment the slot is taken over —
asserting its absence from the wrong place would prove nothing.

## Where the product documents stop

Each product documents its own side of the contract — what its segments mean,
what its explain surface says, what it will not do while rendering — and refers
the slot itself here. None of the three ships a second settings patcher or a
copied enable/uninstall recipe, which is the property that matters: a divergent
snippet in a product repo is how a user ends up following instructions that
predate a safety fix.

Audited at the time of writing: `fornax-core/docs/dogfooding-status-line.md`
names the lifecycle program and states that Fornax deliberately has no patcher
of its own; `circinus/docs/statusline.md` names it as the shared program
HORO-1566 and defers ownership questions to it; `libra-governor/docs/statusline.md`
links the provider contract and sends slot questions to "the host's own
documentation". All three describe migration as something the user does, and
none instructs a restore from a copy of the settings file.

Two of the three refer to this host by path and repository rather than by
hyperlink, because this document did not exist when they were written. That is
a discoverability gap, not a safety one, and it is recorded rather than fixed
here — those files belong to their own repositories.

Fornax's document also retains the original FORNX-30 `settings.local.json`
snippet. That is deliberate: people are still running that setup, and deleting
the only description of a live configuration would leave them with no way to
recognise what they have. It is labelled as the superseded historical route,
with the project-scope replacement trap stated in place.

## Test contract

`scripts/test_statusline_lifecycle.py` carries 41 cases for `explain` in six
groups — what the decode says about a reading, the hand-over to the product,
read-only-and-quiet behaviour, presentation, information depth, and the CLI —
and 5 for migration, one per real starting point plus the preference.

Two of them are the anti-vacuity anchors:

- The decode's quoted fragment is required to appear **verbatim in the line the
  real compositor produces**, so every other assertion about a fragment is an
  assertion about the user's line rather than about a second renderer. That
  anchor holds at *either* information depth, which is what stops `explain` from
  quoting a `detail` fragment to a reader whose line is at `clear`; the readings
  `clear` leaves out are still decoded in full, and marked as not being on the
  line.
- The "recorded explain command never runs on the render path" case then
  asserts that the same registration *does* answer `explain`, so the negative
  is about the render path rather than about a command that never worked.

Every guard in both groups has been run against a deliberately broken
implementation and shown to fail for its own reason: a drifting rendered
fragment, an invented reason clause, a dropped hypothetical marker, an
unavailable provider reported as available, a token absent from the key, a
reproduced stderr, a dropped quote prefix, a raised-instead-of-skipped malformed
registration, a paraphrased missing explain surface, a legend that probes
providers anyway, a settings file echoed into the report, a recorded original
command named in it, a state directory created just to answer, a key pinned to
the default mode, an unreadable preference reported as the reader's own,
narrowing ignored, an unregistered name met with silence, notes treated as a
refusal, a shebang normalised in the user's script, a project-local file tidied
away, product calls stripped from the wrapper, unrelated keys dropped on
migration, and upstream recorded as our own command.

Two of those mutations survived their first attempt, and neither survival was
accepted. A guard that passes because the broken line never executed is not a
guard, so each was pursued to a version that does execute: the first — treating
notes as a refusal — survived because no existing case produced a report with
notes in it, and was answered by writing that case, `test_a_note_is_not_a_refusal`;
the second — stripping product calls out of the wrapper — survived because the
recorded upstream is absent on a first `enable`, and was answered by retargeting
the mutation at `plan.registry_after`, which the first `enable` does write.

The information-depth wiring was put through the same treatment — twelve
mutations, each detected, each by the case that claims the thing it broke: a line
that renders at `detail` whatever the registry says, an unreadable depth widening
instead of narrowing, a switch that reads the host settings file, a switch that
re-runs the registered providers, a preference block rebuilt rather than copied,
an `enable` that normalises a stored depth away, a no-op switch reported as a
change, `explain` quoting a `detail` fragment to a `clear` reader, `explain`
narrowing its decode to what the line happens to show, a left-out reading decoded
without saying it is left out, `doctor` not naming the depth in force, and an
unreadable saved depth reported as the reader's own.

One caveat recorded honestly: a single full-suite run during this work reported
one failure whose name was lost to a truncated pipe. Fifteen subsequent runs,
six of them under artificial CPU load, were clean. The most likely candidate by
inspection is the compositor's concurrency timing assertion. It is not
reproduced and is not claimed to be fixed.

## Status

Implemented and tested against fixtures, including the two information depths.
HORO-1572's safety matrix, compositor and host mutation fixtures, and latency
measurement are in place. What remains is HORO-1628 — giving each product its own
physical line in `detail`, so a multi-product diagnostic view is readable — and
HORO-1627, the gate over both depths: the per-product state matrix, the switching
cases, the mutations for this capability added without weakening the existing
ones, and cold/warm latency at each depth. Behind those sits the Founder DogFood
readability judgement against a real workstation, which is the first test of
whether any of this prose is actually legible to the person reading the line.
