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
