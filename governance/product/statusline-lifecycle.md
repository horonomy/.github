# Statusline install lifecycle (HORO-1566)

How Claude Code's single `statusLine` slot is taken, shared and given back.
Implementation: `scripts/statusline_lifecycle.py`; tests:
`scripts/test_statusline_lifecycle.py`. Read
[`product-integration-safety.md`](./product-integration-safety.md) (ADR-0009)
first — this document does not restate that invariant, it records the
decisions this particular integration made under it, and why. The rendering
half is [`statusline-host-compositor.md`](./statusline-host-compositor.md) and
[`statusline-provider-contract.md`](./statusline-provider-contract.md).

## What is owned, and what that is worth

Claude Code offers exactly one `statusLine.command` per scope. A product that
writes it owns *that one key* and nothing else in the file — not the
`statusLine` object around it, and certainly not the file, which on a real
workstation holds `model`, `permissions`, `hooks` belonging to other products,
and credentials in `env`.

So ownership is marked explicitly, inside the object, with
`statusLine._horonom = {"owner": ..., "version": ...}`. Six ownership states
are distinguished, and the two that look redundant are the load-bearing ones:

| State | Meaning | Why it is not folded into another |
|---|---|---|
| `ABSENT` | no `statusLine` at all | a valid starting state, not an error |
| `USER_OWNED` | someone else's command | the case that must become upstream, not be replaced |
| `HORONOM_OWNED` | our marker **and** our command | the only state that licenses restoration |
| `horonom_command_unmarked` (ADOPTABLE) | our command, no marker | we did not necessarily put it there; guessing is how a foreign config gets claimed |
| `horonom_marker_without_command` (DRIFTED) | our marker, someone else's command | the user changed their mind after installing; their choice wins |
| `UNSUPPORTED_SHAPE` | `null`, a bare string, an unknown `type`, a foreign marker | a future Claude Code feature must not be silently overwritten by ours |

`ADOPTABLE` and `DRIFTED` both mean "reported, never corrected". Detecting
drift and quietly retaking the slot would make upgrade indistinguishable from
install, which a user who deliberately changed their statusline experiences as
the tool fighting them.

## Three decisions a reader is most likely to assume were oversights

**There is no backup of the settings file, and no receipt artifact.** Atomicity
comes from temp-file → fsync → rename → fsync-directory, so an interruption
leaves either the whole old file or the whole new one; a copy adds nothing to
that. What it *does* add is a second copy of a file that routinely holds
credentials, bought for a recovery path nothing reads. ADR-0009 already rejects
backup-as-removal-authority; this goes one step further and declines to create
the artifact at all. The record of what we did is the provider registry's own
`lifecycle` block: only our facts, re-read and re-fingerprinted before every
write.

**Restoration is a delta, not a snapshot.** Releasing the slot writes the
recorded command string back into whatever `statusLine` object is on disk at
that moment. The original object is never stored, because a user who adjusted
`padding` after enabling would lose that adjustment: `A + B + C` must become
`A + C`, and a snapshot restore produces `A`. The only thing remembered about
the user's statusline is its command string, and it is authority over nothing
else — and over nothing at all unless ownership is proven.

**Nothing in the registry records a time.** No `installed_at`, no
`last_modified`. That is what makes repeated enabling idempotent structurally
rather than by everyone remembering to compare before writing: the registry is
a function of the intended state alone, so an operation that changes nothing
produces a byte-identical document and no write happens. A timestamp would make
every `enable` a write to a shared artifact for no reason.

## Write ordering — which crash is survivable

The two files are written in an order chosen so that an interruption between
them can only leave the user's own statusline working:

- **Taking the slot:** registry first, then settings. A crash leaves the
  original command recorded and still in charge.
- **Giving it back:** settings first, then registry. A crash leaves the
  original command restored and merely a stale registry behind it.

In both directions the file that could strand the user is written last. Deletion
of our own state comes after both, and the settings file is never in that list.

## Mutation sequence

read + fingerprint → parse (refuse, never default) → classify ownership →
preserve the entire existing object → construct a minimal patch → re-read and
re-fingerprint both files → atomic write → read back → compare against the
plan → re-validate the registry through the compositor's own parser.

That last step is the one worth naming separately: the interesting question
after a write is not "did our bytes land" but "is the statusline still
working", and only the consumer's parser answers it.

## Disclosure

Plans carry HORO-1000's categories as data (`product_owned_add`,
`product_owned_update`, `product_owned_remove`,
`host_user_state_preserved`, `other_product_state_preserved`,
`unknown_state_blocking_mutation`), the scope of the file being written,
whether ownership is whole-artifact or shared-artifact, and whether OS
authorization is required. `--dry-run` prints the same `Plan` object `apply`
acts on — a preview generated by separate code is a preview of nothing.

Two things are deliberately asymmetric:

- A **plan** names the provider command in full. It is what the user just
  typed, and HORO-1000 requires the material change be disclosed concretely.
- A **doctor** report names only basenames and counts unrelated keys without
  listing them. Its output is what gets pasted into an issue.

Neither surface ever reproduces a value read out of the settings file.

## Test contract

All 14 of [`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)'s
property IDs are cited by name in `test_statusline_lifecycle.py` at the
assertion that proves them, against a deliberately rich fixture. Two of them
needed a real trigger rather than a plausible-looking one:

- `POST_INSTALL_USER_CHANGES_SURVIVE_UPGRADE` is indistinguishable from repair
  unless something about the install actually changes, so it is driven by a
  relocated (symlinked) compositor path — which still classifies as ours,
  because identity is by inode, and is exactly the case where re-deriving from
  a template would discard the user's `padding`.
- `HOST_TOOL_REMAINS_USABLE_AFTER_EACH_LIFECYCLE_STEP` is proven by running the
  configured command through a shell with a Claude-shaped stdin payload at
  every lifecycle step, because that contract is explicit that a test which
  "reads back clean" but breaks the tool doesn't count.

The suite has been run against 15 deliberately broken variants of the
implementation (that contract's mutation table plus whole-document
replacement, template re-derivation, stale restore over drift, index-based
removal, a missing read-back, and a diagnostic that prints the configured
command). All 15 were killed, each by the test that names the property. Two
findings from that run were defects in the *tests*, not the implementation, and
were fixed: the read-back assertion was passing because a different check in
the same function raised, and the diagnostic's privacy assertion only ran
against a slot we already owned.

One mutation was withdrawn rather than deleted: a plan that reproduces the
provider command it is registering survived, and should — see the asymmetry
above.

## Prior art and the shared-library question

Circinus's `src/circinus/adapters/claude_code/installer.py` solves the same
shape of problem in the same language, against the same file, and was read
before this was written. Its `_atomic_replace` is temp-file-then-rename but
does not `fsync`, does not preserve the destination's mode, and does not read
back — the three things added here. Those gaps are Circinus-owned and tracked
in HORO-1020; they are not restated as new findings.

That makes this the second Python implementation of shared-file key-level
ownership against Claude Code's settings, which is precisely the signal
[`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)'s
shared-helper decision names as worth revisiting ("two products in the **same
language** solving the **same shape** of problem"). **Decision: still no shared
library.** Revisiting was the obligation, extracting was not. The two
implementations agree on the reasoning and disagree on almost everything
mechanical — Circinus merges entries into a `hooks.<event>` array and can
tolerate several of its own; this owns a single scalar key that by construction
only one product can hold, which is why the multiplexing lives in the
compositor instead. A helper covering both would be an interface over one
`shlex`-quoted string and one list merge. The shared vocabulary is already
doing the work the code would have.

## Status

The lifecycle is implemented and tested; the Founder DogFood readability and
coexistence gate (HORO-1572) is what remains before it is proven against a real
workstation rather than a fixture.
