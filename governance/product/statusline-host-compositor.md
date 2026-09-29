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
